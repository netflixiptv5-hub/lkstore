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

# FORÇAR cleanup v2 removendo todos markers antigos
rm -f "$DATA_DIR/.cleanup_sales_done"
rm -f "$DATA_DIR/.cleanup_v2_done"

# Limpeza v2: reimporta só vendas reais
python cleanup_sales.py 2>&1
echo "[start.sh] cleanup exit code: $?"

exec python bot.py
