"""Smoke test Phase 4 - TIDAK butuh Telegram/internet.

Bagian A: logika murni (selalu jalan).
Bagian B: uji nyata dengan PyMuPDF (otomatis dilewati jika PyMuPDF belum terpasang).
Jalankan dari folder project:   python tests/smoke_phase4.py
"""
from __future__ import annotations

import io
import random
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from services.compress_pdf import PROFILES, recompress_image  # noqa: E402
from services.jpg_to_pdf import fit_rect, page_size_for, prepare_image  # noqa: E402
from services.pdf_to_jpg import build_zip, compute_scale, delivery_mode  # noqa: E402

results: list[tuple[bool, str]] = []


def check(name: str, condition: bool) -> None:
    results.append((condition, name))
    print(("  PASS  " if condition else "  FAIL  ") + name)


def noisy_image(w: int, h: int, seed: int = 1) -> Image.Image:
    """Gambar acak (sulit dikompres) supaya efek kompresi terlihat nyata."""
    rnd = random.Random(seed)
    img = Image.frombytes("RGB", (w, h), bytes(rnd.getrandbits(8) for _ in range(w * h * 3)))
    return img.resize((w, h)).filter(__import__("PIL.ImageFilter", fromlist=["x"]).GaussianBlur(1.2))


def jpeg_bytes(img: Image.Image, quality: int = 95, **kw) -> bytes:
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=quality, **kw); return buf.getvalue()


def part_a(tmp: Path) -> None:
    print("\n[A1] compute_scale / delivery_mode / zip")
    check("A4 (595x842) pada 150 dpi -> skala 150/72", abs(compute_scale(595, 842) - 150 / 72) < 1e-9)
    big = compute_scale(5000, 3000)
    check("halaman raksasa dibatasi 3000 px", abs(5000 * big - 3000) < 1e-6)
    check("halaman ukuran 0 tidak crash", compute_scale(0, 0) > 0)
    check("1 halaman -> single", delivery_mode(1) == "single")
    check("2 dan 10 halaman -> multiple", delivery_mode(2) == "multiple" and delivery_mode(10) == "multiple")
    check("11 halaman -> zip", delivery_mode(11) == "zip")
    files = []
    for i in range(3):
        p = tmp / f"p{i}.jpg"; Image.new("RGB", (20, 20), "red").save(p); files.append((p, f"doc_page_{i + 1:02d}.jpg"))
    z = tmp / "pages.zip"; build_zip(files, z)
    check("isi ZIP sesuai", zipfile.ZipFile(z).namelist() == ["doc_page_01.jpg", "doc_page_02.jpg", "doc_page_03.jpg"])

    print("\n[A2] JPG -> PDF (persiapan gambar)")
    check("gambar portrait -> A4 portrait", page_size_for(300, 400) == (595.0, 842.0))
    check("gambar landscape -> A4 landscape", page_size_for(400, 300) == (842.0, 595.0))
    x0, y0, x1, y1 = fit_rect(1000, 500, 595, 842, 20)
    check("gambar muat di dalam margin & di tengah", abs(x0 - 20) < 1e-6 and abs(x1 - 575) < 1e-6 and abs((y0 + y1) / 2 - 421) < 1e-6)

    plain = tmp / "plain.jpg"; Image.new("RGB", (400, 300), "blue").save(plain, "JPEG")
    data, w, h = prepare_image(plain)
    check("JPEG biasa disisipkan apa adanya (tanpa kompres ulang)", data == plain.read_bytes() and (w, h) == (400, 300))

    rgba = tmp / "t.png"; Image.new("RGBA", (50, 50), (255, 0, 0, 0)).save(rgba)
    data, w, h = prepare_image(rgba)
    px = Image.open(io.BytesIO(data)).convert("RGB").getpixel((25, 25))
    check("PNG transparan -> latar putih", min(px) > 240)

    exif = tmp / "rot.jpg"
    im = Image.new("RGB", (400, 200), "green"); ex = im.getexif(); ex[0x0112] = 6
    im.save(exif, "JPEG", exif=ex)
    data, w, h = prepare_image(exif)
    check("foto HP miring (EXIF) ditegakkan: 400x200 -> 200x400", (w, h) == (200, 400))

    huge = tmp / "huge.png"; Image.new("RGB", (6000, 3000), "white").save(huge)
    data, w, h = prepare_image(huge)
    check("gambar raksasa diperkecil ke <= 3000 px", max(w, h) == 3000 and Image.open(io.BytesIO(data)).size == (w, h))

    gray = tmp / "g.png"; Image.new("L", (30, 30), 128).save(gray)
    check("grayscale tidak crash", prepare_image(gray)[1:] == (30, 30))

    print("\n[A3] recompress_image (inti Compress PDF)")
    src = jpeg_bytes(noisy_image(2400, 1800), 95)
    low = recompress_image(src, PROFILES["low"])
    med = recompress_image(src, PROFILES["medium"])
    high = recompress_image(src, PROFILES["high"])
    check("semua level menghasilkan data lebih kecil", all(x is not None and len(x) < len(src) for x in (low, med, high)))
    check("urutan ukuran: low > medium > high", len(low) > len(med) > len(high))
    check("medium: sisi terpanjang <= 1800 px", max(Image.open(io.BytesIO(med)).size) <= 1800)
    check("high: sisi terpanjang <= 1200 px", max(Image.open(io.BytesIO(high)).size) <= 1200)
    check("low: resolusi tidak berubah", Image.open(io.BytesIO(low)).size == (2400, 1800))
    check("gambar kecil (<20 KB) dilewati", recompress_image(jpeg_bytes(Image.new("RGB", (40, 40), "red")), PROFILES["high"]) is None)
    check("data bukan gambar dilewati", recompress_image(b"x" * 50000, PROFILES["high"]) is None)
    already = jpeg_bytes(noisy_image(600, 400), 30)
    check("gambar yang sudah kecil/jelek tidak dibesarkan", recompress_image(already, PROFILES["low"]) is None)
    transparent = io.BytesIO(); Image.new("RGBA", (300, 300), (1, 2, 3, 4)).save(transparent, "PNG")
    check("PNG transparan dilewati", recompress_image(transparent.getvalue() + b"\0" * 30000, PROFILES["high"]) is None)


def part_b(tmp: Path) -> None:
    from services.pdf_common import get_fitz
    try:
        fitz = get_fitz()
    except Exception:
        print("\n[B] SKIP - PyMuPDF belum terpasang (python -m pip install PyMuPDF)")
        return
    from services.compress_pdf import compress_pdf
    from services.jpg_to_pdf import images_to_pdf
    from services.pdf_to_jpg import render_pages

    print(f"\n[B] Uji nyata dengan PyMuPDF {getattr(fitz, 'VersionBind', '?')}")
    # PDF contoh: 3 halaman, tiap halaman berisi teks + foto besar.
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), f"Halaman {i + 1}", fontsize=20)
        page.insert_image(fitz.Rect(50, 100, 545, 500), stream=jpeg_bytes(noisy_image(1600, 1300, seed=i), 95))
    sample = tmp / "sample.pdf"; doc.save(str(sample)); doc.close()

    sizes = {}
    for level in ("low", "medium", "high"):
        out = tmp / f"c_{level}.pdf"
        original, compressed = compress_pdf(sample, level, out)
        with fitz.open(str(out)) as d:
            ok_pages = d.page_count == 3
        sizes[level] = compressed
        check(f"compress {level}: PDF valid, 3 halaman, tidak lebih besar dari asli", ok_pages and compressed <= original)
    check("compress: high <= medium <= low", sizes["high"] <= sizes["medium"] <= sizes["low"])
    check("compress high memperkecil file secara nyata (>20%)", sizes["high"] < sample.stat().st_size * 0.8)

    out_dir = tmp / "jpgs"; out_dir.mkdir()
    pages = render_pages(sample, out_dir)
    check("PDF -> JPG: 3 file JPG terbaca", len(pages) == 3 and all(Image.open(p).format == "JPEG" for p in pages))
    check("PDF -> JPG: ukuran A4 pada 150 dpi (~1240x1754)", abs(Image.open(pages[0]).size[0] - 1240) <= 2)

    imgs = [tmp / "i1.jpg", tmp / "i2.png", tmp / "i3.jpg"]
    Image.new("RGB", (800, 1200), "red").save(imgs[0], "JPEG")
    Image.new("RGBA", (1200, 600), (0, 0, 255, 128)).save(imgs[1])
    noisy_image(500, 500).save(imgs[2], "JPEG")
    out = tmp / "images.pdf"
    n = images_to_pdf(imgs, out)
    with fitz.open(str(out)) as d:
        dims = [(round(p.rect.width), round(p.rect.height)) for p in d]
    check("JPG -> PDF: 3 halaman A4, orientasi mengikuti gambar", n == 3 and dims == [(595, 842), (842, 595), (595, 842)])


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="docbot_p4_"))
    part_a(tmp)
    part_b(tmp)
    failed = [name for ok, name in results if not ok]
    print(f"\nHasil: {len(results) - len(failed)}/{len(results)} lulus")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
