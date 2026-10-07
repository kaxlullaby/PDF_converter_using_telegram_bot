"""JPG/PNG -> PDF: setiap gambar menjadi satu halaman A4.

- Orientasi halaman mengikuti gambar (gambar landscape -> halaman landscape).
- Gambar diskalakan agar muat di halaman (dengan margin), diletakkan di tengah.
- Foto dari HP yang "miring" (EXIF orientation) otomatis ditegakkan.
- JPEG biasa disisipkan apa adanya (tanpa kompresi ulang = kualitas tetap).
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps

from services.pdf_common import PdfProcessingError, get_fitz

A4_PORTRAIT = (595.0, 842.0)   # ukuran A4 dalam poin (1 poin = 1/72 inci)
MARGIN = 20.0
MAX_SIDE_PX = 3000             # gambar lebih besar diperkecil (~250 dpi di A4)
JPEG_QUALITY = 88
EXIF_ORIENTATION = 0x0112


def page_size_for(img_w: int, img_h: int) -> tuple[float, float]:
    """A4 landscape untuk gambar yang lebih lebar daripada tinggi, selain itu portrait."""
    w, h = A4_PORTRAIT
    return (h, w) if img_w > img_h else (w, h)


def fit_rect(
    img_w: float, img_h: float, page_w: float, page_h: float, margin: float = MARGIN
) -> tuple[float, float, float, float]:
    """Kotak (x0, y0, x1, y1) tempat gambar diletakkan: muat di halaman, di tengah."""
    avail_w, avail_h = page_w - 2 * margin, page_h - 2 * margin
    scale = min(avail_w / img_w, avail_h / img_h)
    w, h = img_w * scale, img_h * scale
    x0, y0 = (page_w - w) / 2, (page_h - h) / 2
    return (x0, y0, x0 + w, y0 + h)


def prepare_image(path: Path) -> tuple[bytes, int, int]:
    """Siapkan gambar: kembalikan (data_jpeg, lebar, tinggi) yang siap disisipkan ke PDF."""
    with Image.open(path) as src:
        fmt = src.format
        width, height = src.size
        orientation = src.getexif().get(EXIF_ORIENTATION, 1)

        # JPEG normal: pakai data aslinya (tanpa kompresi ulang).
        if (
            fmt == "JPEG"
            and orientation in (None, 1)
            and src.mode in ("RGB", "L")
            and max(width, height) <= MAX_SIDE_PX
        ):
            return Path(path).read_bytes(), width, height

        img = ImageOps.exif_transpose(src)  # tegakkan foto HP
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")  # latar transparan -> putih
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        elif img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        if max(img.size) > MAX_SIDE_PX:
            img.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
        return buf.getvalue(), img.size[0], img.size[1]


def images_to_pdf(inputs: list[Path], output: Path) -> int:
    """Gabungkan gambar (sesuai urutan list) menjadi satu PDF. Return jumlah halaman."""
    if not inputs:
        raise PdfProcessingError("Minimal 1 gambar untuk dikonversi.")
    fitz = get_fitz()

    doc = fitz.open()
    try:
        for path in inputs:
            data, width, height = prepare_image(Path(path))
            page_w, page_h = page_size_for(width, height)
            page = doc.new_page(width=page_w, height=page_h)
            page.insert_image(fitz.Rect(*fit_rect(width, height, page_w, page_h)), stream=data)
        doc.save(str(output), garbage=3, deflate=True)
    finally:
        doc.close()
    return len(inputs)
