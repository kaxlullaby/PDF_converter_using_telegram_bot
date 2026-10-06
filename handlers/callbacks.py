"""Router untuk semua tombol Inline Keyboard (CallbackQuery)."""
from __future__ import annotations

import logging

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import Config
from handlers.start import MAIN_MENU_TEXT, build_help_text
from utils.keyboards import (
    CB_BACK,
    CB_CANCEL,
    CB_MENU_HELP,
    CB_MENU_MAIN,
    FEATURE_PREFIX,
    FEATURES,
    back_keyboard,
    main_menu_keyboard,
    nav_keyboard,
)

logger = logging.getLogger(__name__)


async def _safe_edit(query, text: str, markup: InlineKeyboardMarkup) -> None:
    """Edit pesan tanpa crash jika isinya sama (user menekan tombol dua kali)."""
    try:
        await query.edit_message_text(
            text=text, reply_markup=markup, parse_mode=ParseMode.HTML
        )
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise


async def _show_main_menu(query) -> None:
    await _safe_edit(query, MAIN_MENU_TEXT, main_menu_keyboard())


async def _show_help(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    await _safe_edit(query, build_help_text(config), back_keyboard())


async def _show_feature(query, feature_key: str) -> None:
    feature = FEATURES.get(feature_key)
    if feature is None:
        await _show_main_menu(query)
        return

    # Phase 3+: ganti placeholder ini dengan handler fitur yang sebenarnya.
    text = (
        f"<b>{feature.label}</b>\n\n"
        f"{feature.description}\n\n"
        f"🚧 Fitur ini akan diaktifkan pada <b>Phase {feature.phase}</b>."
    )
    await _safe_edit(query, text, nav_keyboard())


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # hentikan animasi "loading" di tombol
    data = query.data or ""

    if data in (CB_MENU_MAIN, CB_BACK):
        await _show_main_menu(query)
    elif data == CB_CANCEL:
        # Phase 2: hapus session + temp files user di sini.
        await _show_main_menu(query)
    elif data == CB_MENU_HELP:
        await _show_help(query, context)
    elif data.startswith(FEATURE_PREFIX):
        await _show_feature(query, data[len(FEATURE_PREFIX):])
    else:
        logger.warning("callback_data tidak dikenal: %r", data)
        await _show_main_menu(query)
