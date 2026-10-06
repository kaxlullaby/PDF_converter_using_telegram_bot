"""Smoke test Phase 3 - TIDAK butuh Telegram/internet.

Menguji parser halaman dan service Merge / Split / Rotate.
Jalankan dari folder project:   python tests/smoke_phase3.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pypdf import PdfReader, PdfWriter  # noqa: E402

from services.merge_pdf import merge_pdfs  # noqa: E402
from services.pdf_common import PdfProcessingError, get_page_count  # noqa: E402
from services.rotate_pdf import rotate_pdf  # noqa: E402
from services.split_pdf import PageSelectionError, parse_page_ranges, split_pdf  # noqa: E402

results: list[tuple[bool, str]] = []


def check(name: str, condition: bool) -> None:
    results.append((condition, name))
    print(("  PASS  " if condition else "  FAIL  ") + name)


def raises(exc_type, fn) -> bool:
    try:
        fn()
    except exc_type:
        return True
    return False


def make_pdf(path: Path, widths: list[int]) -> Path:
    """Buat PDF; lebar tiap halaman berbeda supaya urutannya bisa dibuktikan."""
    w = PdfWriter()
    for width in widths:
        w.add_blank_page(width=width, height=100)
    with path.open("wb") as fh:
        w.write(fh)
    return path


def widths(path: Path) -> list[int]:
    return [int(p.mediabox.width) for p in PdfReader(str(path)).pages]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="docbot_p3_"))

    print("\n[1] parse_page_ranges")
    check('"1-5"', parse_page_ranges("1-5", 12) == [1, 2, 3, 4, 5])
    check('"2,4,7"', parse_page_ranges("2,4,7", 12) == [2, 4, 7])
    check('"3-8"', parse_page_ranges("3-8", 12) == [3, 4, 5, 6, 7, 8])
    check("campuran + spasi", parse_page_ranges("1-3, 6, 9 - 10", 12) == [1, 2, 3, 6, 9, 10])
    check("urutan ketikan dipertahankan", parse_page_ranges("5,1", 12) == [5, 1])
    check("duplikat dibuang", parse_page_ranges("1,1-2", 12) == [1, 2])
    for bad in ("", "0", "5-1", "13", "1-13", "abc", "1-", "-3", "1..3", "1;;x", "9" * 300):
        check(f"ditolak: {bad[:12]!r}", raises(PageSelectionError, lambda b=bad: parse_page_ranges(b, 12)))
    check("rentang raksasa tidak meledak", raises(PageSelectionError, lambda: parse_page_ranges("1-999999999", 12)))

    print("\n[2] merge_pdfs")
    a = make_pdf(tmp / "a.pdf", [101])
    b = make_pdf(tmp / "b.pdf", [201, 202])
    c = make_pdf(tmp / "c.pdf", [301, 302, 303])
    out = tmp / "merged.pdf"
    total = merge_pdfs([c, a, b], out)
    check("total halaman benar", total == 6 and get_page_count(out) == 6)
    check("urutan mengikuti list", widths(out) == [301, 302, 303, 101, 201, 202])
    check("minimal 2 file", raises(PdfProcessingError, lambda: merge_pdfs([a], tmp / "x.pdf")))

    enc = tmp / "enc.pdf"
    try:
        w = PdfWriter(); w.add_blank_page(width=401, height=100); w.encrypt(user_password="")
        with enc.open("wb") as fh:
            w.write(fh)
        merge_pdfs([a, enc], tmp / "m2.pdf")
        check("PDF terenkripsi (password kosong) bisa digabung", widths(tmp / "m2.pdf") == [101, 401])
    except Exception as exc:
        print(f"  SKIP  tes enkripsi ({exc.__class__.__name__})")

    print("\n[3] split_pdf")
    out = tmp / "split.pdf"
    n = split_pdf(c, [3, 1], out)
    check("halaman terpilih sesuai urutan", n == 2 and widths(out) == [303, 301])
    check("halaman di luar jangkauan ditolak", raises(PdfProcessingError, lambda: split_pdf(c, [4], tmp / "y.pdf")))

    print("\n[4] rotate_pdf")
    out = tmp / "rot.pdf"
    n = rotate_pdf(c, 90, out)
    rotations = [p.rotation % 360 for p in PdfReader(str(out)).pages]
    check("semua halaman diputar 90", n == 3 and rotations == [90, 90, 90])
    out2 = tmp / "rot2.pdf"
    rotate_pdf(out, 270, out2)
    check("rotasi bertumpuk (90 + 270 = 0)", [p.rotation % 360 for p in PdfReader(str(out2)).pages] == [0, 0, 0])
    rotate_pdf(c, 180, tmp / "rot3.pdf")
    check("180 derajat", {p.rotation % 360 for p in PdfReader(str(tmp / 'rot3.pdf')).pages} == {180})
    check("sudut tidak valid ditolak", raises(ValueError, lambda: rotate_pdf(c, 45, tmp / "z.pdf")))

    failed = [name for ok, name in results if not ok]
    print(f"\nHasil: {len(results) - len(failed)}/{len(results)} lulus")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
