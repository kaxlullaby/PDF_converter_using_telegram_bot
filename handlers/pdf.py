"""Pemrosesan dokumen: Merge, Split, Rotate, Compress, PDF <-> JPG, Word <-> PDF.

Setiap proses berjalan sebagai TASK LATAR BELAKANG (_job_task), sehingga handler langsung
selesai dan bot tetap melayani user lain. Alurnya:

    antre (batas MAX_CONCURRENT_JOBS) -> kerjakan di thread (dengan progress & bisa dibatalkan)
    -> kirim hasil -> catat ke database -> (apa pun hasilnya) hapus session + file temp
"""
from __future__ import annotations

import asyncio
import contextvars
import errno
import html
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from telegram import InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError
from telegram.ext import ContextTypes

from handlers.common import MSG_BUSY, Services, get_services, notify_admins, serialized
from handlers.files import format_size
from handlers.progress import ProgressTracker, render_progress
from handlers.start import EXPIRED_TEXT
from services.compress_pdf import PROFILES, compress_pdf
from services.job_context import JobCancelled, cancel_event, progress_callback
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
from utils.keyboards import job_keyboard, menu_only_keyboard
from utils.session_manager import Session, SessionExpired

logger = logging.getLogger(__name__)

# Batas upload bot ke Telegram (download hanya 20 MB, upload sampai 50 MB).
TELEGRAM_UPLOAD_LIMIT = 50 * 1024 * 1024

MSG_QUEUED = "⏳ <b>Menunggu antrean...</b>\n\nServer sedang sibuk, proses Anda akan segera dimulai."
MSG_DONE = "✅ <b>Processing complete!</b>\n\nYour file is ready."
MSG_FAILED = MSG_CORRUPT + "\n\nSilakan mulai lagi dari menu."
MSG_CANCELLED = "❌ Proses dibatalkan.\n\nFile sementara sudah dihapus."
MSG_SEND_FAILED = "❌ Gagal mengirim hasil ke Telegram.\n\nSilakan coba lagi dari menu."
MSG_STORAGE_FULL = "❌ Server sedang kehabisan penyimpanan.\n\nSilakan coba lagi beberapa saat lagi."
MSG_RESULT_TOO_BIG = (
    "❌ Hasil terlalu besar untuk dikirim lewat Telegram (maksimal 50 MB).\n\n"
    "Coba kurangi jumlah atau ukuran file."
)


class JobTimeout(Exception):
    """Proses melewati PROCESS_TIMEOUT."""


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


def _retry_seconds(exc: RetryAfter) -> float:
    value = getattr(exc, "retry_after", 1)
    return value.total_seconds() if hasattr(value, "total_seconds") else float(value)


async def _send_document(
    bot, chat_id: int, path: Path, name: str, caption: str | None
) -> None:
    """Kirim satu file; ulangi (maks. 3x) jika Telegram membatasi kecepatan atau koneksi putus."""
    for attempt in range(3):
        try:
            with path.open("rb") as fh:
                await bot.send_document(
                    chat_id=chat_id,
                    document=fh,
                    filename=name,
                    caption=caption,
                    read_timeout=60,
                    write_timeout=120,
                )
            return
        except RetryAfter as exc:
            if attempt == 2:
                raise
            await asyncio.sleep(min(_retry_seconds(exc) + 1, 30))
        except BadRequest:
            raise  # permintaan salah: mengulang tidak ada gunanya
        except NetworkError:  # termasuk TimedOut
            if attempt == 2:
                raise
            await asyncio.sleep(2 * (attempt + 1))


async def _execute(
    work: Callable[[Path], JobOutput],
    workdir: Path,
    session: Session,
    tracker: ProgressTracker,
    timeout: float,
) -> JobOutput:
    """Jalankan `work` di thread, membawa konteks pembatalan + progress ke dalamnya."""
    ctx = contextvars.copy_context()
    ctx.run(cancel_event.set, session.cancel_event)
    ctx.run(progress_callback.set, tracker.update)
    future = asyncio.get_running_loop().run_in_executor(None, ctx.run, work, workdir)
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout)
    except (asyncio.TimeoutError, TimeoutError):
        session.cancel_event.set()  # minta thread berhenti di titik periksa berikutnya
        try:
            await asyncio.wait_for(asyncio.shield(future), 15)
        except BaseException:  # noqa: BLE001 - kita hanya memberi kesempatan berhenti
            pass
        raise JobTimeout from None
    except asyncio.CancelledError:  # bot dimatikan
        session.cancel_event.set()
        raise


async def _run_job(
    context: ContextTypes.DEFAULT_TYPE,
    session: Session,
    status: Message,
    *,
    work: Callable[[Path], JobOutput],
    feature_key: str,
) -> None:
    """Mulai proses di latar belakang lalu LANGSUNG kembali (handler tidak tertahan)."""
    services = get_services(context)
    session.busy = True
    session.cancel_event.clear()
    task = asyncio.create_task(
        _job_task(
            context, services, session, status, work, feature_key,
            len(session.files), sum(f.size for f in session.files),
        )
    )
    services.tasks.add(task)
    task.add_done_callback(services.tasks.discard)


async def _job_task(
    context: ContextTypes.DEFAULT_TYPE,
    services: Services,
    session: Session,
    status: Message,
    work: Callable[[Path], JobOutput],
    feature_key: str,
    input_files: int,
    input_bytes: int,
) -> None:
    config, sessions, files, db = services.config, services.sessions, services.files, services.db
    started = time.monotonic()
    outcome, error_text, output_bytes = "failed", None, None
    slot_held = False
    tracker = ProgressTracker(lambda text: _edit(status, text, job_keyboard()))

    try:
        workdir = files.new_work_dir(session.user_id)

        # 1. Antre: hanya MAX_CONCURRENT_JOBS proses berat berjalan bersamaan.
        if services.job_slots.locked():
            await _edit(status, MSG_QUEUED, job_keyboard())
        await services.job_slots.acquire()
        slot_held = True
        if session.cancel_event.is_set():
            raise JobCancelled

        # 2. Kerjakan di thread, dengan progress bar & bisa dibatalkan.
        await _edit(status, render_progress(None, 0), job_keyboard())
        tracker.start()
        output = await _execute(work, workdir, session, tracker, config.process_timeout)
        await tracker.stop()
        services.job_slots.release()  # slot dilepas sebelum mengirim hasil
        slot_held = False

        # 3. Kirim hasil (kecuali user sudah membatalkan).
        if session.cancel_event.is_set():
            raise JobCancelled
        if any(path.stat().st_size > TELEGRAM_UPLOAD_LIMIT for path, _ in output.files):
            error_text = "hasil lebih dari 50 MB"
            await _edit(status, MSG_RESULT_TOO_BIG, menu_only_keyboard())
            return

        total = len(output.files)
        for index, (path, name) in enumerate(output.files, start=1):
            if session.cancel_event.is_set():
                raise JobCancelled
            await _send_document(
                context.bot, session.chat_id, path, name,
                output.caption if index == total else None,
            )
            if index < total:
                await asyncio.sleep(0.3)  # jeda agar tidak kena batas kecepatan Telegram
        output_bytes = sum(path.stat().st_size for path, _ in output.files)
        outcome = "success"
        await _edit(status, MSG_DONE, menu_only_keyboard())

    except JobCancelled:
        outcome = "cancelled"
        await _edit(status, MSG_CANCELLED, menu_only_keyboard())
    except JobTimeout:
        outcome = "timeout"
        error_text = f"melebihi {config.process_timeout} detik"
        await _edit(
            status,
            f"❌ Proses terlalu lama (lebih dari {config.process_timeout} detik) dan dihentikan.\n\n"
            "Coba file yang lebih kecil atau lebih sederhana.",
            menu_only_keyboard(),
        )
    except PdfProcessingError as exc:
        error_text = exc.user_message
        await _edit(status, "❌ " + html.escape(exc.user_message), menu_only_keyboard())
    except TelegramError as exc:
        error_text = f"telegram: {type(exc).__name__}"
        logger.exception("Gagal mengirim hasil ke user %s", session.user_id)
        await _edit(status, MSG_SEND_FAILED, menu_only_keyboard())
    except OSError as exc:
        error_text = f"os: {exc.errno}"
        logger.exception("Error disk pada job %s", feature_key)
        if exc.errno == errno.ENOSPC:
            await notify_admins(context, "⚠️ Disk server penuh! Proses gagal karena kehabisan penyimpanan.")
            await _edit(status, MSG_STORAGE_FULL, menu_only_keyboard())
        else:
            await _edit(status, MSG_FAILED, menu_only_keyboard())
    except asyncio.CancelledError:  # bot sedang dimatikan
        outcome = "cancelled"
        raise
    except Exception as exc:
        error_text = type(exc).__name__
        logger.exception("Pemrosesan %s gagal untuk user %s", feature_key, session.user_id)
        await notify_admins(context, f"⚠️ Proses {feature_key} gagal: {type(exc).__name__}: {exc}")
        await _edit(status, MSG_FAILED, menu_only_keyboard())
    finally:
        await tracker.stop()
        if slot_held:
            services.job_slots.release()
        await db.log_operation(
            session.user_id, feature_key, outcome,
            input_files=input_files, input_bytes=input_bytes,
            output_bytes=output_bytes,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=error_text,
        )
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
        context, session, update.callback_query.message,
        work=_single_pdf("merged.pdf", fn), feature_key="merge",
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
        context, session, update.callback_query.message,
        work=_single_pdf(_output_name("rotated", item.name), fn), feature_key="rotate",
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
        context, session, update.callback_query.message,
        work=_single_pdf(_output_name("compressed", item.name), fn), feature_key="compress",
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
        context, session, update.callback_query.message,
        work=_single_pdf("images.pdf", fn), feature_key="jpg2pdf",
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

    await _run_job(
        context, session, update.callback_query.message, work=work, feature_key="pdf2jpg"
    )


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

    await _run_job(
        context, session, update.callback_query.message, work=work, feature_key="word2pdf"
    )


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

    await _run_job(
        context, session, update.callback_query.message, work=work, feature_key="pdf2word"
    )


# ---------------------------------------------------------------------------
# Split (pesan teks berisi nomor halaman)
# ---------------------------------------------------------------------------
@serialized
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pesan teks biasa: dipakai untuk input halaman Split; selain itu diberi petunjuk."""
    message = update.effective_message
    user = update.effective_user
    services = get_services(context)
    sessions = services.sessions

    try:
        session = sessions.get(user.id)
    except SessionExpired:
        await message.reply_text(EXPIRED_TEXT, reply_markup=menu_only_keyboard())
        return
    if session is None:
        await message.reply_text("Silakan mulai dari menu dengan /start.")
        return
    if session.busy:
        await message.reply_text(MSG_BUSY)
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

    status = await message.reply_text(render_progress(None, 0), parse_mode=ParseMode.HTML)

    def fn(out: Path) -> str:
        count = split_pdf(item.path, pages, out)
        return f"✅ Split PDF selesai: {count} dari {total} halaman diambil."

    await _run_job(
        context, session, status,
        work=_single_pdf(_output_name("split", item.name), fn), feature_key="split",
    )
