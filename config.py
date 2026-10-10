"""Konfigurasi aplikasi.

Semua nilai (terutama BOT_TOKEN) dibaca dari file .env / environment variable.
Jangan pernah menulis token langsung di source code.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
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
    # repr=False: token tidak ikut tercetak jika objek config tak sengaja masuk log
    bot_token: str = field(repr=False)
    max_file_size: int
    max_files: int
    max_session_size: int
    session_timeout: int
    temp_dir: Path
    log_level: str
    libreoffice_path: str | None = None
    tesseract_path: str | None = None
    ocr_languages: str = "ind+eng"
    convert_timeout: int = 90
    database_path: Path = Path("data/bot.db")
    log_file: Path | None = None
    max_concurrent_jobs: int = 2
    process_timeout: int = 300
    admin_ids: frozenset = frozenset()
    history_retention_days: int = 90

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


def _resolve_path(raw: str | None, default: str) -> Path:
    """Path relatif dihitung dari folder project."""
    path = Path((raw or "").strip() or default)
    return path if path.is_absolute() else BASE_DIR / path


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

    ocr_languages = os.getenv("OCR_LANGUAGES", "ind+eng").strip() or "ind+eng"
    if not re.fullmatch(r"[A-Za-z0-9_]+(\+[A-Za-z0-9_]+)*", ocr_languages):
        raise ConfigError("OCR_LANGUAGES tidak valid. Contoh yang benar: ind+eng")

    raw_ids = os.getenv("ADMIN_IDS", "").strip()
    try:
        admin_ids = frozenset(int(x) for x in re.split(r"[,\s]+", raw_ids) if x)
    except ValueError:
        raise ConfigError("ADMIN_IDS harus berupa angka dipisah koma, mis. 12345,67890") from None

    database_path = _resolve_path(os.getenv("DATABASE_PATH"), "data/bot.db")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    log_value = (os.getenv("LOG_FILE") or "logs/bot.log").strip()
    log_file = None if log_value.lower() in ("off", "none", "-") else _resolve_path(log_value, "logs/bot.log")

    temp_dir = BASE_DIR / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    return Config(
        bot_token=token,
        max_file_size=max_file_size,
        max_files=_get_int("MAX_FILES", 20),
        max_session_size=_get_int("MAX_SESSION_SIZE", 100 * 1024 * 1024),
        session_timeout=_get_int("SESSION_TIMEOUT", 600, minimum=30),
        temp_dir=temp_dir,
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        libreoffice_path=os.getenv("LIBREOFFICE_PATH", "").strip() or None,
        tesseract_path=os.getenv("TESSERACT_PATH", "").strip() or None,
        ocr_languages=ocr_languages,
        convert_timeout=_get_int("CONVERT_TIMEOUT", 90, minimum=10),
        database_path=database_path,
        log_file=log_file,
        max_concurrent_jobs=_get_int("MAX_CONCURRENT_JOBS", 2),
        process_timeout=_get_int("PROCESS_TIMEOUT", 300, minimum=30),
        admin_ids=admin_ids,
        history_retention_days=_get_int("HISTORY_RETENTION_DAYS", 90),
    )
