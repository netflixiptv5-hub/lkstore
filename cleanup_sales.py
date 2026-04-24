"""
One-time cleanup: remove imported sales from 2026-04-07 00:20:13
Run on Railway: python cleanup_sales.py
"""
import os
import sqlite3

DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", ".")
DB_PATH = os.path.join(DATA_DIR, "lkstore.db")
MARKER = os.path.join(DATA_DIR, ".cleanup_sales_done")

def run():
    if os.path.exists(MARKER):
        print("Cleanup already done, skipping.")
        return
    
    conn = sqlite3.connect(DB_PATH)
    
    # Count before
    total_before = conn.execute("SELECT count(*) FROM sales").fetchone()[0]
    mass_count = conn.execute("SELECT count(*) FROM sales WHERE created_at = '2026-04-07 00:20:13'").fetchone()[0]
    
    print(f"Total vendas antes: {total_before}")
    print(f"Vendas de import (2026-04-07 00:20:13): {mass_count}")
    
    if mass_count > 0:
        conn.execute("DELETE FROM sales WHERE created_at = '2026-04-07 00:20:13'")
        conn.commit()
        print(f"DELETADAS {mass_count} vendas de import!")
    
    total_after = conn.execute("SELECT count(*) FROM sales").fetchone()[0]
    print(f"Total vendas depois: {total_after}")
    
    conn.close()
    
    with open(MARKER, 'w') as f:
        f.write(f"Cleaned {mass_count} mass-imported sales. Before: {total_before}, After: {total_after}")
    
    print("Done!")

if __name__ == "__main__":
    run()
