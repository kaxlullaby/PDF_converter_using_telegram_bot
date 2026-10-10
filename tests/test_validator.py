"""Validasi file masuk: metadata, isi, dan serangan (zip bomb, tautan eksternal, PDF rusak)."""
from __future__ import annotations

import time
import unittest
import zipfile

from PIL import Image
from pypdf import PdfWriter

from tests.fakes import MB, TmpCase
from tests.samples import make_docx_zip, make_jpeg, make_pdf, make_png, rels_xml
from utils import file_validator as fv
from utils.file_validator import ValidationError, check_metadata, inspect_content


def code_of(fn) -> str | None:
    try:
        fn()
    except ValidationError as exc:
        return exc.code
    return None


class TestMetadata(unittest.TestCase):
    def test_extension_ditolak(self):
        for name in ("document.pdf.exe", "virus.exe", "a.txt", "tanpa_ekstensi", ".pdf", "a.pdf ", "x.pdf\u202eexe"):
            with self.subTest(name=name):
                self.assertEqual(code_of(lambda: check_metadata(name, "application/pdf", 1000, "pdf", 20 * MB)), "bad_extension")

    def test_mime_tidak_cocok_ditolak_tapi_generik_diizinkan(self):
        self.assertEqual(code_of(lambda: check_metadata("a.pdf", "image/png", 1000, "pdf", 20 * MB)), "bad_mime")
        self.assertEqual(check_metadata("a.docx", "application/octet-stream", 1000, "word", 20 * MB), ".docx")
        self.assertEqual(check_metadata("A.PDF", None, 1000, "pdf", 20 * MB), ".pdf")

    def test_ukuran(self):
        self.assertEqual(code_of(lambda: check_metadata("a.pdf", None, 25 * MB, "pdf", 20 * MB)), "too_large")
        self.assertEqual(code_of(lambda: check_metadata("a.pdf", None, 0, "pdf", 20 * MB)), "empty")
        self.assertEqual(check_metadata("a.pdf", None, None, "pdf", 20 * MB), ".pdf")  # ukuran tak diketahui -> dicek setelah unduh

    def test_jenis_fitur(self):
        self.assertEqual(code_of(lambda: check_metadata("a.pdf", None, 1, "image", 20 * MB)), "bad_extension")
        self.assertEqual(check_metadata("a.PNG", "image/png", 1, "image", 20 * MB), ".png")


class TestContent(TmpCase):
    def inspect(self, path, kind="pdf", limit=20 * MB):
        return inspect_content(path, kind, limit)

    def test_file_valid(self):
        info = self.inspect(make_pdf(self.tmp / "a.pdf", [100, 101, 102]))
        self.assertEqual((info.detected, info.pages), ("pdf", 3))
        self.assertEqual(self.inspect(make_png(self.tmp / "a.png"), "image").detected, "png")
        self.assertEqual(self.inspect(make_jpeg(self.tmp / "a.jpg"), "image").detected, "jpeg")
        self.assertEqual(self.inspect(make_docx_zip(self.tmp / "a.docx"), "word").detected, "docx")

    def test_file_palsu_atau_rusak(self):
        cases = {
            "exe berkedok pdf": (b"MZ\x90\x00 program", "bad_signature"),
            "html berkedok pdf": (b"<html><script>alert(1)</script>", "bad_signature"),
            "pdf terpotong": (b"%PDF-1.4\nbukan pdf sebenarnya\n" * 3, "corrupt"),
            "file kosong": (b"", "empty"),
        }
        for label, (data, expected) in cases.items():
            with self.subTest(label=label):
                (self.tmp / "x.pdf").write_bytes(data)
                self.assertEqual(code_of(lambda: self.inspect(self.tmp / "x.pdf")), expected)

    def test_jenis_isi_harus_sesuai_fitur(self):
        self.assertEqual(code_of(lambda: self.inspect(make_png(self.tmp / "a.png"), "pdf")), "bad_signature")
        self.assertEqual(code_of(lambda: self.inspect(make_pdf(self.tmp / "a.pdf"), "image")), "bad_signature")

    def test_ukuran_asli_melebihi_batas_walau_metadata_bohong(self):
        pdf = make_pdf(self.tmp / "a.pdf")
        self.assertEqual(code_of(lambda: self.inspect(pdf, limit=100)), "too_large")

    def test_pdf_berpassword_ditolak(self):
        writer = PdfWriter(); writer.add_blank_page(100, 100); writer.encrypt("rahasia")
        with (self.tmp / "e.pdf").open("wb") as fh:
            writer.write(fh)
        self.assertEqual(code_of(lambda: self.inspect(self.tmp / "e.pdf")), "encrypted")

    def test_pdf_terlalu_banyak_halaman(self):
        self.assertEqual(code_of(lambda: self.inspect(make_pdf(self.tmp / "b.pdf", [100] * (fv.MAX_PDF_PAGES + 1)))), "too_many_pages")

    def test_gambar_terlalu_besar_pikselnya(self):
        Image.new("L", (9000, 9000)).save(self.tmp / "big.png")  # 81 megapiksel, file kecil
        self.assertLess((self.tmp / "big.png").stat().st_size, 2 * MB)
        self.assertEqual(code_of(lambda: self.inspect(self.tmp / "big.png", "image")), "image_too_big")

    def test_pdf_jahat_tidak_hang_dan_ditolak_cepat(self):
        def build(objs):
            out, offsets = b"%PDF-1.4\n", []
            for i, body in enumerate(objs, 1):
                offsets.append(len(out)); out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
            xref = len(out)
            out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
            return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)

        catalog = b"<< /Type /Catalog /Pages 2 0 R >>"
        deep = [catalog] + [b"<< /Type /Pages /Kids [%d 0 R] /Count 1 >>" % (d + 3) for d in range(3000)]
        deep.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>")
        cases = {
            "Kids menunjuk dirinya sendiri": [catalog, b"<< /Type /Pages /Kids [2 0 R] /Count 1 >>"],
            "Kids bersiklus A<->B": [catalog, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", b"<< /Type /Pages /Kids [2 0 R] /Count 1 >>"],
            "pohon bersarang 3000 tingkat": deep,
        }
        for label, objs in cases.items():
            with self.subTest(label=label):
                (self.tmp / "evil.pdf").write_bytes(build(objs))
                started = time.perf_counter()
                self.assertEqual(code_of(lambda: self.inspect(self.tmp / "evil.pdf")), "corrupt")
                self.assertLess(time.perf_counter() - started, 3)


class TestDocxSecurity(TmpCase):
    def check(self, extra=None, **kwargs):
        path = make_docx_zip(self.tmp / "d.docx", extra, **kwargs)
        return code_of(lambda: inspect_content(path, "word", 20 * MB))

    def test_docx_biasa_dan_hyperlink_diizinkan(self):
        self.assertIsNone(self.check())
        link = rels_xml(("hyperlink", "https://contoh.id", "External"), ("image", "media/a.png", None))
        self.assertIsNone(self.check({"word/_rels/document.xml.rels": link}))

    def test_tautan_eksternal_berbahaya_ditolak(self):
        for kind in ("image", "attachedTemplate", "oleObject", "frame", "subDocument", "externalLink", "aFChunk"):
            with self.subTest(kind=kind):
                rels = rels_xml((kind, "http://10.0.0.5/rahasia", "External"))
                self.assertEqual(self.check({"word/_rels/document.xml.rels": rels}), "external_link")

    def test_trik_penyamaran_tidak_lolos(self):
        base = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                "<Relationship Id='r1' Type='http://x/relationships/image' Target='http://evil/a.png' {attr}/></Relationships>")
        variants = {
            "spasi di sekitar '='": "TargetMode = 'External'",
            "karakter entitas": 'TargetMode="&#69;xternal"',
            "huruf besar-kecil": 'TargetMode="EXTERNAL"',
        }
        for label, attr in variants.items():
            with self.subTest(label=label):
                self.assertEqual(self.check({"word/_rels/document.xml.rels": base.format(attr=attr)}), "external_link")

    def test_dtd_dan_xml_rusak_ditolak(self):
        bomb = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><Relationships>&a;</Relationships>'
        self.assertEqual(self.check({"word/_rels/document.xml.rels": bomb}), "external_link")
        self.assertEqual(self.check({"word/_rels/document.xml.rels": "<Relationships><oops"}), "external_link")

    def test_zip_bomb_ukuran(self):
        path = self.tmp / "bomb.docx"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", "<Types/>"); zf.writestr("word/document.xml", "<w/>")
            with zf.open("word/media.bin", "w", force_zip64=True) as out:
                chunk = b"\0" * MB
                for _ in range(210):
                    out.write(chunk)
        self.assertLess(path.stat().st_size, 2 * MB)  # file kecil, isi 210 MB
        self.assertEqual(code_of(lambda: inspect_content(path, "word", 20 * MB)), "zip_bomb")

    def test_zip_bomb_jumlah_entri(self):
        extra = {f"word/x{i}.xml": "" for i in range(fv.MAX_DOCX_ENTRIES + 1)}
        self.assertEqual(self.check(extra), "zip_bomb")

    def test_zip_bukan_word_ditolak(self):
        path = self.tmp / "x.docx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types/>"); zf.writestr("xl/workbook.xml", "<x/>")
        self.assertEqual(code_of(lambda: inspect_content(path, "word", 20 * MB)), "corrupt")


if __name__ == "__main__":
    unittest.main()
