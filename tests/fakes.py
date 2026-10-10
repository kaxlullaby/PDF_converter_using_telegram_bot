"""Pustaka Telegram tiruan + objek palsu (pesan, tombol, bot) untuk menguji handler tanpa jaringan."""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

MB = 1024 * 1024


# ---------------------------------------------------------------------------
# Pustaka telegram tiruan (selalu dipakai, apa pun yang terpasang di komputer)
# ---------------------------------------------------------------------------
class TelegramError(Exception):
    pass


class NetworkError(TelegramError):
    pass


class BadRequest(NetworkError):
    pass


class TimedOut(NetworkError):
    pass


class Conflict(TelegramError):
    pass



class RetryAfter(TelegramError):
    def __init__(self, retry_after):
        super().__init__("flood")
        self.retry_after = retry_after


class InlineKeyboardButton:
    def __init__(self, text, callback_data=None):
        self.text, self.callback_data = text, callback_data


class InlineKeyboardMarkup:
    def __init__(self, rows):
        self.rows = rows
        self.inline_keyboard = rows


def install_stubs() -> None:
    def mod(name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module

    mod("telegram", InlineKeyboardButton=InlineKeyboardButton, InlineKeyboardMarkup=InlineKeyboardMarkup,
        Update=type("Update", (), {}), Message=object, BotCommand=lambda *a: a)
    mod("telegram.constants", ParseMode=types.SimpleNamespace(HTML="HTML"))
    mod("telegram.error", TelegramError=TelegramError, NetworkError=NetworkError, BadRequest=BadRequest,
        TimedOut=TimedOut, RetryAfter=RetryAfter, Conflict=Conflict)
    mod("telegram.ext", Application=MagicMock(), CallbackQueryHandler=MagicMock(), CommandHandler=MagicMock(),
        ContextTypes=types.SimpleNamespace(DEFAULT_TYPE=None), MessageHandler=MagicMock(), filters=MagicMock())
    mod("dotenv", load_dotenv=lambda *a, **k: None)  # tes tidak boleh membaca .env asli Anda


def button_texts(markup) -> list[str]:
    return [b.text for row in markup.rows for b in row]


def button_data(markup) -> list[str]:
    return [b.callback_data for row in markup.rows for b in row]


# ---------------------------------------------------------------------------
# Objek palsu
# ---------------------------------------------------------------------------
class FakeMsg:
    _counter = 1000

    def __init__(self, bot, text=None):
        FakeMsg._counter += 1
        self.bot, self.message_id, self.text, self.markup = bot, FakeMsg._counter, text, None
        self.document = None

    async def reply_text(self, text, reply_markup=None, parse_mode=None):
        msg = FakeMsg(self.bot, text)
        msg.markup = reply_markup
        self.bot.outbox.append(("reply", text))
        return msg

    async def edit_text(self, text, reply_markup=None, parse_mode=None):
        self.text, self.markup = text, reply_markup
        self.bot.outbox.append(("edit", text))


class FakeFile:
    def __init__(self, bot, source, file_id):
        self.bot, self.source, self.file_id = bot, source, file_id

    async def download_to_drive(self, custom_path=None):
        await asyncio.sleep(self.bot.delays.get(self.file_id, 0))
        shutil.copy(self.source, custom_path)
        return custom_path


class FakeBot:
    def __init__(self):
        self.outbox, self.docs, self.sources, self.sent = [], [], {}, []
        self.messages, self.delays, self.fail_next = [], {}, []

    async def get_file(self, file_id):
        return FakeFile(self, self.sources[file_id], file_id)

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        msg = FakeMsg(self, text)
        msg.markup = reply_markup
        self.sent.append(msg)
        self.messages.append((chat_id, text))
        self.outbox.append(("send", text))
        return msg

    async def delete_message(self, chat_id, message_id):
        self.outbox.append(("delete", message_id))

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.outbox.append(("rm_markup", message_id))

    async def send_document(self, chat_id, document, filename=None, caption=None, **kwargs):
        if self.fail_next:
            raise self.fail_next.pop(0)
        self.docs.append((filename, caption, document.read()))

    async def set_my_commands(self, commands):
        return True


class FakeQuery:
    def __init__(self, bot, data, message):
        self.bot, self.data, self.message, self.answered = bot, data, message, []

    async def edit_message_text(self, text, reply_markup=None, parse_mode=None):
        self.message.text, self.message.markup = text, reply_markup
        self.bot.outbox.append(("qedit", text))

    async def answer(self, text=None, show_alert=False):
        self.answered.append((text, show_alert))


class FakeUpdate:
    def __init__(self, user_id=1, message=None, query=None, first_name="Budi"):
        self.effective_user = types.SimpleNamespace(id=user_id, first_name=first_name, username=f"user{user_id}")
        self.effective_chat = types.SimpleNamespace(id=user_id * 10)
        self.effective_message, self.callback_query = message, query


# ---------------------------------------------------------------------------
# Lingkungan bot lengkap (config + manajer + database + bot palsu)
# ---------------------------------------------------------------------------
class BotEnv:
    def __init__(self, tmp: Path, **config_overrides):
        import config as cfg
        from database.database import Database
        from utils.file_manager import FileManager
        from utils.session_manager import SessionManager

        self.tmp = Path(tmp)
        self.config = cfg.Config(
            bot_token="123:fake", max_file_size=20 * MB, max_files=20, max_session_size=100 * MB,
            session_timeout=600, temp_dir=self.tmp / "temp", log_level="INFO", **config_overrides,
        )
        self.files = FileManager(self.config.temp_dir)
        self.sessions = SessionManager(self.files, self.config.session_timeout)
        self.db = Database(self.tmp / "bot.db")
        self.bot = FakeBot()
        self.data = {
            "config": self.config, "files": self.files, "sessions": self.sessions, "db": self.db,
            "unavailable": frozenset(), "job_slots": asyncio.Semaphore(2), "tasks": set(),
        }
        self.context = types.SimpleNamespace(bot=self.bot, application=types.SimpleNamespace(bot_data=self.data))

    def set_config(self, **changes) -> None:
        import dataclasses
        self.config = dataclasses.replace(self.config, **changes)
        self.data["config"] = self.config

    def add_source(self, file_id: str, path: Path) -> None:
        self.bot.sources[file_id] = Path(path)

    async def drain(self) -> None:
        while self.data["tasks"]:
            await asyncio.gather(*list(self.data["tasks"]), return_exceptions=True)

    def user_dir_exists(self, uid: int = 1) -> bool:
        return self.files.user_dir(uid).exists()

    def replies(self) -> list[str]:
        return [text for kind, text in self.bot.outbox if kind == "reply"]

    # ---- aksi user ----
    async def press(self, data, uid=1, msg=None, wait=True):
        from handlers import callbacks
        msg = msg or FakeMsg(self.bot, "menu")
        query = FakeQuery(self.bot, data, msg)
        await callbacks.handle_callback(FakeUpdate(uid, query=query), self.context)
        if wait:
            await self.drain()
        return query, msg

    async def send_file(self, name, source_id, uid=1, mime="application/pdf", size=1000):
        from handlers import files as files_h
        msg = FakeMsg(self.bot)
        self.bot.outbox.clear()
        await files_h._receive(FakeUpdate(uid, message=msg), self.context, file_id=source_id,
                               file_name=name, mime_type=mime, file_size=size)
        return msg

    def document_message(self, file_id, name, mime="application/pdf", size=1000) -> FakeMsg:
        msg = FakeMsg(self.bot)
        msg.document = types.SimpleNamespace(file_id=file_id, file_name=name, mime_type=mime, file_size=size)
        return msg

    async def say(self, text, uid=1):
        from handlers import pdf as pdf_h
        msg = FakeMsg(self.bot, text)
        self.bot.outbox.clear()
        await pdf_h.handle_text(FakeUpdate(uid, message=msg), self.context)
        await self.drain()
        return msg

    async def command(self, handler, uid=1, text="/cmd", first_name="Budi"):
        msg = FakeMsg(self.bot, text)
        self.bot.outbox.clear()
        await handler(FakeUpdate(uid, message=msg, first_name=first_name), self.context)
        return msg


class TmpCase(unittest.TestCase):
    """TestCase dengan folder sementara `self.tmp` yang dihapus otomatis."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="docbot_test_")
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp = Path(self._tmpdir.name)


class EnvCase(unittest.IsolatedAsyncioTestCase):
    """TestCase async dengan lingkungan bot lengkap di `self.env`."""

    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="docbot_test_")
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp = Path(self._tmpdir.name)
        self.env = BotEnv(self.tmp)
