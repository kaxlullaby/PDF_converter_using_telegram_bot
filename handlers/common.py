"""Helper bersama untuk semua handler."""
from __future__ import annotations

from typing import NamedTuple

from telegram.ext import ContextTypes

from config import Config
from utils.file_manager import FileManager
from utils.session_manager import SessionManager


class Services(NamedTuple):
    config: Config
    sessions: SessionManager
    files: FileManager


def get_services(context: ContextTypes.DEFAULT_TYPE) -> Services:
    """Ambil objek bersama yang didaftarkan di bot.py (bot_data)."""
    data = context.application.bot_data
    return Services(data["config"], data["sessions"], data["files"])
