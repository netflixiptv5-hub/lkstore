"""One-time users+balance import from old bot - run on Railway"""
import os
import sqlite3
import urllib.request

DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", ".")
DB_PATH = os.path.join(DATA_DIR, "lkstore.db")
USERS_URL = "https://storage.googleapis.com/runable-templates/cli-uploads%2FjyTUNKTaQ3xTfEXENUeaM9y9QcPvbbL0%2FxPQQ74ek5W3IrY29gBhFo%2Fprompt_nFkk-L.txt"
MARKER = os.path.join(DATA_DIR, ".users_imported")

def run():
    if os.path.exists(MARKER):
        print("Users already imported, skipping.")
        return
    
    print("Downloading users data...")
    data = urllib.request.urlopen(USERS_URL).read().decode('utf-8', errors='ignore')
    lines = [l.strip() for l in data.split('\n') if l.strip()]
    print(f"Found {len(lines)} users")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Ensure users table exists
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id TEXT UNIQUE NOT NULL,
        username TEXT,
        first_name TEXT,
        balance REAL DEFAULT 0,
        blocked INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    
    imported = 0
    updated = 0
    errors = 0
    
    for line in lines:
        try:
            parts = line.split('|')
            if len(parts) < 3:
                errors += 1
                continue
            
            telegram_id = parts[0].strip()
            balance = float(parts[2].strip())
            
            if not telegram_id:
                errors += 1
                continue
            
            # Skip absurd balances (test accounts)
            if balance > 1000000:
                print(f"Skipping test account {telegram_id} with balance {balance}")
                continue
            
            # Check if user already exists
            existing = conn.execute("SELECT telegram_id, balance FROM users WHERE telegram_id = ?", 
                                   (telegram_id,)).fetchone()
            
            if existing:
                # Update balance if old balance is higher
                if balance > 0 and balance > existing['balance']:
                    conn.execute("UPDATE users SET balance = ? WHERE telegram_id = ?",
                                (balance, telegram_id))
                    updated += 1
            else:
                # Insert new user
                conn.execute(
                    "INSERT INTO users (telegram_id, username, first_name, balance) VALUES (?, ?, ?, ?)",
                    (telegram_id, None, None, balance)
                )
                imported += 1
                
        except Exception as e:
            errors += 1
    
    conn.commit()
    conn.close()
    
    with open(MARKER, 'w') as f:
        f.write(f"Imported {imported} new, updated {updated}, {errors} errors")
    
    print(f"Done! {imported} new users, {updated} updated, {errors} errors")

if __name__ == "__main__":
    run()
