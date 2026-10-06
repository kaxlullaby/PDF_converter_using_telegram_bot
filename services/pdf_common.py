"""Fungsi bersama untuk semua service PDF (tanpa dependensi Telegram)."""
from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter

# Batas halaman hasil akhir, supaya server tidak kehabisan memori.
MAX_OUTPUT_PAGES = 2000


class PdfProcessingError(Exception):
    """Kesalahan yang pesannya aman ditampilkan ke user (tanpa awalan emoji)."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


def open_reader(path: Path) -> PdfReader:
    """Buka PDF. PDF terenkripsi dengan password kosong otomatis dibuka."""
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted and not reader.decrypt(""):
        raise PdfProcessingError("PDF dilindungi password.")
    return reader


def get_page_count(path: Path) -> int:
    return len(open_reader(path).pages)


def write_pdf(writer: PdfWriter, output: Path) -> None:
    with Path(output).open("wb") as fh:
        writer.write(fh)
