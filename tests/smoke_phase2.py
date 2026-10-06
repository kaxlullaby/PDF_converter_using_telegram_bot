"""Smoke test Phase 2 - TIDAK butuh Telegram/internet.

Menguji validator, file manager, dan session manager secara offline.
Jalankan dari folder project:   python tests/smoke_phase2.py
(Di Phase 7 tes ini akan dikembangkan menjadi test suite pytest.)
"""
from __future__ import annotations

import io
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402
from pypdf import PdfWriter  # noqa: E402

from utils.file_manager import FileManager, sanitize_filename  # noqa: E402
from utils.file_validator import ValidationError, check_content, check_metadata  # noqa: E402
from utils.session_manager import SessionExpired, SessionFile, SessionManager  # noqa: E402

MB = 1024 * 1024
results: list[tuple[bool, str]] = []


def check(name: str, condition: bool) -> None:
    results.append((condition, name))
    print(("  PASS  " if condition else "  FAIL  ") + name)


def rejects(fn, code: str) -> bool:
    try:
        fn()
    except ValidationError as exc:
        return exc.code == code
    return False


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="docbot_test_"))

    print("\n[1] sanitize_filename")
    check("path traversal dibuang", sanitize_filename("..\\..\\evil<>.pdf") == "evil__.pdf")
    check("nama kosong -> 'file'", sanitize_filename("") == "file" and sanitize_filename(None) == "file")
    check("nama panjang dipotong, extension aman", len(sanitize_filename("a" * 200 + ".pdf")) <= 60
          and sanitize_filename("a" * 200 + ".pdf").endswith(".pdf"))

    print("\n[2] check_metadata (sebelum download)")
    check("document.pdf.exe ditolak", rejects(lambda: check_metadata("document.pdf.exe", "application/pdf", 1000, "pdf", 20 * MB), "bad_extension"))
    check("MIME tidak cocok ditolak", rejects(lambda: check_metadata("a.pdf", "image/png", 1000, "pdf", 20 * MB), "bad_mime"))
    check("25 MB ditolak (batas 20 MB)", rejects(lambda: check_metadata("a.pdf", "application/pdf", 25 * MB, "pdf", 20 * MB), "too_large"))
    check("file kosong ditolak", rejects(lambda: check_metadata("a.pdf", "application/pdf", 0, "pdf", 20 * MB), "empty"))
    check("PDF valid lolos", check_metadata("Tugas.PDF", "application/pdf", 1000, "pdf", 20 * MB) == ".pdf")
    check("octet-stream tetap lolos (dicek lewat isi file)", check_metadata("a.docx", "application/octet-stream", 1000, "word", 20 * MB) == ".docx")

    print("\n[3] check_content (setelah download)")
    pdf = tmp / "ok.pdf"
    w = PdfWriter(); w.add_blank_page(200, 200); w.write(pdf)
    check("PDF valid lolos", check_content(pdf, "pdf", 20 * MB) == "pdf")

    fake = tmp / "fake.pdf"; fake.write_bytes(b"MZ\x90\x00 ini sebenarnya program .exe")
    check("file palsu (.exe di-rename .pdf) ditolak", rejects(lambda: check_content(fake, "pdf", 20 * MB), "bad_signature"))

    broken = tmp / "broken.pdf"; broken.write_bytes(b"%PDF-1.4\nini bukan pdf yang benar\n" * 3)
    check("PDF corrupt ditolak", rejects(lambda: check_content(broken, "pdf", 20 * MB), "corrupt"))

    empty = tmp / "empty.pdf"; empty.write_bytes(b"")
    check("file 0 byte ditolak", rejects(lambda: check_content(empty, "pdf", 20 * MB), "empty"))

    big = tmp / "big.pdf"; big.write_bytes(pdf.read_bytes())
    check("ukuran asli > batas ditolak", rejects(lambda: check_content(big, "pdf", 100), "too_large"))

    try:
        w = PdfWriter(); w.add_blank_page(200, 200); w.encrypt("rahasia")
        enc = tmp / "enc.pdf"; w.write(enc)
        check("PDF ber-password ditolak", rejects(lambda: check_content(enc, "pdf", 20 * MB), "encrypted"))
    except Exception as exc:  # library crypto tidak tersedia
        print(f"  SKIP  tes PDF ber-password ({exc.__class__.__name__})")

    png = tmp / "a.png"; Image.new("RGB", (50, 50), "red").save(png)
    jpg = tmp / "a.jpg"; Image.new("RGB", (50, 50), "blue").save(jpg)
    check("PNG valid lolos", check_content(png, "image", 20 * MB) == "png")
    check("JPG valid lolos", check_content(jpg, "image", 20 * MB) == "jpeg")
    check("PNG dikirim ke fitur PDF ditolak", rejects(lambda: check_content(png, "pdf", 20 * MB), "bad_signature"))

    docx = tmp / "ok.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>"); zf.writestr("word/document.xml", "<w:document/>")
    check("DOCX valid lolos", check_content(docx, "word", 20 * MB) == "docx")
    xlsx_like = tmp / "x.docx"
    with zipfile.ZipFile(xlsx_like, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>"); zf.writestr("xl/workbook.xml", "<x/>")
    check("ZIP bukan Word ditolak", rejects(lambda: check_content(xlsx_like, "word", 20 * MB), "corrupt"))

    print("\n[4] FileManager")
    fm = FileManager(tmp / "temp")
    p1, p2 = fm.new_file_path(111, ".pdf"), fm.new_file_path(222, ".pdf")
    check("folder user terpisah", p1.parent != p2.parent and p1.parent.name == "user_111")
    check("nama file acak", p1.stem != p2.stem and len(p1.stem) == 32)
    try:
        fm.new_file_path(111, "../../x.sh"); bad_ext = False
    except ValueError:
        bad_ext = True
    check("ekstensi berbahaya ditolak", bad_ext)
    try:
        fm.user_dir("1/../../etc"); traversal = False
    except ValueError:
        traversal = True
    check("path traversal lewat user_id ditolak", traversal)
    p1.write_bytes(b"x"); p2.write_bytes(b"y")
    fm.delete_user_dir(111)
    check("hapus folder user A tidak menyentuh user B", not p1.parent.exists() and p2.exists())
    check("cleanup_all membersihkan sisa", fm.cleanup_all() == 1 and not p2.parent.exists())

    print("\n[5] SessionManager")
    fm = FileManager(tmp / "temp2")
    sm = SessionManager(fm, timeout=1)
    a = sm.start(1, 100, "merge"); b = sm.start(2, 200, "merge")
    for s, n in ((a, 3), (b, 2)):
        for i in range(n):
            path = fm.new_file_path(s.user_id, ".pdf"); path.write_bytes(b"x")
            s.files.append(SessionFile(fid=f"{s.user_id}{i}", name=f"f{i}.pdf", path=path, size=1, kind="pdf"))
    check("session user terpisah", len(a.files) == 3 and len(b.files) == 2)
    a.move("11", +1)
    check("urutan bisa diubah", [f.fid for f in a.files] == ["10", "12", "11"])
    removed_path = a.find("12").path
    sm.remove_file(a, "12")
    check("hapus 1 file ikut menghapus dari disk", not removed_path.exists() and len(a.files) == 2)
    sm.end(1)
    check("end() menghapus semua file user", not fm.user_dir(1).exists() and fm.user_dir(2).exists())
    time.sleep(1.2)
    check("session kedaluwarsa terdeteksi sweeper", [s.user_id for s in sm.expired_sessions()] == [2])
    try:
        sm.get(2); expired = False
    except SessionExpired:
        expired = True
    check("get() pada session expired -> SessionExpired + file dihapus", expired and not fm.user_dir(2).exists())

    failed = [name for ok, name in results if not ok]
    print(f"\nHasil: {len(results) - len(failed)}/{len(results)} lulus")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
