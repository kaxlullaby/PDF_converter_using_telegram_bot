"""Konteks pekerjaan: pembatalan + laporan progress.

Service (merge, compress, OCR, ...) berjalan di thread terpisah. Lewat ContextVar,
handler bisa "menitipkan" dua hal ke thread itu tanpa mengubah tanda tangan fungsi service:
  - cancel_event       : jika di-set (user menekan Cancel), service berhenti secepatnya
  - progress_callback  : service melaporkan kemajuan (0.0 - 1.0) untuk progress bar

Di luar sebuah job (mis. saat tes), keduanya kosong dan semua fungsi di sini tidak berbuat apa-apa.
"""
from __future__ import annotations

import threading
from contextvars import ContextVar
from typing import Callable


class JobCancelled(Exception):
    """Pekerjaan dibatalkan oleh user (atau bot sedang berhenti)."""


cancel_event: ContextVar[threading.Event | None] = ContextVar("cancel_event", default=None)
progress_callback: ContextVar[Callable[[float], None] | None] = ContextVar(
    "progress_callback", default=None
)


def check_cancelled() -> None:
    event = cancel_event.get()
    if event is not None and event.is_set():
        raise JobCancelled


def checkpoint(done: float, total: float, start: float = 0.0, end: float = 1.0) -> None:
    """Dipanggil di dalam perulangan: cek pembatalan, lalu laporkan progress.

    `done/total` = kemajuan tahap ini; `start..end` = bagian dari seluruh pekerjaan
    yang diwakili tahap ini (mis. OCR = 0.2 sampai 0.9).
    """
    check_cancelled()
    callback = progress_callback.get()
    if callback is None or total <= 0:
        return
    fraction = min(max(done / total, 0.0), 1.0)
    callback(start + (end - start) * fraction)
