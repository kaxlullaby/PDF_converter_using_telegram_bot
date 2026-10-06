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
FEATURE_PREFIX = "feature:"


@dataclass(frozen=True)
class Feature:
    key: str          # dipakai di callback_data -> "feature:<key>"
    label: str        # teks tombol di menu utama
    description: str  # penjelasan singkat
    phase: int        # phase pengembangan saat fitur diaktifkan

    @property
    def callback_data(self) -> str:
        return f"{FEATURE_PREFIX}{self.key}"


FEATURES: dict[str, Feature] = {
    f.key: f
    for f in (
        Feature("merge", "🔗 Merge PDF",
                "Gabungkan 2–20 file PDF menjadi satu.", 3),
        Feature("split", "✂️ Split PDF",
                "Ambil halaman tertentu dari satu PDF (contoh: 1-5 atau 2,4,7).", 3),
        Feature("compress", "📦 Compress PDF",
                "Perkecil ukuran PDF (Low / Medium / High).", 4),
        Feature("pdf2jpg", "🖼 PDF → JPG",
                "Ubah setiap halaman PDF menjadi gambar JPG.", 4),
        Feature("jpg2pdf", "📄 JPG → PDF",
                "Gabungkan 1–20 gambar (JPG/PNG) menjadi satu PDF.", 4),
        Feature("rotate", "🔄 Rotate PDF",
                "Putar halaman PDF 90°, 180°, atau 270°.", 3),
        Feature("word2pdf", "📝 Word → PDF",
                "Ubah file .doc / .docx menjadi PDF.", 5),
        Feature("pdf2word", "📄 PDF → Word",
                "Ubah PDF menjadi .docx (dengan OCR untuk PDF hasil scan).", 5),
    )
}


def _btn(feature_key: str) -> InlineKeyboardButton:
    f = FEATURES[feature_key]
    return InlineKeyboardButton(f.label, callback_data=f.callback_data)


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
    """Tombol Back + Cancel yang dipakai di setiap layar proses."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK),
                InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
            ]
        ]
    )


def back_keyboard() -> InlineKeyboardMarkup:
    """Hanya tombol Back (dipakai di halaman Help)."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK)]]
    )
