#!/bin/bash
# Restore seed DB to volume if not already present
DATA_DIR="${RAILWAY_VOLUME_MOUNT_PATH:-/app/data}"
mkdir -p "$DATA_DIR"

if [ ! -f "$DATA_DIR/lkstore.db" ]; then
    echo "[start.sh] lkstore.db not found, seeding from backup..."
    cp /app/seed.db "$DATA_DIR/lkstore.db"
    echo "[start.sh] Seed complete."
else
    echo "[start.sh] lkstore.db already exists, skipping seed."
fi

# One-time cleanup v2: reimporta só vendas reais do bot novo
python cleanup_sales.py || true

exec python bot.py
