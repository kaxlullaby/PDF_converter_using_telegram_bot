"""Pemrosesan dokumen: Merge, Split, Rotate, Compress, PDF <-> JPG, Word <-> PDF.

Semua proses memakai alur yang sama (_run_job):
    status "Processing..." -> kerjakan di thread -> kirim hasil -> status "complete"
    -> (apa pun hasilnya) hapus session + semua file temp user.
"""
from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from telegram import InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from handlers.common import get_services
from handlers.files import format_size
from handlers.start import EXPIRED_TEXT
from services.compress_pdf import PROFILES, compress_pdf
from services.jpg_to_pdf import images_to_pdf
from services.merge_pdf import merge_pdfs
from services.pdf_common import PdfProcessingError, get_page_count
from services.pdf_to_jpg import build_zip, delivery_mode, render_pages
from services.pdf_to_word import KIND_LABELS, ConversionReport, pdf_to_word
from services.rotate_pdf import VALID_ANGLES, rotate_pdf
from services.split_pdf import EXAMPLE_HINT, PageSelectionError, parse_page_ranges, split_pdf
from services.word_to_pdf import word_to_pdf
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


@dataclass
class JobOutput:
    """Hasil sebuah proses: daftar (path_di_disk, nama_saat_dikirim) + keterangan."""

    files: list[tuple[Path, str]]
    caption: str


def _single_pdf(output_name: str, fn: Callable[[Path], str]) -> Callable[[Path], JobOutput]:
    """Bungkus fungsi yang menghasilkan satu PDF: fn(path_output) -> caption."""

    def work(workdir: Path) -> JobOutput:
        out = workdir / "result.pdf"
        caption = fn(out)
        return JobOutput([(out, output_name)], caption)

    return work


def _output_name(prefix: str, original: str, ext: str = ".pdf") -> str:
    return sanitize_filename(f"{prefix}_{Path(original).stem}{ext}")


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
    work: Callable[[Path], JobOutput],
) -> None:
    """Jalankan `work(workdir) -> JobOutput` di thread, kirim hasilnya, lalu bersihkan."""
    _config, sessions, files = get_services(context)
    try:
        workdir = files.new_work_dir(session.user_id)
        await _edit(status, MSG_PROCESSING)
        output = await asyncio.to_thread(work, workdir)

        if any(path.stat().st_size > TELEGRAM_UPLOAD_LIMIT for path, _ in output.files):
            await _edit(status, MSG_RESULT_TOO_BIG, menu_only_keyboard())
            return

        total = len(output.files)
        for index, (path, name) in enumerate(output.files, start=1):
            with path.open("rb") as fh:
                await context.bot.send_document(
                    chat_id=session.chat_id,
                    document=fh,
                    filename=name,
                    caption=output.caption if index == total else None,
                    read_timeout=60,
                    write_timeout=120,
                )
            if index < total:
                await asyncio.sleep(0.3)  # beri jeda agar tidak kena batas kecepatan Telegram
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


# ---------------------------------------------------------------------------
# Merge (tombol Merge PDF)
# ---------------------------------------------------------------------------
async def run_merge(update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session) -> None:
    paths = [f.path for f in session.files]  # urutan = urutan di daftar

    def fn(out: Path) -> str:
        total = merge_pdfs(paths, out)
        return f"✅ Merge PDF selesai: {len(paths)} file, {total} halaman."

    await _run_job(
        context, session, update.callback_query.message, work=_single_pdf("merged.pdf", fn)
    )


# ---------------------------------------------------------------------------
# Rotate (tombol pilihan sudut)
# ---------------------------------------------------------------------------
async def run_rotate(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session, angle: int
) -> None:
    if angle not in VALID_ANGLES or not session.files:
        return
    item = session.files[0]

    def fn(out: Path) -> str:
        pages = rotate_pdf(item.path, angle, out)
        return f"✅ Rotate PDF selesai: {pages} halaman diputar {angle}° searah jarum jam."

    await _run_job(
        context,
        session,
        update.callback_query.message,
        work=_single_pdf(_output_name("rotated", item.name), fn),
    )


# ---------------------------------------------------------------------------
# Compress (tombol pilihan level)
# ---------------------------------------------------------------------------
def compress_caption(level: str, original: int, compressed: int) -> str:
    label = PROFILES[level].label
    if compressed >= original:
        head = f"ℹ️ Compress PDF ({label}): ukuran tidak bisa diperkecil lagi."
        percent = 0
    else:
        head = f"✅ Compress PDF ({label}) selesai."
        percent = round((1 - compressed / original) * 100)
    return (
        f"{head}\n\n"
        f"Original: {format_size(original)}\n"
        f"Compressed: {format_size(compressed)}\n"
        f"Reduction: {percent}%"
    )


async def run_compress(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session, level: str
) -> None:
    if level not in PROFILES or not session.files:
        return
    item = session.files[0]

    def fn(out: Path) -> str:
        original, compressed = compress_pdf(item.path, level, out)
        return compress_caption(level, original, compressed)

    await _run_job(
        context,
        session,
        update.callback_query.message,
        work=_single_pdf(_output_name("compressed", item.name), fn),
    )


# ---------------------------------------------------------------------------
# JPG -> PDF (tombol Convert to PDF)
# ---------------------------------------------------------------------------
async def run_jpg_to_pdf(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session
) -> None:
    paths = [f.path for f in session.files]  # urutan = urutan di daftar

    def fn(out: Path) -> str:
        count = images_to_pdf(paths, out)
        return f"✅ JPG → PDF selesai: {count} gambar menjadi {count} halaman."

    await _run_job(
        context, session, update.callback_query.message, work=_single_pdf("images.pdf", fn)
    )


# ---------------------------------------------------------------------------
# PDF -> JPG (tombol Convert to JPG)
# ---------------------------------------------------------------------------
async def run_pdf_to_jpg(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session
) -> None:
    item = session.files[0]
    stem = Path(item.name).stem

    def work(workdir: Path) -> JobOutput:
        pages = render_pages(item.path, workdir)
        count = len(pages)
        named = [
            (path, _output_name(f"{stem}_page", f"{i:02d}", ".jpg"))
            for i, path in enumerate(pages, start=1)
        ]
        if delivery_mode(count) == "zip":  # lebih dari 10 halaman -> satu file ZIP
            zip_path = workdir / "pages.zip"
            build_zip(named, zip_path)
            return JobOutput(
                [(zip_path, sanitize_filename(f"{stem}_jpg.zip"))],
                f"✅ PDF → JPG selesai: {count} halaman dalam 1 file ZIP.",
            )
        return JobOutput(named, f"✅ PDF → JPG selesai: {count} halaman.")

    await _run_job(context, session, update.callback_query.message, work=work)


# ---------------------------------------------------------------------------
# Word -> PDF (tombol Convert to PDF)
# ---------------------------------------------------------------------------
async def run_word_to_pdf(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session
) -> None:
    config = get_services(context).config
    item = session.files[0]

    def work(workdir: Path) -> JobOutput:
        out = workdir / "result.pdf"
        pages = word_to_pdf(
            item.path, out, soffice_path=config.libreoffice_path, timeout=config.convert_timeout
        )
        caption = (
            f"✅ Word → PDF selesai: {pages} halaman.\n\n"
            "ℹ️ Jika tampilan sedikit berbeda dari aslinya, biasanya karena font "
            "dokumen tidak tersedia di server."
        )
        return JobOutput([(out, sanitize_filename(f"{Path(item.name).stem}.pdf"))], caption)

    await _run_job(context, session, update.callback_query.message, work=work)


# ---------------------------------------------------------------------------
# PDF -> Word (tombol Convert to Word)
# ---------------------------------------------------------------------------
def word_caption(report: ConversionReport) -> str:
    lines = [
        f"✅ PDF → Word selesai: {report.page_count} halaman.",
        "",
        f"Jenis PDF: {KIND_LABELS[report.kind]}",
    ]
    lines += [f"⚠️ {w}" for w in report.warnings]
    lines += ["", "ℹ️ Hasil mungkin tidak 100% mempertahankan layout asli (kolom, tabel, dan gambar tidak ikut)."]
    return "\n".join(lines)


async def run_pdf_to_word(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session
) -> None:
    config = get_services(context).config
    item = session.files[0]

    def work(workdir: Path) -> JobOutput:
        out = workdir / "result.docx"
        report = pdf_to_word(
            item.path,
            out,
            workdir,
            tesseract_path=config.tesseract_path,
            languages=config.ocr_languages,
        )
        return JobOutput(
            [(out, sanitize_filename(f"{Path(item.name).stem}.docx"))], word_caption(report)
        )

    await _run_job(context, session, update.callback_query.message, work=work)


# ---------------------------------------------------------------------------
# Split (pesan teks berisi nomor halaman)
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

    def fn(out: Path) -> str:
        count = split_pdf(item.path, pages, out)
        return f"✅ Split PDF selesai: {count} dari {total} halaman diambil."

    await _run_job(
        context, session, status, work=_single_pdf(_output_name("split", item.name), fn)
    )
