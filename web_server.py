"""
LK LOGINS STORE — Web API
Roda em paralelo com o bot Telegram, compartilha o mesmo SQLite DB.
"""
import os
import sys
import json
import sqlite3
import time
import hashlib
import secrets
import base64
import logging
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

import mercadopago

BRT = timezone(timedelta(hours=-3))

# ===== CONFIG =====
DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/app/data") if os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") else "."
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "lkstore.db"))
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "APP_USR-2507246895625254-100915-cba2bb9c86daed78244bcf5748f74642-1505061824")
WEB_PORT = int(os.environ.get("PORT", os.environ.get("WEB_PORT", 5000)))
ADMIN_IDS = [925542353]
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8701402389:AAGAj33V5dgLJp2JbP8QJUd9hXTSL2f0_TY")

sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("web")

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

# ===== SESSION STORE (in-memory, simple) =====
_sessions = {}  # token -> {"telegram_id": str, "username": str, "created": float}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def get_config(key):
    conn = get_db()
    row = conn.execute("SELECT value FROM bot_config WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else ''

def send_telegram_message(chat_id, text):
    """Send a Telegram message via Bot API (fire-and-forget for notifications)."""
    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        logger.warning(f"Telegram notify error: {e}")

# ===== AUTH =====
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        session = _sessions.get(token)
        if not session:
            return jsonify({"ok": False, "error": "Não autenticado"}), 401
        # Expire after 24h
        if time.time() - session["created"] > 86400:
            _sessions.pop(token, None)
            return jsonify({"ok": False, "error": "Sessão expirada"}), 401
        request.user = session
        return f(*args, **kwargs)
    return decorated

# ===== ROUTES =====

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/auth/login", methods=["POST"])
def login():
    """Login via Telegram username → lookup in DB."""
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower().lstrip("@")
    if not username:
        return jsonify({"ok": False, "error": "Informe seu @username do Telegram"})
    
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE LOWER(username) = ?", (username,)).fetchone()
    conn.close()
    
    if not user:
        return jsonify({"ok": False, "error": "Usuário não encontrado. Primeiro use o bot @LKLOGINSSTORE77_BOT no Telegram para se cadastrar."})
    
    if user["banned"]:
        return jsonify({"ok": False, "error": "Conta bloqueada. Entre em contato com o suporte."})
    
    token = secrets.token_urlsafe(32)
    _sessions[token] = {
        "telegram_id": user["telegram_id"],
        "username": username,
        "first_name": user["first_name"] or username,
        "created": time.time()
    }
    
    return jsonify({
        "ok": True,
        "token": token,
        "user": {
            "telegram_id": user["telegram_id"],
            "username": username,
            "first_name": user["first_name"] or username,
            "balance": user["balance"]
        }
    })

@app.route("/api/me")
@require_auth
def me():
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (request.user["telegram_id"],)).fetchone()
    conn.close()
    if not user:
        return jsonify({"ok": False, "error": "Usuário não encontrado"}), 404
    return jsonify({
        "ok": True,
        "user": {
            "telegram_id": user["telegram_id"],
            "username": user["username"],
            "first_name": user["first_name"],
            "balance": user["balance"]
        }
    })

@app.route("/api/products")
def products():
    conn = get_db()
    rows = conn.execute(
        "SELECT name, price, COUNT(*) as qty FROM products WHERE sold = 0 GROUP BY name, price ORDER BY name"
    ).fetchall()
    conn.close()
    return jsonify({
        "ok": True,
        "products": [{"name": r["name"], "price": r["price"], "qty": r["qty"]} for r in rows]
    })

@app.route("/api/buy", methods=["POST"])
@require_auth
def buy():
    data = request.get_json() or {}
    product_name = data.get("product_name", "")
    price = float(data.get("price", 0))
    qty = int(data.get("qty", 1))
    
    if qty < 1 or not product_name:
        return jsonify({"ok": False, "error": "Dados inválidos"})
    
    total = price * qty
    tid = request.user["telegram_id"]
    
    conn = get_db()
    # Check balance
    user = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (tid,)).fetchone()
    if not user or user["balance"] < total:
        conn.close()
        return jsonify({"ok": False, "error": f"Saldo insuficiente. Seu saldo: R${user['balance']:.2f}, Total: R${total:.2f}"})
    
    # Get items
    items = conn.execute(
        "SELECT id, credentials, validity, message FROM products WHERE name = ? AND price = ? AND sold = 0 LIMIT ?",
        (product_name, price, qty)
    ).fetchall()
    
    if len(items) < qty:
        conn.close()
        return jsonify({"ok": False, "error": f"Estoque insuficiente. Disponível: {len(items)}"})
    
    # Process sale
    delivered = []
    now_brt = datetime.now(BRT).strftime("%Y-%m-%d %H:%M:%S")
    for item in items:
        conn.execute("UPDATE products SET sold = 1, sold_to = ?, sold_at = datetime('now','localtime') WHERE id = ?",
                     (tid, item["id"]))
        conn.execute("INSERT INTO sales (telegram_id, product_name, product_id, price, credentials, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (tid, product_name, item["id"], price, item["credentials"], now_brt))
        delivered.append({"credentials": item["credentials"], "validity": item["validity"] or "30 DIAS"})
    
    conn.execute("UPDATE users SET balance = balance - ? WHERE telegram_id = ?", (total, tid))
    conn.execute("INSERT INTO transactions (telegram_id, type, amount, description) VALUES (?, 'debit', ?, ?)",
                 (tid, total, f"Compra Web: {qty}x {product_name}"))
    conn.commit()
    
    new_balance = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (tid,)).fetchone()["balance"]
    conn.close()
    
    # Notify admin via Telegram
    username = request.user.get("username", "?")
    creds_list = "\n".join([f"  {i+1}. {d['credentials']}" for i, d in enumerate(delivered)])
    for admin_id in ADMIN_IDS:
        send_telegram_message(admin_id,
            f"🌐 <b>VENDA WEB!</b>\n\n"
            f"👤 @{username} (<code>{tid}</code>)\n"
            f"📦 {product_name} x{qty}\n"
            f"💵 R${total:.2f}\n"
            f"💰 Saldo: R${new_balance:.2f}\n\n"
            f"🔑 Logins:\n{creds_list}")
    
    return jsonify({
        "ok": True,
        "items": delivered,
        "total": total,
        "balance": new_balance
    })

@app.route("/api/orders")
@require_auth
def orders():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM sales WHERE telegram_id = ? ORDER BY created_at DESC LIMIT 50",
        (request.user["telegram_id"],)
    ).fetchall()
    conn.close()
    return jsonify({
        "ok": True,
        "orders": [{"product_name": r["product_name"], "price": r["price"],
                     "credentials": r["credentials"], "created_at": r["created_at"]} for r in rows]
    })

@app.route("/api/pix/create", methods=["POST"])
@require_auth
def create_pix():
    data = request.get_json() or {}
    amount = float(data.get("amount", 0))
    
    pix_min = float(get_config('pix_min') or '1')
    pix_max = float(get_config('pix_max') or '500')
    
    if amount < pix_min:
        return jsonify({"ok": False, "error": f"Mínimo: R${pix_min:.0f}"})
    if amount > pix_max:
        return jsonify({"ok": False, "error": f"Máximo: R${pix_max:.0f}"})
    
    tid = request.user["telegram_id"]
    
    try:
        payment_data = {
            "transaction_amount": float(amount),
            "description": f"LK Store - Saldo R${amount:.2f}",
            "payment_method_id": "pix",
            "payer": {"email": f"user{tid}@lkstore.com"}
        }
        result = sdk.payment().create(payment_data)
        payment = result["response"]
        
        if payment.get("status") == "pending":
            pix_qr = payment["point_of_interaction"]["transaction_data"]["qr_code_base64"]
            pix_code = payment["point_of_interaction"]["transaction_data"]["qr_code"]
            mp_id = str(payment["id"])
            
            conn = get_db()
            conn.execute(
                "INSERT INTO payments (telegram_id, mp_payment_id, amount, pix_qr, pix_code) VALUES (?, ?, ?, ?, ?)",
                (tid, mp_id, amount, pix_qr, pix_code)
            )
            conn.commit()
            conn.close()
            
            # Notify admin
            for admin_id in ADMIN_IDS:
                send_telegram_message(admin_id,
                    f"🌐 <b>PIX WEB GERADO</b>\n\n"
                    f"👤 @{request.user.get('username','?')} (<code>{tid}</code>)\n"
                    f"💰 R${amount:.2f}\n"
                    f"🆔 <code>{mp_id}</code>")
            
            return jsonify({
                "ok": True,
                "mp_id": mp_id,
                "pix_qr": pix_qr,
                "pix_code": pix_code,
                "amount": amount
            })
        else:
            return jsonify({"ok": False, "error": "Erro ao gerar PIX"})
    except Exception as e:
        logger.error(f"PIX error: {e}")
        return jsonify({"ok": False, "error": "Erro ao gerar PIX. Tente novamente."})

@app.route("/api/pix/check/<mp_id>")
@require_auth
def check_pix(mp_id):
    try:
        result = sdk.payment().get(mp_id)
        payment = result["response"]
        
        if payment.get("status") == "approved":
            conn = get_db()
            p = conn.execute("SELECT * FROM payments WHERE mp_payment_id = ? AND status = 'pending'", (mp_id,)).fetchone()
            if p:
                conn.execute("UPDATE payments SET status = 'approved' WHERE mp_payment_id = ?", (mp_id,))
                conn.commit()
                
                credit_amount = p["amount"]
                bonus_text = ""
                
                # Saldo em dobro
                saldo_dobro = get_config('saldo_dobro') or '0'
                saldo_dobro_min = float(get_config('saldo_dobro_min') or '0')
                if saldo_dobro == '1' and credit_amount >= saldo_dobro_min:
                    credit_amount = credit_amount * 2
                    bonus_text = f" (dobro!)"
                
                # Porcentagem por recarga
                porcent = get_config('porcent_recarga') or '0'
                porcent_min = float(get_config('porcent_recarga_min') or '0')
                porcent_val = float(get_config('porcent_recarga_porcent') or '0')
                if porcent == '1' and credit_amount >= porcent_min and porcent_val > 0:
                    bonus = p["amount"] * (porcent_val / 100)
                    credit_amount += bonus
                    bonus_text += f" +{porcent_val:.0f}%"
                
                # Credit balance
                conn.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (credit_amount, p["telegram_id"]))
                conn.execute("INSERT INTO transactions (telegram_id, type, amount, description) VALUES (?, 'credit', ?, ?)",
                             (p["telegram_id"], credit_amount, f"PIX Web R${p['amount']:.2f}{bonus_text}"))
                conn.commit()
                
                balance = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (p["telegram_id"],)).fetchone()["balance"]
                conn.close()
                
                # Notify admin
                for admin_id in ADMIN_IDS:
                    send_telegram_message(admin_id,
                        f"💰 <b>PIX WEB PAGO!</b>\n\n"
                        f"👤 <code>{p['telegram_id']}</code>\n"
                        f"💵 R${p['amount']:.2f}{bonus_text}\n"
                        f"💎 Saldo: R${balance:.2f}")
                
                return jsonify({"ok": True, "status": "approved", "credited": credit_amount, "balance": balance})
            else:
                conn.close()
                # Already processed
                conn2 = get_db()
                balance = conn2.execute("SELECT balance FROM users WHERE telegram_id = ?", (request.user["telegram_id"],)).fetchone()["balance"]
                conn2.close()
                return jsonify({"ok": True, "status": "already_approved", "balance": balance})
        
        elif payment.get("status") == "pending":
            return jsonify({"ok": True, "status": "pending"})
        else:
            return jsonify({"ok": True, "status": payment.get("status", "unknown")})
    except Exception as e:
        logger.error(f"Check PIX error: {e}")
        return jsonify({"ok": False, "error": "Erro ao verificar pagamento"})

@app.route("/api/gift/redeem", methods=["POST"])
@require_auth
def redeem_gift():
    data = request.get_json() or {}
    code = (data.get("code") or "").strip().upper()
    if not code:
        return jsonify({"ok": False, "error": "Código obrigatório"})
    
    tid = request.user["telegram_id"]
    conn = get_db()
    gift = conn.execute("SELECT * FROM gifts WHERE code = ? AND redeemed_by IS NULL", (code,)).fetchone()
    
    if not gift:
        conn.close()
        return jsonify({"ok": False, "error": "Código inválido ou já resgatado"})
    
    conn.execute("UPDATE gifts SET redeemed_by = ?, redeemed_at = datetime('now','localtime') WHERE code = ?",
                 (tid, code))
    conn.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (gift["amount"], tid))
    conn.execute("INSERT INTO transactions (telegram_id, type, amount, description) VALUES (?, 'credit', ?, ?)",
                 (tid, gift["amount"], f"Gift Web: {code}"))
    conn.commit()
    
    balance = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (tid,)).fetchone()["balance"]
    conn.close()
    
    return jsonify({"ok": True, "amount": gift["amount"], "balance": balance})

# Catch-all for SPA
@app.route("/<path:path>")
def catch_all(path):
    return send_from_directory("static", "index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=WEB_PORT, debug=False)
