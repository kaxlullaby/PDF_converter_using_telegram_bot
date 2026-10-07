"""PDF -> JPG: setiap halaman PDF dirender menjadi satu gambar JPG."""
from __future__ import annotations

import zipfile
from pathlib import Path

from PIL import Image

from services.pdf_common import PdfProcessingError, get_fitz, open_fitz_document

DPI = 150               # kualitas cukup untuk dibaca di layar / dicetak biasa
MAX_SIDE_PX = 3000      # batas sisi terpanjang gambar (cegah halaman raksasa)
JPEG_QUALITY = 85
MAX_PAGES = 100         # batas halaman agar server tidak kewalahan
ZIP_THRESHOLD = 10      # lebih dari ini -> hasil dikemas dalam ZIP


def compute_scale(width_pt: float, height_pt: float, dpi: int = DPI, max_side_px: int = MAX_SIDE_PX) -> float:
    """Skala render. 72 poin = 1 inci, jadi skala normal = dpi / 72."""
    scale = dpi / 72
    longest = max(width_pt, height_pt, 1.0)
    if longest * scale > max_side_px:
        scale = max_side_px / longest
    return scale


def delivery_mode(page_count: int) -> str:
    """1 halaman -> "single"; 2-10 -> "multiple" (kirim satu per satu); >10 -> "zip"."""
    if page_count <= 1:
        return "single"
    if page_count <= ZIP_THRESHOLD:
        return "multiple"
    return "zip"


def render_pages(input_path: Path, out_dir: Path) -> list[Path]:
    """Render semua halaman ke out_dir sebagai page_001.jpg, page_002.jpg, ..."""
    fitz = get_fitz()
    out_dir = Path(out_dir)
    paths: list[Path] = []

    with open_fitz_document(input_path) as doc:
        count = doc.page_count
        if count < 1:
            raise PdfProcessingError("PDF tidak memiliki halaman.")
        if count > MAX_PAGES:
            raise PdfProcessingError(
                f"PDF memiliki {count} halaman. Maksimal {MAX_PAGES} halaman untuk fitur ini.\n\n"
                "Gunakan Split PDF untuk memotongnya terlebih dahulu."
            )
        for index in range(count):
            page = doc[index]
            scale = compute_scale(page.rect.width, page.rect.height)
            pix = page.get_pixmap(
                matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False
            )
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            path = out_dir / f"page_{index + 1:03d}.jpg"
            image.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True)
            paths.append(path)
    return paths


def build_zip(files: list[tuple[Path, str]], zip_path: Path) -> None:
    """Kemas file ke ZIP. files = [(path_di_disk, nama_di_dalam_zip), ...]"""
    # JPG sudah terkompresi, jadi ZIP_STORED (tanpa kompresi) lebih cepat.
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for path, arcname in files:
            zf.write(path, arcname=arcname)
