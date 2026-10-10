# Document Bot dalam container. Bangun:  docker compose build   Jalankan:  docker compose up -d
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Program pendukung: LibreOffice (Word -> PDF), Tesseract (OCR), dan font umum
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer \
      tesseract-ocr tesseract-ocr-eng tesseract-ocr-ind \
      fonts-liberation fonts-crosextra-carlito fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# Jalankan sebagai user biasa, bukan root
RUN useradd --system --create-home --home-dir /home/docbot --shell /usr/sbin/nologin docbot

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN mkdir -p data logs temp && chown -R docbot:docbot /app
USER docbot

# Database dan log disimpan di volume agar tidak hilang saat container dibuat ulang
VOLUME ["/app/data", "/app/logs"]

CMD ["python", "bot.py"]
