"""One-time sales history import script - run on Railway"""
import os
import sqlite3
import urllib.request
from datetime import datetime

DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", ".")
DB_PATH = os.path.join(DATA_DIR, "lkstore.db")
SALES_URL = "https://storage.googleapis.com/runable-templates/cli-uploads%2FjyTUNKTaQ3xTfEXENUeaM9y9QcPvbbL0%2FoqTlFSCX9dJHKn9SeZdVW%2Fvendas_2026.txt"
MARKER = os.path.join(DATA_DIR, ".sales_imported_2026")

def run():
    if os.path.exists(MARKER):
        print("Sales 2026 already imported, skipping.")
        return
    
    print("Downloading sales 2026...")
    data = urllib.request.urlopen(SALES_URL).read().decode('utf-8', errors='ignore')
    lines = [l.strip() for l in data.split('\n') if l.strip()]
    print(f"Found {len(lines)} lines")
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id TEXT NOT NULL,
        product_name TEXT NOT NULL,
        product_id INTEGER,
        price REAL NOT NULL,
        credentials TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    
    imported = 0
    errors = 0
    for line in lines:
        try:
            parts = line.split('|')
            if len(parts) < 4:
                errors += 1
                continue
            product_name = parts[0].strip()
            price = float(parts[1].strip())
            email = parts[2].strip()
            senha = parts[3].strip()
            credentials = f"{email}:{senha}"
            telegram_id = parts[4].strip() if len(parts) > 4 else "0"
            date_str = parts[5].strip() if len(parts) > 5 else None
            created_at = None
            if date_str:
                for fmt in ["%d/%m/%Y - %H:%M", "%d/%m/%Y"]:
                    try:
                        dt = datetime.strptime(date_str.strip(), fmt)
                        created_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                        break
                    except:
                        pass
            if created_at:
                conn.execute(
                    "INSERT INTO sales (telegram_id, product_name, product_id, price, credentials, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (telegram_id, product_name, 0, price, credentials, created_at)
                )
            else:
                conn.execute(
                    "INSERT INTO sales (telegram_id, product_name, product_id, price, credentials) VALUES (?, ?, ?, ?, ?)",
                    (telegram_id, product_name, 0, price, credentials)
                )
            imported += 1
        except Exception as e:
            errors += 1
    
    conn.commit()
    conn.close()
    
    with open(MARKER, 'w') as f:
        f.write(f"Imported {imported} sales, {errors} errors")
    
    print(f"Done! {imported} imported, {errors} errors")

if __name__ == "__main__":
    run()
