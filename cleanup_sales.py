"""
Limpeza total: apaga TODAS as vendas e reimporta só as 362 vendas reais do bot novo.
Roda UMA VEZ no start (marker file impede repetição).
"""
import os
import sqlite3
from datetime import datetime

DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", ".")
DB_PATH = os.path.join(DATA_DIR, "lkstore.db")
MARKER = os.path.join(DATA_DIR, ".cleanup_v2_done")

def run():
    if os.path.exists(MARKER):
        print("[cleanup] v2 já executado, pulando.")
        return

    vendas_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendas_bot_novo_completo.txt")
    if not os.path.exists(vendas_file):
        print(f"[cleanup] ERRO: {vendas_file} não encontrado!")
        return

    with open(vendas_file, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    print(f"[cleanup] Vendas reais a importar: {len(lines)}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    old_count = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    print(f"[cleanup] Vendas no banco antes: {old_count}")

    # Limpar tudo
    conn.execute("DELETE FROM sales")
    conn.execute("DELETE FROM sqlite_sequence WHERE name='sales'")

    # Reimportar
    imported = 0
    for line in lines:
        parts = line.split("|")
        # Formato: PRODUTO|PREÇO|CREDENCIAIS||TELEGRAM_ID|DATA
        if len(parts) < 6:
            continue
        product_name = parts[0]
        try:
            price = float(parts[1])
        except:
            continue
        credentials = parts[2]
        # parts[3] é vazio (entre || )
        telegram_id = parts[4]
        date_str = parts[5].strip()

        # Converter DD/MM/YYYY - HH:MM para YYYY-MM-DD HH:MM:SS
        try:
            dt = datetime.strptime(date_str, "%d/%m/%Y - %H:%M")
            created_at = dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            created_at = date_str

        conn.execute(
            "INSERT INTO sales (telegram_id, product_name, product_id, price, credentials, created_at) VALUES (?, ?, 0, ?, ?, ?)",
            (telegram_id, product_name, price, credentials, created_at)
        )
        imported += 1

    conn.commit()
    new_count = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    print(f"[cleanup] Importadas: {imported}")
    print(f"[cleanup] Vendas no banco depois: {new_count}")
    conn.close()

    with open(MARKER, "w") as f:
        f.write(f"v2 cleanup done. Before: {old_count}, After: {new_count}, Imported: {imported}")

    print("[cleanup] CONCLUÍDO!")

if __name__ == "__main__":
    run()
