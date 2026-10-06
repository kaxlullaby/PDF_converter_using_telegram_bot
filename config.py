"""Konfigurasi aplikasi.

Semua nilai (terutama BOT_TOKEN) dibaca dari file .env / environment variable.
Jangan pernah menulis token langsung di source code.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Batas download file untuk bot yang memakai server Telegram resmi (cloud Bot API).
# Bot hanya bisa mengunduh file sampai 20 MB (method getFile) dan mengirim file
# sampai 50 MB. Batas ini bisa dinaikkan hanya jika memakai Local Bot API Server.
TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024

_PLACEHOLDER_TOKENS = {"", "your_telegram_bot_token"}

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Dilempar jika konfigurasi tidak valid."""


@dataclass(frozen=True)
class Config:
    bot_token: str
    max_file_size: int
    max_files: int
    session_timeout: int
    temp_dir: Path
    log_level: str

    @property
    def max_file_size_mb(self) -> int:
        return self.max_file_size // (1024 * 1024)

    @property
    def session_timeout_minutes(self) -> int:
        return max(1, self.session_timeout // 60)


def _get_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} harus berupa angka bulat, bukan '{raw}'.") from None
    if value < minimum:
        raise ConfigError(f"{name} minimal {minimum}, bukan {value}.")
    return value


def load_config() -> Config:
    """Baca environment variable, validasi, lalu kembalikan objek Config."""
    token = os.getenv("BOT_TOKEN", "").strip()
    if token in _PLACEHOLDER_TOKENS:
        raise ConfigError(
            "BOT_TOKEN belum diisi. Salin .env.example menjadi .env, "
            "lalu isi BOT_TOKEN dengan token dari @BotFather."
        )

    max_file_size = _get_int("MAX_FILE_SIZE", 20 * 1024 * 1024)
    if max_file_size > TELEGRAM_DOWNLOAD_LIMIT:
        logger.warning(
            "MAX_FILE_SIZE (%s byte) lebih besar dari batas download Telegram "
            "(20 MB). File di atas 20 MB tetap akan gagal diunduh kecuali "
            "Anda memakai Local Bot API Server.",
            max_file_size,
        )

    temp_dir = BASE_DIR / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    return Config(
        bot_token=token,
        max_file_size=max_file_size,
        max_files=_get_int("MAX_FILES", 20),
        session_timeout=_get_int("SESSION_TIMEOUT", 600, minimum=30),
        temp_dir=temp_dir,
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )
