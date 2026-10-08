"""Split PDF: ambil halaman tertentu dari satu PDF menjadi PDF baru."""
from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfWriter

from services.job_context import checkpoint
from services.pdf_common import PdfProcessingError, open_reader, write_pdf

_TOKEN = re.compile(r"^(\d+)(?:-(\d+))?$")
MAX_INPUT_LENGTH = 200

EXAMPLE_HINT = "Contoh format:\n1-5\n2,4,7\n3-8"


class PageSelectionError(ValueError):
    """Input halaman dari user tidak valid. Pesannya aman ditampilkan ke user."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


def parse_page_ranges(text: str, total_pages: int) -> list[int]:
    """Ubah "1-3, 7, 9-10" menjadi [1, 2, 3, 7, 9, 10] (nomor halaman mulai dari 1).

    - Urutan mengikuti yang diketik user; halaman duplikat hanya diambil sekali.
    - Raise PageSelectionError jika format salah atau halaman di luar jangkauan.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise PageSelectionError("Halaman belum diisi.")
    if len(cleaned) > MAX_INPUT_LENGTH:
        raise PageSelectionError("Input terlalu panjang.")

    cleaned = cleaned.replace("–", "-").replace("—", "-").replace(";", ",")
    cleaned = re.sub(r"\s*-\s*", "-", cleaned)  # "3 - 8" -> "3-8"
    tokens = [t for t in re.split(r"[,\s]+", cleaned) if t]

    pages: list[int] = []
    seen: set[int] = set()
    for token in tokens:
        match = _TOKEN.match(token)
        if not match:
            raise PageSelectionError(f'Format "{token[:20]}" tidak valid.')
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if start < 1 or end < 1:
            raise PageSelectionError("Nomor halaman dimulai dari 1.")
        if start > end:
            raise PageSelectionError(f"Rentang {start}-{end} terbalik (awal lebih besar dari akhir).")
        if end > total_pages:
            raise PageSelectionError(
                f"Halaman {end} tidak ada. PDF ini hanya memiliki {total_pages} halaman."
            )
        for n in range(start, end + 1):
            if n not in seen:
                seen.add(n)
                pages.append(n)

    if not pages:
        raise PageSelectionError("Halaman belum diisi.")
    return pages


def split_pdf(input_path: Path, pages: list[int], output: Path) -> int:
    """Simpan `pages` (1-based) dari input_path ke output. Return jumlah halaman hasil."""
    reader = open_reader(input_path)
    total = len(reader.pages)
    if not pages or any(n < 1 or n > total for n in pages):
        raise PdfProcessingError("Nomor halaman di luar jangkauan.")

    writer = PdfWriter()
    for k, n in enumerate(pages):
        checkpoint(k, len(pages), 0.0, 0.9)
        writer.add_page(reader.pages[n - 1])
    write_pdf(writer, output)
    return len(pages)
