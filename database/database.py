"""Database SQLite sederhana: pengguna + riwayat operasi.

YANG DISIMPAN : ID user, username, nama depan, jumlah pemakaian, waktu, dan RINGKASAN
                tiap operasi (fitur, status, jumlah & ukuran file, durasi).
YANG TIDAK    : isi file maupun nama file user.

Semua method bersifat async (query dijalankan di thread agar event loop tidak tertahan).
Kegagalan database TIDAK boleh mengganggu bot: error dicatat ke log dan fungsi
mengembalikan nilai bawaan.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT,
    first_name   TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    usage_count  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS operations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    feature       TEXT NOT NULL,
    status        TEXT NOT NULL,          -- success | failed | cancelled | timeout
    input_files   INTEGER NOT NULL DEFAULT 0,
    input_bytes   INTEGER NOT NULL DEFAULT 0,
    output_bytes  INTEGER,
    duration_ms   INTEGER,
    error         TEXT,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (user_id)
);
CREATE INDEX IF NOT EXISTS idx_operations_user ON operations (user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_operations_time ON operations (created_at);
"""

STATUSES = ("success", "failed", "cancelled", "timeout")
TOUCH_INTERVAL = 60  # detik: jangan menulis last_seen lebih sering dari ini


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _safe(default: Any):
    """Jalankan method sinkron di thread; error database -> catat ke log, return `default`."""

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            try:
                return await asyncio.to_thread(fn, self, *args, **kwargs)
            except sqlite3.Error:
                logger.exception("Database error pada %s", fn.__name__)
                return default() if callable(default) else default

        return wrapper

    return decorator


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: dict[int, tuple[tuple, float]] = {}
        try:
            self._init_schema()
        except sqlite3.DatabaseError:
            # File database rusak: simpan sebagai cadangan lalu buat yang baru,
            # supaya bot tetap bisa start.
            backup = self.path.with_suffix(f".corrupt-{int(time.time())}")
            logger.error("Database rusak, dipindahkan ke %s dan dibuat ulang", backup.name)
            self.path.replace(backup)
            self._init_schema()

    # ---------- koneksi ----------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("PRAGMA journal_mode = WAL")  # baca & tulis tidak saling memblokir
            conn.executescript(SCHEMA)

    # ---------- pengguna ----------
    async def touch_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        """Catat/perbarui user. Dibatasi 1x per menit per user agar tidak membebani disk."""
        key = (username, first_name)
        cached = self._seen.get(user_id)
        now = time.monotonic()
        if cached and cached[0] == key and now - cached[1] < TOUCH_INTERVAL:
            return
        self._seen[user_id] = (key, now)
        await self._touch_user(user_id, username, first_name)

    @_safe(None)
    def _touch_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        now = _now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO users (user_id, username, first_name, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       username = excluded.username,
                       first_name = excluded.first_name,
                       last_seen = excluded.last_seen""",
                (user_id, username, first_name, now, now),
            )

    # ---------- operasi ----------
    @_safe(None)
    def log_operation(
        self,
        user_id: int,
        feature: str,
        status: str,
        input_files: int = 0,
        input_bytes: int = 0,
        output_bytes: int | None = None,
        duration_ms: int | None = None,
        error: str | None = None,
    ) -> None:
        if status not in STATUSES:
            raise ValueError(f"status tidak dikenal: {status}")
        now = _now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, first_seen, last_seen) VALUES (?, ?, ?)",
                (user_id, now, now),
            )
            conn.execute(
                """INSERT INTO operations (user_id, feature, status, input_files, input_bytes,
                                           output_bytes, duration_ms, error, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, feature, status, input_files, input_bytes, output_bytes,
                 duration_ms, (error or None) and error[:200], now),
            )
            if status == "success":
                conn.execute(
                    "UPDATE users SET usage_count = usage_count + 1, last_seen = ? WHERE user_id = ?",
                    (now, user_id),
                )

    @_safe(lambda: {"usage_count": 0, "first_seen": None})
    def user_summary(self, user_id: int) -> dict:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT usage_count, first_seen FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else {"usage_count": 0, "first_seen": None}

    @_safe(list)
    def recent_operations(self, user_id: int, limit: int = 5) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT feature, status, input_files, input_bytes, output_bytes,
                          duration_ms, created_at
                   FROM operations WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    @_safe(dict)
    def global_stats(self) -> dict:
        with closing(self._connect()) as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            total, ok = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(status = 'success'), 0) FROM operations"
            ).fetchone()
            last_day = conn.execute(
                "SELECT COUNT(*) FROM operations WHERE created_at >= datetime('now', '-1 day')"
            ).fetchone()[0]
            top = conn.execute(
                """SELECT feature, COUNT(*) AS n FROM operations
                   GROUP BY feature ORDER BY n DESC LIMIT 5"""
            ).fetchall()
        return {
            "users": users,
            "operations": total,
            "success": ok,
            "last_24h": last_day,
            "top_features": [(r["feature"], r["n"]) for r in top],
        }

    @_safe(0)
    def purge_older_than(self, days: int) -> int:
        """Hapus riwayat operasi lebih tua dari `days` hari. Return jumlah baris terhapus."""
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "DELETE FROM operations WHERE created_at < datetime('now', ?)", (f"-{int(days)} days",)
            )
            return cursor.rowcount
