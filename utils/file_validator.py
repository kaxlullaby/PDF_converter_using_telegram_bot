"""Validasi file berlapis (tidak hanya extension).

Tahap 1 - check_metadata()  : SEBELUM download -> extension, MIME type (dari Telegram), ukuran.
Tahap 2 - check_content()   : SESUDAH download -> ukuran asli, magic bytes, bisa dibaca library.

Contoh: "document.pdf.exe" ditolak di tahap 1 (extension akhirnya .exe).
        "virus.exe" yang di-rename "tugas.pdf" ditolak di tahap 2 (magic bytes bukan %PDF).
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

logger = logging.getLogger(__name__)

MB = 1024 * 1024
MAX_PDF_PAGES = 500
MAX_IMAGE_PIXELS = 60_000_000          # ~60 megapiksel, cegah "decompression bomb"
MAX_DOCX_UNCOMPRESSED = 200 * MB       # cegah "zip bomb"

# kind = jenis input yang diminta sebuah fitur
ALLOWED_EXTENSIONS: dict[str, set[str]] = {
    "pdf": {".pdf"},
    "image": {".jpg", ".jpeg", ".png"},
    "word": {".doc", ".docx"},
}
# jenis isi file (hasil deteksi magic bytes) yang diterima tiap kind
ALLOWED_CONTENT: dict[str, set[str]] = {
    "pdf": {"pdf"},
    "image": {"jpeg", "png"},
    "word": {"docx", "doc"},
}
KIND_LABELS: dict[str, str] = {
    "pdf": "PDF (.pdf)",
    "image": "gambar (.jpg, .jpeg, .png)",
    "word": "Word (.doc, .docx)",
}

_JPEG_MIMES = {"image/jpeg", "image/jpg", "image/pjpeg"}
_WORD_MIMES = {
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_MIME_BY_EXT: dict[str, set[str]] = {
    ".pdf": {"application/pdf"},
    ".jpg": _JPEG_MIMES,
    ".jpeg": _JPEG_MIMES,
    ".png": {"image/png"},
    ".doc": _WORD_MIMES,
    ".docx": _WORD_MIMES,
}

MSG_CORRUPT = (
    "❌ File tidak dapat diproses.\n\n"
    "Kemungkinan file corrupt atau format tidak didukung."
)


def too_large_message(max_size: int) -> str:
    return (
        "❌ File terlalu besar.\n\n"
        f"Maksimal ukuran file: {max_size // MB} MB.\n"
        "Silakan compress file terlebih dahulu."
    )


class ValidationError(Exception):
    """File ditolak. `user_message` aman ditampilkan langsung ke user."""

    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(code)
        self.code = code
        self.user_message = user_message


# --------------------------------------------------------------------------
# Tahap 1: sebelum download
# --------------------------------------------------------------------------
def check_metadata(
    file_name: str | None,
    mime_type: str | None,
    file_size: int | None,
    kind: str,
    max_size: int,
) -> str:
    """Validasi cepat dari data yang diberikan Telegram. Return extension (lowercase)."""
    ext = Path(file_name or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS[kind]:
        raise ValidationError(
            "bad_extension",
            "❌ Format file tidak didukung.\n\n"
            f"Fitur ini hanya menerima file {KIND_LABELS[kind]}.",
        )

    mime = (mime_type or "").strip().lower()
    if mime and mime != "application/octet-stream" and mime not in _MIME_BY_EXT[ext]:
        raise ValidationError(
            "bad_mime",
            "❌ Format file tidak didukung.\n\n"
            f"Fitur ini hanya menerima file {KIND_LABELS[kind]}.",
        )

    if file_size is not None:
        if file_size > max_size:
            raise ValidationError("too_large", too_large_message(max_size))
        if file_size == 0:
            raise ValidationError("empty", MSG_CORRUPT)
    return ext


# --------------------------------------------------------------------------
# Tahap 2: setelah download
# --------------------------------------------------------------------------
def detect_content_type(head: bytes) -> str | None:
    """Deteksi jenis file dari magic bytes (bukan dari nama)."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"PK\x03\x04"):
        return "docx"  # zip; dipastikan lagi di _check_docx
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "doc"   # OLE2 (Word 97-2003)
    if b"%PDF-" in head[:1024]:
        return "pdf"
    return None


def check_content(path: Path, kind: str, max_size: int) -> str:
    """Validasi isi file. BLOCKING -> panggil lewat asyncio.to_thread().

    Return jenis isi file ("pdf", "jpeg", "png", "docx", "doc").
    """
    path = Path(path)
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            head = fh.read(1032)
    except OSError:
        raise ValidationError("unreadable", MSG_CORRUPT) from None

    if size == 0:
        raise ValidationError("empty", MSG_CORRUPT)
    if size > max_size:
        raise ValidationError("too_large", too_large_message(max_size))

    detected = detect_content_type(head)
    if detected not in ALLOWED_CONTENT[kind]:
        raise ValidationError("bad_signature", MSG_CORRUPT)

    try:
        if detected == "pdf":
            _check_pdf(path)
        elif detected in ("jpeg", "png"):
            _check_image(path)
        elif detected == "docx":
            _check_docx(path)
        else:
            _check_doc(size)
    except ValidationError:
        raise
    except Exception:  # library gagal membaca -> anggap corrupt
        logger.warning("Validasi isi gagal untuk %s", path.name, exc_info=True)
        raise ValidationError("corrupt", MSG_CORRUPT) from None
    return detected


def _check_pdf(path: Path) -> None:
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        try:
            decrypted = reader.decrypt("")  # PDF "terkunci" tapi tanpa password user
        except Exception:
            decrypted = 0
        if not decrypted:
            raise ValidationError(
                "encrypted",
                "❌ PDF dilindungi password.\n\n"
                "Hapus password PDF terlebih dahulu, lalu kirim ulang.",
            )
    pages = len(reader.pages)
    if pages < 1:
        raise ValidationError("corrupt", MSG_CORRUPT)
    if pages > MAX_PDF_PAGES:
        raise ValidationError(
            "too_many_pages",
            f"❌ PDF terlalu banyak halaman.\n\nMaksimal {MAX_PDF_PAGES} halaman per file.",
        )
    _ = reader.pages[0]  # paksa parsing halaman pertama


def _check_image(path: Path) -> None:
    with Image.open(path) as img:
        if img.format not in ("JPEG", "PNG"):
            raise ValidationError("corrupt", MSG_CORRUPT)
        width, height = img.size
        if width * height > MAX_IMAGE_PIXELS:
            raise ValidationError(
                "image_too_big",
                "❌ Resolusi gambar terlalu besar.\n\nMaksimal sekitar 60 megapiksel.",
            )
        img.verify()


def _check_docx(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise ValidationError("corrupt", MSG_CORRUPT)
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise ValidationError("corrupt", MSG_CORRUPT)
        if sum(info.file_size for info in zf.infolist()) > MAX_DOCX_UNCOMPRESSED:
            raise ValidationError("zip_bomb", MSG_CORRUPT)


def _check_doc(size: int) -> None:
    # Validasi mendalam .doc dilakukan LibreOffice saat konversi (Phase 5).
    if size < 512:
        raise ValidationError("corrupt", MSG_CORRUPT)
