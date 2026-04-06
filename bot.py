"""
LK STORE - Bot de Vendas Telegram
Sistema completo: saldo, PIX automático, estoque, gifts, spam, admin
"""
import os
import json
import sqlite3
import asyncio
import logging
import random
import string
import re
import time
from datetime import datetime, timedelta
from io import BytesIO

import mercadopago
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)
from telegram.constants import ParseMode

# ===== CONFIG =====
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8701402389:AAGAj33V5dgLJp2JbP8QJUd9hXTSL2f0_TY")
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "APP_USR-2507246895625254-100915-cba2bb9c86daed78244bcf5748f74642-1505061824")
ADMIN_IDS = [925542353]
SUPPORT_BOT = "https://t.me/SUPORTESLKLOGINSSTORE77_BOT?start=suporte"
DB_PATH = os.environ.get("DB_PATH", "lkstore.db")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

# ===== DATABASE =====
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            telegram_id TEXT UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0,
            banned INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            credentials TEXT NOT NULL,
            validity TEXT DEFAULT '30 DIAS',
            message TEXT DEFAULT '',
            added_by TEXT,
            added_at TEXT DEFAULT (datetime('now','localtime')),
            sold INTEGER DEFAULT 0,
            sold_to TEXT,
            sold_at TEXT
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT NOT NULL,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            description TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT NOT NULL,
            mp_payment_id TEXT,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            pix_qr TEXT,
            pix_code TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            amount REAL NOT NULL,
            created_by TEXT,
            redeemed_by TEXT,
            redeemed_at TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            product_id INTEGER,
            price REAL NOT NULL,
            credentials TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS bot_config (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS scheduled_spam (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_type TEXT,
            media_file_id TEXT,
            text TEXT,
            scheduled_times TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            status TEXT DEFAULT 'active'
        );
    """)
    # Default config
    defaults = {
        'welcome_text': '🌟 𝗦𝗘𝗝𝗔 𝗕𝗘𝗠 𝗩𝗜𝗡𝗗𝗢 𝗔 𝗟𝗞 𝗦𝗧𝗢𝗥𝗘 ⭐⭐⭐⭐⭐\n\n🔥 Logins Premium com entrega automática!\n💰 Carregue seu saldo via PIX e compre na hora!',
        'welcome_photo': '',
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()

def get_config(key):
    conn = get_db()
    row = conn.execute("SELECT value FROM bot_config WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else ''

def set_config(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def is_admin(user_id):
    return int(user_id) in ADMIN_IDS

def ensure_user(telegram_id, username=None, first_name=None):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (str(telegram_id),)).fetchone()
    if not user:
        conn.execute("INSERT INTO users (telegram_id, username, first_name) VALUES (?, ?, ?)",
                     (str(telegram_id), username, first_name))
        conn.commit()
    else:
        conn.execute("UPDATE users SET username = ?, first_name = ? WHERE telegram_id = ?",
                     (username, first_name, str(telegram_id)))
        conn.commit()
    conn.close()

def get_balance(telegram_id):
    conn = get_db()
    row = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (str(telegram_id),)).fetchone()
    conn.close()
    return row['balance'] if row else 0

def update_balance(telegram_id, amount, description=""):
    conn = get_db()
    conn.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (amount, str(telegram_id)))
    conn.execute("INSERT INTO transactions (telegram_id, type, amount, description) VALUES (?, ?, ?, ?)",
                 (str(telegram_id), 'credit' if amount > 0 else 'debit', abs(amount), description))
    conn.commit()
    conn.close()

def is_banned(telegram_id):
    conn = get_db()
    row = conn.execute("SELECT banned FROM users WHERE telegram_id = ?", (str(telegram_id),)).fetchone()
    conn.close()
    return row and row['banned'] == 1

# ===== MAIN MENU =====
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 COMPRAR", callback_data="buy"),
         InlineKeyboardButton("💰 SALDO", callback_data="balance")],
        [InlineKeyboardButton("📋 MEUS PEDIDOS", callback_data="orders"),
         InlineKeyboardButton("🎁 RESGATAR GIFT", callback_data="gift")],
        [InlineKeyboardButton("🆘 SUPORTE", url=SUPPORT_BOT)],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username, user.first_name)
    
    if is_banned(user.id):
        await update.message.reply_text("⛔ Você foi bloqueado. Entre em contato com o suporte.")
        return
    
    welcome_text = get_config('welcome_text')
    welcome_photo = get_config('welcome_photo')
    
    if welcome_photo:
        try:
            await update.message.reply_photo(
                photo=welcome_photo,
                caption=welcome_text,
                reply_markup=main_menu_keyboard(),
                parse_mode=ParseMode.HTML
            )
        except:
            await update.message.reply_text(welcome_text, reply_markup=main_menu_keyboard(), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(welcome_text, reply_markup=main_menu_keyboard(), parse_mode=ParseMode.HTML)

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if is_banned(query.from_user.id):
        await query.edit_message_text("⛔ Você foi bloqueado.")
        return
    
    if query.data == "main_menu":
        welcome_text = get_config('welcome_text')
        try:
            await query.edit_message_text(welcome_text, reply_markup=main_menu_keyboard(), parse_mode=ParseMode.HTML)
        except:
            await query.message.reply_text(welcome_text, reply_markup=main_menu_keyboard(), parse_mode=ParseMode.HTML)

# ===== COMPRAR =====
async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    conn = get_db()
    # Get available product categories with count and price
    products = conn.execute(
        "SELECT name, price, COUNT(*) as qty FROM products WHERE sold = 0 GROUP BY name, price ORDER BY name"
    ).fetchall()
    conn.close()
    
    if not products:
        await query.edit_message_text(
            "😔 Nenhum produto disponível no momento.\n\nVolte mais tarde!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar", callback_data="main_menu")]])
        )
        return
    
    text = "🛒 <b>PRODUTOS DISPONÍVEIS</b>\n\nEscolha o produto:\n"
    buttons = []
    for p in products:
        btn_text = f"{p['name']} - R${p['price']:.0f} ({p['qty']} un)"
        cb_data = f"product_{p['name']}_{p['price']}"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=cb_data[:64])])
    
    buttons.append([InlineKeyboardButton("🔙 Voltar", callback_data="main_menu")])
    
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)
    except:
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)

async def product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Parse product info from callback - format: product_NAME_PRICE
    data = query.data
    # Extract after "product_" and get the last part as price
    parts = data[8:]  # remove "product_"
    last_underscore = parts.rfind('_')
    product_name = parts[:last_underscore]
    price = float(parts[last_underscore+1:])
    
    context.user_data['buy_product'] = product_name
    context.user_data['buy_price'] = price
    
    conn = get_db()
    qty = conn.execute("SELECT COUNT(*) as c FROM products WHERE name = ? AND price = ? AND sold = 0",
                       (product_name, price)).fetchone()['c']
    conn.close()
    
    balance = get_balance(query.from_user.id)
    
    text = (f"📦 <b>{product_name}</b>\n"
            f"💲 Preço: R${price:.0f} por unidade\n"
            f"📊 Estoque: {qty} disponíveis\n"
            f"💰 Seu saldo: R${balance:.2f}\n\n"
            f"🔢 Digite a quantidade que deseja comprar:")
    
    context.user_data['awaiting_qty'] = True
    
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar", callback_data="buy")]]))
    except:
        await query.message.reply_text(text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar", callback_data="buy")]]))

async def handle_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_qty'):
        return
    
    try:
        qty = int(update.message.text.strip())
        if qty < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Digite um número válido!")
        return
    
    context.user_data['awaiting_qty'] = False
    product_name = context.user_data.get('buy_product')
    price = context.user_data.get('buy_price')
    total = price * qty
    balance = get_balance(update.effective_user.id)
    
    # Check stock
    conn = get_db()
    available = conn.execute("SELECT COUNT(*) as c FROM products WHERE name = ? AND price = ? AND sold = 0",
                             (product_name, price)).fetchone()['c']
    conn.close()
    
    if qty > available:
        await update.message.reply_text(
            f"❌ Estoque insuficiente!\n\nDisponível: {available}\nSolicitado: {qty}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar", callback_data="buy")]])
        )
        return
    
    if balance < total:
        falta = total - balance
        text = (f"❌ <b>Saldo insuficiente!</b>\n\n"
                f"💰 Seu saldo: R${balance:.2f}\n"
                f"💲 Total: R${total:.2f}\n"
                f"📉 Falta: R${falta:.2f}\n\n"
                f"💡 Carregue seu saldo usando /pix {falta:.0f}")
        
        # Generate mini GIF/instructions for adding balance
        text += (f"\n\n━━━━━━━━━━━━━━━━\n"
                 f"📲 <b>COMO CARREGAR SALDO:</b>\n\n"
                 f"1️⃣ Digite: /pix <i>valor</i>\n"
                 f"   Ex: <code>/pix {int(falta) + 1}</code>\n\n"
                 f"2️⃣ Escaneie o QR Code ou copie o código PIX\n\n"
                 f"3️⃣ Pague e o saldo cai na hora! ⚡\n"
                 f"━━━━━━━━━━━━━━━━")
        
        await update.message.reply_text(text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"💰 Carregar R${int(falta)+1}", callback_data=f"pix_{int(falta)+1}")],
                [InlineKeyboardButton("🔙 Voltar", callback_data="buy")]
            ]))
        return
    
    # Process purchase
    context.user_data['buy_qty'] = qty
    context.user_data['buy_total'] = total
    
    text = (f"🛒 <b>CONFIRMAR COMPRA</b>\n\n"
            f"📦 {product_name}\n"
            f"🔢 Quantidade: {qty}\n"
            f"💲 Total: R${total:.2f}\n"
            f"💰 Saldo atual: R${balance:.2f}\n"
            f"💰 Saldo após: R${balance - total:.2f}")
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ CONFIRMAR COMPRA", callback_data="confirm_buy")],
            [InlineKeyboardButton("❌ Cancelar", callback_data="buy")]
        ]))

async def confirm_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    product_name = context.user_data.get('buy_product')
    price = context.user_data.get('buy_price')
    qty = context.user_data.get('buy_qty', 1)
    total = context.user_data.get('buy_total', price)
    
    balance = get_balance(query.from_user.id)
    if balance < total:
        await query.edit_message_text("❌ Saldo insuficiente! Carregue com /pix")
        return
    
    conn = get_db()
    items = conn.execute(
        "SELECT id, credentials, validity, message FROM products WHERE name = ? AND price = ? AND sold = 0 LIMIT ?",
        (product_name, price, qty)
    ).fetchall()
    
    if len(items) < qty:
        conn.close()
        await query.edit_message_text("❌ Estoque esgotou! Tente novamente.")
        return
    
    # Mark as sold and debit balance
    delivered = []
    for item in items:
        conn.execute("UPDATE products SET sold = 1, sold_to = ?, sold_at = datetime('now','localtime') WHERE id = ?",
                     (user_id, item['id']))
        conn.execute("INSERT INTO sales (telegram_id, product_name, product_id, price, credentials) VALUES (?, ?, ?, ?, ?)",
                     (user_id, product_name, item['id'], price, item['credentials']))
        delivered.append(item)
    
    conn.execute("UPDATE users SET balance = balance - ? WHERE telegram_id = ?", (total, user_id))
    conn.execute("INSERT INTO transactions (telegram_id, type, amount, description) VALUES (?, 'debit', ?, ?)",
                 (user_id, total, f"Compra: {qty}x {product_name}"))
    conn.commit()
    conn.close()
    
    # Build delivery message
    validity = delivered[0]['validity'] if delivered[0]['validity'] else '30 DIAS'
    custom_msg = delivered[0]['message'] if delivered[0]['message'] else ''
    
    text = f"✅ <b>COMPRA REALIZADA COM SUCESSO!</b>\n\n"
    text += f"📦 <b>{product_name}</b>\n"
    text += f"🔢 Quantidade: {qty}\n"
    text += f"💲 Total: R${total:.2f}\n"
    text += f"⏱ Validade: {validity}\n\n"
    text += f"━━━━━━━━━━━━━━━━\n"
    text += f"🔑 <b>SEUS LOGINS:</b>\n\n"
    
    for i, item in enumerate(delivered, 1):
        creds = item['credentials'].strip()
        if qty > 1:
            text += f"<b>{i}.</b> <code>{creds}</code>\n"
        else:
            text += f"<code>{creds}</code>\n"
    
    text += f"\n━━━━━━━━━━━━━━━━\n"
    if custom_msg:
        text += f"\n{custom_msg}\n"
    
    new_balance = get_balance(query.from_user.id)
    text += f"\n💰 Saldo restante: R${new_balance:.2f}"
    
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]]))
    except:
        await query.message.reply_text(text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]]))

# ===== SALDO =====
async def balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    balance = get_balance(query.from_user.id)
    
    text = (f"💰 <b>SEU SALDO</b>\n\n"
            f"💎 R${balance:.2f}\n\n"
            f"📲 Para carregar, use:\n"
            f"<code>/pix VALOR</code>\n\n"
            f"Exemplo: <code>/pix 50</code>")
    
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Carregar R$10", callback_data="pix_10"),
             InlineKeyboardButton("💰 Carregar R$25", callback_data="pix_25")],
            [InlineKeyboardButton("💰 Carregar R$50", callback_data="pix_50"),
             InlineKeyboardButton("💰 Carregar R$100", callback_data="pix_100")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="main_menu")]
        ]))

# ===== PIX =====
async def pix_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username, user.first_name)
    
    if is_banned(user.id):
        await update.message.reply_text("⛔ Bloqueado.")
        return
    
    try:
        amount = float(context.args[0])
        if amount < 1:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Use: /pix VALOR\nExemplo: /pix 50\nMínimo: R$1")
        return
    
    await generate_pix(update, context, amount, user.id)

async def pix_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    amount = float(query.data.split('_')[1])
    await generate_pix(query, context, amount, query.from_user.id, is_callback=True)

async def generate_pix(update_or_query, context, amount, user_id, is_callback=False):
    """Generate PIX QR code via Mercado Pago"""
    try:
        payment_data = {
            "transaction_amount": float(amount),
            "description": f"LK Store - Saldo R${amount:.2f}",
            "payment_method_id": "pix",
            "payer": {
                "email": f"user{user_id}@lkstore.com"
            }
        }
        
        result = sdk.payment().create(payment_data)
        payment = result["response"]
        
        if payment.get("status") == "pending":
            pix_qr = payment["point_of_interaction"]["transaction_data"]["qr_code_base64"]
            pix_code = payment["point_of_interaction"]["transaction_data"]["qr_code"]
            mp_id = str(payment["id"])
            
            # Save payment
            conn = get_db()
            conn.execute(
                "INSERT INTO payments (telegram_id, mp_payment_id, amount, pix_qr, pix_code) VALUES (?, ?, ?, ?, ?)",
                (str(user_id), mp_id, amount, pix_qr, pix_code)
            )
            conn.commit()
            conn.close()
            
            text = (f"💳 <b>PIX - R${amount:.2f}</b>\n\n"
                    f"📱 Escaneie o QR Code ou copie o código abaixo:\n\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"<code>{pix_code}</code>\n"
                    f"━━━━━━━━━━━━━━━━\n\n"
                    f"⚡ O saldo será creditado automaticamente após o pagamento!\n"
                    f"⏱ Validade: 30 minutos")
            
            # Send QR code image
            import base64
            qr_bytes = base64.b64decode(pix_qr)
            
            if is_callback:
                await update_or_query.message.reply_photo(
                    photo=BytesIO(qr_bytes),
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Verificar Pagamento", callback_data=f"check_{mp_id}")],
                        [InlineKeyboardButton("🔙 Voltar", callback_data="main_menu")]
                    ])
                )
            else:
                await update_or_query.message.reply_photo(
                    photo=BytesIO(qr_bytes),
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Verificar Pagamento", callback_data=f"check_{mp_id}")],
                        [InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]
                    ])
                )
            
            # Start auto-check
            context.job_queue.run_repeating(
                check_payment_job,
                interval=5,
                first=5,
                data={"mp_id": mp_id, "user_id": str(user_id), "amount": amount, "chat_id": update_or_query.message.chat_id if is_callback else update_or_query.message.chat_id},
                name=f"payment_{mp_id}",
                job_kwargs={"misfire_grace_time": 60}
            )
            # Auto cancel after 30 min
            context.job_queue.run_once(
                cancel_payment_job, 1800,
                data={"mp_id": mp_id},
                name=f"cancel_{mp_id}"
            )
        else:
            msg = "❌ Erro ao gerar PIX. Tente novamente."
            if is_callback:
                await update_or_query.message.reply_text(msg)
            else:
                await update_or_query.message.reply_text(msg)
    except Exception as e:
        logger.error(f"PIX error: {e}")
        msg = "❌ Erro ao gerar PIX. Tente novamente mais tarde."
        if is_callback:
            await update_or_query.message.reply_text(msg)
        else:
            await update_or_query.message.reply_text(msg)

async def check_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    mp_id = query.data.replace("check_", "")
    
    try:
        result = sdk.payment().get(mp_id)
        payment = result["response"]
        
        if payment.get("status") == "approved":
            conn = get_db()
            p = conn.execute("SELECT * FROM payments WHERE mp_payment_id = ?", (mp_id,)).fetchone()
            if p and p['status'] == 'pending':
                conn.execute("UPDATE payments SET status = 'approved' WHERE mp_payment_id = ?", (mp_id,))
                conn.commit()
                update_balance(p['telegram_id'], p['amount'], f"PIX R${p['amount']:.2f}")
                balance = get_balance(p['telegram_id'])
                conn.close()
                
                await query.answer("✅ Pagamento confirmado!", show_alert=True)
                await query.message.reply_text(
                    f"✅ <b>PAGAMENTO CONFIRMADO!</b>\n\n"
                    f"💰 +R${p['amount']:.2f} creditado\n"
                    f"💎 Saldo atual: R${balance:.2f}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Comprar", callback_data="buy"), InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]])
                )
                # Remove job
                jobs = context.job_queue.get_jobs_by_name(f"payment_{mp_id}")
                for job in jobs:
                    job.schedule_removal()
            else:
                conn.close()
                await query.answer("✅ Já foi creditado!", show_alert=True)
        elif payment.get("status") == "pending":
            await query.answer("⏳ Aguardando pagamento...", show_alert=True)
        else:
            await query.answer(f"❌ Status: {payment.get('status')}", show_alert=True)
    except Exception as e:
        await query.answer("❌ Erro ao verificar. Tente novamente.", show_alert=True)

async def check_payment_job(context: ContextTypes.DEFAULT_TYPE):
    """Auto-check payment status"""
    data = context.job.data
    mp_id = data['mp_id']
    
    try:
        result = sdk.payment().get(mp_id)
        payment = result["response"]
        
        if payment.get("status") == "approved":
            conn = get_db()
            p = conn.execute("SELECT * FROM payments WHERE mp_payment_id = ? AND status = 'pending'", (mp_id,)).fetchone()
            if p:
                conn.execute("UPDATE payments SET status = 'approved' WHERE mp_payment_id = ?", (mp_id,))
                conn.commit()
                update_balance(p['telegram_id'], p['amount'], f"PIX R${p['amount']:.2f}")
                balance = get_balance(p['telegram_id'])
                conn.close()
                
                await context.bot.send_message(
                    chat_id=data['chat_id'],
                    text=f"✅ <b>PAGAMENTO CONFIRMADO!</b>\n\n"
                         f"💰 +R${data['amount']:.2f} creditado\n"
                         f"💎 Saldo atual: R${balance:.2f}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Comprar", callback_data="buy"), InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]])
                )
            else:
                conn.close()
            
            # Remove repeating job
            context.job.schedule_removal()
    except:
        pass

async def cancel_payment_job(context: ContextTypes.DEFAULT_TYPE):
    """Cancel payment after timeout"""
    mp_id = context.job.data['mp_id']
    conn = get_db()
    conn.execute("UPDATE payments SET status = 'expired' WHERE mp_payment_id = ? AND status = 'pending'", (mp_id,))
    conn.commit()
    conn.close()
    # Remove check job
    jobs = context.job_queue.get_jobs_by_name(f"payment_{mp_id}")
    for job in jobs:
        job.schedule_removal()

# ===== MEUS PEDIDOS =====
async def orders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    conn = get_db()
    orders = conn.execute(
        "SELECT * FROM sales WHERE telegram_id = ? ORDER BY created_at DESC LIMIT 20",
        (str(query.from_user.id),)
    ).fetchall()
    conn.close()
    
    if not orders:
        await query.edit_message_text(
            "📋 Nenhum pedido ainda.\n\nFaça sua primeira compra! 🛒",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 Comprar", callback_data="buy")],
                [InlineKeyboardButton("🔙 Voltar", callback_data="main_menu")]
            ])
        )
        return
    
    text = "📋 <b>SEUS PEDIDOS</b>\n\n"
    for o in orders:
        text += f"📦 {o['product_name']} - R${o['price']:.0f}\n"
        text += f"   🔑 <code>{o['credentials']}</code>\n"
        text += f"   📅 {o['created_at']}\n\n"
    
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar", callback_data="main_menu")]]))

# ===== GIFT =====
async def gift_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['awaiting_gift'] = True
    
    await query.edit_message_text(
        "🎁 <b>RESGATAR GIFT</b>\n\nDigite o código do gift:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar", callback_data="main_menu")]])
    )

async def handle_gift_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_gift'):
        return
    
    context.user_data['awaiting_gift'] = False
    code = update.message.text.strip().upper()
    
    conn = get_db()
    gift = conn.execute("SELECT * FROM gifts WHERE code = ? AND redeemed_by IS NULL", (code,)).fetchone()
    
    if not gift:
        conn.close()
        await update.message.reply_text("❌ Código inválido ou já resgatado!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]]))
        return
    
    conn.execute("UPDATE gifts SET redeemed_by = ?, redeemed_at = datetime('now','localtime') WHERE code = ?",
                 (str(update.effective_user.id), code))
    conn.commit()
    conn.close()
    
    update_balance(update.effective_user.id, gift['amount'], f"Gift: {code}")
    balance = get_balance(update.effective_user.id)
    
    await update.message.reply_text(
        f"🎁 <b>GIFT RESGATADO!</b>\n\n"
        f"💰 +R${gift['amount']:.2f}\n"
        f"💎 Saldo: R${balance:.2f}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Comprar", callback_data="buy"), InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]])
    )

# ===== ADMIN COMMANDS =====

# /addlogin PRODUTO===CATEGORIA===PRECO===email senha===...===VALIDADE===MSG
async def addlogin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    text = update.message.text
    # Remove /addlogin or /addlogintelas
    if text.startswith('/addlogintelas'):
        text = text[14:].strip()
    elif text.startswith('/addlogin'):
        text = text[9:].strip()
    
    parts = text.split('===')
    if len(parts) < 4:
        await update.message.reply_text(
            "❌ Formato: /addlogin PRODUTO===CATEGORIA===PREÇO===email1 senha1===email2 senha2===...===VALIDADE===MENSAGEM\n\n"
            "Exemplo:\n/addlogin TELA EXTRA===TELA EXTRA===10===email@test.com 123===30 DIAS===Aproveite!"
        )
        return
    
    product_name = parts[0].strip()
    # parts[1] = categoria (pode ignorar, usamos product_name)
    price = float(parts[2].strip())
    
    # Find validity and message - they're the last 2 parts
    # Credentials are everything between index 3 and the last 2
    validity = "30 DIAS"
    message = ""
    
    # The last part is message, second-to-last is validity
    # But credentials can have multiple === separated entries
    # Try to detect: if last part doesn't look like email, it's the message
    # If second-to-last doesn't look like email, it's validity
    
    cred_parts = parts[3:]
    
    # Check if last part is a message (contains non-email text)
    if len(cred_parts) >= 2 and not '@' in cred_parts[-1]:
        message = cred_parts[-1].strip()
        cred_parts = cred_parts[:-1]
    
    if len(cred_parts) >= 2 and not '@' in cred_parts[-1]:
        validity = cred_parts[-1].strip()
        cred_parts = cred_parts[:-1]
    
    added = 0
    for cred in cred_parts:
        cred = cred.strip()
        if not cred:
            continue
        # Clean up credentials
        cred = re.sub(r'✉️\s*Email:\s*', '', cred)
        cred = cred.strip()
        if cred:
            conn = get_db()
            conn.execute(
                "INSERT INTO products (name, price, credentials, validity, message, added_by) VALUES (?, ?, ?, ?, ?, ?)",
                (product_name, price, cred, validity, message, str(update.effective_user.id))
            )
            conn.commit()
            conn.close()
            added += 1
    
    await update.message.reply_text(
        f"✅ {added} login(s) adicionado(s)!\n\n"
        f"📦 {product_name}\n"
        f"💲 R${price:.0f}\n"
        f"⏱ {validity}"
    )

async def estoque(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    conn = get_db()
    products = conn.execute(
        "SELECT name, price, COUNT(*) as qty FROM products WHERE sold = 0 GROUP BY name, price ORDER BY name"
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) as c FROM products WHERE sold = 0").fetchone()['c']
    conn.close()
    
    text = f"📊 <b>ESTOQUE ({total} total)</b>\n\n"
    for p in products:
        text += f"📦 {p['name']} - R${p['price']:.0f} → {p['qty']} un\n"
    
    if not products:
        text += "Vazio! Use /addlogin para adicionar."
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def removelogin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if not context.args:
        # Show products to remove
        conn = get_db()
        products = conn.execute(
            "SELECT name, price, COUNT(*) as qty FROM products WHERE sold = 0 GROUP BY name, price"
        ).fetchall()
        conn.close()
        
        text = "🗑 <b>REMOVER LOGINS</b>\n\nUse:\n"
        text += "/removelogin NOME DO PRODUTO - remove todos\n"
        text += "/removelogin id:123 - remove por ID\n\n"
        for p in products:
            text += f"📦 {p['name']} ({p['qty']} un)\n"
        
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return
    
    arg = ' '.join(context.args)
    conn = get_db()
    
    if arg.startswith('id:'):
        pid = int(arg[3:])
        conn.execute("DELETE FROM products WHERE id = ? AND sold = 0", (pid,))
        removed = conn.total_changes
    else:
        cursor = conn.execute("DELETE FROM products WHERE name = ? AND sold = 0", (arg,))
        removed = cursor.rowcount
    
    conn.commit()
    conn.close()
    
    await update.message.reply_text(f"✅ {removed} login(s) removido(s)!")

# /ban USER_ID
async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if not context.args:
        await update.message.reply_text("Use: /ban ID_TELEGRAM")
        return
    
    uid = context.args[0]
    conn = get_db()
    conn.execute("UPDATE users SET banned = 1 WHERE telegram_id = ?", (uid,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"⛔ Usuário {uid} bloqueado!")

# /unban USER_ID
async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    uid = context.args[0]
    conn = get_db()
    conn.execute("UPDATE users SET banned = 0 WHERE telegram_id = ?", (uid,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Usuário {uid} desbloqueado!")

# /addsaldo USER_ID VALOR
async def addsaldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Use: /addsaldo ID_TELEGRAM VALOR")
        return
    
    uid = context.args[0]
    amount = float(context.args[1])
    update_balance(uid, amount, "Admin: saldo adicionado")
    balance = get_balance(uid)
    await update.message.reply_text(f"✅ +R${amount:.2f} para {uid}\n💎 Saldo: R${balance:.2f}")

# /removesaldo USER_ID VALOR
async def removesaldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Use: /removesaldo ID_TELEGRAM VALOR")
        return
    
    uid = context.args[0]
    amount = float(context.args[1])
    update_balance(uid, -amount, "Admin: saldo removido")
    balance = get_balance(uid)
    await update.message.reply_text(f"✅ -R${amount:.2f} de {uid}\n💎 Saldo: R${balance:.2f}")

# /gift VALOR
async def create_gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if not context.args:
        await update.message.reply_text("Use: /gift VALOR\nExemplo: /gift 50")
        return
    
    amount = float(context.args[0])
    code = 'LK' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    
    conn = get_db()
    conn.execute("INSERT INTO gifts (code, amount, created_by) VALUES (?, ?, ?)",
                 (code, amount, str(update.effective_user.id)))
    conn.commit()
    conn.close()
    
    await update.message.reply_text(
        f"🎁 <b>GIFT CRIADO!</b>\n\n"
        f"💰 Valor: R${amount:.2f}\n"
        f"🔑 Código: <code>{code}</code>\n\n"
        f"📲 Envie isso pro cliente:\n\n"
        f"<code>/resgatar {code}</code>",
        parse_mode=ParseMode.HTML
    )

# /historico
async def historico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    conn = get_db()
    sales = conn.execute(
        "SELECT s.*, u.username, u.first_name FROM sales s LEFT JOIN users u ON s.telegram_id = u.telegram_id ORDER BY s.created_at DESC LIMIT 50"
    ).fetchall()
    conn.close()
    
    if not sales:
        await update.message.reply_text("📭 Nenhuma venda ainda.")
        return
    
    text = "📊 <b>HISTÓRICO DE VENDAS (últimas 50)</b>\n\n"
    for s in sales:
        name = s['first_name'] or s['username'] or 'N/A'
        text += (f"📦 {s['product_name']} - R${s['price']:.0f}\n"
                f"   👤 {name} (ID: {s['telegram_id']})\n"
                f"   📅 {s['created_at']}\n\n")
    
    # Split if too long
    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await update.message.reply_text(text[i:i+4000], parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# /stats
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()['c']
    total_sales = conn.execute("SELECT COUNT(*) as c FROM sales").fetchone()['c']
    total_revenue = conn.execute("SELECT COALESCE(SUM(price), 0) as s FROM sales").fetchone()['s']
    total_stock = conn.execute("SELECT COUNT(*) as c FROM products WHERE sold = 0").fetchone()['c']
    total_balance = conn.execute("SELECT COALESCE(SUM(balance), 0) as s FROM users").fetchone()['s']
    today_sales = conn.execute(
        "SELECT COUNT(*) as c, COALESCE(SUM(price), 0) as s FROM sales WHERE date(created_at) = date('now','localtime')"
    ).fetchone()
    conn.close()
    
    text = (f"📊 <b>ESTATÍSTICAS</b>\n\n"
            f"👥 Usuários: {total_users}\n"
            f"🛒 Vendas total: {total_sales}\n"
            f"💰 Receita total: R${total_revenue:.2f}\n"
            f"📦 Estoque: {total_stock}\n"
            f"💎 Saldo total users: R${total_balance:.2f}\n\n"
            f"📅 <b>HOJE:</b>\n"
            f"🛒 Vendas: {today_sales['c']}\n"
            f"💰 Receita: R${today_sales['s']:.2f}")
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# /setwelcome TEXT
async def setwelcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    text = update.message.text.replace('/setwelcome', '', 1).strip()
    if not text:
        await update.message.reply_text("Use: /setwelcome TEXTO DE BOAS VINDAS")
        return
    
    set_config('welcome_text', text)
    await update.message.reply_text("✅ Texto de boas-vindas atualizado!")

# /setphoto - reply to a photo
async def setphoto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if update.message.reply_to_message and update.message.reply_to_message.photo:
        file_id = update.message.reply_to_message.photo[-1].file_id
        set_config('welcome_photo', file_id)
        await update.message.reply_text("✅ Foto de boas-vindas atualizada!")
    else:
        await update.message.reply_text("❌ Responda a uma foto com /setphoto")

# ===== SPAM =====
async def spam_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    context.user_data['spam_step'] = 'media'
    context.user_data['spam_media_type'] = None
    context.user_data['spam_media_id'] = None
    context.user_data['spam_text'] = None
    
    await update.message.reply_text(
        "📢 <b>CRIAR SPAM</b>\n\n"
        "Envie o conteúdo do spam:\n"
        "📸 Foto\n"
        "🎥 Vídeo\n"
        "📝 Texto\n"
        "📸+📝 Foto/Vídeo com legenda\n\n"
        "Ou /cancelar para sair",
        parse_mode=ParseMode.HTML
    )

async def handle_spam_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('spam_step') != 'media':
        return False
    
    msg = update.message
    
    if msg.photo:
        context.user_data['spam_media_type'] = 'photo'
        context.user_data['spam_media_id'] = msg.photo[-1].file_id
        context.user_data['spam_text'] = msg.caption or ''
    elif msg.video:
        context.user_data['spam_media_type'] = 'video'
        context.user_data['spam_media_id'] = msg.video.file_id
        context.user_data['spam_text'] = msg.caption or ''
    elif msg.text and not msg.text.startswith('/'):
        context.user_data['spam_media_type'] = 'text'
        context.user_data['spam_text'] = msg.text
    else:
        return False
    
    context.user_data['spam_step'] = 'schedule'
    
    await msg.reply_text(
        "✅ Conteúdo recebido!\n\nAgora escolha:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Enviar Agora", callback_data="spam_now")],
            [InlineKeyboardButton("⏰ Programar Horários", callback_data="spam_schedule")],
            [InlineKeyboardButton("❌ Cancelar", callback_data="spam_cancel")]
        ])
    )
    return True

async def spam_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "spam_now":
        await query.edit_message_text("🚀 Enviando spam...")
        sent, failed = await send_spam(context, 
            context.user_data.get('spam_media_type'),
            context.user_data.get('spam_media_id'),
            context.user_data.get('spam_text'))
        
        context.user_data['spam_step'] = None
        await query.message.reply_text(f"✅ Spam enviado!\n\n📨 Enviados: {sent}\n❌ Falhas: {failed}")
    
    elif query.data == "spam_schedule":
        context.user_data['spam_step'] = 'times'
        await query.edit_message_text(
            "⏰ <b>PROGRAMAR SPAM</b>\n\n"
            "Digite os horários separados por espaço:\n"
            "Exemplo: <code>12:30 14:00 18:30 21:00</code>\n\n"
            "O spam será enviado hoje nesses horários.",
            parse_mode=ParseMode.HTML
        )
    
    elif query.data == "spam_cancel":
        context.user_data['spam_step'] = None
        await query.edit_message_text("❌ Spam cancelado.")

async def handle_spam_times(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('spam_step') != 'times':
        return False
    
    times_text = update.message.text.strip()
    times = re.findall(r'\d{1,2}:\d{2}', times_text)
    
    if not times:
        await update.message.reply_text("❌ Formato inválido! Use: 12:30 14:00 18:30")
        return True
    
    media_type = context.user_data.get('spam_media_type')
    media_id = context.user_data.get('spam_media_id')
    text = context.user_data.get('spam_text')
    
    scheduled = []
    now = datetime.now()
    
    for t in times:
        hour, minute = map(int, t.split(':'))
        scheduled_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        if scheduled_time <= now:
            continue  # Skip past times
        
        delay = (scheduled_time - now).total_seconds()
        
        context.job_queue.run_once(
            scheduled_spam_job,
            delay,
            data={"media_type": media_type, "media_id": media_id, "text": text},
            name=f"spam_{t}"
        )
        scheduled.append(t)
    
    context.user_data['spam_step'] = None
    
    if scheduled:
        await update.message.reply_text(
            f"⏰ <b>SPAM PROGRAMADO!</b>\n\n"
            f"📅 Horários:\n" + "\n".join(f"  ▸ {t}" for t in scheduled),
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("❌ Todos os horários já passaram! Use horários futuros.")

async def scheduled_spam_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    sent, failed = await send_spam(context, data['media_type'], data['media_id'], data['text'])
    # Notify admin
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, f"⏰ Spam programado enviado!\n📨 {sent} enviados | ❌ {failed} falhas")
        except:
            pass

async def send_spam(context, media_type, media_id, text):
    conn = get_db()
    users = conn.execute("SELECT telegram_id FROM users WHERE banned = 0").fetchall()
    conn.close()
    
    sent = 0
    failed = 0
    
    for user in users:
        try:
            chat_id = int(user['telegram_id'])
            if media_type == 'photo':
                if text:
                    await context.bot.send_photo(chat_id, photo=media_id, caption=text, parse_mode=ParseMode.HTML)
                else:
                    await context.bot.send_photo(chat_id, photo=media_id)
            elif media_type == 'video':
                if text:
                    await context.bot.send_video(chat_id, video=media_id, caption=text, parse_mode=ParseMode.HTML)
                else:
                    await context.bot.send_video(chat_id, video=media_id)
            elif media_type == 'text':
                await context.bot.send_message(chat_id, text=text, parse_mode=ParseMode.HTML)
            sent += 1
        except Exception as e:
            failed += 1
        
        await asyncio.sleep(0.05)  # Rate limiting
    
    return sent, failed

# /resgatar CODIGO
async def resgatar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username, user.first_name)
    
    if is_banned(user.id):
        await update.message.reply_text("⛔ Bloqueado.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Use: /resgatar CODIGO\nExemplo: /resgatar LK9KW6JQHC")
        return
    
    code = context.args[0].strip().upper()
    
    conn = get_db()
    gift = conn.execute("SELECT * FROM gifts WHERE code = ? AND redeemed_by IS NULL", (code,)).fetchone()
    
    if not gift:
        conn.close()
        await update.message.reply_text("❌ Código inválido ou já resgatado!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]]))
        return
    
    conn.execute("UPDATE gifts SET redeemed_by = ?, redeemed_at = datetime('now','localtime') WHERE code = ?",
                 (str(user.id), code))
    conn.commit()
    conn.close()
    
    update_balance(user.id, gift['amount'], f"Gift: {code}")
    balance = get_balance(user.id)
    saldo_anterior = balance - gift['amount']
    
    await update.message.reply_text(
        f"🎁 <b>GIFT RESGATADO!</b>\n\n"
        f"💰 +R${gift['amount']:.2f}\n"
        f"💎 Saldo anterior: R${saldo_anterior:.2f}\n"
        f"💎 Saldo atual: R${balance:.2f}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Comprar", callback_data="buy"), InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]])
    )

# /admin - show admin panel
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    text = (
        "⚙️ <b>PAINEL ADMIN</b>\n\n"
        "📦 <b>Estoque:</b>\n"
        "  /addlogin - Adicionar logins\n"
        "  /estoque - Ver estoque\n"
        "  /removelogin - Remover logins\n\n"
        "👥 <b>Usuários:</b>\n"
        "  /ban ID - Bloquear usuário\n"
        "  /unban ID - Desbloquear\n"
        "  /addsaldo ID VALOR - Dar saldo\n"
        "  /removesaldo ID VALOR - Tirar saldo\n\n"
        "🎁 <b>Gift:</b>\n"
        "  /gift VALOR - Gerar gift card\n\n"
        "📢 <b>Spam:</b>\n"
        "  /spam - Enviar spam\n\n"
        "📊 <b>Relatórios:</b>\n"
        "  /historico - Histórico de vendas\n"
        "  /stats - Estatísticas\n\n"
        "🎨 <b>Personalizar:</b>\n"
        "  /setwelcome TEXTO - Mudar boas-vindas\n"
        "  /setphoto - Mudar foto (responda a uma foto)\n"
    )
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ===== GENERIC MESSAGE HANDLER =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        # Check for spam media
        if context.user_data.get('spam_step') == 'media':
            await handle_spam_media(update, context)
        return
    
    text = update.message.text.strip()
    
    # Check spam steps
    if context.user_data.get('spam_step') == 'media':
        result = await handle_spam_media(update, context)
        if result:
            return
    
    if context.user_data.get('spam_step') == 'times':
        result = await handle_spam_times(update, context)
        if result:
            return
    
    # Check gift code
    if context.user_data.get('awaiting_gift'):
        await handle_gift_code(update, context)
        return
    
    # Check quantity input
    if context.user_data.get('awaiting_qty'):
        await handle_quantity(update, context)
        return

# /cancelar
async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelado.", reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]]
    ))

# ===== MAIN =====
def main():
    init_db()
    
    app = Application.builder().token(TOKEN).build()
    
    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pix", pix_command))
    app.add_handler(CommandHandler("cancelar", cancelar))
    app.add_handler(CommandHandler("resgatar", resgatar_command))
    
    # Admin commands
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("addlogin", addlogin))
    app.add_handler(CommandHandler("addlogintelas", addlogin))
    app.add_handler(CommandHandler("estoque", estoque))
    app.add_handler(CommandHandler("removelogin", removelogin))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("addsaldo", addsaldo))
    app.add_handler(CommandHandler("removesaldo", removesaldo))
    app.add_handler(CommandHandler("gift", create_gift))
    app.add_handler(CommandHandler("historico", historico))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("setwelcome", setwelcome))
    app.add_handler(CommandHandler("setphoto", setphoto))
    app.add_handler(CommandHandler("spam", spam_command))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(buy_callback, pattern="^buy$"))
    app.add_handler(CallbackQueryHandler(product_callback, pattern="^product_"))
    app.add_handler(CallbackQueryHandler(confirm_buy, pattern="^confirm_buy$"))
    app.add_handler(CallbackQueryHandler(balance_callback, pattern="^balance$"))
    app.add_handler(CallbackQueryHandler(pix_callback, pattern="^pix_"))
    app.add_handler(CallbackQueryHandler(check_payment_callback, pattern="^check_"))
    app.add_handler(CallbackQueryHandler(orders_callback, pattern="^orders$"))
    app.add_handler(CallbackQueryHandler(gift_callback, pattern="^gift$"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(spam_action_callback, pattern="^spam_"))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("🚀 LK Store Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
