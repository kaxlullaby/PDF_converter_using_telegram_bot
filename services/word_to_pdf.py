"""Word (.doc / .docx) -> PDF memakai LibreOffice headless.

Keamanan & kestabilan:
- Perintah dijalankan sebagai list argumen (tanpa shell); path yang dipakai milik kita
  sendiri (nama acak), bukan nama file dari user.
- Setiap konversi memakai profil LibreOffice sendiri (-env:UserInstallation), sehingga
  beberapa konversi tidak saling mengunci dan tidak ada sisa pengaturan antar user.
- Ada batas waktu; proses LibreOffice yang macet dihentikan paksa.
"""
from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from services.pdf_common import MissingDependencyError, PdfProcessingError, get_page_count
from services.process_utils import ProcessTimeout, run_command

logger = logging.getLogger(__name__)

_FALLBACK_PATHS = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
)

MSG_CONVERT_FAILED = (
    "Dokumen tidak dapat dikonversi.\n\n"
    "Kemungkinan file corrupt atau berisi format yang tidak didukung."
)


def find_libreoffice(configured: str | None = None) -> str | None:
    """Cari soffice: pengaturan .env dulu, lalu PATH, lalu lokasi instalasi umum."""
    if configured:
        if Path(configured).is_file():
            return str(configured)
        found = shutil.which(configured)
        if found:
            return found
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _FALLBACK_PATHS:
        if Path(candidate).is_file():
            return candidate
    return None


def word_to_pdf(
    input_path: Path,
    output_path: Path,
    *,
    soffice_path: str | None = None,
    timeout: int = 90,
) -> int:
    """Konversi dokumen Word ke PDF. Return jumlah halaman PDF hasil."""
    soffice = find_libreoffice(soffice_path)
    if soffice is None:
        logger.error("LibreOffice tidak ditemukan. Pasang LibreOffice atau isi LIBREOFFICE_PATH di .env")
        raise MissingDependencyError("Fitur ini belum siap di server (LibreOffice belum terpasang).")

    input_path, output_path = Path(input_path).resolve(), Path(output_path).resolve()
    work = output_path.parent / f"lo_{uuid.uuid4().hex[:8]}"
    out_dir = work / "out"
    out_dir.mkdir(parents=True)

    try:
        cmd = [
            soffice,
            f"-env:UserInstallation={(work / 'profile').as_uri()}",
            "--headless",
            "--norestore",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--convert-to", "pdf",
            "--outdir", str(out_dir),
            str(input_path),
        ]
        try:
            code, _stdout, stderr = run_command(cmd, timeout=timeout)
        except ProcessTimeout:
            raise PdfProcessingError(
                f"Konversi terlalu lama (lebih dari {timeout} detik) dan dihentikan.\n\n"
                "Coba dokumen yang lebih kecil atau sederhana."
            ) from None

        produced = out_dir / f"{input_path.stem}.pdf"
        if code != 0 or not produced.is_file() or produced.stat().st_size == 0:
            logger.warning("LibreOffice gagal (kode %s): %s", code, stderr[-500:].decode("utf-8", "replace"))
            raise PdfProcessingError(MSG_CONVERT_FAILED)
        shutil.move(str(produced), str(output_path))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    return get_page_count(output_path)
