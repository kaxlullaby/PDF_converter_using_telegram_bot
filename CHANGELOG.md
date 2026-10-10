# Riwayat Perubahan

## 1.0.0 — rilis pertama
Dikembangkan bertahap dalam 8 fase.

- **Fase 1** — kerangka project, bot Telegram, `/start`, menu utama (Inline Keyboard).
- **Fase 2** — penerimaan file, validasi berlapis (ekstensi, MIME, magic bytes, bisa dibaca library), folder temp per user, session.
- **Fase 3** — Merge PDF, Split PDF, Rotate PDF.
- **Fase 4** — Compress PDF, PDF → JPG, JPG → PDF.
- **Fase 5** — Word → PDF (LibreOffice), PDF → Word (PyMuPDF + python-docx), OCR (Tesseract).
- **Fase 6** — proses di latar belakang dengan antrean, progress bar, tombol Cancel, timeout, database SQLite (`/history`, `/stats`), log ke file, pembersihan otomatis.
- **Fase 7** — suite tes (unittest/pytest), tes keamanan dengan serangan nyata, *mutation testing*, benchmark, dan dua optimasi terukur.
- **Fase 8** — deployment (systemd, Docker, Windows), `doctor.py`, README, pembersihan akhir. Bot kini menunggu internet saat start (tidak langsung berhenti) dan menampilkan pesan ringkas, bukan traceback panjang, jika koneksi gagal.
