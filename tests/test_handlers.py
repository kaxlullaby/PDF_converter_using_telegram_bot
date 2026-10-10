"""Alur lengkap dari sisi user: tombol, upload, proses latar belakang, hasil, pembersihan, database."""
from __future__ import annotations

import asyncio
import errno
import io
import shutil
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from docx import Document
from PIL import Image
from pypdf import PdfReader

import services.word_to_pdf as w2p
from handlers import info as info_h
from handlers import pdf as pdf_h
from handlers import start as start_h
from handlers.common import MSG_BUSY, menu_keyboard
from services.job_context import checkpoint
from services.pdf_common import MissingDependencyError
from services.pdf_to_word import ConversionReport
from tests.fakes import EnvCase, FakeMsg, FakeUpdate, button_data, button_texts
from tests.samples import make_pdf, make_real_docx, page_widths

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class FlowCase(EnvCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.A = make_pdf(self.tmp / "a.pdf", [101])
        self.B = make_pdf(self.tmp / "b.pdf", [201, 202])
        self.C = make_pdf(self.tmp / "c.pdf", [301, 302, 303, 304, 305])
        fake = self.tmp / "fake.pdf"; fake.write_bytes(b"MZ not a pdf at all")
        for key, path in (("A", self.A), ("B", self.B), ("C", self.C), ("FAKE", fake)):
            self.env.add_source(key, path)

    @property
    def bot(self): return self.env.bot

    async def start_merge(self, uid=1, n=2):
        await self.env.press("feature:merge", uid=uid)
        for k in range(n):
            await self.env.send_file(f"f{k}.pdf", "A", uid=uid)
        return self.bot.sent[-1]

    @staticmethod
    def slow_merge(seconds, steps=20):
        def fn(inputs, out):
            for i in range(steps):
                checkpoint(i, steps); time.sleep(seconds / steps)
            shutil.copyfile(inputs[0], out)
            return 3
        return fn

    async def last_operation(self, uid=1):
        return (await self.env.db.recent_operations(uid, 1))[0]


class TestMergeSplitRotate(FlowCase):
    async def test_merge_lengkap(self):
        env = self.env
        await env.press("feature:merge")
        await env.send_file("a.pdf", "A")
        query, _ = await env.press("act:done")
        self.assertTrue(query.answered[-1][1] and "Minimal 2" in query.answered[-1][0])
        await env.send_file("b.pdf", "B")
        await env.send_file("virus.pdf.exe", "FAKE"); self.assertTrue(any("tidak didukung" in t for t in env.replies()))
        await env.send_file("palsu.pdf", "FAKE"); self.assertTrue(any("tidak dapat diproses" in t for t in env.replies()))
        session = env.sessions.get(1)
        self.assertEqual(len(session.files), 2)
        self.assertEqual(len(list(env.files.user_dir(1).iterdir())), 2)  # file gagal sudah dihapus dari disk
        await env.press(f"ord:down:{session.files[0].fid}")
        await env.press("act:done")
        self.assertEqual(self.bot.docs[0][0], "merged.pdf")
        self.assertEqual(page_widths(self.bot.docs[0][2]), [201, 202, 101])
        self.assertIsNone(env.sessions.get(1)); self.assertFalse(env.user_dir_exists())
        self.assertEqual((await self.last_operation())["status"], "success")

    async def test_split(self):
        env = self.env
        await env.press("feature:split"); await env.send_file("laporan.pdf", "C")
        self.assertEqual((env.sessions.get(1).awaiting, env.sessions.get(1).files[0].pages), ("pages", 5))
        await env.send_file("lain.pdf", "A"); self.assertTrue(any("Maximum 1 file" in t for t in env.replies()))
        await env.say("2-9"); self.assertTrue(any("tidak ada" in t for t in env.replies()) and env.sessions.get(1))
        await env.say("halo"); self.assertTrue(any("tidak valid" in t for t in env.replies()))
        await env.say("4,2-3")
        self.assertEqual(self.bot.docs[0][0], "split_laporan.pdf")
        self.assertEqual(page_widths(self.bot.docs[0][2]), [304, 302, 303])
        self.assertFalse(env.user_dir_exists())

    async def test_rotate_dan_tombol_basi(self):
        env = self.env
        await env.press("feature:rotate"); await env.send_file("scan.pdf", "B")
        await env.press("rot:90")
        rotations = [p.rotation % 360 for p in PdfReader(io.BytesIO(self.bot.docs[0][2])).pages]
        self.assertEqual((self.bot.docs[0][0], rotations), ("rotated_scan.pdf", [90, 90]))
        await env.press("rot:90")  # tombol lama setelah selesai: tidak crash, tidak ada hasil kedua
        self.assertEqual(len(self.bot.docs), 1)

    async def test_user_terpisah_dan_cancel(self):
        env = self.env
        await env.press("feature:merge", uid=1); await env.press("feature:merge", uid=2)
        await env.send_file("a.pdf", "A", uid=1); await env.send_file("b.pdf", "B", uid=2)
        self.assertEqual(([f.name for f in env.sessions.get(1).files], [f.name for f in env.sessions.get(2).files]), (["a.pdf"], ["b.pdf"]))
        await env.press("nav:cancel", uid=1)
        self.assertTrue(env.sessions.get(1) is None and not env.user_dir_exists(1) and env.user_dir_exists(2))

    async def test_tanpa_sesi_dan_batas_jumlah(self):
        env = self.env
        await env.send_file("a.pdf", "A", uid=7); self.assertTrue(any("Pilih fitur" in t for t in env.replies()))
        await env.press("act:done", uid=7)
        self.assertTrue(any("Session expired" in t for kind, t in self.bot.outbox if kind == "qedit"))
        await env.press("feature:merge", uid=3)
        for i in range(20):
            await env.send_file(f"f{i}.pdf", "A", uid=3)
        self.assertEqual(len(env.sessions.get(3).files), 20)
        await env.send_file("f21.pdf", "A", uid=3)
        self.assertTrue(any("Maximum 20 files per operation" in t for t in env.replies()))

    async def test_batas_total_ukuran_sesi(self):
        await self.env.press("feature:merge")
        await self.env.send_file("a.pdf", "A"); await self.env.send_file("b.pdf", "A")
        used = sum(f.size for f in self.env.sessions.get(1).files)  # ukuran sebenarnya di disk
        self.env.set_config(max_session_size=used + 500)
        await self.env.send_file("c.pdf", "A", size=1000)  # 1000 byte lagi akan melewati batas
        self.assertTrue(any("Total ukuran" in t for t in self.env.replies()) and len(self.env.sessions.get(1).files) == 2)


class TestOtherFeatures(FlowCase):
    async def test_compress(self):
        env = self.env
        state = {"gain": True}

        def fake_compress(path, level, out):
            shutil.copyfile(path, out); size = Path(path).stat().st_size
            return (size, int(size * 0.4)) if state["gain"] else (size, size)

        with patch.object(pdf_h, "compress_pdf", fake_compress):
            await env.press("feature:compress"); await env.send_file("laporan besar.pdf", "C")
            panel = self.bot.sent[-1]
            self.assertEqual(button_data(panel.markup)[:3], ["cmp:low", "cmp:medium", "cmp:high"])
            self.assertEqual(button_texts(panel.markup)[:3], ["🟢 Low Compression", "🟡 Medium Compression", "🔴 High Compression"])
            await env.press("cmp:medium", msg=panel)
            name, caption, _ = self.bot.docs[0]
            self.assertEqual(name, "compressed_laporan besar.pdf")
            self.assertTrue(all(k in caption for k in ("Original:", "Compressed:", "Reduction: 60%")))
            state["gain"] = False; self.bot.docs.clear()
            await env.press("feature:compress"); await env.send_file("kecil.pdf", "A"); await env.press("cmp:high", msg=self.bot.sent[-1])
            self.assertIn("tidak bisa diperkecil", self.bot.docs[0][1]); self.assertIn("Reduction: 0%", self.bot.docs[0][1])
            await env.press("feature:compress"); await env.send_file("x.pdf", "A"); self.bot.docs.clear()
            await env.press("cmp:ultra", msg=self.bot.sent[-1]); self.assertEqual(self.bot.docs, [])  # level palsu diabaikan

    async def test_pdf_ke_jpg_tiga_mode_pengiriman(self):
        env = self.env
        self.env.add_source("P3", make_pdf(self.tmp / "p3.pdf", [100, 101, 102]))
        self.env.add_source("P12", make_pdf(self.tmp / "p12.pdf", list(range(100, 112))))

        def fake_render(path, out_dir):
            from services.pdf_common import get_page_count
            paths = []
            for i in range(get_page_count(path)):
                p = Path(out_dir) / f"page_{i + 1:03d}.jpg"; Image.new("RGB", (30, 40)).save(p); paths.append(p)
            return paths

        with patch.object(pdf_h, "render_pages", fake_render):
            for source, expected_files in (("A", 1), ("P3", 3), ("P12", 1)):
                with self.subTest(source=source):
                    self.bot.docs.clear(); self.bot.sent.clear()
                    await env.press("feature:pdf2jpg"); await env.send_file("dok.pdf", source)
                    self.assertIn("🖼 Convert to JPG", button_texts(self.bot.sent[-1].markup))
                    await env.press("act:done", msg=self.bot.sent[-1])
                    self.assertEqual(len(self.bot.docs), expected_files)
                    if source == "P3":
                        self.assertEqual([d[0] for d in self.bot.docs], ["dok_page_01.jpg", "dok_page_02.jpg", "dok_page_03.jpg"])
                    if source == "P12":
                        archive = zipfile.ZipFile(io.BytesIO(self.bot.docs[0][2]))
                        self.assertEqual((self.bot.docs[0][0], len(archive.namelist())), ("dok_jpg.zip", 12))
                    self.assertTrue(self.bot.docs[-1][1] and all(d[1] is None for d in self.bot.docs[:-1]))  # caption di file terakhir
                    self.assertFalse(env.user_dir_exists())

    async def test_jpg_ke_pdf(self):
        env = self.env
        env.add_source("JPG", self.tmp / "foto1.jpg"); env.add_source("PNG", self.tmp / "foto2.png")
        Image.new("RGB", (200, 300), "red").save(self.tmp / "foto1.jpg", "JPEG"); Image.new("RGBA", (300, 200), (0, 0, 255, 100)).save(self.tmp / "foto2.png")

        def fake_images(paths, out):
            ims = [Image.open(p).convert("RGB") for p in paths]
            ims[0].save(out, "PDF", save_all=True, append_images=ims[1:]); return len(paths)

        with patch.object(pdf_h, "images_to_pdf", fake_images):
            await env.press("feature:jpg2pdf")
            await env.send_file("foto1.jpg", "JPG", mime="image/jpeg"); await env.send_file("foto2.png", "PNG", mime="image/png")
            from handlers import files as files_h
            await files_h._receive(FakeUpdate(1, message=FakeMsg(self.bot)), env.context, file_id="JPG", file_name=None,
                                   mime_type="image/jpeg", file_size=900, is_photo=True)
            self.assertEqual([f.name for f in env.sessions.get(1).files], ["foto1.jpg", "foto2.png", "photo_3.jpg"])
            panel = self.bot.sent[-1]
            self.assertIn("📄 Convert to PDF", button_texts(panel.markup))
            await env.send_file("dokumen.pdf", "A"); self.assertTrue(any("hanya menerima file gambar" in t for t in env.replies()))
            fid = env.sessions.get(1).files[2].fid
            await env.press(f"ord:up:{fid}", msg=panel); await env.press(f"ord:up:{fid}", msg=panel)
            await env.press("act:done", msg=panel)
            self.assertEqual((self.bot.docs[0][0], len(page_widths(self.bot.docs[0][2]))), ("images.pdf", 3))
            self.assertFalse(env.user_dir_exists())

    @unittest.skipUnless(w2p.find_libreoffice(), "LibreOffice belum terpasang")
    async def test_word_ke_pdf_dengan_libreoffice_asli(self):
        env = self.env
        env.add_source("DOCX", make_real_docx(self.tmp / "surat.docx"))
        await env.press("feature:word2pdf")
        await env.send_file("catatan.txt", "FAKE", mime="text/plain"); self.assertTrue(any("Word (.doc, .docx)" in t for t in env.replies()))
        await env.send_file("Surat Lamaran.docx", "DOCX", mime=DOCX_MIME)
        panel = self.bot.sent[-1]
        self.assertIn("📄 Convert to PDF", button_texts(panel.markup))
        await env.press("act:done", msg=panel)
        self.assertEqual(self.bot.docs[0][0], "Surat Lamaran.pdf")
        body = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(self.bot.docs[0][2])).pages)
        self.assertTrue("Surat Resmi" in body and "percobaan" in body and "font" in self.bot.docs[0][1])
        self.assertFalse(env.user_dir_exists())

    async def test_word_ke_pdf_libreoffice_hilang(self):
        env = self.env
        env.add_source("DOCX", make_real_docx(self.tmp / "surat.docx"))
        with patch.object(w2p, "find_libreoffice", lambda *_: None):
            await env.press("feature:word2pdf"); await env.send_file("a.docx", "DOCX", mime=DOCX_MIME)
            panel = self.bot.sent[-1]; await env.press("act:done", msg=panel)
        self.assertTrue("LibreOffice belum terpasang" in panel.text and not self.bot.docs and not env.user_dir_exists())

    async def test_pdf_ke_word(self):
        env = self.env

        def fake_p2w(path, out, workdir, *, tesseract_path=None, languages="eng"):
            doc = Document(); doc.add_paragraph("isi hasil konversi"); doc.save(str(out))
            return ConversionReport("mixed", 4, 1, ["Bahasa OCR ind belum terpasang; memakai eng."])

        with patch.object(pdf_h, "pdf_to_word", fake_p2w):
            await env.press("feature:pdf2word"); await env.send_file("Laporan Akhir.pdf", "C")
            panel = self.bot.sent[-1]
            self.assertTrue("Text-based" in panel.text and "Scanned" in panel.text and "📄 Convert to Word" in button_texts(panel.markup))
            await env.press("act:done", msg=panel)
        name, caption, data = self.bot.docs[0]
        self.assertEqual((name, data[:2]), ("Laporan Akhir.docx", b"PK"))
        self.assertTrue(all(k in caption for k in ("Campuran", "Bahasa OCR ind", "tidak 100% mempertahankan layout")) and len(caption) <= 1024)

        def scan_without_ocr(*args, **kwargs):
            raise MissingDependencyError("PDF ini berupa hasil scan dan membutuhkan OCR, tetapi Tesseract belum terpasang di server.")

        self.bot.docs.clear()
        with patch.object(pdf_h, "pdf_to_word", scan_without_ocr):
            await env.press("feature:pdf2word"); await env.send_file("scan.pdf", "A"); panel = self.bot.sent[-1]
            await env.press("act:done", msg=panel)
        self.assertTrue("Tesseract belum terpasang" in panel.text and not self.bot.docs and not env.user_dir_exists())


class TestBackgroundJobs(FlowCase):
    async def test_progress_bar_dan_database(self):
        env = self.env
        with patch.object(pdf_h, "merge_pdfs", self.slow_merge(3.4)):
            panel = await self.start_merge()
            started = time.monotonic()
            await env.press("act:done", msg=panel, wait=False)
            self.assertLess(time.monotonic() - started, 0.5)  # handler langsung kembali
            self.assertTrue(env.sessions.peek(1).busy)
            await env.drain()
        edits = [t for kind, t in self.bot.outbox if kind == "edit"]
        self.assertTrue(any("[" in t and "%" in t and "█" in t for t in edits))
        self.assertTrue(len(self.bot.docs) == 1 and env.sessions.get(1) is None and not env.user_dir_exists())
        op = await self.last_operation()
        self.assertEqual((op["feature"], op["status"], op["input_files"]), ("merge", "success", 2))
        self.assertGreaterEqual(op["duration_ms"], 3000); self.assertGreater(op["output_bytes"], 0)
        self.assertGreaterEqual((await env.db.user_summary(1))["usage_count"], 1)

    async def test_cancel_dan_penolakan_saat_sibuk(self):
        env = self.env
        with patch.object(pdf_h, "merge_pdfs", self.slow_merge(6.0, steps=60)):
            panel = await self.start_merge()
            await env.press("act:done", msg=panel, wait=False); await asyncio.sleep(0.4)
            await env.send_file("baru.pdf", "A"); self.assertTrue(any("Masih ada proses" in t for t in env.replies()))
            query, _ = await env.press("act:done", msg=panel, wait=False)
            self.assertTrue(query.answered[-1][1] and "sedang berjalan" in query.answered[-1][0])
            query, _ = await env.press("feature:split", wait=False); self.assertEqual(query.answered[-1][0], MSG_BUSY)
            await env.command(start_h.start_command, text="/start")
            self.assertTrue(any("Masih ada proses" in t for t in env.replies()) and env.sessions.peek(1) is not None)
            started = time.monotonic()
            await env.press("job:cancel", msg=panel, wait=False); await env.drain()
            self.assertLess(time.monotonic() - started, 2.0)  # dihentikan, bukan menunggu 6 detik
        self.assertTrue(not self.bot.docs and "dibatalkan" in panel.text)
        self.assertTrue(env.sessions.get(1) is None and not env.user_dir_exists())
        self.assertEqual((await self.last_operation())["status"], "cancelled")

    async def test_dua_user_paralel_dan_antrean(self):
        env = self.env
        with patch.object(pdf_h, "merge_pdfs", self.slow_merge(1.2, steps=12)):
            panels = {uid: await self.start_merge(uid) for uid in (11, 12, 13)}
            started = time.monotonic()
            for uid in panels:
                await env.press("act:done", uid=uid, msg=panels[uid], wait=False)
            await asyncio.sleep(0.4)
            self.assertIn("antrean", panels[13].text.lower()); self.assertNotIn("antrean", panels[11].text.lower())
            await env.drain()
            elapsed = time.monotonic() - started
        self.assertEqual(len(self.bot.docs), 3)
        self.assertTrue(2.0 < elapsed < 3.4, elapsed)  # 2 slot: ~2,4 dtk, bukan 3,6 dtk berurutan

    async def test_urutan_file_terjaga_walau_unduhan_tidak_berurutan(self):
        from handlers import files as files_h
        env = self.env
        for key, path in (("S1", self.A), ("S2", self.B), ("S3", self.C)):
            env.add_source(key, path)
        self.bot.delays.update({"S1": 0.5, "S2": 0.0, "S3": 0.1})
        await env.press("feature:merge", uid=21)
        await asyncio.gather(*[files_h.handle_document(FakeUpdate(21, message=env.document_message(fid, name)), env.context)
                               for fid, name in (("S1", "pertama.pdf"), ("S2", "kedua.pdf"), ("S3", "ketiga.pdf"))])
        self.assertEqual([f.name for f in env.sessions.get(21).files], ["pertama.pdf", "kedua.pdf", "ketiga.pdf"])

    async def test_timeout_disk_penuh_dan_error_tak_terduga(self):
        env = self.env
        env.set_config(process_timeout=1, admin_ids=frozenset({999}))
        with patch.object(pdf_h, "merge_pdfs", self.slow_merge(10.0, steps=100)):
            panel = await self.start_merge()
            started = time.monotonic(); await env.press("act:done", msg=panel)
        self.assertTrue(time.monotonic() - started < 4 and "terlalu lama" in panel.text and not self.bot.docs)
        self.assertEqual((await self.last_operation())["status"], "timeout"); self.assertFalse(env.user_dir_exists())

        def disk_full(inputs, out): raise OSError(errno.ENOSPC, "No space left on device")
        def boom(inputs, out): raise RuntimeError("bug tak terduga")

        with patch.object(pdf_h, "merge_pdfs", disk_full):
            panel = await self.start_merge(); self.bot.messages.clear(); await env.press("act:done", msg=panel)
        self.assertTrue("kehabisan penyimpanan" in panel.text and any(c == 999 and "Disk" in t for c, t in self.bot.messages))
        with patch.object(pdf_h, "merge_pdfs", boom):
            panel = await self.start_merge(); self.bot.messages.clear(); await env.press("act:done", msg=panel)
        self.assertTrue("tidak dapat diproses" in panel.text and any(c == 999 and "RuntimeError" in t for c, t in self.bot.messages))
        env.set_config(process_timeout=300)
        with patch.object(pdf_h, "merge_pdfs", self.slow_merge(0.2, steps=4)):
            self.bot.docs.clear(); panel = await self.start_merge(); await env.press("act:done", msg=panel)
        self.assertEqual(len(self.bot.docs), 1)  # setelah error, proses berikutnya normal
        self.assertEqual([o["status"] for o in await env.db.recent_operations(1, 4)], ["success", "failed", "failed", "timeout"])

    async def test_kirim_ulang_jika_telegram_membatasi(self):
        from tests.fakes import BadRequest, NetworkError, RetryAfter
        env = self.env
        with patch.object(pdf_h, "merge_pdfs", self.slow_merge(0.2, steps=4)):
            for label, error, expected_docs, expected_text in (
                ("RetryAfter", RetryAfter(0), 1, "complete"),
                ("koneksi putus", NetworkError("putus"), 1, "complete"),
                ("BadRequest tidak diulang", BadRequest("ditolak"), 0, "Gagal mengirim"),
            ):
                with self.subTest(label=label):
                    self.bot.docs.clear(); self.bot.fail_next[:] = [error]
                    panel = await self.start_merge(); await env.press("act:done", msg=panel)
                    self.assertEqual(len(self.bot.docs), expected_docs); self.assertIn(expected_text, panel.text)
                    self.assertFalse(env.user_dir_exists())


class TestMenuAndInfo(FlowCase):
    async def test_fitur_tidak_tersedia_disembunyikan(self):
        env = self.env
        env.data["unavailable"] = frozenset({"word2pdf"})
        texts = button_texts(menu_keyboard(env.context))
        self.assertTrue("📝 Word → PDF" not in texts and "📄 PDF → Word" in texts)
        query, _ = await env.press("feature:word2pdf", wait=False)
        self.assertTrue("tidak tersedia" in query.answered[-1][0] and env.sessions.get(1) is None)
        env.data["unavailable"] = frozenset()
        self.assertIn("📝 Word → PDF", button_texts(menu_keyboard(env.context)))

    async def test_history_dan_stats(self):
        env = self.env
        await env.db.log_operation(1, "merge", "success", 3, 3_000_000, 2_000_000, 1800)
        await env.db.log_operation(1, "compress", "failed", 1, 5000, None, 90)
        msg = await env.command(info_h.history_command)
        text = env.replies()[0]
        self.assertTrue(all(k in text for k in ("Total proses berhasil", "✅ 🔗 Merge PDF", "❌", "tidak disimpan")))
        await env.command(info_h.stats_command); self.assertIn("hanya untuk admin", env.replies()[0])
        env.set_config(admin_ids=frozenset({999}))
        await env.command(info_h.stats_command, uid=999); self.assertTrue("Statistik bot" in env.replies()[0] and "Pengguna:" in env.replies()[0])

    async def test_menu_utama_sesuai_desain(self):
        texts = button_texts(menu_keyboard(self.env.context))
        self.assertEqual(texts, ["🔗 Merge PDF", "✂️ Split PDF", "📦 Compress PDF", "🖼 PDF → JPG", "📄 JPG → PDF",
                                 "🔄 Rotate PDF", "📝 Word → PDF", "📄 PDF → Word", "ℹ️ Help"])


if __name__ == "__main__":
    unittest.main()
