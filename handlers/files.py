"""Penerimaan file dari user + tampilan daftar file ("panel").

Alur satu file masuk:
    cek session -> cek batas jumlah -> validasi metadata -> cek disk
    -> download (nama acak) -> validasi isi (thread) -> simpan ke session -> tampilkan panel
"""
from __future__ import annotations

import asyncio
import html
import logging
import uuid

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import ContextTypes

from config import Config
from handlers.common import MSG_BUSY, get_services, menu_keyboard, serialized
from handlers.start import EXPIRED_TEXT
from utils.file_manager import StorageError, sanitize_filename
from utils.file_validator import (
    KIND_LABELS,
    MSG_CORRUPT,
    ValidationError,
    check_metadata,
    inspect_content,
    too_large_message,
)
from utils.keyboards import (
    Feature,
    FEATURES,
    add_more_keyboard,
    cancel_only_keyboard,
    collecting_keyboard,
    compress_keyboard,
    menu_only_keyboard,
    nav_keyboard,
    reorder_keyboard,
    rotate_keyboard,
)
from utils.session_manager import Session, SessionExpired, SessionFile

logger = logging.getLogger(__name__)

# Fitur yang setelah menerima file menunggu input teks dari user.
AWAIT_INPUT = {"split": "pages"}

MSG_STORAGE_FULL = "❌ Server sedang kehabisan penyimpanan.\n\nSilakan coba lagi beberapa saat lagi."
MSG_DOWNLOAD_FAILED = "❌ Gagal mengunduh file dari Telegram.\n\nSilakan kirim ulang file Anda."


# ---------------------------------------------------------------------------
# Helper tampilan
# ---------------------------------------------------------------------------
def max_files_for(feature: Feature, config: Config) -> int:
    return config.max_files if feature.multi else 1


def format_size(num_bytes: int) -> str:
    if num_bytes < 1024 * 1024:
        return f"{max(1, round(num_bytes / 1024))} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def _file_lines(session: Session) -> str:
    return "\n".join(
        f"{i}. {html.escape(f.name)}" for i, f in enumerate(session.files, start=1)
    )


def build_prompt(feature: Feature, config: Config) -> tuple[str, InlineKeyboardMarkup]:
    """Layar awal setelah user memilih fitur (belum ada file)."""
    kind = KIND_LABELS[feature.kind]
    if feature.multi:
        limit = config.max_files
        text = (
            f"📂 <b>{feature.label}</b>\n\n"
            f"Kirim file {kind} satu per satu.\n\n"
            f"Files: 0/{limit}\n\n"
            "Silakan kirim file pertama."
        )
    else:
        text = (
            f"📂 <b>{feature.label}</b>\n\n"
            f"Kirim 1 file {kind}.\n"
            f"Maksimal {config.max_file_size_mb} MB."
        )
    return text, nav_keyboard()


def build_panel(
    session: Session, feature: Feature, config: Config
) -> tuple[str, InlineKeyboardMarkup]:
    """Daftar file yang sudah diterima + tombol aksi."""
    if not session.files:
        return build_prompt(feature, config)

    if feature.multi:
        limit = config.max_files
        count = len(session.files)
        if count < feature.min_files:
            footer = f"Minimal {feature.min_files} file untuk diproses. Silakan kirim file berikutnya."
        elif count >= limit:
            footer = "Batas file tercapai. Tekan Done untuk memproses."
        else:
            footer = "Silakan kirim file berikutnya."
        text = (
            f"📂 <b>{feature.label}</b>\n\n"
            f"Files: {count}/{limit}\n\n"
            f"{_file_lines(session)}\n\n{footer}"
        )
    else:
        item = session.files[0]
        pages = f", {item.pages} halaman" if item.pages else ""
        received = f"✅ File diterima:\n{html.escape(item.name)} ({format_size(item.size)}{pages})"
        if feature.key == "split":
            text = (
                f"📂 <b>{feature.label}</b>\n\n{received}\n\n"
                "Masukkan halaman yang ingin dipisahkan.\n\n"
                "Contoh:\n<code>1-5</code>\n<code>2,4,7</code>\n<code>3-8</code>\n\n"
                "Hasil mengikuti urutan yang Anda ketik."
            )
            return text, cancel_only_keyboard()
        if feature.key == "rotate":
            text = (
                f"📂 <b>{feature.label}</b>\n\n{received}\n\n"
                "Pilih rotasi (searah jarum jam):"
            )
            return text, rotate_keyboard()
        if feature.key == "compress":
            text = (
                f"📂 <b>{feature.label}</b>\n\n{received}\n\n"
                "Pilih tingkat kompresi:\n\n"
                "🟢 <b>Low</b> – kualitas hampir sama, ukuran sedikit berkurang\n"
                "🟡 <b>Medium</b> – seimbang antara kualitas dan ukuran\n"
                "🔴 <b>High</b> – ukuran terkecil, kualitas gambar menurun"
            )
            return text, compress_keyboard()
        if feature.key == "pdf2jpg":
            text = (
                f"📂 <b>{feature.label}</b>\n\n{received}\n\n"
                "Setiap halaman akan dikonversi menjadi satu gambar JPG.\n"
                "Tekan tombol di bawah untuk memulai."
            )
            return text, collecting_keyboard(False, feature.done_label)
        if feature.key == "word2pdf":
            text = (
                f"📂 <b>{feature.label}</b>\n\n{received}\n\n"
                "Dokumen akan dikonversi ke PDF dengan LibreOffice; format dipertahankan sebisa mungkin.\n"
                "Tekan tombol di bawah untuk memulai."
            )
            return text, collecting_keyboard(False, feature.done_label)
        if feature.key == "pdf2word":
            text = (
                f"📂 <b>{feature.label}</b>\n\n{received}\n\n"
                "Bot akan mengenali jenis PDF:\n"
                "📄 <b>Text-based</b> – teks diambil langsung\n"
                "🖼 <b>Scanned</b> – dibaca dengan OCR\n\n"
                "Catatan: hasil mungkin tidak 100% mempertahankan layout asli.\n"
                "Tekan tombol di bawah untuk memulai."
            )
            return text, collecting_keyboard(False, feature.done_label)
        # Cadangan umum untuk fitur satu-file tanpa panel khusus.
        text = f"📂 <b>{feature.label}</b>\n\n{received}\n\nTekan tombol di bawah untuk memulai."
    return text, collecting_keyboard(feature.multi, feature.done_label)


def build_add_more(
    session: Session, feature: Feature, config: Config
) -> tuple[str, InlineKeyboardMarkup]:
    count = len(session.files)
    text = (
        f"📂 <b>{feature.label}</b>\n\n"
        f"Files: {count}/{config.max_files}\n\n"
        "📥 Silakan kirim file berikutnya sekarang."
    )
    return text, add_more_keyboard(feature.done_label)


def build_reorder(session: Session, feature: Feature) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"🔄 <b>Ubah urutan — {feature.label}</b>\n\n"
        "Gunakan ⬆️ ⬇️ untuk memindahkan dan 🗑 untuk menghapus.\n\n"
        f"{_file_lines(session)}"
    )
    return text, reorder_keyboard([f.fid for f in session.files])


async def refresh_panel(
    context: ContextTypes.DEFAULT_TYPE, session: Session, feature: Feature, config: Config
) -> None:
    """Kirim panel baru lalu hapus panel lama, supaya selalu ada di paling bawah chat."""
    text, markup = build_panel(session, feature, config)
    old_id = session.panel_message_id
    sent = await context.bot.send_message(
        chat_id=session.chat_id, text=text, reply_markup=markup, parse_mode=ParseMode.HTML
    )
    session.panel_message_id = sent.message_id
    if old_id:
        try:
            await context.bot.delete_message(chat_id=session.chat_id, message_id=old_id)
        except TelegramError:
            pass  # pesan lama mungkin sudah dihapus user / terlalu lama


# ---------------------------------------------------------------------------
# Penerimaan file
# ---------------------------------------------------------------------------
async def _receive(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    file_id: str,
    file_name: str | None,
    mime_type: str | None,
    file_size: int | None,
    is_photo: bool = False,
) -> None:
    message = update.effective_message
    user = update.effective_user
    services = get_services(context)
    config, sessions, files = services.config, services.sessions, services.files

    # 1. Harus ada session aktif (user sudah memilih fitur)
    try:
        session = sessions.get(user.id)
    except SessionExpired:
        await message.reply_text(EXPIRED_TEXT, reply_markup=menu_only_keyboard())
        return
    if session is None:
        await message.reply_text(
            "📂 Pilih fitur terlebih dahulu sebelum mengirim file.",
            reply_markup=menu_keyboard(context),
        )
        return
    if session.busy:  # proses sebelumnya masih berjalan
        await message.reply_text(MSG_BUSY)
        return

    feature = FEATURES[session.feature_key]
    limit = max_files_for(feature, config)

    # 2. Batas jumlah file
    if len(session.files) >= limit:
        text = f"❌ Maximum {limit} file{'s' if limit > 1 else ''} per operation."
        if not feature.multi:
            text += "\n\nTekan Cancel jika ingin memakai file lain."
        await message.reply_text(text)
        return

    # 2b. Batas total ukuran semua file dalam satu proses
    used = sum(f.size for f in session.files)
    if used + (file_size or 0) > config.max_session_size:
        await message.reply_text(
            "❌ Total ukuran file dalam satu proses terlalu besar.\n\n"
            f"Maksimal {config.max_session_size // (1024 * 1024)} MB per proses."
        )
        return

    # 3. Validasi metadata + disk (sebelum download)
    if is_photo:
        file_name = f"photo_{len(session.files) + 1}.jpg"
    try:
        ext = check_metadata(file_name, mime_type, file_size, feature.kind, config.max_file_size)
        files.check_disk_space(file_size or 0)
    except ValidationError as exc:
        await message.reply_text(exc.user_message)
        return
    except StorageError:
        await message.reply_text(MSG_STORAGE_FULL)
        return

    # 4. Download ke nama acak di folder temp user
    dest = files.new_file_path(user.id, ext)
    try:
        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(custom_path=dest)
    except BadRequest as exc:
        files.delete_file(dest)
        if "too big" in str(exc).lower():
            await message.reply_text(too_large_message(config.max_file_size))
        else:
            logger.warning("get_file gagal: %s", exc)
            await message.reply_text(MSG_DOWNLOAD_FAILED)
        return
    except (NetworkError, TelegramError):  # NetworkError termasuk TimedOut
        files.delete_file(dest)
        logger.warning("Download gagal", exc_info=True)
        await message.reply_text(MSG_DOWNLOAD_FAILED)
        return
    except OSError:
        files.delete_file(dest)
        logger.exception("Gagal menulis file temp")
        await message.reply_text(MSG_STORAGE_FULL)
        return

    # 5. Validasi isi file (magic bytes + bisa dibaca library) di thread terpisah
    try:
        info = await asyncio.to_thread(
            inspect_content, dest, feature.kind, config.max_file_size
        )
    except ValidationError as exc:
        files.delete_file(dest)
        await message.reply_text(exc.user_message)
        return
    except Exception:
        files.delete_file(dest)
        logger.exception("Validasi isi file error")
        await message.reply_text(MSG_CORRUPT)
        return

    # Session bisa saja kedaluwarsa selama download berlangsung.
    if not sessions.is_active(session):
        files.delete_file(dest)
        await message.reply_text(EXPIRED_TEXT, reply_markup=menu_only_keyboard())
        return

    # 6. Simpan ke session lalu tampilkan daftar terbaru
    session.touch()
    session.files.append(
        SessionFile(
            fid=uuid.uuid4().hex[:6],
            name=sanitize_filename(file_name),
            path=dest,
            size=dest.stat().st_size,
            kind=info.detected,
            pages=info.pages,
        )
    )
    session.awaiting = AWAIT_INPUT.get(feature.key)
    await refresh_panel(context, session, feature, config)


@serialized
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.effective_message.document
    await _receive(
        update,
        context,
        file_id=doc.file_id,
        file_name=doc.file_name,
        mime_type=doc.mime_type,
        file_size=doc.file_size,
    )


@serialized
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Foto yang dikirim sebagai 'photo' (sudah dikompres Telegram jadi JPEG)."""
    photo = update.effective_message.photo[-1]  # resolusi terbesar
    await _receive(
        update,
        context,
        file_id=photo.file_id,
        file_name=None,
        mime_type="image/jpeg",
        file_size=photo.file_size,
        is_photo=True,
    )


@serialized
async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Video, audio, voice, sticker, dll."""
    await update.effective_message.reply_text(
        "❌ Jenis pesan ini tidak didukung.\n\n"
        "Kirim file sebagai dokumen (📎 → File) atau sebagai foto."
    )
