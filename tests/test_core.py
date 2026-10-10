"""Komponen inti: FileManager, SessionManager, Database, progress bar, pembatalan, konfigurasi."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import threading
import time
import unittest

import config as cfg
from database.database import Database
from handlers.progress import ProgressTracker, render_bar, render_progress
from services import job_context
from services.job_context import JobCancelled, checkpoint
from services.process_utils import run_command
from tests.fakes import TmpCase
from utils.file_manager import FileManager, sanitize_filename
from utils.session_manager import SessionBusy, SessionExpired, SessionFile, SessionManager


class TestFileManager(TmpCase):
    def test_folder_terpisah_nama_acak_dan_hapus(self):
        fm = FileManager(self.tmp / "temp")
        p1, p2 = fm.new_file_path(111, ".pdf"), fm.new_file_path(222, ".pdf")
        self.assertEqual((p1.parent.name, p2.parent.name), ("user_111", "user_222"))
        self.assertNotEqual(p1.stem, p2.stem); self.assertEqual(len(p1.stem), 32)
        p1.write_bytes(b"x"); p2.write_bytes(b"y")
        fm.delete_user_dir(111)
        self.assertFalse(p1.parent.exists()); self.assertTrue(p2.exists())
        self.assertEqual(fm.cleanup_all(), 1)

    def test_ekstensi_dan_user_id_berbahaya_ditolak(self):
        fm = FileManager(self.tmp / "temp")
        for ext in ("../../x.sh", ".pdf/../..", "pdf", ".p d f", ".toolongext"):
            with self.subTest(ext=ext), self.assertRaises(ValueError):
                fm.new_file_path(1, ext)
        for uid in ("1/../../etc", "../x", "abc"):
            with self.subTest(uid=uid), self.assertRaises(ValueError):
                fm.user_dir(uid)

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename("..\\..\\evil<>.pdf"), "evil__.pdf")
        self.assertEqual((sanitize_filename(""), sanitize_filename(None), sanitize_filename("...")), ("file",) * 3)
        long = sanitize_filename("a" * 200 + ".pdf")
        self.assertTrue(len(long) <= 60 and long.endswith(".pdf"))
        self.assertEqual(sanitize_filename("laporan\u202efdp.exe"), "laporanfdp.exe")  # karakter pembalik arah dibuang
        for reserved in ("CON.pdf", "nul.pdf", "COM1.docx", "lpt9.pdf"):
            self.assertTrue(sanitize_filename(reserved).startswith("_"), reserved)
        self.assertEqual(sanitize_filename("Tugas Akhir (revisi).docx"), "Tugas Akhir (revisi).docx")

    def test_cleanup_orphans(self):
        fm = FileManager(self.tmp / "temp")
        for uid in (1, 2, 3):
            fm.prepare_user_dir(uid); (fm.user_dir(uid) / "x.pdf").write_bytes(b"x")
        old = time.time() - 3600
        for uid in (1, 2):
            os.utime(fm.user_dir(uid), (old, old))
        self.assertEqual(fm.cleanup_orphans({1}, older_than=600), 1)
        self.assertTrue(fm.user_dir(1).exists() and fm.user_dir(3).exists() and not fm.user_dir(2).exists())
        self.assertGreater(fm.free_bytes(), 0)


class TestSessionManager(TmpCase):
    def make(self, timeout=1):
        self.fm = FileManager(self.tmp / "temp")
        return SessionManager(self.fm, timeout)

    def test_isolasi_urutan_dan_hapus_file(self):
        sm = self.make(600)
        a, b = sm.start(1, 10, "merge"), sm.start(2, 20, "merge")
        for s, n in ((a, 3), (b, 2)):
            for i in range(n):
                path = self.fm.new_file_path(s.user_id, ".pdf"); path.write_bytes(b"x")
                s.files.append(SessionFile(fid=f"{s.user_id}{i}", name=f"f{i}.pdf", path=path, size=1, kind="pdf"))
        self.assertEqual((len(a.files), len(b.files)), (3, 2))
        a.move("11", +1)
        self.assertEqual([f.fid for f in a.files], ["10", "12", "11"])
        gone = a.find("12").path
        sm.remove_file(a, "12")
        self.assertFalse(gone.exists())
        sm.end(1)
        self.assertFalse(self.fm.user_dir(1).exists()); self.assertTrue(self.fm.user_dir(2).exists())

    def test_kedaluwarsa(self):
        sm = self.make(1)
        s = sm.start(1, 10, "merge"); s.last_activity -= 5
        self.assertEqual([x.user_id for x in sm.expired_sessions()], [1])
        with self.assertRaises(SessionExpired):
            sm.get(1)
        self.assertFalse(self.fm.user_dir(1).exists())

    def test_sesi_sibuk_tidak_kedaluwarsa_dan_tidak_bisa_diganti(self):
        sm = self.make(1)
        s = sm.start(1, 10, "merge"); s.busy = True; s.last_activity -= 5
        self.assertEqual(sm.expired_sessions(), [])
        self.assertIs(sm.get(1), s)
        with self.assertRaises(SessionBusy):
            sm.start(1, 10, "split")
        self.assertEqual(sm.busy_count, 1)
        s.busy = False; s.last_activity -= 5
        self.assertEqual(len(sm.expired_sessions()), 1)


class TestUserLock(unittest.IsolatedAsyncioTestCase):
    async def test_urutan_user_sama_terjaga_user_lain_tidak_menunggu(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sm = SessionManager(FileManager(tmp), 600)
            order: list[str] = []

            async def handler(user, name, delay):
                async with sm.user_lock(user):
                    await asyncio.sleep(delay); order.append(name)

            await asyncio.gather(handler(1, "a1", 0.15), handler(1, "a2", 0.01), handler(1, "a3", 0.01), handler(2, "b1", 0.02))
            self.assertEqual([n for n in order if n.startswith("a")], ["a1", "a2", "a3"])
            self.assertEqual(order[0], "b1")
            sm.prune_locks()
            self.assertEqual(sm._locks, {})


class TestDatabase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile
        from pathlib import Path
        self._tmp = tempfile.TemporaryDirectory(); self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.path = self.tmp / "sub" / "dir" / "bot.db"
        self.db = Database(self.path)

    async def test_riwayat_dan_pemakaian(self):
        db = self.db
        self.assertTrue(self.path.exists())
        await db.touch_user(1, "budi", "Budi")
        await db.log_operation(1, "merge", "success", 3, 3_000_000, 2_500_000, 1800)
        await db.log_operation(1, "compress", "failed", 1, 500_000, None, 900, "PDF dilindungi password.")
        await db.log_operation(1, "split", "cancelled", 1, 100)
        await db.log_operation(1, "rotate", "success", 1, 200, 200, 50)
        self.assertEqual((await db.user_summary(1))["usage_count"], 2)
        ops = await db.recent_operations(1, 3)
        self.assertEqual([o["feature"] for o in ops], ["rotate", "split", "compress"])
        self.assertEqual((ops[2]["input_bytes"], ops[2]["duration_ms"]), (500_000, 900))
        await db.log_operation(99, "merge", "success", 1, 10)
        self.assertEqual((await db.user_summary(99))["usage_count"], 1)
        stats = await db.global_stats()
        self.assertEqual((stats["users"], stats["operations"], stats["success"]), (2, 5, 3))
        self.assertEqual(stats["top_features"][0], ("merge", 2))
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE operations SET created_at = '2020-01-01 00:00:00' WHERE feature IN ('split', 'compress')")
        self.assertEqual(await db.purge_older_than(90), 2)

    async def test_touch_user_dibatasi_dan_memperbarui_nama(self):
        await self.db.touch_user(1, "budi", "Budi")
        await self.db.touch_user(1, "budi_baru", "Budi")
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT username FROM users WHERE user_id = 1").fetchone()[0], "budi_baru")
        before = self.db._seen[1][1]
        await self.db.touch_user(1, "budi_baru", "Budi")
        self.assertEqual(self.db._seen[1][1], before)

    async def test_ketahanan(self):
        bad = self.tmp / "rusak.db"; bad.write_bytes(b"ini bukan database sqlite" * 50)
        db2 = Database(bad)
        await db2.log_operation(5, "merge", "success")
        self.assertTrue(any(p.name.startswith("rusak.corrupt-") for p in self.tmp.iterdir()))
        self.assertEqual((await db2.user_summary(5))["usage_count"], 1)
        db3 = Database(self.tmp / "hilang.db"); db3.path.unlink()
        self.assertEqual(await db3.user_summary(1), {"usage_count": 0, "first_seen": None})  # error db tidak melempar exception


class TestProgressAndCancel(unittest.IsolatedAsyncioTestCase):
    def test_render(self):
        self.assertEqual(render_bar(0.6), "██████░░░░")
        self.assertEqual((render_bar(-1), render_bar(7)), ("░" * 10, "█" * 10))
        self.assertIn("[██████░░░░] 60%", render_progress(0.6, 3))
        self.assertIn("(10 detik)", render_progress(None, 12.7)); self.assertNotIn("detik", render_progress(None, 2))

    async def test_tracker_dari_thread(self):
        edits: list[str] = []

        async def fake_edit(text): edits.append(text)

        tracker = ProgressTracker(fake_edit, interval=0.05); tracker.start()
        worker = threading.Thread(target=lambda: [tracker.update(0.3), time.sleep(0.2), tracker.update(0.9)])
        worker.start(); await asyncio.sleep(0.45); worker.join(); await tracker.stop()
        self.assertTrue(any("30%" in t for t in edits) and any("90%" in t for t in edits))
        self.assertEqual(len(edits), len(set(edits)))
        count = len(edits); await asyncio.sleep(0.15)
        self.assertEqual(len(edits), count)
        quick = ProgressTracker(fake_edit, interval=5); quick.start(); await quick.stop()
        self.assertEqual(len(edits), count)  # proses cepat: tanpa progress bar

    def test_checkpoint_dan_pembatalan(self):
        seen: list[float] = []; event = threading.Event()
        tokens = (job_context.progress_callback.set(seen.append), job_context.cancel_event.set(event))
        try:
            checkpoint(1, 4, 0.2, 0.8); checkpoint(4, 4, 0.2, 0.8)
            self.assertEqual([round(x, 2) for x in seen], [0.35, 0.8])
            event.set()
            with self.assertRaises(JobCancelled):
                checkpoint(1, 2)
            event.clear(); threading.Timer(0.6, event.set).start()
            started = time.monotonic()
            with self.assertRaises(JobCancelled):
                run_command([sys.executable, "-c", "import time; time.sleep(60)"], timeout=30)
            self.assertLess(time.monotonic() - started, 6)  # program eksternal dihentikan saat dibatalkan
        finally:
            job_context.progress_callback.reset(tokens[0]); job_context.cancel_event.reset(tokens[1])
        checkpoint(1, 2)  # di luar job: tidak berbuat apa-apa

    def test_timeout_menghentikan_proses(self):
        from services.process_utils import ProcessTimeout
        started = time.monotonic()
        with self.assertRaises(ProcessTimeout):
            run_command([sys.executable, "-c", "import time; time.sleep(60)"], timeout=1)
        self.assertLess(time.monotonic() - started, 8)
        code, out, _ = run_command([sys.executable, "-c", "print('halo')"], timeout=20)
        self.assertEqual((code, out.strip()), (0, b"halo"))


class TestConfig(TmpCase):
    KEYS = ["BOT_TOKEN", "ADMIN_IDS", "DATABASE_PATH", "LOG_FILE", "MAX_CONCURRENT_JOBS", "PROCESS_TIMEOUT", "OCR_LANGUAGES"]

    def setUp(self):
        super().setUp()
        saved = {k: os.environ.get(k) for k in self.KEYS}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        for key in self.KEYS:
            os.environ.pop(key, None)
        os.environ.update(BOT_TOKEN="123:abc", DATABASE_PATH=str(self.tmp / "cfg" / "b.db"), LOG_FILE="off")

    def test_nilai_terbaca(self):
        os.environ.update(ADMIN_IDS="111, 222", MAX_CONCURRENT_JOBS="3", PROCESS_TIMEOUT="120")
        c = cfg.load_config()
        self.assertEqual((c.admin_ids, c.max_concurrent_jobs, c.process_timeout), ({111, 222}, 3, 120))
        self.assertTrue(c.log_file is None and c.database_path.parent.exists())

    def test_nilai_salah_ditolak(self):
        cases = {"BOT_TOKEN": "your_telegram_bot_token", "ADMIN_IDS": "abc", "PROCESS_TIMEOUT": "5",
                 "OCR_LANGUAGES": "ind; rm -rf /", "MAX_CONCURRENT_JOBS": "0"}
        for key, value in cases.items():
            with self.subTest(key=key):
                os.environ[key] = value
                with self.assertRaises(cfg.ConfigError):
                    cfg.load_config()
                os.environ.pop(key); os.environ["BOT_TOKEN"] = "123:abc"

    def test_token_tidak_ikut_tercetak(self):
        self.assertNotIn("123:abc", repr(cfg.load_config()))


if __name__ == "__main__":
    unittest.main()
