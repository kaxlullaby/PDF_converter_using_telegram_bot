# Telegram Document Bot

Bot Telegram untuk mengolah PDF dan dokumen — seperti *mini iLovePDF*, tetapi seluruh prosesnya lewat chat Telegram.
Dibuat dengan Python, modular, dan dilengkapi validasi file, session per user, pembersihan otomatis, serta pengujian keamanan.

## Daftar isi
1. [Project Overview](#1-project-overview) · 2. [Fitur](#2-fitur) · 3. [Persyaratan](#3-persyaratan) · 4. [Instalasi](#4-instalasi) · 5. [Variabel Lingkungan](#5-variabel-lingkungan)
6. [Membuat Bot lewat BotFather](#6-membuat-bot-lewat-botfather) · 7. [Menjalankan Bot](#7-menjalankan-bot) · 8. [Struktur Project](#8-struktur-project) · 9. [Cara Menggunakan Setiap Fitur](#9-cara-menggunakan-setiap-fitur)
10. [Keterbatasan](#10-keterbatasan) · 11. [Keamanan](#11-keamanan) · 12. [Deployment (24/7)](#12-deployment-247) · [Pengujian](#pengujian) · [Troubleshooting](#troubleshooting) · [Lisensi](#lisensi-dan-pustaka-pihak-ketiga)

## Quick start tbrk

```bash
in terminal:

cd telegram-document-bot
python -m venv .venv                          # Windows (PowerShell): .venv\Scripts\Activate.ps1
source .venv/bin/activate                     # Windows (Git Bash)  : source .venv/Scripts/activate
python -m pip install -r requirements.txt
cp .env.example .env                          # Windows: copy .env.example .env
python doctor.py                              # Cek kesiapan sblm start
python bot.py
```

## Uji test bot
```bash
python -m unittest discover -s tests -t . -v      # tanpa instalasi tambahan
python -m pip install -r requirements-dev.txt && pytest
python tests/benchmark.py                         # ukur kecepatan bagian-bagian bot
python doctor.py                                  # periksa kesiapan lingkungan
```

---

## 1. Project Overview

User memilih fitur lewat tombol, mengirim file, lalu bot memproses dan mengirim hasilnya. File tidak disimpan permanen.

```
User → Telegram Bot → pilih fitur → bot meminta file → user mengirim file
     → validasi file → antre → proses (di latar belakang, ada progress & tombol Cancel)
     → hasil dikirim → file temp dihapus
```

Gambaran arsitektur:

```
handlers/   menerima pesan & tombol Telegram (lapisan antarmuka)
services/   logika pemrosesan murni: PDF, gambar, Word, OCR (tanpa kode Telegram, mudah diuji)
utils/      validasi file, folder temp per user, session
database/   SQLite: pengguna & ringkasan riwayat operasi
```

## 2. Fitur

| Fitur | Ringkasan |
|---|---|
|  Merge PDF | Gabungkan 2–20 PDF, urutan bisa diatur |
|  Split PDF | Ambil halaman tertentu (`1-5`, `2,4,7`, `3-8`) |
|  Compress PDF | Tiga level: Low / Medium / High, dengan laporan ukuran sebelum–sesudah |
|  PDF → JPG | 1 halaman = 1 JPG; 2–10 halaman = file satu per satu; lebih dari 10 = ZIP |
|  JPG → PDF | 1–20 gambar (JPG/PNG) menjadi PDF A4, urutan bisa diatur |
|  Rotate PDF | Putar 90° / 180° / 270° searah jarum jam |
|  Word → PDF | `.doc` / `.docx` lewat LibreOffice |
|  PDF → Word | PDF teks diambil langsung; PDF hasil scan dibaca dengan OCR |

Tambahan: antrean proses, progress bar, tombol Cancel, `/history` (riwayat Anda), `/stats` (khusus admin), dan `doctor.py` (pemeriksa kesiapan).

## 3. Persyaratan

- **Python 3.10 atau lebih baru**
- Paket Python: lihat `requirements.txt` (python-telegram-bot, pypdf, PyMuPDF, Pillow, python-docx, python-dotenv)
- Program pendukung (bukan paket pip; pasang terpisah):
  - **LibreOffice** — untuk Word → PDF
  - **Tesseract OCR** — untuk PDF hasil scan (pasang juga data bahasa **Indonesian** jika dokumen berbahasa Indonesia)
- Koneksi internet dari komputer/server yang menjalankan bot

Fitur yang program pendukungnya belum ada otomatis disembunyikan dari menu; fitur lain tetap berfungsi.

> Pengguna bot **tidak perlu** memasang apa pun. Program di atas hanya dibutuhkan di komputer/server tempat bot berjalan.

## 4. Instalasi

```bash
# 1. Salin / ekstrak project, lalu masuk ke foldernya
cd telegram-document-bot

# 2. Buat virtual environment
python -m venv .venv
source .venv/bin/activate            # Windows (PowerShell): .venv\Scripts\Activate.ps1
                                     # Windows (Git Bash)  : source .venv/Scripts/activate

# 3. Pasang paket Python
python -m pip install -r requirements.txt

# 4. Siapkan konfigurasi
cp .env.example .env                 # Windows: copy .env.example .env
#    lalu isi BOT_TOKEN di file .env (lihat bagian 6)

# 5. Periksa kesiapan
python doctor.py
```

Pasang program pendukung:
- **Ubuntu/Debian:** `sudo apt install libreoffice-writer tesseract-ocr tesseract-ocr-eng tesseract-ocr-ind fonts-liberation fonts-crosextra-carlito`
- **Windows:** pasang LibreOffice (libreoffice.org) dan Tesseract (installer UB Mannheim, centang bahasa *Indonesian*). Jika bahasa Indonesia tidak ditawarkan, unduh `ind.traineddata` dari repositori `tesseract-ocr/tessdata_fast` dan simpan di folder `tessdata` milik Tesseract.
- **macOS:** `brew install --cask libreoffice` dan `brew install tesseract tesseract-lang`

## 5. Variabel Lingkungan

Semua diatur di file `.env` (contoh lengkap: `.env.example`). Hanya `BOT_TOKEN` yang wajib.

| Variabel | Bawaan | Fungsi |
|---|---|---|
| `BOT_TOKEN` | — (wajib) | Token dari @BotFather |
| `MAX_FILE_SIZE` | `20971520` (20 MB) | Ukuran maksimal per file (byte). Batas download bot Telegram memang 20 MB |
| `MAX_FILES` | `20` | File maksimal per proses (Merge / JPG → PDF) |
| `MAX_SESSION_SIZE` | `104857600` (100 MB) | Total ukuran semua file dalam satu proses |
| `SESSION_TIMEOUT` | `600` | Detik tanpa aktivitas sebelum sesi berakhir (10 menit) |
| `MAX_CONCURRENT_JOBS` | `2` | Proses berat yang boleh berjalan bersamaan; sisanya antre |
| `PROCESS_TIMEOUT` | `300` | Batas waktu satu proses (detik) |
| `LIBREOFFICE_PATH` | otomatis | Lokasi `soffice` jika tidak ditemukan otomatis |
| `TESSERACT_PATH` | otomatis | Lokasi `tesseract` jika tidak ditemukan otomatis |
| `OCR_LANGUAGES` | `ind+eng` | Bahasa OCR (harus terpasang di Tesseract) |
| `CONVERT_TIMEOUT` | `90` | Batas waktu Word → PDF (detik) |
| `DATABASE_PATH` | `data/bot.db` | Lokasi database SQLite |
| `LOG_FILE` | `logs/bot.log` | Lokasi log (`off` untuk mematikan) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `HISTORY_RETENTION_DAYS` | `90` | Riwayat operasi lebih tua dihapus otomatis |
| `ADMIN_IDS` | kosong | ID Telegram admin (pisah koma): akses `/stats` + pemberitahuan error |

Token **tidak pernah** ditulis di kode; `.env` sudah ada di `.gitignore` dan `.dockerignore`.

## 6. Membuat Bot lewat BotFather

1. Buka Telegram, cari **@BotFather** (akun resmi bercentang biru), kirim `/newbot`.
2. Isi **nama** bot (bebas, mis. *Document Bot*), lalu **username** yang diakhiri `bot` (mis. `dokumenku_bot`).
3. BotFather membalas dengan **token** berbentuk `123456789:AAH...`. Salin ke `.env`:
   ```
   BOT_TOKEN=123456789:AAH...
   ```
4. Opsional: `/setdescription`, `/setuserpic`, `/setcommands` di BotFather untuk mempercantik bot (daftar perintah sudah didaftarkan otomatis saat bot start).
5. Jika token bocor: `/revoke` di BotFather, lalu perbarui `.env`.

## 7. Menjalankan Bot

```bash
python doctor.py --online     # opsional: periksa token & koneksi ke Telegram
python bot.py
```

Log "Application started" berarti bot aktif. Buka bot Anda di Telegram dan kirim `/start`. Hentikan dengan `Ctrl+C` (semua file temp dihapus otomatis).

Jika internet belum siap saat bot dinyalakan, bot **menunggu dan mencoba lagi sendiri** — tidak perlu dijalankan ulang. Untuk menjalankannya terus-menerus tanpa diawasi, lihat [bagian 12](#12-deployment-247).

## 8. Struktur Project

```
telegram-document-bot/
├── bot.py                  titik masuk: perakitan aplikasi, tugas rutin, penanganan error global
├── config.py               membaca & memvalidasi .env
├── doctor.py               pemeriksa kesiapan (python doctor.py [--online])
├── requirements.txt        paket untuk dijalankan  (requirements-dev.txt: + pytest)
├── .env.example            contoh konfigurasi
├── Dockerfile · docker-compose.yml · .dockerignore
├── deploy/
│   ├── docbot.service      layanan systemd (Linux)
│   ├── install_ubuntu.sh   pemasangan otomatis di Ubuntu/Debian
│   └── windows_task.ps1    jalan otomatis di Windows (Task Scheduler)
├── handlers/               lapisan Telegram
│   ├── start.py            /start, /menu, /help
│   ├── callbacks.py        router semua tombol
│   ├── files.py            penerimaan file + tampilan daftar file
│   ├── pdf.py              menjalankan proses di latar belakang, mengirim hasil
│   ├── progress.py         progress bar
│   ├── info.py             /history, /stats
│   └── common.py           objek bersama + kunci per user
├── services/               logika pemrosesan (tanpa Telegram)
│   ├── merge_pdf.py · split_pdf.py · rotate_pdf.py · compress_pdf.py
│   ├── pdf_to_jpg.py · jpg_to_pdf.py
│   ├── word_to_pdf.py · pdf_to_word.py
│   ├── pdf_common.py       fungsi PDF bersama
│   ├── process_utils.py    menjalankan LibreOffice/Tesseract dengan aman
│   └── job_context.py      pembatalan & laporan progress dari thread
├── database/
│   └── database.py         SQLite: pengguna & riwayat
├── utils/
│   ├── file_validator.py   validasi berlapis
│   ├── file_manager.py     folder temp per user, nama acak, pembersihan
│   ├── session_manager.py  session per user
│   └── keyboards.py        daftar fitur + Inline Keyboard
├── tests/                  suite tes (lihat bagian Pengujian)
├── temp/                   file sementara (dikosongkan otomatis)
├── data/                   database SQLite (dibuat otomatis)
└── logs/                   file log (dibuat otomatis)
```

## 9. Cara Menggunakan Setiap Fitur

Mulai dengan `/start` atau `/menu`, lalu tekan tombol fitur. Kirim file **sebagai dokumen** (📎 → File) agar kualitasnya utuh. Tombol **⬅️ Back** dan **❌ Cancel** tersedia di setiap langkah.

- **Merge PDF** — kirim PDF satu per satu (2–20). Daftar file tampil setelah setiap kiriman. **🔄 Change Order** untuk menaikkan/menurunkan/menghapus file. Tekan **✅ Merge PDF**.
- **Split PDF** — kirim satu PDF, bot menampilkan jumlah halamannya. Ketik halaman: `1-5`, `2,4,7`, atau `1-3, 6, 9-10`. Hasil mengikuti urutan yang diketik.
- **Compress PDF** — kirim satu PDF, pilih 🟢 Low / 🟡 Medium / 🔴 High. Hasil disertai `Original`, `Compressed`, `Reduction`. PDF yang isinya hampir teks saja tidak banyak mengecil; efek terbesar pada PDF berisi foto/scan.
- **PDF → JPG** — kirim satu PDF, tekan **Convert to JPG** (maks. 100 halaman, 150 dpi).
- **JPG → PDF** — kirim 1–20 gambar (JPG/JPEG/PNG; foto biasa juga diterima tetapi sudah dikompres Telegram). Atur urutan, tekan **Convert to PDF**. Tiap gambar menjadi satu halaman A4; foto HP yang miring ditegakkan otomatis.
- **Rotate PDF** — kirim satu PDF, pilih 90° / 180° / 270° (searah jarum jam).
- **Word → PDF** — kirim `.doc`/`.docx`, tekan **Convert to PDF**. Format dipertahankan sebisa mungkin; font yang tidak ada di server bisa diganti.
- **PDF → Word** — kirim satu PDF, tekan **Convert to Word**. Bot mengenali PDF 📄 *text-based* (teks diambil langsung) atau 🖼 *scanned* (dibaca dengan OCR, maks. 20 halaman scan).

Selama proses berjalan tampil progress bar dengan tombol **❌ Cancel**. Perintah lain: `/history` (5 proses terakhir + total pemakaian), `/help`, dan `/stats` (admin).

## 10. Keterbatasan

- **Ukuran:** bot hanya bisa mengunduh file hingga **20 MB** dan mengirim hingga **50 MB** (batas Telegram Bot API). Total file per proses maks. 100 MB (`MAX_SESSION_SIZE`).
- **Halaman:** PDF maks. 500 halaman per file; PDF → JPG maks. 100 halaman; OCR maks. 20 halaman scan; hasil Merge maks. 2000 halaman.
- **PDF berpassword** tidak didukung (PDF dengan password kosong bisa).
- **PDF → Word** hanya membawa **teks** (dengan ukuran huruf, tebal, miring). Tabel, kolom, dan gambar **tidak** ikut; layout asli tidak 100% terjaga.
- **OCR** bergantung pada kualitas scan dan data bahasa Tesseract; akurasi tidak 100%.
- **Word → PDF:** tampilan bisa sedikit berbeda jika font dokumen tidak ada di server. DOCX dengan gambar/template **tertaut dari luar** ditolak (alasan keamanan); gunakan *Break Link* di Word.
- **Bot hanya melayani chat pribadi**, bukan grup. Satu user menjalankan satu proses pada satu waktu.
- **Sesi disimpan di memori:** jika bot dimulai ulang, sesi yang sedang berjalan hilang (file temp ikut dibersihkan).
- Dirancang untuk satu server; belum mendukung beberapa server sekaligus.

## 11. Keamanan

Yang diterapkan (dan diuji di `tests/test_security.py`):

- Token hanya di `.env` (diabaikan git/docker), tidak muncul di log maupun `repr` config.
- **Validasi berlapis:** ekstensi, MIME, *magic bytes*, ukuran asli, dan keterbacaan oleh library. `dokumen.pdf.exe` dan file yang diganti namanya ditolak.
- Tidak ada perintah shell dari input user: program eksternal dijalankan sebagai daftar argumen, dengan batas waktu dan bisa dihentikan paksa.
- Nama file dari user hanya dipakai untuk tampilan (dibersihkan + di-escape); di disk selalu nama acak. Tidak ada path traversal.
- **Isolasi:** satu folder dan satu session per user; user tidak bisa menyentuh file user lain meski memalsukan `callback_data`.
- **Batas:** ukuran file, jumlah file, total ukuran, halaman, piksel gambar, ukuran isi ZIP/DOCX, waktu proses, dan jumlah proses bersamaan.
- DOCX dengan tautan eksternal (gambar/template/objek) ditolak untuk mencegah SSRF lewat LibreOffice.
- File temp dihapus setelah sukses, gagal, Cancel, timeout, bot berhenti, dan saat bot start; ada penyapu folder yatim.
- Dokumen user tidak disimpan permanen. Database hanya memuat ringkasan (fitur, jumlah/ukuran file, durasi), **bukan** isi atau nama file.

**Risiko yang tersisa:** PDF "bom kompresi" bisa menghabiskan memori saat dirender (batasi memori server — sudah ada di `docbot.service`/`docker-compose.yml`); file `.doc` biner tidak bisa dipindai tautan eksternalnya (batasi koneksi keluar server dengan firewall bila perlu); belum ada pembatasan laju per user; parser PDF adalah sasaran serangan umum — **perbarui `PyMuPDF` dan `pypdf` secara berkala**.

## 12. Deployment (24/7)

Bot memakai *long polling*: tidak perlu domain, port terbuka, atau IP publik. Cukup satu komputer yang menyala terus dan sebuah "penjaga" yang menyalakan bot saat boot dan saat crash.

### A. VPS Linux (disarankan)

Spesifikasi: Ubuntu 22.04/24.04, **RAM ≥ 2 GB** (LibreOffice + OCR), disk ≥ 20 GB. Cara otomatis:

```bash
sudo bash deploy/install_ubuntu.sh        # DRY_RUN=1 bash deploy/install_ubuntu.sh  → hanya menampilkan langkah
sudo nano /opt/telegram-document-bot/.env # isi BOT_TOKEN
sudo -u docbot /opt/telegram-document-bot/.venv/bin/python /opt/telegram-document-bot/doctor.py --online
sudo systemctl enable --now docbot
```

Skrip memasang paket sistem, membuat user `docbot`, menyalin project ke `/opt/telegram-document-bot`, membuat venv, dan memasang layanan `docbot.service` (otomatis menyala saat boot, restart saat crash, batas memori 1,5 GB). Berkas `.env`, `data/`, dan `logs/` tidak ditimpa saat dijalankan ulang.

```bash
systemctl status docbot            # hidup atau mati
journalctl -u docbot -f            # log langsung (Ctrl+C untuk keluar)
sudo systemctl restart docbot      # setelah mengubah kode atau .env
```

**Memperbarui:** unggah versi baru, jalankan ulang `sudo bash deploy/install_ubuntu.sh`, lalu `sudo systemctl restart docbot`.
Pengamanan systemd yang lebih ketat tersedia (dikomentari) di `deploy/docbot.service`; aktifkan satu per satu dan uji Word → PDF.
Keamanan server: gunakan SSH key, aktifkan firewall hanya untuk SSH (`sudo ufw allow OpenSSH && sudo ufw enable`) — bot tidak membuka port apa pun.

### B. Windows (komputer/laptop sendiri)

Bot hanya aktif selama komputer menyala, online, dan tidak *sleep* (Settings → Power → Sleep: *Never*). Buka PowerShell **sebagai Administrator** di folder project:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows_task.ps1            # pasang + jalankan
powershell -ExecutionPolicy Bypass -File deploy\windows_task.ps1 -Remove    # hapus
```

Bot menyala saat Windows hidup (tanpa perlu login) dan dicoba lagi tiap menit jika mati. Log ada di `logs\bot.log`.

### C. Docker

```bash
cp .env.example .env     # isi BOT_TOKEN
docker compose up -d --build
docker compose logs -f
```

Container berjalan sebagai user biasa dengan batas memori/CPU/proses; database dan log disimpan di volume.

### Pemeliharaan

- **Backup database:** salin `data/bot.db` (opsional; hanya berisi ringkasan riwayat).
- **Log:** `logs/bot.log` berputar otomatis (maks. ±4 MB).
- **Jangan menjalankan dua bot dengan token yang sama.** Saat pindah ke server, hentikan bot di komputer lama (jika tidak: error `Conflict`).
- Perbarui dependensi sesekali: `python -m pip install -U -r requirements.txt`, lalu jalankan tes.

## Pengujian

```bash
python -m unittest discover -s tests -t . -v      # tanpa instalasi tambahan
python -m pip install -r requirements-dev.txt && pytest
python tests/benchmark.py                         # ukur kecepatan bagian-bagian bot
python doctor.py                                  # periksa kesiapan lingkungan
```

Tes tidak butuh internet atau token (Telegram disimulasikan). Tes yang membutuhkan PyMuPDF, LibreOffice, atau Tesseract otomatis dilewati jika programnya belum terpasang.

## Troubleshooting

| Gejala | Penyebab dan solusi |
|---|---|
| `getaddrinfo failed` / "Tidak bisa terhubung ke Telegram" | DNS atau internet bermasalah, VPN/proxy, atau jaringan memblokir Telegram. Jalankan `python doctor.py --online`; coba ganti DNS (8.8.8.8 / 1.1.1.1), buka `https://api.telegram.org` di browser, matikan/nyalakan VPN. Bot akan menunggu dan mencoba lagi sendiri |
| "Token ditolak Telegram" | `BOT_TOKEN` salah/sudah di-revoke. Salin ulang dari @BotFather, tanpa spasi atau tanda kutip |
| `Conflict: terminated by other getUpdates request` | Ada dua bot dengan token yang sama. Hentikan salah satunya |
| Tombol Word → PDF tidak muncul | LibreOffice tidak ditemukan saat start. Pasang atau isi `LIBREOFFICE_PATH`, lalu restart bot |
| Fitur Compress / PDF ↔ JPG / PDF → Word tidak muncul | PyMuPDF belum terpasang: `python -m pip install -r requirements.txt` |
| OCR salah baca teks Indonesia | Data bahasa `ind` belum terpasang di Tesseract (cek log start / `doctor.py`) |
| `Error opening data file` dari Tesseract | Isi variabel lingkungan `TESSDATA_PREFIX` dengan folder `tessdata` |
| "Dokumen berisi gambar/objek yang ditautkan dari luar" | Di Word: File → Info → Edit Links to Files → Break Link, lalu simpan ulang |
| "Masih ada proses yang berjalan" | Proses sebelumnya belum selesai. Tekan ❌ Cancel pada pesan prosesnya |
| "Proses terlalu lama" | Melebihi `PROCESS_TIMEOUT`. Naikkan nilainya atau pakai file lebih kecil |
| `ModuleNotFoundError` | Virtual environment belum aktif atau `pip install -r requirements.txt` belum dijalankan |

## Lisensi dan Pustaka Pihak Ketiga

Project ini belum menyertakan berkas lisensi; pilih sendiri sebelum dipublikasikan. Perhatikan lisensi pustaka yang dipakai: **PyMuPDF berlisensi AGPL-3.0** (atau lisensi komersial dari Artifex) — periksa kewajibannya sebelum menawarkan bot ini sebagai layanan publik. python-telegram-bot: LGPL-3.0; pypdf: BSD; Pillow: HPND; python-docx: MIT; LibreOffice: MPL-2.0; Tesseract: Apache-2.0.
