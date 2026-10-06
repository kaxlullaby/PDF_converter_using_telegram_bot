"""Entry point Telegram Document Bot.

Jalankan dengan:  python bot.py
"""
from __future__ import annotations

import logging

from telegram import BotCommand, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

from config import Config, ConfigError, load_config
from handlers.callbacks import handle_callback
from handlers.start import help_command, menu_command, start_command

logger = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        level=getattr(logging, level, logging.INFO),
    )
    # Penting untuk keamanan: httpx mencatat URL request di level INFO,
    # sedangkan URL Telegram API mengandung BOT_TOKEN.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def post_init(application: Application) -> None:
    """Dijalankan sekali saat bot start: daftarkan menu perintah (tombol '/')."""
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Tampilkan menu utama"),
            BotCommand("menu", "Tampilkan menu utama"),
            BotCommand("help", "Bantuan & batasan"),
        ]
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tangkap semua error yang tidak tertangani supaya bot tidak crash."""
    logger.error("Unhandled exception", exc_info=context.error)

    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Terjadi kesalahan pada bot.\nSilakan coba lagi dengan /start.",
            )
        except TelegramError:
            logger.exception("Gagal mengirim pesan error ke user")


def build_application(config: Config) -> Application:
    app = Application.builder().token(config.bot_token).post_init(post_init).build()

    # Simpan config agar bisa diakses dari semua handler:
    # config = context.application.bot_data["config"]
    app.bot_data["config"] = config

    # Bot ini dirancang untuk chat pribadi (private) agar file tiap user terpisah.
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", start_command, filters=private))
    app.add_handler(CommandHandler("menu", menu_command, filters=private))
    app.add_handler(CommandHandler("help", help_command, filters=private))

    # Semua tombol Inline Keyboard masuk ke satu router.
    app.add_handler(CallbackQueryHandler(handle_callback))

    app.add_error_handler(error_handler)
    return app


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        raise SystemExit(f"❌ Konfigurasi error: {exc}") from None

    setup_logging(config.log_level)
    logger.info("Document Bot dimulai...")

    app = build_application(config)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
