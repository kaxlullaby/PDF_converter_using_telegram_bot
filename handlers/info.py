"""/history (untuk semua user) dan /stats (khusus admin)."""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from handlers.common import get_services, serialized
from handlers.files import format_size
from utils.keyboards import FEATURES

WIB = timezone(timedelta(hours=7))
STATUS_ICONS = {"success": "✅", "failed": "❌", "cancelled": "🚫", "timeout": "⌛"}


def _local_time(created_at: str) -> str:
    """Waktu di database disimpan dalam UTC; ditampilkan dalam WIB (UTC+7)."""
    try:
        moment = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return moment.astimezone(WIB).strftime("%d/%m %H:%M")
    except (TypeError, ValueError):
        return "?"


def format_operation(op: dict) -> str:
    feature = FEATURES.get(op["feature"])
    label = html.escape(feature.label if feature else str(op["feature"]))
    parts = [f"{STATUS_ICONS.get(op['status'], '•')} {label}"]
    if op.get("input_files"):
        parts.append(f"{op['input_files']} file")
    if op.get("input_bytes"):
        parts.append(format_size(op["input_bytes"]))
    if op.get("duration_ms") is not None:
        parts.append(f"{op['duration_ms'] / 1000:.1f} dtk")
    parts.append(_local_time(op["created_at"]))
    return " · ".join(parts)


@serialized
async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    user = update.effective_user
    summary = await services.db.user_summary(user.id)
    operations = await services.db.recent_operations(user.id, 5)

    lines = ["📊 <b>Riwayat Anda</b>", "", f"Total proses berhasil: <b>{summary['usage_count']}</b>"]
    if operations:
        lines += ["", "5 proses terakhir (WIB):"] + [format_operation(op) for op in operations]
    else:
        lines += ["", "Belum ada riwayat. Pilih fitur dari menu dengan /menu."]
    lines += ["", "ℹ️ Yang dicatat hanya ringkasan (fitur, jumlah & ukuran file, durasi). "
                  "Isi dan nama file Anda tidak disimpan."]
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@serialized
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if update.effective_user.id not in services.config.admin_ids:
        await update.effective_message.reply_text("❌ Perintah ini hanya untuk admin.")
        return

    stats = await services.db.global_stats()
    total = stats.get("operations", 0)
    success_rate = f"{stats.get('success', 0) * 100 // total}%" if total else "-"
    top = ", ".join(
        f"{FEATURES[k].label if k in FEATURES else k} ({n})" for k, n in stats.get("top_features", [])
    ) or "-"
    try:
        free_gb = f"{services.files.free_bytes() / 1024 ** 3:.1f} GB"
    except OSError:
        free_gb = "?"
    text = (
        "📈 <b>Statistik bot</b>\n\n"
        f"Pengguna: {stats.get('users', 0)}\n"
        f"Total proses: {total} (berhasil {success_rate})\n"
        f"24 jam terakhir: {stats.get('last_24h', 0)}\n"
        f"Fitur terpopuler: {html.escape(top)}\n\n"
        f"Sesi aktif: {services.sessions.active_count} (sedang diproses: {services.sessions.busy_count})\n"
        f"Ruang disk kosong: {free_gb}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
