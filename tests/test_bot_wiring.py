"""Perakitan bot.py: handler terdaftar, tugas rutin, shutdown, penanganan error global."""
from __future__ import annotations

import asyncio
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import bot
import config as cfg
from database.database import Database
import logging
from unittest.mock import MagicMock, patch

from tests.fakes import Conflict, InvalidToken, NetworkError, TmpCase
from utils.file_manager import FileManager
from utils.session_manager import SessionManager


class TestBotWiring(TmpCase):
    def setUp(self):
        super().setUp()
        saved = {k: os.environ.get(k) for k in ("BOT_TOKEN", "DATABASE_PATH", "LOG_FILE")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        os.environ.update(BOT_TOKEN="1:x", DATABASE_PATH=str(self.tmp / "b.db"), LOG_FILE=str(self.tmp / "logs" / "bot.log"))
        self.config = cfg.load_config()

    def test_aplikasi_dirakit_lengkap(self):
        app = bot.build_application(self.config, frozenset({"word2pdf"}))
        self.assertEqual(app.add_handler.call_count, 10)  # 5 perintah + tombol + dokumen + foto + lampiran lain + teks
        app.add_error_handler.assert_called_once()
        self.assertTrue((self.tmp / "b.db").exists())

    def test_logging_ke_file(self):
        bot.setup_logging("INFO", self.config.log_file)
        import logging
        logging.getLogger("tes").warning("halo")
        self.assertTrue(self.config.log_file.exists())


class TestStartupErrors(TmpCase):
    """Internet putus / token salah saat start: tidak boleh ada traceback panjang, dan bot menunggu internet."""

    def setUp(self):
        super().setUp()
        saved = {k: os.environ.get(k) for k in ("BOT_TOKEN", "DATABASE_PATH", "LOG_FILE")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        os.environ.update(BOT_TOKEN="1:x", DATABASE_PATH=str(self.tmp / "b.db"), LOG_FILE="off")

    def run_main(self, error=None):
        app = MagicMock()
        if error:
            app.run_polling.side_effect = error
        with patch.object(bot, "build_application", return_value=app), patch.object(bot, "compute_unavailable", return_value=frozenset()), \
                patch.object(bot, "setup_logging"):
            try:
                bot.main()
                exit_message = None
            except SystemExit as exc:
                exit_message = str(exc)
        return app, exit_message

    def test_menunggu_internet_dengan_percobaan_tanpa_batas(self):
        app, _ = self.run_main()
        self.assertEqual(app.run_polling.call_args.kwargs["bootstrap_retries"], -1)

    def test_jaringan_gagal_pesan_ramah(self):
        _, message = self.run_main(NetworkError("httpx.ConnectError: [Errno 11001] getaddrinfo failed"))
        self.assertTrue("Tidak bisa terhubung" in message and "doctor.py" in message and "11001" in message)
        self.assertNotIn("Traceback", message)

    def test_token_salah_pesan_ramah(self):
        _, message = self.run_main(InvalidToken())
        self.assertTrue("BOT_TOKEN" in message and "BotFather" in message)

    def test_log_network_retry_diringkas_tanpa_traceback(self):
        try:
            raise NetworkError("httpx.ConnectError: [Errno 11001] getaddrinfo failed")
        except NetworkError as exc:
            record = logging.LogRecord("telegram.ext", logging.ERROR, __file__, 1, "Network Retry Loop (Bootstrap): Failed run number 0 of 0. Aborting.", (), (type(exc), exc, exc.__traceback__))
        bot.ConciseNetworkErrors().filter(record)
        self.assertIsNone(record.exc_info)
        self.assertIn("Penyebab: httpx.ConnectError", record.getMessage())
        self.assertNotIn("Traceback", logging.Formatter().format(record))

    def test_error_lain_tidak_disentuh(self):
        try:
            raise ValueError("bug")
        except ValueError as exc:
            record = logging.LogRecord("x", logging.ERROR, __file__, 1, "Unhandled exception", (), (type(exc), exc, exc.__traceback__))
        bot.ConciseNetworkErrors().filter(record)
        self.assertIsNotNone(record.exc_info)  # traceback bug sungguhan tetap lengkap


class TestMaintenance(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        os.environ.setdefault("BOT_TOKEN", "1:x")
        self.config = cfg.Config(bot_token="1:x", max_file_size=1, max_files=20, max_session_size=1, session_timeout=1,
                                 temp_dir=tmp / "temp", log_level="INFO")
        self.fm = FileManager(self.config.temp_dir); self.sm = SessionManager(self.fm, 1)
        self.app = types.SimpleNamespace(bot=AsyncMock(), bot_data={"config": self.config, "files": self.fm,
                                                                    "sessions": self.sm, "db": Database(tmp / "d.db")})

    async def test_post_init_sesi_kedaluwarsa_yatim_dan_shutdown(self):
        await bot.post_init(self.app)
        self.assertIsInstance(self.app.bot_data["job_slots"], asyncio.Semaphore)
        self.app.bot.set_my_commands.assert_awaited_once()

        session = self.sm.start(7, 70, "merge"); session.last_activity -= 10
        await bot._expire_sessions(self.app)
        self.assertTrue(self.sm.peek(7) is None and self.app.bot.send_message.await_count == 1)

        self.fm.prepare_user_dir(8); os.utime(self.fm.user_dir(8), (1, 1))
        await bot._housekeeping(self.app)
        self.assertFalse(self.fm.user_dir(8).exists())

        self.sm.start(9, 90, "merge").busy = True
        await bot.post_shutdown(self.app)
        self.assertTrue(self.sm.active_count == 0 and not self.fm.user_dir(9).exists())

    async def test_error_handler_global(self):
        ctx = types.SimpleNamespace(error=Conflict(), bot=AsyncMock(), application=self.app)
        await bot.error_handler(object(), ctx); ctx.bot.send_message.assert_not_awaited()  # tidak spam user
        ctx = types.SimpleNamespace(error=NetworkError("putus"), bot=AsyncMock(), application=self.app)
        await bot.error_handler(object(), ctx); ctx.bot.send_message.assert_not_awaited()  # cukup dicatat

        import dataclasses
        self.app.bot_data["config"] = dataclasses.replace(self.config, admin_ids=frozenset({999}))
        update = bot.Update(); update.effective_chat = types.SimpleNamespace(id=5)
        ctx = types.SimpleNamespace(error=ValueError("bug"), bot=AsyncMock(), application=self.app)
        await bot.error_handler(update, ctx)
        targets = [call.kwargs["chat_id"] for call in ctx.bot.send_message.await_args_list]
        self.assertEqual(sorted(targets), [5, 999])  # user dapat pesan umum, admin dapat detail


if __name__ == "__main__":
    unittest.main()
