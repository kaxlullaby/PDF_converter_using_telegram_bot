"""Progress bar untuk pesan status proses."""
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Awaitable, Callable

BAR_WIDTH = 10


def render_bar(fraction: float, width: int = BAR_WIDTH) -> str:
    filled = round(min(max(fraction, 0.0), 1.0) * width)
    return "█" * filled + "░" * (width - filled)


def render_progress(fraction: float | None, elapsed: float) -> str:
    """Teks pesan status. `fraction=None` = progress tidak diketahui (mis. LibreOffice)."""
    if fraction is None:
        seconds = int(elapsed) // 5 * 5
        suffix = f" ({seconds} detik)" if seconds >= 5 else ""
        return f"⏳ <b>Processing...</b>\n\nPlease wait...{suffix}"
    percent = int(min(max(fraction, 0.0), 1.0) * 100)
    return f"⏳ <b>Processing...</b>\n\n[{render_bar(fraction)}] {percent}%\n\nPlease wait..."


class ProgressTracker:
    """Menerima laporan kemajuan dari thread worker dan memperbarui pesan Telegram secara berkala.

    Telegram membatasi frekuensi edit pesan, jadi pesan hanya diperbarui tiap `interval` detik
    dan hanya jika teksnya berubah. Proses yang selesai lebih cepat dari itu tidak memunculkan
    progress bar sama sekali (sesuai spesifikasi: untuk operasi cepat tidak wajib).
    """

    def __init__(self, edit: Callable[[str], Awaitable[None]], interval: float = 2.5) -> None:
        self._edit = edit
        self._interval = interval
        self._started = time.monotonic()
        self._last_text: str | None = None
        self._task: asyncio.Task | None = None
        self.fraction: float | None = None

    def update(self, fraction: float) -> None:
        """Dipanggil dari thread worker (cukup menulis satu angka, aman antar-thread)."""
        self.fraction = fraction

    def start(self) -> None:
        self._started = time.monotonic()
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            text = render_progress(self.fraction, time.monotonic() - self._started)
            if text != self._last_text:
                self._last_text = text
                await self._edit(text)

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
