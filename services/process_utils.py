"""Menjalankan program eksternal (LibreOffice, Tesseract) dengan aman.

- Perintah berupa LIST argumen dan tanpa shell -> input user tidak bisa menyisipkan perintah.
- Ada batas waktu; jika lewat, proses beserta anak-anaknya dihentikan paksa.
- Jika user membatalkan job (cancel_event), proses langsung dihentikan.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time

from services.job_context import JobCancelled, cancel_event


class ProcessTimeout(Exception):
    """Program eksternal melewati batas waktu dan sudah dihentikan."""


def _kill_tree(proc: subprocess.Popen) -> None:
    """Hentikan proses beserta turunannya (soffice.exe memanggil soffice.bin)."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def _reap(proc: subprocess.Popen) -> None:
    _kill_tree(proc)
    try:
        proc.communicate(timeout=5)
    except Exception:
        pass


def run_command(
    cmd: list[str], *, timeout: float, env: dict[str, str] | None = None
) -> tuple[int, bytes, bytes]:
    """Jalankan `cmd`. Return (kode_keluar, stdout, stderr).

    Raise ProcessTimeout jika kelamaan, JobCancelled jika job dibatalkan.
    """
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True  # grup proses sendiri -> bisa di-kill sekaligus

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        **kwargs,
    )
    event = cancel_event.get()
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _reap(proc)
            raise ProcessTimeout
        try:
            out, err = proc.communicate(timeout=min(0.5, remaining))
            return proc.returncode, out, err
        except subprocess.TimeoutExpired:
            if event is not None and event.is_set():
                _reap(proc)
                raise JobCancelled from None
