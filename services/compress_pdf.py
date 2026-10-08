"""Compress PDF.

Cara kerja:
1. Gambar di dalam PDF dikompres ulang ke JPEG (dan diperkecil resolusinya untuk
   level Medium/High). Gambar hanya diganti jika hasilnya benar-benar lebih kecil.
2. Struktur PDF dirapikan (objek ganda dibuang, stream di-deflate) saat disimpan.

Catatan: PDF yang isinya hampir hanya teks tidak banyak berkurang ukurannya,
karena teks sudah kecil. Efek terbesar terasa pada PDF berisi foto / hasil scan.
"""
from __future__ import annotations

import io
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from services.job_context import checkpoint
from services.pdf_common import get_fitz, open_fitz_document

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Profile:
    label: str
    jpeg_quality: int
    max_side: int | None  # sisi terpanjang gambar (piksel); None = tidak diperkecil


PROFILES: dict[str, Profile] = {
    "low": Profile("Low", jpeg_quality=85, max_side=None),
    "medium": Profile("Medium", jpeg_quality=70, max_side=1800),
    "high": Profile("High", jpeg_quality=45, max_side=1200),
}

MIN_IMAGE_BYTES = 20 * 1024   # gambar lebih kecil dari ini tidak layak dikompres
MIN_SAVING_RATIO = 0.95       # gambar baru dipakai hanya jika <= 95% ukuran lama


def recompress_image(data: bytes, profile: Profile) -> bytes | None:
    """Kompres ulang satu gambar ke JPEG. Return None jika tidak layak / tidak menghemat."""
    if len(data) < MIN_IMAGE_BYTES:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None  # format tidak dikenal (mis. JPEG2000 / JBIG2) -> biarkan apa adanya

    if img.mode in ("RGBA", "LA", "CMYK") or "transparency" in img.info:
        return None  # jangan merusak transparansi / warna CMYK
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    if profile.max_side and max(img.size) > profile.max_side:
        img.thumbnail((profile.max_side, profile.max_side), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=profile.jpeg_quality, optimize=True)
    new_data = buf.getvalue()
    return new_data if len(new_data) <= len(data) * MIN_SAVING_RATIO else None


def compress_pdf(input_path: Path, level: str, output_path: Path) -> tuple[int, int]:
    """Kompres PDF. Return (ukuran_asli, ukuran_hasil) dalam byte.

    Jika hasil tidak lebih kecil, file asli yang dikembalikan (ukuran hasil = asli).
    """
    if level not in PROFILES:
        raise ValueError(f"Level kompresi tidak dikenal: {level}")
    profile = PROFILES[level]
    input_path, output_path = Path(input_path), Path(output_path)
    original = input_path.stat().st_size
    fitz = get_fitz()

    with open_fitz_document(input_path) as doc:
        seen: set[int] = set()
        for page_no, page in enumerate(doc):
            checkpoint(page_no, doc.page_count, 0.0, 0.9)
            for img in page.get_images(full=True):
                xref, smask = img[0], img[1]
                if xref in seen or smask:  # smask != 0 -> gambar punya transparansi
                    continue
                seen.add(xref)
                try:
                    info = doc.extract_image(xref)
                    if not info or info.get("bpc") == 1 or info.get("colorspace") not in (1, 3):
                        continue
                    new_data = recompress_image(info["image"], profile)
                    if new_data:
                        page.replace_image(xref, stream=new_data)
                except Exception:
                    # Satu gambar bermasalah tidak boleh membatalkan seluruh proses.
                    logger.debug("Gambar xref=%s dilewati", xref, exc_info=True)

        doc.save(
            str(output_path),
            garbage=4,
            deflate=True,
            encryption=fitz.PDF_ENCRYPT_NONE,
        )

    compressed = output_path.stat().st_size
    if compressed >= original:
        shutil.copyfile(input_path, output_path)
        compressed = original
    return original, compressed
