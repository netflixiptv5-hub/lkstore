#!/bin/bash
DATA_DIR="${RAILWAY_VOLUME_MOUNT_PATH:-/app/data}"
mkdir -p "$DATA_DIR"

if [ ! -f "$DATA_DIR/lkstore.db" ]; then
    echo "[start.sh] lkstore.db not found, seeding from backup..."
    cp /app/seed.db "$DATA_DIR/lkstore.db"
    echo "[start.sh] Seed complete."
else
    echo "[start.sh] lkstore.db already exists, skipping seed."
fi

# Start web server in background
echo "[start.sh] Starting web server on port ${WEB_PORT:-5000}..."
python web_server.py &

# Start bot (foreground)
echo "[start.sh] Starting Telegram bot..."
exec python bot.py
