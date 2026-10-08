"""Pengelolaan file sementara per user.

Struktur:
    temp/
    ├── user_12345/
    │   ├── <random>.pdf
    │   └── <random>.pdf
    └── user_67890/
        └── <random>.jpg

- Satu folder per user_id  -> file antar user tidak bisa tercampur.
- Nama file di disk acak (uuid) -> nama dari user tidak pernah dipakai sebagai path.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Sisakan ruang kosong minimal ini di disk sebelum menerima file baru.
MIN_FREE_BYTES = 200 * 1024 * 1024

_SAFE_EXT = re.compile(r"^\.[a-z0-9]{1,5}$")
_UNSAFE_NAME_CHARS = re.compile(r"[^\w .()\-]", re.UNICODE)
_USER_DIR = re.compile(r"^user_\d+$")


class StorageError(RuntimeError):
    """Disk penuh atau folder temp tidak bisa dipakai."""


def sanitize_filename(name: str | None, max_length: int = 60) -> str:
    """Bersihkan nama file dari user untuk TAMPILAN (bukan untuk path di disk)."""
    if not name:
        return "file"
    # Ambil nama saja: buang path gaya Windows maupun Linux ("..\\..\\x.pdf").
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable())
    name = _UNSAFE_NAME_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        return "file"
    if len(name) > max_length:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 5:
            name = stem[: max_length - len(ext) - 1] + "." + ext
        else:
            name = name[:max_length]
    return name


def _rmtree(path: Path) -> None:
    """Hapus folder; di Windows file read-only/terkunci butuh perlakuan ekstra."""
    try:
        shutil.rmtree(path)
        return
    except OSError:
        pass
    for p in path.rglob("*"):
        try:
            os.chmod(p, stat.S_IWRITE)
        except OSError:
            pass
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        logger.error("Gagal menghapus folder temp sepenuhnya: %s", path)


class FileManager:
    def __init__(self, temp_dir: Path) -> None:
        self.temp_dir = Path(temp_dir).resolve()
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    # ---------- path ----------
    def user_dir(self, user_id: int) -> Path:
        """Folder session milik user. user_id di-cast int -> tidak bisa path traversal."""
        path = (self.temp_dir / f"user_{int(user_id)}").resolve()
        if path.parent != self.temp_dir:
            raise ValueError("Path di luar folder temp")
        return path

    def prepare_user_dir(self, user_id: int) -> Path:
        path = self.user_dir(user_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def new_file_path(self, user_id: int, ext: str) -> Path:
        """Path baru dengan nama acak. Ekstensi harus lolos whitelist karakter."""
        ext = ext.lower()
        if not _SAFE_EXT.match(ext):
            raise ValueError(f"Ekstensi tidak aman: {ext!r}")
        return self.prepare_user_dir(user_id) / f"{uuid.uuid4().hex}{ext}"

    def new_work_dir(self, user_id: int) -> Path:
        """Sub-folder kerja untuk hasil proses (ikut terhapus bersama folder user)."""
        path = self.prepare_user_dir(user_id) / f"work_{uuid.uuid4().hex[:8]}"
        path.mkdir()
        return path

    # ---------- disk ----------
    def check_disk_space(self, needed: int = 0) -> None:
        try:
            free = shutil.disk_usage(self.temp_dir).free
        except OSError as exc:
            raise StorageError("Tidak bisa membaca kapasitas disk") from exc
        if free < needed + MIN_FREE_BYTES:
            raise StorageError("Ruang penyimpanan server hampir penuh")

    # ---------- hapus ----------
    def delete_file(self, path: Path) -> None:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            logger.warning("Gagal menghapus file temp: %s", path)

    def delete_user_dir(self, user_id: int) -> None:
        path = self.user_dir(user_id)
        if path.exists():
            _rmtree(path)

    def cleanup_all(self) -> int:
        """Hapus SEMUA folder user_* (dipanggil saat bot start/stop).

        Session disimpan di memori, jadi setelah bot restart folder lama
        sudah tidak punya pemilik dan aman dihapus.
        """
        removed = 0
        for child in self.temp_dir.iterdir():
            if child.is_dir() and _USER_DIR.match(child.name):
                _rmtree(child)
                removed += 1
        return removed

    def cleanup_orphans(self, active_user_ids: set[int], older_than: float) -> int:
        """Hapus folder user_* yang TIDAK punya session aktif dan sudah lama tidak berubah.

        Jaring pengaman: folder yang tertinggal karena error aneh / proses yang dihentikan paksa.
        """
        removed = 0
        now = time.time()
        for child in self.temp_dir.iterdir():
            if not (child.is_dir() and _USER_DIR.match(child.name)):
                continue
            if int(child.name[5:]) in active_user_ids:
                continue
            try:
                age = now - child.stat().st_mtime
            except OSError:
                continue
            if age > older_than:
                _rmtree(child)
                removed += 1
        return removed

    def free_bytes(self) -> int:
        """Ruang disk kosong di lokasi folder temp (untuk pemantauan)."""
        return shutil.disk_usage(self.temp_dir).free
