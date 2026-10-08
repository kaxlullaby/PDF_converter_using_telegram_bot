"""Smoke test Phase 6 - TIDAK butuh Telegram/internet.

Menguji database, progress bar, pembatalan, session sibuk, kunci per user,
pembersihan folder yatim, dan pembacaan konfigurasi.
Jalankan dari folder project:   python tests/smoke_phase6.py
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.database import Database  # noqa: E402
from handlers.progress import ProgressTracker, render_bar, render_progress  # noqa: E402
from services import job_context  # noqa: E402
from services.job_context import JobCancelled, checkpoint  # noqa: E402
from services.process_utils import run_command  # noqa: E402
from utils.file_manager import FileManager  # noqa: E402
from utils.session_manager import SessionBusy, SessionManager  # noqa: E402

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


async def part_db(tmp: Path) -> None:
    print("\n[1] Database")
    db = Database(tmp / "sub" / "dir" / "bot.db")
    check("folder database dibuat otomatis", (tmp / "sub" / "dir" / "bot.db").exists())
    await db.touch_user(1, "budi", "Budi")
    await db.log_operation(1, "merge", "success", 3, 3_000_000, 2_500_000, 1800)
    await db.log_operation(1, "compress", "failed", 1, 500_000, None, 900, "PDF dilindungi password.")
    await db.log_operation(1, "split", "cancelled", 1, 100)
    await db.log_operation(1, "rotate", "success", 1, 200, 200, 50)
    summary = await db.user_summary(1)
    check("usage_count hanya bertambah untuk proses berhasil", summary["usage_count"] == 2)
    ops = await db.recent_operations(1, 3)
    check("riwayat terbaru dulu, dibatasi limit", [o["feature"] for o in ops] == ["rotate", "split", "compress"])
    check("data ringkasan tersimpan (ukuran, durasi)", ops[2]["input_bytes"] == 500_000 and ops[2]["duration_ms"] == 900)

    await db.log_operation(99, "merge", "success", 1, 10)  # user belum pernah touch
    check("operasi user baru otomatis membuat baris user", (await db.user_summary(99))["usage_count"] == 1)

    stats = await db.global_stats()
    check("statistik global benar", stats["users"] == 2 and stats["operations"] == 5 and stats["success"] == 3 and stats["last_24h"] == 5)
    check("fitur terpopuler terurut", stats["top_features"][0] == ("merge", 2))

    with sqlite3.connect(tmp / "sub" / "dir" / "bot.db") as conn:
        conn.execute("UPDATE operations SET created_at = '2020-01-01 00:00:00' WHERE feature IN ('split', 'compress')")
    check("riwayat lama dihapus (purge)", await db.purge_older_than(90) == 2 and len(await db.recent_operations(1, 10)) == 2)

    await db.touch_user(1, "budi_baru", "Budi")
    with sqlite3.connect(tmp / "sub" / "dir" / "bot.db") as conn:
        name = conn.execute("SELECT username FROM users WHERE user_id = 1").fetchone()[0]
    check("username berubah -> diperbarui", name == "budi_baru")
    before = db._seen[1][1]
    await db.touch_user(1, "budi_baru", "Budi")
    check("sentuhan berulang < 1 menit tidak menulis ulang", db._seen[1][1] == before)

    bad = tmp / "rusak.db"; bad.write_bytes(b"ini bukan database sqlite" * 50)
    db2 = Database(bad)
    await db2.log_operation(5, "merge", "success")
    check("database rusak: dicadangkan & dibuat ulang, bot tidak crash", any(p.name.startswith("rusak.corrupt-") for p in tmp.iterdir()) and (await db2.user_summary(5))["usage_count"] == 1)

    db3 = Database(tmp / "kosong.db")
    db3.path.unlink()  # file hilang saat berjalan -> dibuat lagi oleh sqlite, tabel tidak ada
    result = await db3.user_summary(1)
    check("error database tidak melempar exception (nilai bawaan)", result == {"usage_count": 0, "first_seen": None})


async def part_progress() -> None:
    print("\n[2] Progress bar")
    check("bar 60%", render_bar(0.6) == "██████░░░░")
    check("bar di luar rentang dipotong", render_bar(-1) == "░" * 10 and render_bar(7) == "█" * 10)
    check("teks sesuai spesifikasi", "[██████░░░░] 60%" in render_progress(0.6, 3) and "Please wait" in render_progress(0.6, 3))
    check("tanpa fraksi: tampil detik per 5 detik", "(10 detik)" in render_progress(None, 12.7) and "detik" not in render_progress(None, 2))

    edits: list[str] = []

    async def fake_edit(text: str) -> None:
        edits.append(text)

    tracker = ProgressTracker(fake_edit, interval=0.05)
    tracker.start()
    worker = threading.Thread(target=lambda: [tracker.update(0.3), time.sleep(0.2), tracker.update(0.9)])
    worker.start(); await asyncio.sleep(0.45); worker.join()
    await tracker.stop()
    check("progress dari thread muncul di pesan, naik", any("30%" in t for t in edits) and any("90%" in t for t in edits))
    check("teks yang sama tidak dikirim berulang", len(edits) == len(set(edits)))
    count = len(edits); await asyncio.sleep(0.15)
    check("stop() menghentikan pembaruan", len(edits) == count)
    fast = ProgressTracker(fake_edit, interval=5); fast.start(); await fast.stop()
    check("proses cepat: tidak ada progress bar sama sekali", len(edits) == count)


def part_cancel() -> None:
    print("\n[3] Pembatalan & konteks job")
    seen: list[float] = []
    ev = threading.Event()
    job_context.progress_callback.set(seen.append)
    job_context.cancel_event.set(ev)
    checkpoint(1, 4, 0.2, 0.8); checkpoint(4, 4, 0.2, 0.8)
    check("checkpoint memetakan progress ke rentang tahap", [round(x, 2) for x in seen] == [0.35, 0.8])
    ev.set()
    check("checkpoint melempar JobCancelled jika dibatalkan", raises(JobCancelled, lambda: checkpoint(1, 2)))

    ev.clear()
    threading.Timer(0.6, ev.set).start()
    started = time.monotonic()
    cancelled = raises(JobCancelled, lambda: run_command([sys.executable, "-c", "import time; time.sleep(60)"], timeout=30))
    check("program eksternal dihentikan saat user membatalkan (<6 dtk)", cancelled and time.monotonic() - started < 6)
    job_context.cancel_event.set(None); job_context.progress_callback.set(None)
    checkpoint(1, 2)  # tanpa konteks: tidak berbuat apa-apa
    check("di luar job, checkpoint aman dipanggil", True)


async def part_sessions(tmp: Path) -> None:
    print("\n[4] Session sibuk & kunci per user")
    fm = FileManager(tmp / "temp"); sm = SessionManager(fm, timeout=1)
    s = sm.start(1, 10, "merge"); s.busy = True
    time.sleep(1.2)
    check("sesi yang sedang diproses tidak kedaluwarsa", sm.expired_sessions() == [] and sm.get(1) is s)
    check("tidak boleh memulai sesi baru saat sibuk", raises(SessionBusy, lambda: sm.start(1, 10, "split")))
    check("peek tidak mengubah aktivitas", sm.peek(1) is s and sm.busy_count == 1)
    s.busy = False; s.last_activity -= 5
    check("setelah selesai, sesi bisa kedaluwarsa lagi", [x.user_id for x in sm.expired_sessions()] == [1])
    sm.end(1)

    order: list[str] = []

    async def handler(user: int, name: str, delay: float) -> None:
        async with sm.user_lock(user):
            await asyncio.sleep(delay)
            order.append(name)

    await asyncio.gather(handler(1, "a1", 0.15), handler(1, "a2", 0.01), handler(1, "a3", 0.01), handler(2, "b1", 0.02))
    check("pesan user yang sama diproses berurutan", [x for x in order if x.startswith("a")] == ["a1", "a2", "a3"])
    check("user berbeda tidak menunggu user lain", order[0] == "b1")
    sm.prune_locks()
    check("kunci yang tidak dipakai dibersihkan", sm._locks == {})


def part_cleanup(tmp: Path) -> None:
    print("\n[5] Pembersihan folder yatim")
    fm = FileManager(tmp / "temp2")
    for uid in (1, 2, 3):
        fm.prepare_user_dir(uid); (fm.user_dir(uid) / "x.pdf").write_bytes(b"x")
    old = time.time() - 3600
    for uid in (1, 2):
        os.utime(fm.user_dir(uid), (old, old))
    removed = fm.cleanup_orphans(active_user_ids={1}, older_than=600)
    check("hanya yatim & lama yang dihapus (aktif & baru aman)", removed == 1 and fm.user_dir(1).exists() and not fm.user_dir(2).exists() and fm.user_dir(3).exists())
    check("free_bytes terbaca", fm.free_bytes() > 0)


def part_config(tmp: Path) -> None:
    print("\n[6] Konfigurasi")
    import types
    try:
        import dotenv  # noqa: F401
    except ImportError:
        sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda *a, **k: None)
    import config as cfg

    keys = ["BOT_TOKEN", "ADMIN_IDS", "DATABASE_PATH", "LOG_FILE", "MAX_CONCURRENT_JOBS", "PROCESS_TIMEOUT", "OCR_LANGUAGES"]
    saved = {k: os.environ.get(k) for k in keys}
    try:
        os.environ.update(BOT_TOKEN="123:abc", DATABASE_PATH=str(tmp / "cfg" / "b.db"), LOG_FILE="off",
                          ADMIN_IDS="111, 222", MAX_CONCURRENT_JOBS="3", PROCESS_TIMEOUT="120")
        c = cfg.load_config()
        check("ADMIN_IDS, batas job & timeout terbaca", c.admin_ids == {111, 222} and c.max_concurrent_jobs == 3 and c.process_timeout == 120)
        check("LOG_FILE=off mematikan file log", c.log_file is None and c.database_path.parent.exists())
        os.environ["ADMIN_IDS"] = "abc"
        check("ADMIN_IDS salah -> pesan error jelas", raises(cfg.ConfigError, cfg.load_config))
        os.environ["ADMIN_IDS"] = ""; os.environ["PROCESS_TIMEOUT"] = "5"
        check("PROCESS_TIMEOUT terlalu kecil ditolak", raises(cfg.ConfigError, cfg.load_config))
        os.environ["PROCESS_TIMEOUT"] = "300"; os.environ["OCR_LANGUAGES"] = "ind; rm -rf /"
        check("OCR_LANGUAGES berbahaya ditolak", raises(cfg.ConfigError, cfg.load_config))
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


async def amain() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="docbot_p6_"))
    await part_db(tmp)
    await part_progress()
    part_cancel()
    await part_sessions(tmp)
    part_cleanup(tmp)
    part_config(tmp)
    failed = [name for ok, name in results if not ok]
    print(f"\nHasil: {len(results) - len(failed)}/{len(results)} lulus")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
