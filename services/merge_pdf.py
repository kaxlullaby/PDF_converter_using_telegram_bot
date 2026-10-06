"""Merge PDF: gabungkan beberapa PDF menjadi satu (urutan = urutan list)."""
from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter

from services.pdf_common import MAX_OUTPUT_PAGES, PdfProcessingError, open_reader, write_pdf


def merge_pdfs(inputs: list[Path], output: Path) -> int:
    """Gabungkan `inputs` ke `output`. Return total halaman hasil."""
    if len(inputs) < 2:
        raise PdfProcessingError("Minimal 2 file PDF untuk digabung.")

    # Reader harus tetap terbuka sampai writer selesai menulis.
    readers = [open_reader(p) for p in inputs]
    total = sum(len(r.pages) for r in readers)
    if total > MAX_OUTPUT_PAGES:
        raise PdfProcessingError(
            f"Total halaman ({total}) melebihi batas {MAX_OUTPUT_PAGES} halaman."
        )

    writer = PdfWriter()
    for reader in readers:
        for page in reader.pages:
            writer.add_page(page)
    write_pdf(writer, output)
    return total
