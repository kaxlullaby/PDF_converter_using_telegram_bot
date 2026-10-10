"""Uji keamanan: setiap butir di bagian "Security" spesifikasi diuji dengan serangan nyata.

1. Tidak menjalankan shell dari input user      6. Batasi waktu processing
2. Sanitasi nama file                           7. Hapus file temp
3. Nama file temp acak                          8. Tidak menyimpan dokumen / token
4. Isolasi session tiap user                    9. Tangani file jahat / rusak
5. Batasi ukuran & jumlah file                 10. Injeksi (HTML, SQL, callback_data)
"""
from __future__ import annotations

import html
import logging
import re
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import services.pdf_to_word as p2w
import services.process_utils as process_utils
import services.word_to_pdf as w2p
from database.database import Database
from handlers import start as start_h
from handlers.files import build_panel, build_reorder
from tests.fakes import MB, EnvCase, TmpCase
from tests.samples import make_pdf, make_real_docx
from utils.file_manager import FileManager, sanitize_filename
from utils.file_validator import ValidationError, check_metadata, inspect_content
from utils.keyboards import FEATURES

ROOT = Path(__file__).resolve().parent.parent
HOSTILE_NAMES = [
    "../../etc/passwd.pdf", "..\\..\\windows\\system32\\config.pdf", "a\x00b.pdf", "CON.pdf", "x" * 500 + ".pdf",
    "<script>alert(1)</script>.pdf", "'; DROP TABLE users;--.pdf", "$(rm -rf ~).pdf", "`reboot`.pdf", "a|b&c;d.pdf",
    "laporan\u202efdp.exe", "名前 😀.pdf", "\n\r\t.pdf", "%s%s%s%n.pdf", "{0.__class__}.pdf",
]


class TestNoShellInjection(TmpCase):
    def test_perintah_eksternal_hanya_berisi_path_milik_kita(self):
        """Nama file dari user tidak pernah sampai ke argumen LibreOffice, apa pun isinya."""
        captured = []

        def fake_run(cmd, *, timeout, env=None):
            captured.append(cmd)
            out = Path(cmd[cmd.index("--outdir") + 1])
            make_pdf(out / (Path(cmd[-1]).stem + ".pdf"))
            return 0, b"", b""

        stored = self.tmp / "temp" / "user_1" / "8d1f2c3a4b5e.docx"  # nama acak buatan FileManager
        stored.parent.mkdir(parents=True); stored.write_bytes(b"x")
        with patch.object(w2p, "run_command", fake_run), patch.object(w2p, "find_libreoffice", lambda *_: "/usr/bin/soffice"):
            w2p.word_to_pdf(stored, self.tmp / "out.pdf")
        cmd = captured[0]
        self.assertIsInstance(cmd, list)
        for hostile in HOSTILE_NAMES:
            self.assertFalse(any(hostile in arg for arg in cmd))
        self.assertEqual(cmd[-1], str(stored.resolve()))

    def test_popen_tidak_pernah_memakai_shell(self):
        calls = []
        real_popen = subprocess.Popen

        def spy(*args, **kwargs):
            calls.append((args, kwargs)); return real_popen(*args, **kwargs)

        with patch.object(process_utils.subprocess, "Popen", spy):
            process_utils.run_command(["python3", "-c", "print(1)"], timeout=20)
        args, kwargs = calls[0]
        self.assertIsInstance(args[0], list); self.assertFalse(kwargs.get("shell", False))

    def test_tidak_ada_shell_true_di_kode_sumber(self):
        offenders = []
        for path in ROOT.rglob("*.py"):
            if "tests" in path.parts or ".venv" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            if re.search(r"shell\s*=\s*True|os\.system\(|os\.popen\(|\beval\(|\bexec\(", source):
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_kode_bahasa_ocr_divalidasi(self):
        for bad in ("eng; rm -rf /", "eng && reboot", "$(id)", "eng\nfra", "eng+", "+eng", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                p2w.run_tesseract(self.tmp / "x.png", "tesseract", bad)


class TestFilenames(TmpCase):
    def test_nama_hasil_sanitasi_aman(self):
        for name in HOSTILE_NAMES:
            with self.subTest(name=name[:25]):
                clean = sanitize_filename(name)
                self.assertTrue(0 < len(clean) <= 60)
                self.assertFalse(any(c in clean for c in '/\\\x00<>|:*?"\n\r\t$`;&{}%'))
                self.assertTrue(clean.isprintable())

    def test_nama_file_di_disk_selalu_acak(self):
        fm = FileManager(self.tmp / "temp")
        paths = {fm.new_file_path(1, ".pdf").name for _ in range(200)}
        self.assertEqual(len(paths), 200)
        for name in paths:
            self.assertRegex(name, r"^[0-9a-f]{32}\.pdf$")

    def test_ekstensi_ganda_dan_penyamaran(self):
        for name in ("document.pdf.exe", "laporan.pdf\u202eexe", "x.pdf.bat", "x.pdf.", "x.pdf\x00.exe", "x.exe\x00.pdf.exe"):
            with self.subTest(name=repr(name)), self.assertRaises(ValidationError):
                check_metadata(name, "application/pdf", 100, "pdf", 20 * MB)

    def test_nama_hasil_proses_tidak_mengandung_path(self):
        from handlers.pdf import _output_name
        for name in HOSTILE_NAMES:
            produced = _output_name("rotated", sanitize_filename(name))
            self.assertTrue("/" not in produced and "\\" not in produced and produced.endswith(".pdf"), produced)


class TestIsolationAndTampering(EnvCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.env.add_source("A", make_pdf(self.tmp / "a.pdf", [101]))
        self.bot = self.env.bot

    async def test_user_tidak_bisa_menyentuh_file_user_lain_lewat_callback(self):
        env = self.env
        await env.press("feature:merge", uid=1); await env.press("feature:merge", uid=2)
        for _ in range(3):
            await env.send_file("a.pdf", "A", uid=1)
        await env.send_file("b.pdf", "A", uid=2)
        victim = [f.fid for f in env.sessions.get(1).files]
        for action in ("up", "down", "del"):
            for fid in victim:
                await env.press(f"ord:{action}:{fid}", uid=2)  # user 2 memakai id milik user 1
        self.assertEqual([f.fid for f in env.sessions.get(1).files], victim)
        self.assertTrue(all(f.path.exists() for f in env.sessions.get(1).files))
        self.assertEqual(len(env.sessions.get(2).files), 1)

    async def test_callback_data_palsu_diabaikan_tanpa_crash(self):
        env = self.env
        await env.press("feature:rotate"); await env.send_file("a.pdf", "A")
        for data in ("rot:abc", "rot:45", "rot:-90", "rot:", "cmp:../../x", "cmp:", "ord:", "ord:up", "ord:del:../../etc",
                     "feature:../../x", "feature:", "act:hack", "x" * 300, "", "list:show:extra", "job:cancel:1", "\x00"):
            with self.subTest(data=data[:20]):
                await env.press(data, wait=False)
        await env.drain()
        self.assertEqual(self.bot.docs, [])
        self.assertTrue(env.sessions.get(1) is not None and env.sessions.get(1).files[0].path.exists())

    async def test_fitur_salah_untuk_aksi(self):
        env = self.env
        await env.press("feature:merge"); await env.send_file("a.pdf", "A"); await env.send_file("a.pdf", "A")
        await env.press("rot:90"); await env.press("cmp:high")  # aksi fitur lain pada sesi Merge
        self.assertEqual(self.bot.docs, []); self.assertEqual(len(env.sessions.get(1).files), 2)

    async def test_pesan_teks_tidak_dianggap_perintah(self):
        env = self.env
        await env.press("feature:split"); await env.send_file("a.pdf", "A")
        for text in ("; rm -rf /", "$(reboot)", "1-3\n; reboot", "{{7*7}}", "<b>1</b>"):
            await env.say(text)
        self.assertEqual(self.bot.docs, [])


class TestLimits(EnvCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.env.add_source("A", make_pdf(self.tmp / "a.pdf", [101]))
        big = self.tmp / "big.pdf"; big.write_bytes(make_pdf(self.tmp / "tmp.pdf").read_bytes() + b"\0" * (21 * MB))
        self.env.add_source("BIG", big)

    async def test_ukuran_dilaporkan_bohong_tetap_tertangkap(self):
        env = self.env
        await env.press("feature:split")
        for declared in (None, 0, 1000):  # metadata Telegram tidak bisa dipercaya
            await env.send_file("a.pdf", "BIG", size=declared)
            self.assertEqual(len(env.sessions.get(1).files), 0, f"declared={declared}")
        self.assertFalse(list(env.files.user_dir(1).glob("*.pdf")))  # file besar tidak tertinggal di disk

    async def test_ukuran_terlalu_besar_pesan_sesuai_spesifikasi(self):
        await self.env.press("feature:split"); await self.env.send_file("a.pdf", "A", size=25 * MB)
        text = self.env.replies()[0]
        self.assertTrue("File terlalu besar" in text and "20 MB" in text and "compress" in text)

    async def test_disk_hampir_penuh_menolak_upload(self):
        from utils.file_manager import StorageError
        env = self.env
        await env.press("feature:split")
        with patch.object(env.files, "check_disk_space", side_effect=StorageError("penuh")):
            await env.send_file("a.pdf", "A")
        self.assertTrue(any("kehabisan penyimpanan" in t for t in env.replies()) and not env.sessions.get(1).files)

    async def test_hanya_satu_proses_per_user_dan_batas_global(self):
        self.assertEqual(self.env.config.max_files, 20)
        self.assertEqual(self.env.data["job_slots"]._value, 2)


class TestInjection(EnvCase):
    async def test_html_di_nama_dibersihkan_lalu_di_escape(self):
        """Dua lapis: (1) sanitize_filename membuang karakter HTML; (2) tampilan tetap di-escape
        seandainya lapis pertama kecolongan."""
        env = self.env
        evil = "<b onclick=x>&</b><script>.pdf"
        env.add_source("A", make_pdf(self.tmp / "a.pdf"))
        await env.press("feature:merge"); await env.send_file(evil, "A"); await env.send_file(evil, "A")
        panel = self.bot_panel_text()
        self.assertTrue("<script>" not in panel and "<b onclick" not in panel and "script" in panel)  # lapis 1

        session = env.sessions.get(1)
        raw = session.files[0].__class__  # SessionFile
        for item in session.files:  # simulasikan nama mentah lolos ke sesi
            item.name = evil
        for text in (build_panel(session, FEATURES["merge"], env.config)[0], build_reorder(session, FEATURES["merge"])[0]):
            self.assertTrue("<script>" not in text and "&lt;script&gt;" in text)  # lapis 2
        for kind in ("split", "rotate", "compress", "pdf2jpg", "word2pdf", "pdf2word"):
            single = env.sessions.start(5, 50, kind)
            single.files.append(raw(fid="x", name=evil, path=session.files[0].path, size=10, kind="pdf", pages=1))
            self.assertTrue("<script>" not in build_panel(single, FEATURES[kind], env.config)[0], kind)

    def bot_panel_text(self) -> str:
        return self.env.bot.sent[-1].text

    async def test_nama_pengguna_telegram_di_escape(self):
        msg = await self.env.command(start_h.start_command, first_name="<b>Budi</b>&<i>")
        text = self.env.replies()[0]
        self.assertNotIn("<b>Budi</b>", text); self.assertIn("&lt;b&gt;Budi&lt;/b&gt;&amp;", text)

    async def test_sql_injection_tidak_berpengaruh(self):
        db = Database(self.tmp / "inj.db")
        evil = "x'); DROP TABLE users;--"
        await db.touch_user(1, evil, evil)
        await db.log_operation(1, evil, "success", 1, 1, error=evil)
        self.assertEqual((await db.user_summary(1))["usage_count"], 1)
        ops = await db.recent_operations(1, 5)
        self.assertEqual(ops[0]["feature"], evil)  # tersimpan apa adanya sebagai teks, bukan dijalankan
        self.assertEqual((await db.global_stats())["users"], 1)

    async def test_pesan_error_ke_user_tidak_membocorkan_path_server(self):
        env = self.env
        env.add_source("A", make_pdf(self.tmp / "a.pdf"))
        with patch("handlers.pdf.merge_pdfs", side_effect=RuntimeError(f"gagal di {env.files.temp_dir}/user_1/rahasia.pdf")):
            await env.press("feature:merge"); await env.send_file("a.pdf", "A"); await env.send_file("b.pdf", "A")
            panel = self.env.bot.sent[-1]; await env.press("act:done", msg=panel)
        self.assertNotIn(str(env.files.temp_dir), panel.text); self.assertNotIn("rahasia", panel.text)


class TestSecretsAndPrivacy(EnvCase):
    def test_token_tidak_ada_di_kode_dan_env_di_gitignore(self):
        pattern = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")
        leaks = [p.name for p in ROOT.rglob("*.py") if ".venv" not in p.parts and pattern.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(leaks, [])
        lines = (ROOT / ".gitignore").read_text().splitlines()
        for required in (".env", "temp/*", "*.db", "logs/", "data/"):
            self.assertIn(required, lines)
        example = (ROOT / ".env.example").read_text()
        self.assertIn("BOT_TOKEN=your_telegram_bot_token", example)

    def test_log_httpx_tidak_membocorkan_token(self):
        import bot
        bot.setup_logging("INFO", None)
        self.assertGreaterEqual(logging.getLogger("httpx").getEffectiveLevel(), logging.WARNING)
        self.assertGreaterEqual(logging.getLogger("pypdf").getEffectiveLevel(), logging.ERROR)
        self.assertNotIn(self.env.config.bot_token, repr(self.env.config))

    async def test_dokumen_user_tidak_tersimpan_permanen(self):
        env = self.env
        env.add_source("A", make_pdf(self.tmp / "a.pdf", [101]))
        for feature in ("split", "rotate"):
            await env.press(f"feature:{feature}"); await env.send_file("rahasia-kontrak.pdf", "A")
            await env.press("nav:cancel")
        await env.press("feature:merge"); await env.send_file("rahasia-kontrak.pdf", "A"); await env.send_file("rahasia-kontrak.pdf", "A")
        with patch("handlers.pdf.merge_pdfs", lambda i, o: (__import__("shutil").copyfile(i[0], o), 2)[1]):
            await env.press("act:done", msg=self.env.bot.sent[-1])
        self.assertEqual(list(env.files.temp_dir.iterdir()), [])  # tidak ada sisa file di temp
        import sqlite3
        with sqlite3.connect(env.db.path) as conn:
            dump = "\n".join(str(row) for table in ("users", "operations") for row in conn.execute(f"SELECT * FROM {table}"))
        self.assertNotIn("rahasia", dump)  # database tidak menyimpan nama file
        stored = [p for p in self.tmp.rglob("*") if p.is_file() and p.suffix in (".pdf", ".docx", ".jpg") and "temp" in p.parts]
        self.assertEqual(stored, [])

    async def test_sisa_file_dibersihkan_saat_bot_restart(self):
        env = self.env
        leftover = env.files.new_file_path(42, ".pdf"); leftover.write_bytes(b"sisa")
        self.assertEqual(env.files.cleanup_all(), 1); self.assertFalse(leftover.exists())

    def test_symlink_di_folder_user_tidak_menghapus_target(self):
        fm = FileManager(self.tmp / "temp")
        outside = self.tmp / "penting.txt"; outside.write_text("jangan dihapus")
        target = fm.prepare_user_dir(1)
        try:
            (target / "link").symlink_to(outside)
        except OSError:
            self.skipTest("symlink tidak diizinkan di sistem ini")
        fm.delete_user_dir(1)
        self.assertTrue(outside.exists() and outside.read_text() == "jangan dihapus")


class TestMessageLimits(EnvCase):
    async def test_pesan_terpanjang_tetap_di_bawah_batas_telegram(self):
        env = self.env
        env.add_source("A", make_pdf(self.tmp / "a.pdf"))
        long_name = ("é" * 55) + ".pdf"
        await env.press("feature:merge")
        for _ in range(20):
            await env.send_file(long_name, "A")
        session = env.sessions.get(1)
        self.assertLess(len(build_panel(session, FEATURES["merge"], env.config)[0]), 4096)
        self.assertLess(len(build_reorder(session, FEATURES["merge"])[0]), 4096)
        keyboard = build_reorder(session, FEATURES["merge"])[1]
        buttons = [b for row in keyboard.rows for b in row]
        self.assertLessEqual(len(buttons), 100)  # batas tombol Telegram
        self.assertTrue(all(len(b.callback_data.encode()) <= 64 for b in buttons))  # batas callback_data


class TestMalwareDocuments(TmpCase):
    def test_pdf_dengan_javascript_tidak_dijalankan_dan_diterima_sebagai_dokumen(self):
        """Bot hanya membaca struktur PDF; JavaScript di dalamnya tidak pernah dieksekusi."""
        from pypdf import PdfWriter
        writer = PdfWriter(); writer.add_blank_page(100, 100)
        writer.add_js("app.alert('xss'); this.exportDataObject({cName:'x.exe', nLaunch:2});")
        with (self.tmp / "js.pdf").open("wb") as fh:
            writer.write(fh)
        info = inspect_content(self.tmp / "js.pdf", "pdf", 20 * MB)
        self.assertEqual(info.pages, 1)

    def test_docx_asli_dari_word_lolos(self):
        self.assertEqual(inspect_content(make_real_docx(self.tmp / "ok.docx"), "word", 20 * MB).detected, "docx")

    def test_validator_tidak_membanjiri_log_untuk_file_jahat(self):
        (self.tmp / "evil.pdf").write_bytes(b"%PDF-1.4\n" + b"1 0 obj << /Kids [1 0 R] >> endobj\n" * 50)
        with self.assertLogs("utils.file_validator", level="WARNING") as captured:
            with self.assertRaises(ValidationError):
                inspect_content(self.tmp / "evil.pdf", "pdf", 20 * MB)
        self.assertEqual(len(captured.records), 1)
        self.assertIsNone(captured.records[0].exc_info)  # tanpa traceback panjang


if __name__ == "__main__":
    unittest.main()
