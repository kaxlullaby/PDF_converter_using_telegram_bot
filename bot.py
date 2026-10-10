"""Entry point Telegram Document Bot.

Jalankan dengan:  python bot.py
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram import BotCommand, Update
from telegram.error import BadRequest, Conflict, InvalidToken, NetworkError, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import Config, ConfigError, load_config
from database.database import Database
from handlers.callbacks import handle_callback
from handlers.files import handle_document, handle_photo, handle_unsupported
from handlers.info import history_command, stats_command
from handlers.pdf import handle_text
from handlers.start import EXPIRED_TEXT, help_command, menu_command, start_command
from services.pdf_common import PdfProcessingError, get_fitz
from services.pdf_to_word import find_tesseract, list_languages
from services.word_to_pdf import find_libreoffice
from utils.file_manager import FileManager
from utils.keyboards import menu_only_keyboard
from utils.session_manager import SessionManager

logger = logging.getLogger(__name__)

HOUSEKEEPING_EVERY = 300          # detik: bersihkan sisa file + pantau disk
PURGE_EVERY = 24 * 3600           # detik: hapus riwayat lama dari database
LOW_DISK_BYTES = 1024 ** 3        # peringatan jika ruang disk < 1 GB


class ConciseNetworkErrors(logging.Filter):
    """Ringkas log "Network Retry Loop" dari python-telegram-bot.

    Saat internet putus, pustaka itu mencetak traceback ratusan baris di setiap percobaan.
    Filter ini menggantinya dengan satu baris berisi penyebab; error lain tidak disentuh.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.getMessage().startswith("Network Retry Loop") and record.exc_info:
            exc = record.exc_info[1]
            reason = str(exc) if exc is not None else "tidak diketahui"
            record.msg = f"{record.getMessage()} Penyebab: {reason}"
            record.args = ()
            record.exc_info = record.exc_text = None
        return True


def setup_logging(level: str, log_file: Path | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        )
    for handler in handlers:
        handler.addFilter(ConciseNetworkErrors())
    logging.basicConfig(
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        level=getattr(logging, level, logging.INFO),
        handlers=handlers,
    )
    # Penting untuk keamanan: httpx mencatat URL request di level INFO,
    # sedangkan URL Telegram API mengandung BOT_TOKEN.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # pypdf mencatat peringatan untuk setiap objek PDF yang rusak; file jahat bisa memicu banjir log.
    logging.getLogger("pypdf").setLevel(logging.ERROR)


def compute_unavailable(config: Config) -> frozenset:
    """Fitur yang disembunyikan dari menu karena program pendukungnya belum terpasang."""
    hidden: set[str] = set()

    office = find_libreoffice(config.libreoffice_path)
    if office:
        logger.info("LibreOffice : %s", office)
    else:
        logger.warning("LibreOffice tidak ditemukan -> Word -> PDF disembunyikan dari menu")
        hidden.add("word2pdf")

    tesseract = find_tesseract(config.tesseract_path)
    if tesseract:
        logger.info("Tesseract   : %s (bahasa: %s)", tesseract, ", ".join(list_languages(tesseract)) or "?")
    else:
        logger.warning("Tesseract tidak ditemukan -> PDF scan tidak bisa di-OCR")

    try:
        get_fitz()
    except PdfProcessingError:
        logger.warning(
            "PyMuPDF belum terpasang -> Compress, PDF<->JPG, dan PDF -> Word disembunyikan. "
            "Jalankan: python -m pip install -r requirements.txt"
        )
        hidden.update({"compress", "pdf2jpg", "jpg2pdf", "pdf2word"})
    return frozenset(hidden)


# ---------------------------------------------------------------------------
# Tugas rutin latar belakang
# ---------------------------------------------------------------------------
async def _expire_sessions(application: Application) -> None:
    sessions: SessionManager = application.bot_data["sessions"]
    for session in sessions.expired_sessions():
        sessions.end(session.user_id)  # hapus folder temp user
        logger.info("Session user %s kedaluwarsa dan dibersihkan", session.user_id)
        try:
            await application.bot.send_message(
                chat_id=session.chat_id, text=EXPIRED_TEXT, reply_markup=menu_only_keyboard()
            )
        except TelegramError:
            logger.warning("Gagal mengirim notifikasi session expired")


async def _housekeeping(application: Application) -> None:
    config: Config = application.bot_data["config"]
    sessions: SessionManager = application.bot_data["sessions"]
    files: FileManager = application.bot_data["files"]

    removed = files.cleanup_orphans(sessions.active_user_ids(), older_than=config.session_timeout + 120)
    if removed:
        logger.warning("Membersihkan %d folder temp yatim", removed)
    sessions.prune_locks()

    free = files.free_bytes()
    if free < LOW_DISK_BYTES:
        text = f"⚠️ Ruang disk server tinggal {free / 1024 ** 2:.0f} MB!"
        logger.warning(text)
        for admin_id in config.admin_ids:
            with contextlib.suppress(TelegramError):
                await application.bot.send_message(chat_id=admin_id, text=text)


async def maintenance_loop(application: Application) -> None:
    """Berjalan terus: akhiri session tidak aktif, bersihkan sisa file, rapikan database."""
    config: Config = application.bot_data["config"]
    db: Database = application.bot_data["db"]
    interval = max(5, min(30, config.session_timeout // 4))
    last_housekeeping = time.monotonic()
    last_purge = float("-inf")

    while True:
        await asyncio.sleep(interval)
        try:
            await _expire_sessions(application)
            now = time.monotonic()
            if now - last_housekeeping >= HOUSEKEEPING_EVERY:
                last_housekeeping = now
                await _housekeeping(application)
            if now - last_purge >= PURGE_EVERY:
                last_purge = now
                deleted = await db.purge_older_than(config.history_retention_days)
                if deleted:
                    logger.info("Menghapus %d riwayat operasi lama", deleted)
        except Exception:
            logger.exception("Error pada maintenance loop (loop tetap berjalan)")


async def post_init(application: Application) -> None:
    """Dijalankan sekali saat bot start (di dalam event loop)."""
    removed = application.bot_data["files"].cleanup_all()
    if removed:
        logger.info("Membersihkan %d folder temp sisa sesi sebelumnya", removed)

    config: Config = application.bot_data["config"]
    application.bot_data["job_slots"] = asyncio.Semaphore(config.max_concurrent_jobs)
    application.bot_data["tasks"] = set()

    await application.bot.set_my_commands(
        [
            BotCommand("start", "Tampilkan menu utama"),
            BotCommand("menu", "Tampilkan menu utama"),
            BotCommand("history", "Riwayat pemakaian Anda"),
            BotCommand("help", "Bantuan & batasan"),
        ]
    )
    # Simpan referensi task supaya tidak di-garbage-collect.
    application.bot_data["maintenance_task"] = asyncio.create_task(maintenance_loop(application))


async def post_shutdown(application: Application) -> None:
    """Dijalankan saat bot berhenti (Ctrl+C): hentikan semua proses + hapus semua file temp."""
    sessions: SessionManager = application.bot_data["sessions"]
    sessions.request_cancel_all()

    jobs = list(application.bot_data.get("tasks", ()))
    for task in jobs:
        task.cancel()
    if jobs:
        await asyncio.gather(*jobs, return_exceptions=True)

    maintenance = application.bot_data.get("maintenance_task")
    if maintenance:
        maintenance.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await maintenance

    sessions.end_all()
    logger.info("Bot berhenti, semua file temp dihapus")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tangkap semua error yang tidak tertangani supaya bot tidak crash."""
    error = context.error

    if isinstance(error, Conflict):
        logger.error(
            "Conflict: ada instance bot lain yang memakai token yang sama. "
            "Hentikan salah satunya."
        )
        return
    if isinstance(error, NetworkError) and not isinstance(error, BadRequest):
        logger.warning("Gangguan jaringan sesaat: %s", error)  # PTB mencoba lagi sendiri
        return

    logger.error("Unhandled exception", exc_info=error)

    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Terjadi kesalahan pada bot.\nSilakan coba lagi dengan /start.",
            )
        except TelegramError:
            logger.exception("Gagal mengirim pesan error ke user")

    config: Config | None = context.application.bot_data.get("config")
    if config is not None:
        for admin_id in config.admin_ids:
            with contextlib.suppress(TelegramError):
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"⚠️ Error: {type(error).__name__}: {str(error)[:300]}",
                )


def build_application(config: Config, unavailable: frozenset = frozenset()) -> Application:
    app = (
        Application.builder()
        .token(config.bot_token)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(15)
        .pool_timeout(15)
        .concurrent_updates(64)  # user berbeda dilayani bersamaan (user sama tetap berurutan)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Objek bersama, diakses dari handler lewat handlers.common.get_services()
    file_manager = FileManager(config.temp_dir)
    app.bot_data["config"] = config
    app.bot_data["files"] = file_manager
    app.bot_data["sessions"] = SessionManager(file_manager, config.session_timeout)
    app.bot_data["db"] = Database(config.database_path)
    app.bot_data["unavailable"] = unavailable

    # Bot ini dirancang untuk chat pribadi (private) agar file tiap user terpisah.
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", start_command, filters=private))
    app.add_handler(CommandHandler("menu", menu_command, filters=private))
    app.add_handler(CommandHandler("help", help_command, filters=private))
    app.add_handler(CommandHandler("history", history_command, filters=private))
    app.add_handler(CommandHandler("stats", stats_command, filters=private))

    # Semua tombol Inline Keyboard masuk ke satu router.
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Penerimaan file. Urutan penting: handler pertama yang cocok yang dipakai.
    app.add_handler(MessageHandler(filters.Document.ALL & private, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO & private, handle_photo))
    app.add_handler(MessageHandler(filters.ATTACHMENT & private, handle_unsupported))

    # Pesan teks biasa (mis. nomor halaman untuk Split PDF).
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & private, handle_text))

    app.add_error_handler(error_handler)
    return app


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        raise SystemExit(f"❌ Konfigurasi error: {exc}") from None

    setup_logging(config.log_level, config.log_file)
    logger.info("Document Bot dimulai...")

    unavailable = compute_unavailable(config)
    app = build_application(config, unavailable)
    try:
        # bootstrap_retries=-1: jika internet belum siap saat start (mis. setelah komputer menyala),
        # bot menunggu dan mencoba lagi terus-menerus, bukan langsung berhenti.
        app.run_polling(
            allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, bootstrap_retries=-1
        )
    except InvalidToken:
        raise SystemExit(
            "❌ Token ditolak Telegram.\n"
            "Periksa BOT_TOKEN di file .env (salin ulang dari @BotFather, tanpa spasi/tanda kutip)."
        ) from None
    except NetworkError as exc:
        raise SystemExit(
            f"❌ Tidak bisa terhubung ke Telegram: {exc}\n"
            "Periksa internet, DNS, VPN/proxy, atau firewall. Diagnosis: python doctor.py --online"
        ) from None


if __name__ == "__main__":
    main()
