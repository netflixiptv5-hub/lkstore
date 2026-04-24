"""
Limpeza v2: apaga TODAS as vendas e reimporta só as 362 vendas reais do bot novo.
Roda UMA VEZ no start (marker file impede repetição).
"""
import os
import sqlite3
from datetime import datetime

DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", ".")
DB_PATH = os.path.join(DATA_DIR, "lkstore.db")
MARKER = os.path.join(DATA_DIR, ".cleanup_v2_done")

def run():
    print(f"[cleanup] DATA_DIR={DATA_DIR}")
    print(f"[cleanup] DB_PATH={DB_PATH}")
    print(f"[cleanup] MARKER={MARKER}")
    print(f"[cleanup] DB exists: {os.path.exists(DB_PATH)}")
    print(f"[cleanup] Marker exists: {os.path.exists(MARKER)}")

    if os.path.exists(MARKER):
        print("[cleanup] v2 já executado, pulando.")
        return

    # Procurar o arquivo de vendas
    script_dir = os.path.dirname(os.path.abspath(__file__))
    vendas_file = os.path.join(script_dir, "vendas_bot_novo_completo.txt")
    print(f"[cleanup] vendas_file={vendas_file}")
    print(f"[cleanup] vendas_file exists: {os.path.exists(vendas_file)}")

    if not os.path.exists(vendas_file):
        # Tentar /app/ diretamente
        vendas_file = "/app/vendas_bot_novo_completo.txt"
        print(f"[cleanup] tentando {vendas_file}: {os.path.exists(vendas_file)}")

    if not os.path.exists(vendas_file):
        print(f"[cleanup] ERRO: arquivo de vendas não encontrado!")
        return

    with open(vendas_file, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    print(f"[cleanup] Vendas reais a importar: {len(lines)}")

    if not os.path.exists(DB_PATH):
        print(f"[cleanup] ERRO: banco não existe em {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    old_count = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    print(f"[cleanup] Vendas no banco ANTES: {old_count}")

    # Limpar tudo
    conn.execute("DELETE FROM sales")
    conn.execute("DELETE FROM sqlite_sequence WHERE name='sales'")

    # Reimportar
    imported = 0
    for line in lines:
        parts = line.split("|")
        if len(parts) < 6:
            print(f"[cleanup] SKIP linha inválida: {line[:80]}")
            continue
        product_name = parts[0]
        try:
            price = float(parts[1])
        except:
            print(f"[cleanup] SKIP preço inválido: {line[:80]}")
            continue
        credentials = parts[2]
        telegram_id = parts[4]
        date_str = parts[5].strip()

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
    print(f"[cleanup] Vendas no banco DEPOIS: {new_count}")
    conn.close()

    with open(MARKER, "w") as f:
        f.write(f"v2 done. Before: {old_count}, After: {new_count}, Imported: {imported}")

    print("[cleanup] CONCLUÍDO!")

if __name__ == "__main__":
    run()
