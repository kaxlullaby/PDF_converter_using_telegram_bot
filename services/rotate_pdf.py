"""Rotate PDF: putar SEMUA halaman searah jarum jam."""
from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter

from services.pdf_common import open_reader, write_pdf

VALID_ANGLES = (90, 180, 270)


def rotate_pdf(input_path: Path, angle: int, output: Path) -> int:
    """Putar semua halaman sebesar `angle` derajat searah jarum jam. Return jumlah halaman."""
    if angle not in VALID_ANGLES:
        raise ValueError(f"Sudut tidak valid: {angle}")

    reader = open_reader(input_path)
    writer = PdfWriter()
    for page in reader.pages:
        page.rotate(angle)  # ditambahkan ke rotasi yang sudah ada
        writer.add_page(page)
    write_pdf(writer, output)
    return len(reader.pages)
