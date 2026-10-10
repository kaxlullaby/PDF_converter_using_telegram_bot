"""Pemeriksa kesiapan Document Bot. Jalankan SEBELUM menjalankan / men-deploy bot.

    python doctor.py             periksa semuanya (tanpa internet)
    python doctor.py --online    juga cek token ke Telegram (memanggil getMe)

Kode keluar: 0 = siap (peringatan boleh ada), 1 = ada pemeriksaan yang GAGAL.
Token tidak pernah dicetak.
"""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

OK, WARN, FAIL = "OK", "PERINGATAN", "GAGAL"
MIN_PYTHON = (3, 10)
LOW_DISK_WARN = 1024 ** 3        # 1 GB
LOW_DISK_FAIL = 200 * 1024 ** 2  # 200 MB (di bawah ini bot menolak semua upload)
TOKEN_PATTERN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")

# (nama paket di pip, wajib?, dipakai untuk)
PACKAGES = (
    ("python-telegram-bot", True, "koneksi ke Telegram"),
    ("python-dotenv", True, "membaca file .env"),
    ("pypdf", True, "Merge, Split, Rotate, validasi PDF"),
    ("Pillow", True, "validasi dan olah gambar"),
    ("python-docx", True, "PDF -> Word"),
    ("PyMuPDF", False, "Compress, PDF <-> JPG, PDF -> Word; tanpa ini 4 fitur dimatikan"),
)


@dataclass(frozen=True)
class Finding:
    level: str
    text: str


def check_python() -> list[Finding]:
    version = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info < MIN_PYTHON:
        return [Finding(FAIL, f"Python {version} terlalu lama; butuh {MIN_PYTHON[0]}.{MIN_PYTHON[1]} atau lebih baru")]
    return [Finding(OK, f"Python {version}")]


def check_packages() -> list[Finding]:
    findings = []
    for name, required, purpose in PACKAGES:
        try:
            findings.append(Finding(OK, f"{name} {metadata.version(name)}"))
        except metadata.PackageNotFoundError:
            hint = "python -m pip install -r requirements.txt"
            findings.append(Finding(FAIL if required else WARN, f"{name} belum terpasang ({purpose}). Jalankan: {hint}"))
    return findings


def load_settings():
    """Return (config, error). Hanya salah satunya yang terisi."""
    try:
        import config as cfg
        return cfg.load_config(), None
    except Exception as exc:  # ConfigError atau gagal impor
        return None, str(exc)


def check_config(config, error: str | None) -> list[Finding]:
    if error:
        return [Finding(FAIL, f"Konfigurasi: {error}")]
    findings = [Finding(OK, f"Konfigurasi terbaca (batas {config.max_file_size_mb} MB/file, {config.max_files} file/proses, "
                            f"sesi {config.session_timeout_minutes} menit, {config.max_concurrent_jobs} proses bersamaan)")]
    if not TOKEN_PATTERN.match(config.bot_token):
        findings.append(Finding(WARN, "BOT_TOKEN tidak berbentuk token Telegram biasa (angka:huruf). Salin ulang dari @BotFather"))
    if not config.admin_ids:
        findings.append(Finding(WARN, "ADMIN_IDS kosong: tidak ada yang menerima pemberitahuan error / bisa memakai /stats"))
    return findings


def check_programs(config) -> list[Finding]:
    try:
        from services.pdf_to_word import find_tesseract, list_languages
        from services.word_to_pdf import find_libreoffice
    except ImportError as exc:
        return [Finding(FAIL, f"Tidak bisa memeriksa program pendukung karena paket belum lengkap ({exc.name})")]

    findings = []
    office = find_libreoffice(config.libreoffice_path if config else None)
    findings.append(Finding(OK, f"LibreOffice: {office}") if office else
                    Finding(WARN, "LibreOffice tidak ditemukan: Word -> PDF disembunyikan dari menu (isi LIBREOFFICE_PATH jika sudah terpasang)"))
    tesseract = find_tesseract(config.tesseract_path if config else None)
    if not tesseract:
        findings.append(Finding(WARN, "Tesseract tidak ditemukan: PDF hasil scan tidak bisa dibaca (OCR). Isi TESSERACT_PATH jika sudah terpasang"))
    else:
        languages = list_languages(tesseract)
        findings.append(Finding(OK, f"Tesseract: {tesseract} (bahasa: {', '.join(languages) or '?'})"))
        wanted = [lang for lang in (config.ocr_languages.split("+") if config else []) if lang]
        missing = [lang for lang in wanted if lang not in languages]
        if missing:
            findings.append(Finding(WARN, f"Bahasa OCR belum terpasang: {', '.join(missing)} (akan memakai bahasa yang tersedia)"))
    return findings


def _writable(folder: Path) -> str | None:
    """None jika bisa ditulis, selain itu alasan gagalnya."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".doctor_probe"
        probe.write_bytes(b"x")
        probe.unlink()
        return None
    except OSError as exc:
        return exc.strerror or str(exc)


def check_paths(config) -> list[Finding]:
    if config is None:
        return []
    folders = {"temp": config.temp_dir, "database": config.database_path.parent}
    if config.log_file is not None:
        folders["log"] = config.log_file.parent
    findings = []
    for label, folder in folders.items():
        problem = _writable(folder)
        findings.append(Finding(OK, f"Folder {label} bisa ditulis: {folder}") if problem is None else
                        Finding(FAIL, f"Folder {label} TIDAK bisa ditulis ({problem}): {folder}"))
    free = shutil.disk_usage(config.temp_dir).free if config.temp_dir.exists() else None
    if free is not None:
        text = f"Ruang disk kosong: {free / 1024 ** 3:.1f} GB"
        level = FAIL if free < LOW_DISK_FAIL else WARN if free < LOW_DISK_WARN else OK
        findings.append(Finding(level, text + ("" if level == OK else " (terlalu sedikit)")))
    return findings


def check_online(config) -> list[Finding]:
    if config is None:
        return []
    url = f"https://api.telegram.org/bot{config.bot_token}/getMe"  # jangan pernah dicetak
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 404):
            return [Finding(FAIL, "Telegram menolak token (salah ketik atau sudah di-revoke). Buat ulang lewat @BotFather")]
        return [Finding(FAIL, f"Telegram membalas error HTTP {exc.code}")]
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if any(key in str(reason).lower() for key in ("getaddrinfo", "name or service", "name resolution", "11001")):
            return [Finding(FAIL, "DNS gagal menerjemahkan api.telegram.org (internet putus, DNS bermasalah, atau situs diblokir). "
                                  "Coba: ganti DNS ke 8.8.8.8 / 1.1.1.1, matikan/nyalakan VPN, buka https://api.telegram.org di browser")]
        return [Finding(FAIL, f"Tidak bisa terhubung ke Telegram ({reason}). Periksa internet / proxy / firewall")]
    except ValueError:
        return [Finding(FAIL, "Balasan Telegram tidak bisa dibaca")]
    if data.get("ok"):
        return [Finding(OK, f"Token valid: bot @{data['result'].get('username', '?')}")]
    return [Finding(FAIL, "Telegram menolak token")]


def run_checks(online: bool) -> list[Finding]:
    config, error = load_settings()
    findings = check_python() + check_packages() + check_config(config, error) + check_programs(config) + check_paths(config)
    if online:
        findings += check_online(config)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Periksa kesiapan Document Bot")
    parser.add_argument("--online", action="store_true", help="juga cek token ke Telegram (butuh internet)")
    args = parser.parse_args(argv)

    findings = run_checks(args.online)
    for item in findings:
        print(f"[{item.level:<10}] {item.text}")
    fails = sum(f.level == FAIL for f in findings)
    warns = sum(f.level == WARN for f in findings)
    print()
    if fails:
        print(f"{fails} pemeriksaan GAGAL, {warns} peringatan. Perbaiki yang GAGAL sebelum menjalankan bot.")
        return 1
    print(f"Siap dijalankan ({warns} peringatan)." if warns else "Siap dijalankan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
