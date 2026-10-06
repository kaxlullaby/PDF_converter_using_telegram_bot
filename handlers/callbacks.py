"""Router untuk semua tombol Inline Keyboard (CallbackQuery)."""
from __future__ import annotations

import logging

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from handlers.common import get_services
from handlers.files import (
    build_add_more,
    build_panel,
    build_prompt,
    build_reorder,
    max_files_for,
)
from handlers.start import EXPIRED_TEXT, MAIN_MENU_TEXT, build_help_text
from utils.keyboards import (
    CB_ADD,
    CB_BACK,
    CB_CANCEL,
    CB_DONE,
    CB_LIST,
    CB_MENU_HELP,
    CB_MENU_MAIN,
    CB_ORDER,
    FEATURE_PREFIX,
    FEATURES,
    ORDER_PREFIX,
    back_keyboard,
    main_menu_keyboard,
    menu_only_keyboard,
)
from utils.session_manager import SessionExpired

logger = logging.getLogger(__name__)

CANCEL_TEXT = "❌ Proses dibatalkan. File sementara sudah dihapus.\n\n" + MAIN_MENU_TEXT


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


async def _answer(query, alert: str | None) -> None:
    """Jawab callback tepat satu kali; `alert` tampil sebagai popup."""
    try:
        if alert:
            await query.answer(text=alert, show_alert=True)
        else:
            await query.answer()
    except TelegramError:
        logger.debug("Gagal menjawab callback query", exc_info=True)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    alert: str | None = None
    try:
        alert = await _dispatch(update, context, query.data or "")
    finally:
        await _answer(query, alert)  # hentikan animasi "loading" di tombol


async def _dispatch(
    update: Update, context: ContextTypes.DEFAULT_TYPE, data: str
) -> str | None:
    """Proses satu tombol. Return teks popup (alert) bila perlu, selain itu None."""
    query = update.callback_query
    user = update.effective_user
    config, sessions, _files = get_services(context)

    # ---------- navigasi (tidak butuh session) ----------
    if data in (CB_MENU_MAIN, CB_BACK):
        sessions.end(user.id)
        await _safe_edit(query, MAIN_MENU_TEXT, main_menu_keyboard())
        return None

    if data == CB_CANCEL:
        sessions.end(user.id)  # hapus semua file temp user
        await _safe_edit(query, CANCEL_TEXT, main_menu_keyboard())
        return None

    if data == CB_MENU_HELP:
        await _safe_edit(query, build_help_text(config), back_keyboard())
        return None

    # ---------- mulai fitur ----------
    if data.startswith(FEATURE_PREFIX):
        feature = FEATURES.get(data[len(FEATURE_PREFIX):])
        if feature is None:
            await _safe_edit(query, MAIN_MENU_TEXT, main_menu_keyboard())
            return None
        session = sessions.start(user.id, update.effective_chat.id, feature.key)
        session.panel_message_id = query.message.message_id
        text, markup = build_prompt(feature, config)
        await _safe_edit(query, text, markup)
        return None

    # ---------- semua aksi di bawah ini butuh session aktif ----------
    try:
        session = sessions.get(user.id)
    except SessionExpired:
        session = None
    if session is None:
        await _safe_edit(query, EXPIRED_TEXT, menu_only_keyboard())
        return None

    feature = FEATURES[session.feature_key]
    session.panel_message_id = query.message.message_id  # panel aktif = pesan ini

    if data == CB_LIST:
        text, markup = build_panel(session, feature, config)
        await _safe_edit(query, text, markup)
        return None

    if data == CB_ADD:
        if len(session.files) >= max_files_for(feature, config):
            return f"Batas {config.max_files} file sudah tercapai. Tekan Done untuk memproses."
        text, markup = build_add_more(session, feature, config)
        await _safe_edit(query, text, markup)
        return None

    if data == CB_ORDER:
        if len(session.files) < 2:
            return "Minimal 2 file untuk mengatur urutan."
        text, markup = build_reorder(session, feature)
        await _safe_edit(query, text, markup)
        return None

    if data.startswith(ORDER_PREFIX):
        await _handle_order(query, session, feature, config, sessions, data)
        return None

    if data == CB_DONE:
        if not session.files:
            return "Belum ada file. Kirim file terlebih dahulu."
        if len(session.files) < feature.min_files:
            return f"Minimal {feature.min_files} file untuk fitur ini."
        # Phase 3+: panggil service pemrosesan di sini.
        return (
            f"🚧 Pemrosesan {feature.label} akan diaktifkan pada Phase {feature.phase}.\n\n"
            "File Anda masih tersimpan sementara. Tekan Cancel untuk menghapusnya."
        )

    logger.warning("callback_data tidak dikenal: %r", data)
    return None


async def _handle_order(query, session, feature, config, sessions, data: str) -> None:
    """ord:up:<fid> | ord:down:<fid> | ord:del:<fid>"""
    parts = data.split(":", 2)
    if len(parts) == 3:
        _, action, fid = parts
        if action == "up":
            session.move(fid, -1)
        elif action == "down":
            session.move(fid, +1)
        elif action == "del":
            sessions.remove_file(session, fid)

    if len(session.files) >= 2:
        text, markup = build_reorder(session, feature)
    else:  # tinggal 0-1 file -> tidak ada yang perlu diurutkan
        text, markup = build_panel(session, feature, config)
    await _safe_edit(query, text, markup)
