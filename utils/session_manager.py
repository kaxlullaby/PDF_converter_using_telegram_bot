"""Session per user (disimpan di memori).

Satu user = satu session. Session menyimpan fitur yang dipilih, daftar file
yang sudah diterima (urut sesuai kedatangan), dan waktu aktivitas terakhir.
Folder temp user ikut dihapus setiap kali session berakhir.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from utils.file_manager import FileManager

logger = logging.getLogger(__name__)


class SessionExpired(Exception):
    """Session sudah melewati batas waktu (dan sudah dibersihkan)."""


@dataclass
class SessionFile:
    fid: str       # id pendek, dipakai di callback_data tombol
    name: str      # nama aman untuk tampilan
    path: Path     # lokasi di temp (nama acak)
    size: int      # ukuran byte
    kind: str      # "pdf" | "jpeg" | "png" | "docx" | "doc"
    pages: int | None = None  # jumlah halaman (khusus PDF)


@dataclass
class Session:
    user_id: int
    chat_id: int
    feature_key: str
    files: list[SessionFile] = field(default_factory=list)
    panel_message_id: int | None = None
    awaiting: str | None = None  # input teks yang ditunggu, mis. "pages" (Split)
    last_activity: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def is_expired(self, timeout: int) -> bool:
        return time.monotonic() - self.last_activity > timeout

    def index_of(self, fid: str) -> int:
        for i, f in enumerate(self.files):
            if f.fid == fid:
                return i
        return -1

    def find(self, fid: str) -> SessionFile | None:
        i = self.index_of(fid)
        return self.files[i] if i >= 0 else None

    def move(self, fid: str, delta: int) -> bool:
        """Geser file ke atas (-1) / bawah (+1). Return False jika tidak bisa."""
        i = self.index_of(fid)
        j = i + delta
        if i < 0 or not 0 <= j < len(self.files):
            return False
        self.files[i], self.files[j] = self.files[j], self.files[i]
        return True


class SessionManager:
    def __init__(self, file_manager: FileManager, timeout: int) -> None:
        self._fm = file_manager
        self._timeout = timeout
        self._sessions: dict[int, Session] = {}

    def get(self, user_id: int) -> Session | None:
        """Ambil session aktif user dan catat aktivitas.

        Return None jika tidak ada session.
        Raise SessionExpired jika session sudah kedaluwarsa (file langsung dihapus).
        """
        session = self._sessions.get(user_id)
        if session is None:
            return None
        if session.is_expired(self._timeout):
            self.end(user_id)
            raise SessionExpired
        session.touch()
        return session

    def start(self, user_id: int, chat_id: int, feature_key: str) -> Session:
        """Mulai session baru. Session lama (beserta file-nya) dibuang."""
        self.end(user_id)
        self._fm.prepare_user_dir(user_id)
        session = Session(user_id=user_id, chat_id=chat_id, feature_key=feature_key)
        self._sessions[user_id] = session
        return session

    def end(self, user_id: int) -> bool:
        """Akhiri session + hapus semua file temp user. Aman dipanggil berkali-kali."""
        session = self._sessions.pop(user_id, None)
        self._fm.delete_user_dir(user_id)  # tetap dihapus walau session tidak ada
        return session is not None

    def is_active(self, session: Session) -> bool:
        """Apakah objek session ini masih session milik user (belum di-end/diganti)?"""
        return self._sessions.get(session.user_id) is session

    def remove_file(self, session: Session, fid: str) -> bool:
        item = session.find(fid)
        if item is None:
            return False
        self._fm.delete_file(item.path)
        session.files.remove(item)
        return True

    def expired_sessions(self) -> list[Session]:
        return [s for s in self._sessions.values() if s.is_expired(self._timeout)]

    def end_all(self) -> None:
        self._sessions.clear()
        self._fm.cleanup_all()

    @property
    def active_count(self) -> int:
        return len(self._sessions)
