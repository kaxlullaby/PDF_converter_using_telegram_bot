"""Helper bersama untuk semua handler."""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Callable, NamedTuple

from telegram import InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from config import Config
from database.database import Database
from utils.file_manager import FileManager
from utils.keyboards import main_menu_keyboard
from utils.session_manager import SessionManager

logger = logging.getLogger(__name__)

MSG_BUSY = (
    "⏳ Masih ada proses yang berjalan.\n\n"
    "Tunggu sampai selesai, atau tekan ❌ Cancel pada pesan proses."
)


class Services(NamedTuple):
    config: Config
    sessions: SessionManager
    files: FileManager
    db: Database
    unavailable: frozenset            # fitur yang disembunyikan (program pendukung belum ada)
    job_slots: asyncio.Semaphore      # batas proses berat yang berjalan bersamaan
    tasks: set                        # referensi task proses latar belakang


def get_services(context: ContextTypes.DEFAULT_TYPE) -> Services:
    """Ambil objek bersama yang didaftarkan di bot.py (bot_data)."""
    data = context.application.bot_data
    return Services(
        data["config"],
        data["sessions"],
        data["files"],
        data["db"],
        data.get("unavailable", frozenset()),
        data["job_slots"],
        data["tasks"],
    )


def menu_keyboard(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    """Menu utama, tanpa fitur yang tidak tersedia di server ini."""
    return main_menu_keyboard(get_services(context).unavailable)


def serialized(handler: Callable) -> Callable:
    """Bungkus handler agar pesan dari USER YANG SAMA diproses satu per satu (berurutan),
    sementara user berbeda tetap berjalan bersamaan. Sekaligus mencatat user ke database."""

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None:
            return await handler(update, context)
        services = get_services(context)
        async with services.sessions.user_lock(user.id):
            await services.db.touch_user(user.id, user.username, user.first_name)
            return await handler(update, context)

    return wrapper


async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Kirim pemberitahuan ke admin (jika ADMIN_IDS diisi). Tidak pernah melempar error."""
    for admin_id in get_services(context).config.admin_ids:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text[:3500])
        except TelegramError:
            logger.warning("Gagal mengirim pemberitahuan ke admin %s", admin_id)
