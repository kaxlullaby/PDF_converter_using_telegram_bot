"""Handler untuk /start, /menu, dan /help + teks pesan yang dipakai bersama."""
from __future__ import annotations

import html

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import Config
from handlers.common import MSG_BUSY, get_services, menu_keyboard, serialized
from utils.keyboards import back_keyboard

MAIN_MENU_TEXT = "📄 <b>DOCUMENT BOT</b>\n\nPilih fitur:"
EXPIRED_TEXT = "⌛ Session expired.\n\nSilakan mulai kembali dengan /start."


def build_help_text(config: Config) -> str:
    return (
        "ℹ️ <b>BANTUAN</b>\n\n"
        "Bot ini membantu mengolah PDF dan dokumen langsung dari Telegram.\n\n"
        "<b>Cara pakai</b>\n"
        "1. Pilih fitur dari menu.\n"
        "2. Kirim file yang diminta (sebagai dokumen/file).\n"
        "3. Tunggu hasilnya dikirim oleh bot.\n\n"
        "<b>Batasan</b>\n"
        f"• Ukuran maksimal per file: {config.max_file_size_mb} MB\n"
        f"• Maksimal {config.max_files} file per operasi (Merge PDF / JPG → PDF)\n"
        f"• Sesi otomatis berakhir setelah {config.session_timeout_minutes} menit tidak aktif\n"
        "• File Anda tidak disimpan permanen dan dihapus setelah proses selesai\n\n"
        "<b>Perintah</b>\n"
        "/start – tampilkan menu utama\n"
        "/menu – tampilkan menu utama\n"
        "/history – riwayat pemakaian Anda\n"
        "/help – tampilkan bantuan ini"
    )


def _reset_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Buang session lama + file temp user. Return False jika ada proses yang masih berjalan."""
    user = update.effective_user
    if user is None:
        return True
    sessions = get_services(context).sessions
    current = sessions.peek(user.id)
    if current is not None and current.busy:
        return False
    sessions.end(user.id)
    return True


@serialized
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start: reset session, sapa user, tampilkan menu utama."""
    if not _reset_session(update, context):
        await update.effective_message.reply_text(MSG_BUSY)
        return
    user = update.effective_user
    name = html.escape(user.first_name) if user and user.first_name else "teman"
    await update.effective_message.reply_text(
        f"👋 Halo, <b>{name}</b>!\n\n{MAIN_MENU_TEXT}",
        reply_markup=menu_keyboard(context),
        parse_mode=ParseMode.HTML,
    )


@serialized
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/menu: reset session, tampilkan menu utama tanpa sapaan."""
    if not _reset_session(update, context):
        await update.effective_message.reply_text(MSG_BUSY)
        return
    await update.effective_message.reply_text(
        MAIN_MENU_TEXT,
        reply_markup=menu_keyboard(context),
        parse_mode=ParseMode.HTML,
    )


@serialized
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help: tampilkan bantuan."""
    config = get_services(context).config
    await update.effective_message.reply_text(
        build_help_text(config),
        reply_markup=back_keyboard(),
        parse_mode=ParseMode.HTML,
    )
