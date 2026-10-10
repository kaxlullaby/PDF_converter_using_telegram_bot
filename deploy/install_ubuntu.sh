#!/usr/bin/env bash
# Memasang Document Bot sebagai layanan di Ubuntu/Debian.
#
#   sudo bash deploy/install_ubuntu.sh
#   DRY_RUN=1 bash deploy/install_ubuntu.sh      # hanya menampilkan langkah, tanpa menjalankan
#
# Bisa dijalankan ulang (aman): file .env yang sudah ada tidak ditimpa.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/telegram-document-bot}"
SERVICE_USER="${SERVICE_USER:-docbot}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN="${DRY_RUN:-0}"

run() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}
info() { printf '\n==> %s\n' "$*"; }

if [ "$DRY_RUN" != "1" ] && [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "Jalankan dengan sudo:  sudo bash deploy/install_ubuntu.sh" >&2
  exit 1
fi

info "1/6 Memasang paket sistem (Python, LibreOffice, Tesseract, font)"
run apt-get update
run apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip rsync \
  libreoffice-writer tesseract-ocr tesseract-ocr-eng tesseract-ocr-ind \
  fonts-liberation fonts-crosextra-carlito fonts-dejavu-core

info "2/6 Menyiapkan user layanan '$SERVICE_USER'"
if [ "$DRY_RUN" = "1" ] || ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  run useradd --system --home-dir "$APP_DIR" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
else
  echo "user $SERVICE_USER sudah ada"
fi

info "3/6 Menyalin project ke $APP_DIR (.env, .venv, temp, data, logs tidak ditimpa)"
run mkdir -p "$APP_DIR"
run rsync -a --delete \
  --exclude '.env' --exclude '.venv' --exclude 'temp/' --exclude 'data/' --exclude 'logs/' \
  --exclude '__pycache__/' --exclude '.git/' \
  "$SOURCE_DIR/" "$APP_DIR/"
run mkdir -p "$APP_DIR/temp" "$APP_DIR/data" "$APP_DIR/logs"
run chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

info "4/6 Membuat virtual environment dan memasang paket Python"
run sudo -u "$SERVICE_USER" python3 -m venv "$APP_DIR/.venv"
run sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
run sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

info "5/6 Menyiapkan .env"
NEED_TOKEN=0
if [ "$DRY_RUN" = "1" ] || [ ! -f "$APP_DIR/.env" ]; then
  run cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  run chown "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/.env"
  run chmod 600 "$APP_DIR/.env"
fi
if [ "$DRY_RUN" = "1" ] || grep -q 'your_telegram_bot_token' "$APP_DIR/.env"; then
  NEED_TOKEN=1
fi

info "6/6 Memasang layanan systemd"
UNIT_TMP="$(mktemp)"
sed -e "s#/opt/telegram-document-bot#$APP_DIR#g" \
    -e "s#^User=.*#User=$SERVICE_USER#" -e "s#^Group=.*#Group=$SERVICE_USER#" \
    "$SOURCE_DIR/deploy/docbot.service" > "$UNIT_TMP"
run install -m 644 "$UNIT_TMP" /etc/systemd/system/docbot.service
rm -f "$UNIT_TMP"
run systemctl daemon-reload

echo
echo "Pemeriksaan kesiapan:"
run sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/doctor.py" || true

if [ "$NEED_TOKEN" = "1" ]; then
  cat <<MSG

Pemasangan selesai, tetapi BOT_TOKEN belum diisi. Langkah berikutnya:
  1. sudo nano $APP_DIR/.env          (isi BOT_TOKEN dari @BotFather)
  2. sudo -u $SERVICE_USER $APP_DIR/.venv/bin/python $APP_DIR/doctor.py --online
  3. sudo systemctl enable --now docbot
MSG
else
  run systemctl enable --now docbot
  echo
  echo "Bot berjalan. Lihat log:  journalctl -u docbot -f"
fi
