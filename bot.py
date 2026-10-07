"""Entry point Telegram Document Bot.

Jalankan dengan:  python bot.py
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from telegram import BotCommand, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import Config, ConfigError, load_config
from handlers.callbacks import handle_callback
from handlers.files import handle_document, handle_photo, handle_unsupported
from handlers.pdf import handle_text
from services.pdf_common import PdfProcessingError, get_fitz
from services.pdf_to_word import find_tesseract, list_languages
from services.word_to_pdf import find_libreoffice
from handlers.start import EXPIRED_TEXT, help_command, menu_command, start_command
from utils.file_manager import FileManager
from utils.keyboards import menu_only_keyboard
from utils.session_manager import SessionManager

logger = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        level=getattr(logging, level, logging.INFO),
    )
    # Penting untuk keamanan: httpx mencatat URL request di level INFO,
    # sedangkan URL Telegram API mengandung BOT_TOKEN.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def session_cleanup_loop(application: Application) -> None:
    """Task latar belakang: akhiri session yang tidak aktif + hapus file-nya."""
    config: Config = application.bot_data["config"]
    sessions: SessionManager = application.bot_data["sessions"]
    interval = max(5, min(30, config.session_timeout // 4))

    while True:
        await asyncio.sleep(interval)
        try:
            for session in sessions.expired_sessions():
                sessions.end(session.user_id)  # hapus folder temp user
                logger.info("Session user %s kedaluwarsa dan dibersihkan", session.user_id)
                try:
                    await application.bot.send_message(
                        chat_id=session.chat_id,
                        text=EXPIRED_TEXT,
                        reply_markup=menu_only_keyboard(),
                    )
                except TelegramError:
                    logger.warning("Gagal mengirim notifikasi session expired")
        except Exception:
            logger.exception("Error pada cleanup loop (loop tetap berjalan)")


async def post_init(application: Application) -> None:
    """Dijalankan sekali saat bot start."""
    removed = application.bot_data["files"].cleanup_all()
    if removed:
        logger.info("Membersihkan %d folder temp sisa sesi sebelumnya", removed)

    await application.bot.set_my_commands(
        [
            BotCommand("start", "Tampilkan menu utama"),
            BotCommand("menu", "Tampilkan menu utama"),
            BotCommand("help", "Bantuan & batasan"),
        ]
    )
    # Simpan referensi task supaya tidak di-garbage-collect.
    application.bot_data["cleanup_task"] = asyncio.create_task(
        session_cleanup_loop(application)
    )


async def post_shutdown(application: Application) -> None:
    """Dijalankan saat bot berhenti (Ctrl+C): hentikan task + hapus semua file temp."""
    task = application.bot_data.get("cleanup_task")
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    application.bot_data["sessions"].end_all()
    logger.info("Bot berhenti, semua file temp dihapus")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tangkap semua error yang tidak tertangani supaya bot tidak crash."""
    logger.error("Unhandled exception", exc_info=context.error)

    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Terjadi kesalahan pada bot.\nSilakan coba lagi dengan /start.",
            )
        except TelegramError:
            logger.exception("Gagal mengirim pesan error ke user")


def log_tool_status(config: Config) -> None:
    """Tampilkan di log alat eksternal mana yang tersedia (membantu saat troubleshooting)."""
    office = find_libreoffice(config.libreoffice_path)
    if office:
        logger.info("LibreOffice : %s", office)
    else:
        logger.warning("LibreOffice tidak ditemukan -> Word -> PDF tidak akan berfungsi")

    tesseract = find_tesseract(config.tesseract_path)
    if tesseract:
        logger.info("Tesseract   : %s (bahasa: %s)", tesseract, ", ".join(list_languages(tesseract)) or "?")
    else:
        logger.warning("Tesseract tidak ditemukan -> PDF scan tidak bisa di-OCR")


def build_application(config: Config) -> Application:
    app = (
        Application.builder()
        .token(config.bot_token)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(15)
        .pool_timeout(15)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Objek bersama, diakses dari handler lewat handlers.common.get_services()
    file_manager = FileManager(config.temp_dir)
    app.bot_data["config"] = config
    app.bot_data["files"] = file_manager
    app.bot_data["sessions"] = SessionManager(file_manager, config.session_timeout)

    # Bot ini dirancang untuk chat pribadi (private) agar file tiap user terpisah.
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", start_command, filters=private))
    app.add_handler(CommandHandler("menu", menu_command, filters=private))
    app.add_handler(CommandHandler("help", help_command, filters=private))

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

    setup_logging(config.log_level)
    logger.info("Document Bot dimulai...")

    try:
        get_fitz()
    except PdfProcessingError:
        logger.warning(
            "PyMuPDF belum terpasang: Compress, PDF -> JPG, dan JPG -> PDF tidak akan berfungsi. "
            "Jalankan: python -m pip install -r requirements.txt"
        )

    log_tool_status(config)

    app = build_application(config)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
