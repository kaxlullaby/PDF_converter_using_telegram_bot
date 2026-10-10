"""Validasi file berlapis (tidak hanya extension).

Tahap 1 - check_metadata()  : SEBELUM download -> extension, MIME type (dari Telegram), ukuran.
Tahap 2 - check_content()   : SESUDAH download -> ukuran asli, magic bytes, bisa dibaca library.

Contoh: "document.pdf.exe" ditolak di tahap 1 (extension akhirnya .exe).
        "virus.exe" yang di-rename "tugas.pdf" ditolak di tahap 2 (magic bytes bukan %PDF).
"""
from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image
from pypdf import PdfReader

logger = logging.getLogger(__name__)

MB = 1024 * 1024
MAX_PDF_PAGES = 500
MAX_IMAGE_PIXELS = 60_000_000          # ~60 megapiksel, cegah "decompression bomb"
MAX_DOCX_UNCOMPRESSED = 200 * MB       # cegah "zip bomb"
MAX_DOCX_ENTRIES = 10_000              # DOCX normal hanya berisi puluhan entri
MAX_RELS_BYTES = 2 * MB                # file relasi DOCX normal hanya beberapa KB
# Tautan EKSTERNAL jenis ini akan diambil LibreOffice saat konversi (risiko SSRF / kebocoran data).
# Hyperlink biasa tidak diambil saat konversi, jadi tetap diizinkan.
_BLOCKED_EXTERNAL_TYPES = {"image", "attachedtemplate", "oleobject", "frame", "subdocument",
                           "externallink", "externallinkpath", "afchunk"}

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


MSG_EXTERNAL_LINK = (
    "❌ Dokumen berisi gambar/objek yang ditautkan dari luar (bukan tertanam).\n\n"
    "Di Word: File → Info → Edit Links to Files → Break Link, lalu simpan dan kirim ulang."
)


@dataclass(frozen=True)
class ContentInfo:
    detected: str               # "pdf" | "jpeg" | "png" | "docx" | "doc"
    pages: int | None = None    # jumlah halaman (khusus PDF)


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
    """Seperti inspect_content(), tetapi hanya mengembalikan jenis isi file."""
    return inspect_content(path, kind, max_size).detected


def inspect_content(path: Path, kind: str, max_size: int) -> ContentInfo:
    """Validasi isi file. BLOCKING -> panggil lewat asyncio.to_thread().

    PDF hanya di-parse SEKALI: jumlah halaman didapat dari proses validasi yang sama.
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

    pages: int | None = None
    try:
        if detected == "pdf":
            pages = _check_pdf(path)
        elif detected in ("jpeg", "png"):
            _check_image(path)
        elif detected == "docx":
            _check_docx(path)
        else:
            _check_doc(size)
    except ValidationError:
        raise
    except Exception as exc:  # library gagal membaca -> anggap corrupt
        # Satu baris saja: file berbahaya bisa memicu traceback ribuan baris (RecursionError, dst.)
        # dan penyerang tidak boleh bisa membanjiri log. Detail lengkap hanya di level DEBUG.
        logger.warning("Validasi isi gagal (%s): %s", path.name, type(exc).__name__)
        logger.debug("Detail kegagalan validasi", exc_info=True)
        raise ValidationError("corrupt", MSG_CORRUPT) from None
    return ContentInfo(detected, pages)


def _check_pdf(path: Path) -> int:
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
    return pages


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
        if len(names) > MAX_DOCX_ENTRIES:
            raise ValidationError("zip_bomb", MSG_CORRUPT)
        if sum(info.file_size for info in zf.infolist()) > MAX_DOCX_UNCOMPRESSED:
            raise ValidationError("zip_bomb", MSG_CORRUPT)
        if _has_blocked_external_link(zf, names):
            raise ValidationError("external_link", MSG_EXTERNAL_LINK)


def _has_blocked_external_link(zf: zipfile.ZipFile, names: set[str]) -> bool:
    """True jika ada relasi EKSTERNAL berjenis gambar/template/objek (akan diunduh LibreOffice).

    File relasi (.rels) di-parse sebagai XML sungguhan, bukan dicari dengan teks biasa, supaya
    tidak bisa dikelabui dengan spasi, tanda kutip, atau karakter entitas (&#69;xternal).
    DTD/ENTITY ditolak karena DOCX asli tidak pernah memakainya (cegah XML bomb).
    """
    for name in names:
        if not name.lower().endswith(".rels"):
            continue
        if zf.getinfo(name).file_size > MAX_RELS_BYTES:
            return True
        data = zf.read(name)
        lowered = data.lower()
        if b"<!doctype" in lowered or b"<!entity" in lowered:
            return True
        try:
            root = ElementTree.fromstring(data)
        except ElementTree.ParseError:
            return True
        for element in root.iter():
            if not element.tag.endswith("Relationship"):
                continue
            mode = (element.get("TargetMode") or "").strip().lower()
            rel_type = (element.get("Type") or "").rstrip("/").rsplit("/", 1)[-1].lower()
            if mode == "external" and rel_type in _BLOCKED_EXTERNAL_TYPES:
                return True
    return False


def _check_doc(size: int) -> None:
    # Validasi mendalam .doc dilakukan LibreOffice saat konversi (Phase 5).
    if size < 512:
        raise ValidationError("corrupt", MSG_CORRUPT)
