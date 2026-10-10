"""Benchmark sederhana: ukur bagian bot yang paling mungkin lambat.

Jalankan dari folder project:   python tests/benchmark.py
Angka bergantung pada komputer Anda; yang penting adalah PERBANDINGAN antar bagian.
PyMuPDF (render, compress) tidak diukur di sini; ukur sendiri dengan file nyata jika perlu.
"""
from __future__ import annotations

import io
import random
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageFilter  # noqa: E402
from pypdf import PdfWriter  # noqa: E402

from services.compress_pdf import PROFILES, recompress_image  # noqa: E402
from services.jpg_to_pdf import prepare_image  # noqa: E402
from services.merge_pdf import merge_pdfs  # noqa: E402
from services.pdf_common import get_page_count  # noqa: E402
from services.rotate_pdf import rotate_pdf  # noqa: E402
from services.split_pdf import split_pdf  # noqa: E402
from utils.file_validator import check_content, inspect_content  # noqa: E402

MB = 1024 * 1024
ROWS: list[tuple[str, float, str]] = []


def bench(name: str, fn, repeat: int = 3, note: str = "") -> float:
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    ROWS.append((name, best * 1000, note))
    return best


def blank_pdf(path: Path, pages: int) -> Path:
    w = PdfWriter()
    for i in range(pages):
        w.add_blank_page(595, 842)
    with path.open("wb") as fh:
        w.write(fh)
    return path


def noisy(w: int, h: int, seed: int = 1) -> Image.Image:
    rnd = random.Random(seed)
    base = Image.frombytes("RGB", (64, 64), bytes(rnd.getrandbits(8) for _ in range(64 * 64 * 3)))
    return base.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(1))


def image_pdf(path: Path, pages: int) -> Path:
    imgs = [noisy(1240, 1754, seed=i) for i in range(pages)]
    imgs[0].save(path, "PDF", save_all=True, append_images=imgs[1:], resolution=150)
    return path


def jpeg(w: int, h: int, quality: int = 92) -> bytes:
    buf = io.BytesIO()
    noisy(w, h).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="docbot_bench_"))

    big = blank_pdf(tmp / "big.pdf", 500)
    scan = image_pdf(tmp / "scan.pdf", 12)
    print(f"PDF 500 halaman: {big.stat().st_size / MB:.2f} MB | PDF scan 12 halaman: {scan.stat().st_size / MB:.1f} MB\n")

    # --- validasi file masuk ---
    bench("validasi PDF 500 hal (check_content)", lambda: check_content(big, "pdf", 20 * MB))
    bench("hitung halaman PDF 500 hal (get_page_count)", lambda: get_page_count(big), note="cara LAMA: parse kedua kali")
    bench("validasi + halaman PDF 500 hal (inspect_content)", lambda: inspect_content(big, "pdf", 20 * MB), note="cara BARU: sekali parse")
    bench("validasi PDF scan 12 hal", lambda: check_content(scan, "pdf", 20 * MB))

    # --- layanan pypdf ---
    parts = [blank_pdf(tmp / f"p{i}.pdf", 25) for i in range(20)]
    bench("merge 20 PDF x 25 hal", lambda: merge_pdfs(parts, tmp / "m.pdf"), repeat=2)
    bench("split 100 halaman dari PDF 500 hal", lambda: split_pdf(big, list(range(1, 101)), tmp / "s.pdf"), repeat=2)
    bench("rotate PDF 500 hal", lambda: rotate_pdf(big, 90, tmp / "r.pdf"), repeat=2)
    bench("merge 3 PDF scan 12 hal (data gambar)", lambda: merge_pdfs([scan] * 3, tmp / "ms.pdf"), repeat=2)

    # --- gambar ---
    photo = jpeg(4000, 3000)
    print(f"Foto contoh 4000x3000: {len(photo) / MB:.1f} MB")
    for level in ("low", "medium", "high"):
        bench(f"compress gambar 4000x3000 level {level}", lambda lv=level: recompress_image(photo, PROFILES[lv]), repeat=2)
    photo_path = tmp / "photo.jpg"; photo_path.write_bytes(photo)
    bench("JPG->PDF: siapkan foto 4000x3000 (perlu diperkecil)", lambda: prepare_image(photo_path), repeat=2)
    page = noisy(1240, 1754)
    bench("simpan halaman 1240x1754 JPEG optimize=True", lambda: page.save(io.BytesIO(), "JPEG", quality=85, optimize=True))
    bench("simpan halaman 1240x1754 JPEG optimize=False", lambda: page.save(io.BytesIO(), "JPEG", quality=85))

    # --- memori puncak ---
    tracemalloc.start()
    merge_pdfs([scan] * 3, tmp / "mem.pdf")
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    ROWS.append(("puncak memori: merge 3 PDF scan (~%.0f MB total)" % (3 * scan.stat().st_size / MB), peak / MB, "MB (bukan ms)"))

    width = max(len(r[0]) for r in ROWS)
    print()
    for name, value, note in ROWS:
        unit = "MB" if note.startswith("MB") else "ms"
        extra = "" if unit == "MB" else (f"  {note}" if note else "")
        print(f"{name:<{width}}  {value:>9.1f} {unit}{extra}")


if __name__ == "__main__":
    main()
