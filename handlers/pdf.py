"""Pemrosesan PDF (Phase 3): Merge, Split, Rotate.

Setiap proses mengikuti alur yang sama (_run_job):
    status "Processing..." -> kerjakan di thread -> kirim hasil -> status "complete"
    -> (apa pun hasilnya) hapus session + semua file temp user.
"""
from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path
from typing import Callable

from telegram import InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from handlers.common import get_services
from handlers.start import EXPIRED_TEXT
from services.merge_pdf import merge_pdfs
from services.pdf_common import PdfProcessingError, get_page_count
from services.rotate_pdf import VALID_ANGLES, rotate_pdf
from services.split_pdf import EXAMPLE_HINT, PageSelectionError, parse_page_ranges, split_pdf
from utils.file_manager import sanitize_filename
from utils.file_validator import MSG_CORRUPT
from utils.keyboards import menu_only_keyboard
from utils.session_manager import Session, SessionExpired

logger = logging.getLogger(__name__)

# Batas upload bot ke Telegram (download hanya 20 MB, upload sampai 50 MB).
TELEGRAM_UPLOAD_LIMIT = 50 * 1024 * 1024

MSG_PROCESSING = "⏳ <b>Processing...</b>\n\nPlease wait..."
MSG_DONE = "✅ <b>Processing complete!</b>\n\nYour file is ready."
MSG_FAILED = MSG_CORRUPT + "\n\nSilakan mulai lagi dari menu."
MSG_SEND_FAILED = "❌ Gagal mengirim hasil ke Telegram.\n\nSilakan coba lagi dari menu."
MSG_RESULT_TOO_BIG = (
    "❌ Hasil terlalu besar untuk dikirim lewat Telegram (maksimal 50 MB).\n\n"
    "Coba kurangi jumlah atau ukuran file."
)


async def _edit(
    message: Message, text: str, markup: InlineKeyboardMarkup | None = None
) -> None:
    """Edit pesan status; kegagalan edit tidak boleh menggagalkan proses."""
    try:
        await message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    except TelegramError:
        logger.debug("Gagal mengedit pesan status", exc_info=True)


async def _run_job(
    context: ContextTypes.DEFAULT_TYPE,
    session: Session,
    status: Message,
    *,
    work: Callable[[Path], str],
    output_name: str,
) -> None:
    """Jalankan `work(out_path) -> caption` di thread, kirim hasilnya, lalu bersihkan."""
    _config, sessions, files = get_services(context)
    out_path = files.new_file_path(session.user_id, ".pdf")
    try:
        await _edit(status, MSG_PROCESSING)
        caption = await asyncio.to_thread(work, out_path)

        if out_path.stat().st_size > TELEGRAM_UPLOAD_LIMIT:
            await _edit(status, MSG_RESULT_TOO_BIG, menu_only_keyboard())
            return

        with out_path.open("rb") as fh:
            await context.bot.send_document(
                chat_id=session.chat_id,
                document=fh,
                filename=output_name,
                caption=caption,
                read_timeout=60,
                write_timeout=120,
            )
        await _edit(status, MSG_DONE, menu_only_keyboard())

    except PdfProcessingError as exc:
        await _edit(status, "❌ " + html.escape(exc.user_message), menu_only_keyboard())
    except TelegramError:
        logger.exception("Gagal mengirim hasil ke user %s", session.user_id)
        await _edit(status, MSG_SEND_FAILED, menu_only_keyboard())
    except Exception:
        logger.exception("Pemrosesan gagal untuk user %s", session.user_id)
        await _edit(status, MSG_FAILED, menu_only_keyboard())
    finally:
        sessions.end(session.user_id)  # sukses ataupun gagal: file temp dihapus


def _output_name(prefix: str, original: str) -> str:
    return sanitize_filename(f"{prefix}_{Path(original).stem}.pdf")


# ---------------------------------------------------------------------------
# Merge (dipicu tombol Done)
# ---------------------------------------------------------------------------
async def run_merge(update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session) -> None:
    paths = [f.path for f in session.files]  # urutan = urutan di daftar

    def work(out: Path) -> str:
        total = merge_pdfs(paths, out)
        return f"✅ Merge PDF selesai: {len(paths)} file, {total} halaman."

    await _run_job(
        context, session, update.callback_query.message, work=work, output_name="merged.pdf"
    )


# ---------------------------------------------------------------------------
# Rotate (dipicu tombol pilihan sudut)
# ---------------------------------------------------------------------------
async def run_rotate(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session, angle: int
) -> None:
    if angle not in VALID_ANGLES or not session.files:
        return
    item = session.files[0]

    def work(out: Path) -> str:
        pages = rotate_pdf(item.path, angle, out)
        return f"✅ Rotate PDF selesai: {pages} halaman diputar {angle}° searah jarum jam."

    await _run_job(
        context,
        session,
        update.callback_query.message,
        work=work,
        output_name=_output_name("rotated", item.name),
    )


# ---------------------------------------------------------------------------
# Split (dipicu pesan teks berisi nomor halaman)
# ---------------------------------------------------------------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pesan teks biasa: dipakai untuk input halaman Split; selain itu diberi petunjuk."""
    message = update.effective_message
    user = update.effective_user
    _config, sessions, _files = get_services(context)

    try:
        session = sessions.get(user.id)
    except SessionExpired:
        await message.reply_text(EXPIRED_TEXT, reply_markup=menu_only_keyboard())
        return
    if session is None:
        await message.reply_text("Silakan mulai dari menu dengan /start.")
        return
    if session.awaiting != "pages" or not session.files:
        await message.reply_text(
            "Gunakan tombol di bawah pesan bot, atau kirim file sesuai fitur yang dipilih."
        )
        return

    item = session.files[0]
    total = item.pages
    if total is None:
        try:
            total = await asyncio.to_thread(get_page_count, item.path)
        except Exception:
            logger.warning("Gagal membaca jumlah halaman", exc_info=True)
            sessions.end(user.id)
            await message.reply_text(MSG_FAILED, reply_markup=menu_only_keyboard())
            return

    try:
        pages = parse_page_ranges(message.text or "", total)
    except PageSelectionError as exc:
        await message.reply_text(f"❌ {exc.user_message}\n\n{EXAMPLE_HINT}")
        return  # session tetap hidup, user boleh mengetik ulang

    session.awaiting = None
    if session.panel_message_id:  # hilangkan tombol Cancel di panel lama
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=session.chat_id, message_id=session.panel_message_id, reply_markup=None
            )
        except TelegramError:
            pass

    status = await message.reply_text(MSG_PROCESSING, parse_mode=ParseMode.HTML)

    def work(out: Path) -> str:
        count = split_pdf(item.path, pages, out)
        return f"✅ Split PDF selesai: {count} dari {total} halaman diambil."

    await _run_job(
        context, session, status, work=work, output_name=_output_name("split", item.name)
    )
