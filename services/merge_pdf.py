"""Merge PDF: gabungkan beberapa PDF menjadi satu (urutan = urutan list)."""
from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter

from services.job_context import checkpoint
from services.pdf_common import MAX_OUTPUT_PAGES, PdfProcessingError, open_reader, write_pdf


def merge_pdfs(inputs: list[Path], output: Path) -> int:
    """Gabungkan `inputs` ke `output`. Return total halaman hasil."""
    if len(inputs) < 2:
        raise PdfProcessingError("Minimal 2 file PDF untuk digabung.")

    # Reader harus tetap terbuka sampai writer selesai menulis.
    readers = []
    for i, path in enumerate(inputs):
        checkpoint(i, len(inputs), 0.0, 0.4)
        readers.append(open_reader(path))
    total = sum(len(r.pages) for r in readers)
    if total > MAX_OUTPUT_PAGES:
        raise PdfProcessingError(
            f"Total halaman ({total}) melebihi batas {MAX_OUTPUT_PAGES} halaman."
        )

    writer = PdfWriter()
    for i, reader in enumerate(readers):
        checkpoint(i, len(readers), 0.4, 0.9)
        for page in reader.pages:
            writer.add_page(page)
    write_pdf(writer, output)
    return total
