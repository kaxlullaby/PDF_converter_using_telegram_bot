"""Definisi fitur + pembuat Inline Keyboard.

Semua callback_data didefinisikan di sini supaya tidak ada "magic string"
yang tersebar di banyak file.
"""
from __future__ import annotations

from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ---- callback_data ----
CB_MENU_MAIN = "menu:main"
CB_MENU_HELP = "menu:help"
CB_BACK = "nav:back"
CB_CANCEL = "nav:cancel"
CB_ADD = "act:add"
CB_ORDER = "act:order"
CB_DONE = "act:done"
CB_LIST = "list:show"
FEATURE_PREFIX = "feature:"
ORDER_PREFIX = "ord:"  # ord:up:<fid> | ord:down:<fid> | ord:del:<fid>


@dataclass(frozen=True)
class Feature:
    key: str          # dipakai di callback_data -> "feature:<key>"
    label: str        # teks tombol di menu utama
    description: str  # penjelasan singkat
    phase: int        # phase pengembangan saat fitur diaktifkan
    kind: str         # jenis input: "pdf" | "image" | "word"
    multi: bool = False   # True = terima banyak file (mode Collecting Files)
    min_files: int = 1    # minimal file agar boleh diproses

    @property
    def callback_data(self) -> str:
        return f"{FEATURE_PREFIX}{self.key}"


FEATURES: dict[str, Feature] = {
    f.key: f
    for f in (
        Feature("merge", "🔗 Merge PDF",
                "Gabungkan 2–20 file PDF menjadi satu.", 3, "pdf",
                multi=True, min_files=2),
        Feature("split", "✂️ Split PDF",
                "Ambil halaman tertentu dari satu PDF (contoh: 1-5 atau 2,4,7).", 3, "pdf"),
        Feature("compress", "📦 Compress PDF",
                "Perkecil ukuran PDF (Low / Medium / High).", 4, "pdf"),
        Feature("pdf2jpg", "🖼 PDF → JPG",
                "Ubah setiap halaman PDF menjadi gambar JPG.", 4, "pdf"),
        Feature("jpg2pdf", "📄 JPG → PDF",
                "Gabungkan 1–20 gambar (JPG/PNG) menjadi satu PDF.", 4, "image",
                multi=True, min_files=1),
        Feature("rotate", "🔄 Rotate PDF",
                "Putar halaman PDF 90°, 180°, atau 270°.", 3, "pdf"),
        Feature("word2pdf", "📝 Word → PDF",
                "Ubah file .doc / .docx menjadi PDF.", 5, "word"),
        Feature("pdf2word", "📄 PDF → Word",
                "Ubah PDF menjadi .docx (dengan OCR untuk PDF hasil scan).", 5, "pdf"),
    )
}


def _btn(feature_key: str) -> InlineKeyboardButton:
    f = FEATURES[feature_key]
    return InlineKeyboardButton(f.label, callback_data=f.callback_data)


def _cancel_btn() -> InlineKeyboardButton:
    return InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Menu utama sesuai desain: PDF tools, Word tools, lalu Help."""
    return InlineKeyboardMarkup(
        [
            [_btn("merge"), _btn("split")],
            [_btn("compress"), _btn("pdf2jpg")],
            [_btn("jpg2pdf"), _btn("rotate")],
            [_btn("word2pdf"), _btn("pdf2word")],
            [InlineKeyboardButton("ℹ️ Help", callback_data=CB_MENU_HELP)],
        ]
    )


def nav_keyboard() -> InlineKeyboardMarkup:
    """Tombol Back + Cancel (layar awal sebuah proses)."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK), _cancel_btn()]]
    )


def back_keyboard() -> InlineKeyboardMarkup:
    """Hanya tombol Back (dipakai di halaman Help)."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK)]]
    )


def menu_only_keyboard() -> InlineKeyboardMarkup:
    """Satu tombol kembali ke menu utama (dipakai setelah session expired)."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 Menu Utama", callback_data=CB_MENU_MAIN)]]
    )


def collecting_keyboard(multi: bool) -> InlineKeyboardMarkup:
    """Tombol di bawah daftar file yang sudah diterima."""
    done = InlineKeyboardButton("✅ Done", callback_data=CB_DONE)
    if multi:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("➕ Add More", callback_data=CB_ADD),
                    InlineKeyboardButton("🔄 Change Order", callback_data=CB_ORDER),
                ],
                [done, _cancel_btn()],
            ]
        )
    return InlineKeyboardMarkup([[done, _cancel_btn()]])


def add_more_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Done", callback_data=CB_DONE), _cancel_btn()]]
    )


def reorder_keyboard(fids: list[str]) -> InlineKeyboardMarkup:
    """Satu baris per file: [N ⬆️] [N ⬇️] [N 🗑]. N = nomor di daftar."""
    rows = [
        [
            InlineKeyboardButton(f"{i} ⬆️", callback_data=f"{ORDER_PREFIX}up:{fid}"),
            InlineKeyboardButton(f"{i} ⬇️", callback_data=f"{ORDER_PREFIX}down:{fid}"),
            InlineKeyboardButton(f"{i} 🗑", callback_data=f"{ORDER_PREFIX}del:{fid}"),
        ]
        for i, fid in enumerate(fids, start=1)
    ]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=CB_LIST), _cancel_btn()])
    return InlineKeyboardMarkup(rows)
