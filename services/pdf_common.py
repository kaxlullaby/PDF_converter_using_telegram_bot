"""Fungsi bersama untuk semua service PDF (tanpa dependensi Telegram)."""
from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader, PdfWriter

logger = logging.getLogger(__name__)

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


class MissingDependencyError(PdfProcessingError):
    """Library yang dibutuhkan belum terpasang di server."""


def get_fitz():
    """Import PyMuPDF saat dibutuhkan (lazy), sehingga bot tetap bisa start tanpanya."""
    try:
        import pymupdf as fitz  # nama modul baru (PyMuPDF >= 1.24.3)
    except ImportError:
        try:
            import fitz  # nama modul lama
        except ImportError:
            logger.error("PyMuPDF belum terpasang. Jalankan: python -m pip install PyMuPDF")
            raise MissingDependencyError(
                "Fitur ini belum siap di server (PyMuPDF belum terpasang)."
            ) from None
    return fitz


def open_fitz_document(path: Path):
    """Buka PDF dengan PyMuPDF. Pakai: `with open_fitz_document(p) as doc:`."""
    fitz = get_fitz()
    doc = fitz.open(str(path))
    if doc.needs_pass and not doc.authenticate(""):
        doc.close()
        raise PdfProcessingError("PDF dilindungi password.")
    return doc
