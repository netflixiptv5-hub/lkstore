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
from datetime import datetime, timedelta, timezone

# Fuso horário de Brasília (UTC-3)
BRT = timezone(timedelta(hours=-3))
from io import BytesIO

import mercadopago
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, LinkPreviewOptions
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)
from telegram.constants import ParseMode

# ===== CONFIG =====
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8701402389:AAGAj33V5dgLJp2JbP8QJUd9hXTSL2f0_TY")
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "APP_USR-2507246895625254-100915-cba2bb9c86daed78244bcf5748f74642-1505061824")
SUPPORT_BOT_TOKEN = os.environ.get("SUPPORT_BOT_TOKEN", "8510312690:AAEz8nzI3PU-_MJJ8iHUkMoQnDjR_UYFgdU")
ADMIN_IDS = [925542353]
SUPPORT_BOT = "https://t.me/SUPORTESLKLOGINSSTORE77_BOT?start=suporte"
WHATSAPP_LINK = "https://wa.me/5516996143454"
SUPPORT_API_URL = os.environ.get("SUPPORT_API_URL", "https://web-production-d061f.up.railway.app")
DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/app/data") if os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") else "."
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "lkstore.db"))

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
        'welcome_photo': 'AgACAgEAAxkDAAIBK2nUFASliWqp0cQhSxMOCuKyA1PZAAKXDWsb92KhRmCeNbGi-p2iAQADAgADeAADOwQ',
    }
    for k, v in defaults.items():
        existing = conn.execute("SELECT value FROM bot_config WHERE key = ?", (k,)).fetchone()
        if not existing or not existing[0]:
            conn.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (k, v))
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
    """Returns True if new user, False if existing."""
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (str(telegram_id),)).fetchone()
    is_new = False
    if not user:
        is_new = True
        bonus = float(get_config('bonus_registro') or '0')
        conn.execute("INSERT INTO users (telegram_id, username, first_name, balance) VALUES (?, ?, ?, ?)",
                     (str(telegram_id), username, first_name, bonus))
        conn.commit()
        if bonus > 0:
            conn.execute("INSERT INTO transactions (telegram_id, type, amount, description) VALUES (?, 'credit', ?, 'Bônus de registro')",
                         (str(telegram_id), bonus))
            conn.commit()
    else:
        conn.execute("UPDATE users SET username = ?, first_name = ? WHERE telegram_id = ?",
                     (username, first_name, str(telegram_id)))
        conn.commit()
    conn.close()
    return is_new

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
def main_menu_keyboard(user_id=None):
    maint_suporte = get_config('maintenance_suporte') or '0'
    buttons = [
        [InlineKeyboardButton("🛒 COMPRAR", callback_data="buy"),
         InlineKeyboardButton("💰 SALDO", callback_data="balance")],
        [InlineKeyboardButton("📋 MEUS PEDIDOS", callback_data="orders"),
         InlineKeyboardButton("📞 DONO", url=WHATSAPP_LINK)],
        [InlineKeyboardButton("❓ COMO COMPRAR", callback_data="tutorial_compra"),
         InlineKeyboardButton("❓ COMO USAR SUPORTE", callback_data="tutorial_suporte")],
    ]
    # Suporte só aparece se bot de suporte NÃO está em manutenção
    if maint_suporte != '1':
        support = get_config('support_link') or SUPPORT_BOT
        buttons.append([InlineKeyboardButton("🆘 SUPORTE", url=support)])
    # Botão ADM só pra admin
    if user_id and is_admin(user_id):
        buttons.append([InlineKeyboardButton("⚙️ ADMIN", callback_data="adm_main")])
    return InlineKeyboardMarkup(buttons)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new = ensure_user(user.id, user.username, user.first_name)
    
    if is_banned(user.id):
        await update.message.reply_text("⛔ Você foi bloqueado. Entre em contato com o suporte.")
        return
    
    # Maintenance mode
    maint = get_config('maintenance') or '0'
    if maint == '1' and not is_admin(user.id):
        await update.message.reply_text("🔧 Bot em manutenção. Volte mais tarde!")
        return
    
    # Force username check
    force_username = get_config('force_username') or '0'
    if force_username == '1' and not user.username and not is_admin(user.id):
        await update.message.reply_text("⚠️ Você precisa definir um @username no Telegram para usar o bot!")
        return
    
    # Send tutorial video for first-time users
    if is_new and not is_admin(user.id):
        try:
            await update.message.reply_video(
                video=TUTORIAL_COMPRA_VIDEO,
                caption="🎬 <b>BEM-VINDO À LK STORE!</b>\n\nAssista o tutorial rápido de como comprar:",
                parse_mode=ParseMode.HTML
            )
        except:
            pass
    
    balance = get_balance(user.id)
    text = (
        f"𝙎𝙀𝙅𝘼 𝘽𝙀𝙈 𝙑𝙄𝙉𝘿𝙊 𝘼 𝙇𝙆 𝙎𝙏𝙊𝙍𝙀  ⭐️⭐️⭐️⭐️⭐️\n\n"
        f"𝙇𝙀𝙄𝘼 𝘾𝙊𝙈 𝘼𝙏𝙀𝙉𝘾𝘼𝙊  ⚠️\n\n"
        f"☑️ 𝘼𝙣𝙩𝙚𝙨 𝙙𝙚 𝙖𝙙𝙞𝙘𝙞𝙤𝙣𝙖𝙧 𝙨𝙖𝙡𝙙𝙤 𝙫𝙚𝙧𝙞𝙛𝙞𝙦𝙪𝙚 𝙨𝙚 𝙤 𝙦𝙪𝙚 𝙙𝙚𝙨𝙚𝙟𝙖 𝙘𝙤𝙢𝙥𝙧𝙖𝙧 𝙚𝙨𝙩𝙖 𝙙𝙞𝙨𝙥𝙤𝙣𝙞𝙫𝙚𝙡! 𝙉𝙖𝙤 𝙛𝙖𝙯𝙚𝙢𝙤𝙨 𝙧𝙚𝙚𝙢𝙗𝙤𝙡𝙨𝙤.\n"
        f"☑️ 𝙎𝙚 𝙖 𝙘𝙤𝙣𝙩𝙖 𝙦𝙪𝙚 𝙫𝙤𝙘𝙚 𝙙𝙚𝙨𝙚𝙟𝙖 𝙣𝙖𝙤 𝙚𝙨𝙩𝙞𝙫𝙚𝙧 𝙙𝙞𝙨𝙥𝙤𝙣𝙞𝙫𝙚𝙡 𝙚𝙣𝙩𝙧𝙚 𝙚𝙢 𝙘𝙤𝙣𝙩𝙖𝙩𝙤!\n"
        f"☑️ Todos Logins tem a garantia e duração de 30 dias!\n\n"
        f"🧾 Seu perfil:\n"
        f"├👤 Id: {user.id}\n"
        f"├💸 Saldo: R${balance:.2f}\n"
        f"└🥇 Cliente LK Store"
    )
    
    try:
        await update.message.reply_photo(
            photo=WELCOME_PHOTO,
            caption=text,
            reply_markup=main_menu_keyboard(user.id)
        )
        return
    except:
        pass
    
    await update.message.reply_text(text, reply_markup=main_menu_keyboard(user.id))

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if is_banned(query.from_user.id):
        await safe_edit(query, "⛔ Você foi bloqueado.")
        return
    
    if query.data == "main_menu":
        user = query.from_user
        balance = get_balance(user.id)
        text = (
            f"𝙎𝙀𝙅𝘼 𝘽𝙀𝙈 𝙑𝙄𝙉𝘿𝙊 𝘼 𝙇𝙆 𝙎𝙏𝙊𝙍𝙀  ⭐️⭐️⭐️⭐️⭐️\n\n"
            f"𝙇𝙀𝙄𝘼 𝘾𝙊𝙈 𝘼𝙏𝙀𝙉𝘾𝘼𝙊  ⚠️\n\n"
            f"☑️ 𝘼𝙣𝙩𝙚𝙨 𝙙𝙚 𝙖𝙙𝙞𝙘𝙞𝙤𝙣𝙖𝙧 𝙨𝙖𝙡𝙙𝙤 𝙫𝙚𝙧𝙞𝙛𝙞𝙦𝙪𝙚 𝙨𝙚 𝙤 𝙦𝙪𝙚 𝙙𝙚𝙨𝙚𝙟𝙖 𝙘𝙤𝙢𝙥𝙧𝙖𝙧 𝙚𝙨𝙩𝙖 𝙙𝙞𝙨𝙥𝙤𝙣𝙞𝙫𝙚𝙡! 𝙉𝙖𝙤 𝙛𝙖𝙯𝙚𝙢𝙤𝙨 𝙧𝙚𝙚𝙢𝙗𝙤𝙡𝙨𝙤.\n"
            f"☑️ 𝙎𝙚 𝙖 𝙘𝙤𝙣𝙩𝙖 𝙦𝙪𝙚 𝙫𝙤𝙘𝙚 𝙙𝙚𝙨𝙚𝙟𝙖 𝙣𝙖𝙤 𝙚𝙨𝙩𝙞𝙫𝙚𝙧 𝙙𝙞𝙨𝙥𝙤𝙣𝙞𝙫𝙚𝙡 𝙚𝙣𝙩𝙧𝙚 𝙚𝙢 𝙘𝙤𝙣𝙩𝙖𝙩𝙤!\n"
            f"☑️ Todos Logins tem a garantia e duração de 30 dias!\n\n"
            f"🧾 Seu perfil:\n"
            f"├👤 Id: {user.id}\n"
            f"├💸 Saldo: R${balance:.2f}\n"
            f"└🥇 Cliente LK Store"
        )
        try:
            await query.message.delete()
        except:
            pass
        try:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=WELCOME_PHOTO,
                caption=text,
                reply_markup=main_menu_keyboard(user.id)
            )
        except:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=main_menu_keyboard(user.id)
            )

# ===== TUTORIAIS =====
async def tutorial_compra_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except:
        pass
    await context.bot.send_video(
        chat_id=query.message.chat_id,
        video=TUTORIAL_COMPRA_VIDEO,
        caption="🎬 <b>COMO COMPRAR NA LK STORE</b>\n\n1️⃣ Escolha o produto\n2️⃣ Pague via PIX\n3️⃣ Receba na hora!",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar ao Menu", callback_data="main_menu")]])
    )

async def tutorial_suporte_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except:
        pass
    await context.bot.send_video(
        chat_id=query.message.chat_id,
        video=TUTORIAL_SUPORTE_VIDEO,
        caption="🎬 <b>COMO USAR O SUPORTE</b>\n\n1️⃣ Clique em Suporte\n2️⃣ Descreva seu problema\n3️⃣ Aguarde atendimento",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar ao Menu", callback_data="main_menu")]])
    )

# ===== COMPRAR =====
async def safe_edit(query, text, reply_markup=None, parse_mode=ParseMode.HTML, disable_web_page_preview=False):
    """Helper to handle editing messages that might be photo captions or text."""
    link_preview = LinkPreviewOptions(is_disabled=True) if disable_web_page_preview else None
    try:
        # Try editing as text first
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode, link_preview_options=link_preview)
    except Exception:
        try:
            # If it's a photo message, try editing caption
            await query.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            try:
                # If caption too long or other error, delete and send new
                await query.message.delete()
            except:
                pass
            await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode, link_preview_options=link_preview)

WELCOME_PHOTO = "AgACAgEAAxkDAAIBzmnUSLG6DETC-kGyHBSaclWyPCYNAAK_DWsb92KhRkSbE_A23vXlAQADAgADeAADOwQ"
BUY_PHOTO = "AgACAgEAAxkDAAIB1WnUSWgyrXUGiz17YfiRTRfazZDLAALADWsb92KhRui7kAUgZ8OHAQADAgADeAADOwQ"
TUTORIAL_COMPRA_VIDEO = "BAACAgEAAxkDAAIBU2nUG0xvB1fwk9zBeqahrJ3LCCuUAAILDQAC92KhRiBGnRbyo7FFOwQ"
TUTORIAL_SUPORTE_VIDEO = "BAACAgEAAxkDAAIBSmnUGdKYgXu_aWnmm7aIBECOIDlpAAIHDQAC92KhRgE5gG4gd-4KOwQ"

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
        await safe_edit(query,
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
    
    # Delete old message and send new one with photo
    try:
        await query.message.delete()
    except:
        pass
    await query.message.chat.send_photo(
        photo=BUY_PHOTO,
        caption=text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML
    )

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
    
    await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar", callback_data="buy")]]))

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
        await safe_edit(query, "❌ Saldo insuficiente! Carregue com /pix")
        return
    
    conn = get_db()
    items = conn.execute(
        "SELECT id, credentials, validity, message FROM products WHERE name = ? AND price = ? AND sold = 0 LIMIT ?",
        (product_name, price, qty)
    ).fetchall()
    
    if len(items) < qty:
        conn.close()
        await safe_edit(query, "❌ Estoque esgotou! Tente novamente.")
        return
    
    # Mark as sold and debit balance
    delivered = []
    for item in items:
        conn.execute("UPDATE products SET sold = 1, sold_to = ?, sold_at = datetime('now','localtime') WHERE id = ?",
                     (user_id, item['id']))
        now_brt = datetime.now(BRT).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO sales (telegram_id, product_name, product_id, price, credentials, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (user_id, product_name, item['id'], price, item['credentials'], now_brt))
        delivered.append(item)
    
    conn.execute("UPDATE users SET balance = balance - ? WHERE telegram_id = ?", (total, user_id))
    conn.execute("INSERT INTO transactions (telegram_id, type, amount, description) VALUES (?, 'debit', ?, ?)",
                 (user_id, total, f"Compra: {qty}x {product_name}"))
    conn.commit()
    conn.close()
    
    # Backup instantâneo - venda realizada
#        trigger_backup_async(f"venda: {qty}x {product_name}")  # REMOVED - backup only every 1h
    
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
    
    # Mensagem pós-venda — dica de redefinição de senha
    text += f"\n\n━━━━━━━━━━━━━━━━"
    text += f"\n🔐 <b>SENHA INCORRETA?</b>"
    text += f"\n"
    text += f"\n🔄 Redefina você mesmo no nosso site!"
    text += f"\n👉 <a href='https://lklogins-production.up.railway.app'>Clique aqui para redefinir</a>"
    text += f"\n"
    text += f"\n⚡ Rápido, fácil e automático!"
    text += f"\n━━━━━━━━━━━━━━━━"
    
    await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]]), disable_web_page_preview=True)
    
    # Notificar admin: compra realizada (espelho completo)
    for admin_id in ADMIN_IDS:
        try:
            username = query.from_user.username or "sem username"
            # Build credentials list (espelho do que o cliente recebeu)
            creds_text = ""
            for i, item in enumerate(delivered, 1):
                creds = item['credentials'].strip()
                if qty > 1:
                    creds_text += f"  {i}. <code>{creds}</code>\n"
                else:
                    creds_text += f"  <code>{creds}</code>\n"
            
            admin_notif = (
                f"🛒 <b>NOVA COMPRA!</b>\n\n"
                f"👤 Usuário: <code>{query.from_user.id}</code> (@{username})\n"
                f"📦 Produto: {product_name}\n"
                f"🔢 Qtd: {qty}\n"
                f"💵 Total: R${total:.2f}\n"
                f"💰 Saldo restante: R${new_balance:.2f}\n\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🔑 <b>LOGINS ENTREGUES:</b>\n\n"
                f"{creds_text}"
                f"━━━━━━━━━━━━━━━━"
            )
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📩 Enviar Mensagem", callback_data=f"adminmsg_{query.from_user.id}")]
            ])
            
            await context.bot.send_message(admin_id, admin_notif,
                parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except:
            pass

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
    
    await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
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
        pix_min = float(get_config('pix_min') or '1')
        pix_max = float(get_config('pix_max') or '500')
        if amount < pix_min:
            await update.message.reply_text(f"❌ Valor mínimo: R${pix_min:.0f}")
            return
        if amount > pix_max:
            await update.message.reply_text(f"❌ Valor máximo: R${pix_max:.0f}")
            return
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
    chat_id = update_or_query.message.chat_id if is_callback else update_or_query.message.chat_id
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
            
            # Notificar admin: PIX gerado
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(admin_id,
                        f"🔔 <b>NOVO PIX GERADO</b>\n\n"
                        f"👤 Usuário: <code>{user_id}</code>\n"
                        f"💰 Valor: R${amount:.2f}\n"
                        f"🆔 ID: <code>{mp_id}</code>",
                        parse_mode=ParseMode.HTML)
                except:
                    pass
            
            # Count user cancellations
            cancel_count = context.bot_data.get(f"cancel_{user_id}", 0)
            max_cancels = 3
            
            text = (f"💳 <b>PIX - R${amount:.2f}</b>\n\n"
                    f"📱 Escaneie o QR Code ou copie o código abaixo:\n\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"<code>{pix_code}</code>\n"
                    f"━━━━━━━━━━━━━━━━\n\n"
                    f"⚡ O saldo será creditado automaticamente após o pagamento!\n"
                    f"⏱ Validade: 30 minutos")
            
            keyboard = [
                [InlineKeyboardButton("🔄 Verificar Pagamento", callback_data=f"check_{mp_id}")],
                [InlineKeyboardButton("❌ Cancelar PIX", callback_data=f"cancelpix_{mp_id}")],
                [InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]
            ]
            
            # Send QR code image
            import base64
            qr_bytes = base64.b64decode(pix_qr)
            
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=BytesIO(qr_bytes),
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            # Start auto-check
            if context.job_queue:
                context.job_queue.run_repeating(
                    check_payment_job,
                    interval=5,
                    first=5,
                    data={"mp_id": mp_id, "user_id": str(user_id), "amount": amount, "chat_id": chat_id},
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
            logger.error(f"PIX status not pending: {payment.get('status')} - {payment}")
            await context.bot.send_message(chat_id=chat_id, text="❌ Erro ao gerar PIX. Tente novamente.")
    except Exception as e:
        logger.error(f"PIX error: {e}")
        await context.bot.send_message(chat_id=chat_id, text="❌ Erro ao gerar PIX. Tente novamente mais tarde.")

async def cancel_pix_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User cancels a PIX payment"""
    query = update.callback_query
    await query.answer()
    
    mp_id = query.data.replace("cancelpix_", "")
    user_id = query.from_user.id
    
    # Count cancellations
    cancel_key = f"cancel_{user_id}"
    cancel_count = context.bot_data.get(cancel_key, 0) + 1
    context.bot_data[cancel_key] = cancel_count
    max_cancels = 3
    remaining = max_cancels - cancel_count
    
    # Cancel MP payment
    try:
        sdk.payment().update(mp_id, {"status": "cancelled"})
    except:
        pass
    
    # Remove auto-check jobs
    if context.job_queue:
        for job in context.job_queue.get_jobs_by_name(f"payment_{mp_id}"):
            job.schedule_removal()
        for job in context.job_queue.get_jobs_by_name(f"cancel_{mp_id}"):
            job.schedule_removal()
    
    # Get payment amount from DB
    conn = get_db()
    p = conn.execute("SELECT amount FROM payments WHERE mp_payment_id = ?", (mp_id,)).fetchone()
    pix_amount = p['amount'] if p else 0
    conn.execute("UPDATE payments SET status = 'cancelled' WHERE mp_payment_id = ?", (mp_id,))
    conn.commit()
    conn.close()
    
    # Notificar admin: PIX cancelado
    for admin_id in ADMIN_IDS:
        try:
            username = query.from_user.username or "sem username"
            await context.bot.send_message(admin_id,
                f"🚫 <b>PIX CANCELADO</b>\n\n"
                f"👤 Usuário: <code>{user_id}</code> (@{username})\n"
                f"💵 Valor: R${pix_amount:.2f}\n"
                f"❌ Cancelamentos: {cancel_count}/{max_cancels}",
                parse_mode=ParseMode.HTML)
        except:
            pass
    
    if remaining <= 0:
        text = ("❌ <b>PIX Cancelado</b>\n\n"
                "⚠️ <b>ATENÇÃO:</b> Você atingiu o limite de cancelamentos!\n"
                "🚫 Cancelamentos excessivos podem resultar em <b>bloqueio permanente</b> do seu acesso ao bot.\n\n"
                "Use com responsabilidade.")
    else:
        text = (f"❌ <b>PIX Cancelado</b>\n\n"
                f"⚠️ Você ainda tem <b>{remaining} cancelamento(s)</b> disponível(is).\n"
                f"🚫 Cancelamentos excessivos podem resultar em <b>bloqueio permanente</b> do seu acesso ao bot.\n\n"
                f"Use com responsabilidade.")
    
    try:
        await query.edit_message_caption(
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]])
        )
    except:
        try:
            await query.edit_message_text(
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]])
            )
        except:
            await query.message.reply_text(text=text, parse_mode=ParseMode.HTML)

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
                # Notificar admin: PIX pago
                for admin_id in ADMIN_IDS:
                    try:
                        await context.bot.send_message(admin_id,
                            f"💰 <b>PIX PAGO!</b>\n\n"
                            f"👤 Usuário: <code>{p['telegram_id']}</code>\n"
                            f"💵 Valor: R${p['amount']:.2f}\n"
                            f"💎 Novo saldo: R${balance:.2f}",
                            parse_mode=ParseMode.HTML)
                    except:
                        pass
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
                
                credit_amount = p['amount']
                bonus_text = ""
                
                # Saldo em dobro
                saldo_dobro = get_config('saldo_dobro') or '0'
                saldo_dobro_min = float(get_config('saldo_dobro_min') or '0')
                if saldo_dobro == '1' and credit_amount >= saldo_dobro_min:
                    credit_amount = credit_amount * 2
                    bonus_text += f"\n🎉 Saldo em dobro! +R${p['amount']:.2f} bônus"
                
                # Porcentagem por recarga
                porcent = get_config('porcent_recarga') or '0'
                porcent_min = float(get_config('porcent_recarga_min') or '0')
                porcent_val = float(get_config('porcent_recarga_porcent') or '0')
                if porcent == '1' and credit_amount >= porcent_min and porcent_val > 0:
                    bonus = p['amount'] * (porcent_val / 100)
                    credit_amount += bonus
                    bonus_text += f"\n🎊 Bônus {porcent_val:.0f}%: +R${bonus:.2f}"
                
                update_balance(p['telegram_id'], credit_amount, f"PIX R${p['amount']:.2f}")
                balance = get_balance(p['telegram_id'])
                conn.close()
                
                await context.bot.send_message(
                    chat_id=data['chat_id'],
                    text=f"✅ <b>PAGAMENTO CONFIRMADO!</b>\n\n"
                         f"💰 +R${credit_amount:.2f} creditado{bonus_text}\n"
                         f"💎 Saldo atual: R${balance:.2f}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Comprar", callback_data="buy"), InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]])
                )
                # Notificar admin: PIX pago (auto-check)
                for admin_id in ADMIN_IDS:
                    try:
                        await context.bot.send_message(admin_id,
                            f"💰 <b>PIX PAGO!</b>\n\n"
                            f"👤 Usuário: <code>{p['telegram_id']}</code>\n"
                            f"💵 Valor: R${p['amount']:.2f}\n"
                            f"💎 Novo saldo: R${balance:.2f}",
                            parse_mode=ParseMode.HTML)
                    except:
                        pass
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
        await safe_edit(query,
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
    
    await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar", callback_data="main_menu")]]))

# ===== GIFT =====
async def gift_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['awaiting_gift'] = True
    
    await safe_edit(query,
        "🎁 <b>RESGATAR GIFT</b>\n\nDigite o código do gift:",
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

DEFAULT_MSG = '𝑁𝐴̃𝑂 𝑆𝐸 𝑃𝑅𝐸𝑂𝐶𝑈𝑃𝐸,𝐴𝑂 𝑉𝐸𝑁𝐶𝐸𝑅 𝑁𝑂𝑇𝐼𝐹𝐼𝐶𝐴𝑅𝐸𝑀𝑂𝑆 𝑉𝑂𝐶𝐸̂!😃🚀'
DEFAULT_VALIDITY = '30 DIAS'

# /add — simplified add logins
async def add_simple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin sends /add then pastes emails/passwords, bot asks which product"""
    if not is_admin(update.effective_user.id):
        return
    
    text = update.message.text
    if text.startswith('/add '):
        text = text[4:].strip()
    elif text.strip() == '/add':
        await update.message.reply_text(
            "📝 <b>ADICIONAR LOGINS (modo fácil)</b>\n\n"
            "Cole os logins (um por linha):\n"
            "<code>/add\n"
            "email1@hotmail.com senha1\n"
            "email2@hotmail.com senha2\n"
            "email3@hotmail.com senha3</code>\n\n"
            "Separador: espaço, : ou |\n"
            "Validade: 30 DIAS (automático)\n"
            "Mensagem: padrão (automático)",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Parse credentials - one per line
    # Filter out lines that are commands (/add, /ADD), empty, or don't contain @
    raw_lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
    lines = []
    skipped = 0
    for l in raw_lines:
        # Skip command lines and lines without @
        if l.startswith('/') or '@' not in l:
            skipped += 1
            continue
        # Remove any leading /add or /ADD that got stuck
        import re
        l = re.sub(r'^/[Aa][Dd][Dd]\s*', '', l).strip()
        if l and '@' in l:
            lines.append(l)
    
    if not lines:
        await update.message.reply_text(
            "❌ Nenhum login válido encontrado.\n\n"
            "Cada linha precisa ter um email (com @).\n"
            "Formato: <code>email@xxx.com senha</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    if skipped > 0:
        logger.info(f"[ADD] Skipped {skipped} invalid lines (no @ or command)")
    
    # Store credentials temporarily
    context.user_data['add_logins'] = lines
    
    # Get distinct products from DB to show as buttons
    conn = get_db()
    products = conn.execute(
        "SELECT DISTINCT name, price FROM products ORDER BY name"
    ).fetchall()
    conn.close()
    
    buttons = []
    for p in products:
        buttons.append([InlineKeyboardButton(
            f"{p['name']} — R${p['price']:.0f}",
            callback_data=f"addto_{p['name']}_{p['price']}"
        )])
    # Option to type new product
    buttons.append([InlineKeyboardButton("➕ Novo Produto (digitar)", callback_data="addto_new")])
    
    await update.message.reply_text(
        f"📦 <b>{len(lines)} login(s) prontos!</b>\n\n"
        f"Escolha o produto:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def add_simple_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle product selection for /add"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        return
    
    logins = context.user_data.get('add_logins', [])
    if not logins:
        await safe_edit(query, "❌ Nenhum login pendente. Use /add primeiro.")
        return
    
    data = query.data
    
    if data == "addto_new":
        context.user_data['add_awaiting_product'] = True
        await safe_edit(query, 
            "✏️ Digite o nome e preço do novo produto:\n\n"
            "Formato: <code>NOME DO PRODUTO===PREÇO</code>\n"
            "Exemplo: <code>TELA EXTRA===10</code>",
            parse_mode=ParseMode.HTML)
        return
    
    # Parse addto_PRODUCT_PRICE
    parts = data.replace("addto_", "", 1)
    # Split by last underscore to get price
    last_underscore = parts.rfind("_")
    product_name = parts[:last_underscore]
    price = float(parts[last_underscore+1:])
    
    # Insert all logins
    added, dupes = await _insert_logins(logins, product_name, price, str(query.from_user.id))
    context.user_data.pop('add_logins', None)
    
    dupe_text = f"\n🔄 Duplicatas substituídas: {dupes}" if dupes > 0 else ""
    await safe_edit(query,
        f"✅ <b>{added} login(s) adicionados!</b>\n\n"
        f"📦 Produto: {product_name}\n"
        f"💵 Preço: R${price:.2f}\n"
        f"⏱ Validade: {DEFAULT_VALIDITY}\n"
        f"🔢 Total adicionado: {added}{dupe_text}")

def _extract_email(cred):
    """Extract email from credential string like 'email@hot.com senha123' or 'email@hot.com:senha123'"""
    cred = cred.strip()
    # Try to find email pattern
    match = re.search(r'[\w\.\-\+]+@[\w\.\-]+\.\w+', cred)
    return match.group(0).lower() if match else None

def _remove_duplicates(conn, product_name, email):
    """Remove existing unsold entries with same email in same product. Returns count removed."""
    if not email:
        return 0
    # Find duplicates: same product, same email, not sold
    existing = conn.execute(
        "SELECT id, credentials FROM products WHERE name = ? AND sold = 0",
        (product_name,)
    ).fetchall()
    removed = 0
    for row in existing:
        existing_email = _extract_email(row['credentials'])
        if existing_email and existing_email == email:
            conn.execute("DELETE FROM products WHERE id = ?", (row['id'],))
            removed += 1
    return removed

async def _insert_logins(logins, product_name, price, added_by):
    """Insert login credentials into products table, removing duplicates in same product"""
    conn = get_db()
    added = 0
    skipped_dupes = 0
    for line in logins:
        line = line.strip()
        if not line:
            continue
        cred = line
        email = _extract_email(cred)
        # Remove existing duplicate in same product (unsold only)
        removed = _remove_duplicates(conn, product_name, email)
        if removed > 0:
            skipped_dupes += removed
        conn.execute(
            "INSERT INTO products (name, price, credentials, validity, message, added_by) VALUES (?, ?, ?, ?, ?, ?)",
            (product_name, price, cred, DEFAULT_VALIDITY, DEFAULT_MSG, added_by)
        )
        added += 1
    conn.commit()
    conn.close()
    
    # Backup instantâneo - logins adicionados
#        trigger_backup_async(f"add: {added}x {product_name}")  # REMOVED - backup only every 1h
    
    return added, skipped_dupes

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
    dupes = 0
    conn = get_db()
    for cred in cred_parts:
        cred = cred.strip()
        if not cred:
            continue
        # Clean up credentials
        cred = re.sub(r'✉️\s*Email:\s*', '', cred)
        cred = cred.strip()
        if cred:
            email = _extract_email(cred)
            removed = _remove_duplicates(conn, product_name, email)
            dupes += removed
            conn.execute(
                "INSERT INTO products (name, price, credentials, validity, message, added_by) VALUES (?, ?, ?, ?, ?, ?)",
                (product_name, price, cred, validity, message, str(update.effective_user.id))
            )
            added += 1
    conn.commit()
    conn.close()
    
    # Backup instantâneo - login adicionado
#        trigger_backup_async(f"addlogin: {added}x {product_name}")  # REMOVED - backup only every 1h
    
    dupe_text = f"\n🔄 Duplicatas substituídas: {dupes}" if dupes > 0 else ""
    await update.message.reply_text(
        f"✅ {added} login(s) adicionado(s)!\n\n"
        f"📦 {product_name}\n"
        f"💲 R${price:.0f}\n"
        f"⏱ {validity}{dupe_text}"
    )

async def limpar_duplicatas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove duplicate emails within the same product (keeps newest)"""
    if not is_admin(update.effective_user.id):
        return
    
    conn = get_db()
    # Get all unsold products grouped by name
    products = conn.execute("SELECT DISTINCT name FROM products WHERE sold = 0").fetchall()
    
    total_removed = 0
    details = []
    
    for prod in products:
        pname = prod['name']
        items = conn.execute(
            "SELECT id, credentials FROM products WHERE name = ? AND sold = 0 ORDER BY id DESC",
            (pname,)
        ).fetchall()
        
        seen_emails = {}
        to_delete = []
        
        for item in items:
            email = _extract_email(item['credentials'])
            if not email:
                continue
            if email in seen_emails:
                # This is older (lower id since we ordered DESC), delete it
                to_delete.append(item['id'])
            else:
                seen_emails[email] = item['id']
        
        if to_delete:
            for did in to_delete:
                conn.execute("DELETE FROM products WHERE id = ?", (did,))
            total_removed += len(to_delete)
            details.append(f"📦 {pname}: {len(to_delete)} removidas")
    
    conn.commit()
    conn.close()
    
    if total_removed == 0:
        await update.message.reply_text("✅ Nenhuma duplicata encontrada no estoque!")
    else:
        detail_text = "\n".join(details)
        await update.message.reply_text(
            f"🧹 <b>LIMPEZA DE DUPLICATAS</b>\n\n"
            f"🗑 Total removidas: {total_removed}\n\n"
            f"{detail_text}",
            parse_mode=ParseMode.HTML
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
        await safe_edit(query, "🚀 Enviando spam...")
        sent, failed = await send_spam(context, 
            context.user_data.get('spam_media_type'),
            context.user_data.get('spam_media_id'),
            context.user_data.get('spam_text'))
        
        context.user_data['spam_step'] = None
        await query.message.reply_text(f"✅ Spam enviado!\n\n📨 Enviados: {sent}\n❌ Falhas: {failed}")
    
    elif query.data == "spam_schedule":
        context.user_data['spam_step'] = 'times'
        await safe_edit(query,
            "⏰ <b>PROGRAMAR SPAM</b>\n\n"
            "Digite os horários separados por espaço:\n"
            "Exemplo: <code>12:30 14:00 18:30 21:00</code>\n\n"
            "O spam será enviado hoje nesses horários."
        )
    
    elif query.data == "spam_cancel":
        context.user_data['spam_step'] = None
        await safe_edit(query, "❌ Spam cancelado.")

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
    
    now = datetime.now(BRT)
    valid_times = []
    
    for t in times:
        hour, minute = map(int, t.split(':'))
        scheduled_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if scheduled_time <= now:
            continue
        valid_times.append(t)
    
    if not valid_times:
        context.user_data['spam_step'] = None
        await update.message.reply_text("❌ Todos os horários já passaram! Use horários futuros.")
        return True
    
    # Salvar no banco — o checker periódico vai enviar
    conn = get_db()
    conn.execute(
        "INSERT INTO scheduled_spam (media_type, media_file_id, text, scheduled_times, status) VALUES (?, ?, ?, ?, 'active')",
        (media_type, media_id, text, json.dumps(valid_times))
    )
    conn.commit()
    conn.close()
    
    context.user_data['spam_step'] = None
    
    await update.message.reply_text(
        f"⏰ <b>SPAM PROGRAMADO!</b>\n\n"
        f"📅 Horários:\n" + "\n".join(f"  ▸ {t}" for t in valid_times) +
        f"\n\n✅ Salvo no banco! Vai enviar mesmo se o bot reiniciar.",
        parse_mode=ParseMode.HTML
    )

async def cancel_spam_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel all scheduled spams"""
    if not is_admin(update.effective_user.id):
        return
    
    conn = get_db()
    active = conn.execute("SELECT COUNT(*) as cnt FROM scheduled_spam WHERE status = 'active'").fetchone()
    if active['cnt'] == 0:
        conn.close()
        await update.message.reply_text("❌ Nenhum spam agendado.")
        return
    
    conn.execute("UPDATE scheduled_spam SET status = 'cancelled' WHERE status = 'active'")
    conn.commit()
    conn.close()
    
    await update.message.reply_text(f"✅ {active['cnt']} spam(s) agendado(s) cancelado(s)!")

async def spam_checker_job(context: ContextTypes.DEFAULT_TYPE):
    """Roda a cada 30s — verifica se tem spam pra enviar agora"""
    try:
        conn = get_db()
        spams = conn.execute("SELECT * FROM scheduled_spam WHERE status = 'active'").fetchall()
        now = datetime.now(BRT)
        current_time = now.strftime("%H:%M")
        
        for spam in spams:
            times_list = json.loads(spam['scheduled_times'])
            remaining_times = []
            sent_now = False
            
            for t in times_list:
                h, m = map(int, t.split(':'))
                scheduled = now.replace(hour=h, minute=m, second=0, microsecond=0)
                diff = (now - scheduled).total_seconds()
                
                # Se tá na janela de envio (0 a 59 segundos depois do horário)
                if 0 <= diff < 60:
                    if not sent_now:
                        sent, failed = await send_spam(
                            context, spam['media_type'], spam['media_file_id'], spam['text']
                        )
                        sent_now = True
                        for admin_id in ADMIN_IDS:
                            try:
                                await context.bot.send_message(
                                    admin_id,
                                    f"⏰ Spam das {t} enviado!\n📨 {sent} enviados | ❌ {failed} falhas"
                                )
                            except:
                                pass
                elif diff < 0:
                    # Ainda não chegou — manter
                    remaining_times.append(t)
            
            # Atualizar banco
            if remaining_times:
                conn.execute(
                    "UPDATE scheduled_spam SET scheduled_times = ? WHERE id = ?",
                    (json.dumps(remaining_times), spam['id'])
                )
            else:
                conn.execute(
                    "UPDATE scheduled_spam SET status = 'done' WHERE id = ?",
                    (spam['id'],)
                )
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Spam checker error: {e}")

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

# /importarvendas - import sales history from txt file
async def importarvendas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        return
    msg = update.message
    # Check if replying to a file
    target = msg.reply_to_message if msg.reply_to_message else msg
    if not target.document:
        await msg.reply_text("Envie um arquivo .txt com o histórico e responda com /importarvendas")
        return
    await msg.reply_text("⏳ Importando histórico de vendas...")
    file = await target.document.get_file()
    data = await file.download_as_bytearray()
    text = data.decode('utf-8', errors='ignore')
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    conn = get_db()
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
            # Parse date
            created_at = None
            if date_str:
                # Try different formats
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
                now_brt = datetime.now(BRT).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "INSERT INTO sales (telegram_id, product_name, product_id, price, credentials, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (telegram_id, product_name, 0, price, credentials, now_brt)
                )
            imported += 1
        except Exception as e:
            errors += 1
    conn.commit()
    conn.close()
    
    # Backup instantâneo - importação de vendas
#        trigger_backup_async(f"importarvendas: {imported} vendas")  # REMOVED - backup only every 1h
    
    await msg.reply_text(f"✅ Importação concluída!\n\n📦 {imported} vendas importadas\n❌ {errors} erros")

# /importar - import stock from pasted text or file
async def importar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    # Check if replying to a document
    if update.message.reply_to_message and update.message.reply_to_message.document:
        doc = update.message.reply_to_message.document
        file = await doc.get_file()
        data = await file.download_as_bytearray()
        text = data.decode('utf-8')
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        text = update.message.reply_to_message.text
    else:
        # Check if text after command
        text = update.message.text.replace('/importar', '', 1).strip()
    
    if not text:
        await update.message.reply_text(
            "📦 <b>IMPORTAR ESTOQUE</b>\n\n"
            "Modo 1: Cole o estoque e responda com /importar\n"
            "Modo 2: Envie um arquivo .txt e responda com /importar\n\n"
            "Formato por linha:\n"
            "<code>PRODUTO|PREÇO|email senha</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    conn = get_db()
    added = 0
    seen = set()
    
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        
        parts = line.split('|')
        if len(parts) < 3:
            continue
        
        name = parts[0].strip()
        # Normalize
        if name == 'DISNEY PADRÃO':
            name = 'DISNEY PADRAO'
        
        try:
            price = float(parts[1].strip())
        except:
            continue
        
        creds = parts[2].strip()
        creds = re.sub(r'✉️\s*Email:\s*', '', creds)
        creds = creds.strip()
        if not creds:
            continue
        
        # Dedupe by email
        email_match = re.match(r'[\w\.\+\-]+@[\w\.\-]+', creds.lower())
        key = email_match.group(0) if email_match else creds.lower()
        if key in seen:
            continue
        seen.add(key)
        
        # Extract validity and message from remaining parts
        validity = "30 DIAS"
        message = "𝑁𝐴̃𝑂 𝑆𝐸 𝑃𝑅𝐸𝑂𝐶𝑈𝑃𝐸,𝐴𝑂 𝑉𝐸𝑁𝐶𝐸𝑅 𝑁𝑂𝑇𝐼𝐹𝐼𝐶𝐴𝑅𝐸𝑀𝑂𝑆 𝑉𝑂𝐶𝐸̂!😃🚀"
        
        remaining = '|'.join(parts[3:])
        val_match = re.search(r'(\d+)\s*DIAS?', remaining)
        if val_match:
            validity = f"{val_match.group(1)} DIAS"
        
        conn.execute(
            "INSERT INTO products (name, price, credentials, validity, message, added_by) VALUES (?, ?, ?, ?, ?, ?)",
            (name, price, creds, validity, message, str(update.effective_user.id))
        )
        added += 1
    
    conn.commit()
    
    # Backup instantâneo - importação
#        trigger_backup_async(f"importar: {added} logins")  # REMOVED - backup only every 1h
    
    # Show summary
    products = conn.execute(
        "SELECT name, price, COUNT(*) as qty FROM products WHERE sold = 0 GROUP BY name, price ORDER BY name"
    ).fetchall()
    conn.close()
    
    text = f"✅ <b>{added} logins importados!</b>\n\n📊 Estoque atual:\n"
    for p in products:
        text += f"  📦 {p['name']} - R${p['price']:.0f} → {p['qty']} un\n"
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

LKLOGINS_URL = "https://lklogins-production.up.railway.app"

async def _monitor_lklogins_restart(context, chat_id, message_id):
    """Background task: polls lklogins every 10s, edits message when back online or after 5min timeout."""
    # Wait 15s before first check (give Railway time to start redeploying)
    await asyncio.sleep(15)
    max_attempts = 30  # 30 x 10s = 5 min max
    for attempt in range(1, max_attempts + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(LKLOGINS_URL, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        elapsed = 15 + (attempt * 10)
                        await context.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=f"✅ <b>LKLogins voltou online!</b>\n\n⏱ Tempo: {elapsed}s",
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🔙 Voltar", callback_data="adm_mais")]
                            ])
                        )
                        return
        except Exception:
            pass
        await asyncio.sleep(10)
    # Timeout
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text="⚠️ <b>LKLogins não respondeu em 5 minutos.</b>\n\nPode estar com problema no deploy.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Tentar novamente", callback_data="adm_restart_lklogins")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_mais")]
        ])
    )

# ===== FULL ADMIN PANEL WITH INLINE MENUS =====

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main /adm command - show admin dashboard"""
    if not is_admin(update.effective_user.id):
        return
    
    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()['c']
    total_stock = conn.execute("SELECT COUNT(*) as c FROM products WHERE sold = 0").fetchone()['c']
    maint_suporte = get_config('maintenance_suporte') or '0'
    conn.close()
    
    ms_icon = "⛔" if maint_suporte == '1' else "✅"
    
    text = (
        f"⚙️ <b>Menu de Administração</b>\n\n"
        f"🔧 Status:\n"
        f"· 🏆 Bot VIP 🏆\n"
        f"· 🆘 Suporte: {'Manutenção' if maint_suporte == '1' else 'Online'} {ms_icon}\n\n"
        f"👥 Usuários Cadastrados:\n"
        f"- Total: <b>{total_users}</b>\n\n"
        f"📦 Estoque: <b>{total_stock}</b> logins disponíveis"
    )
    
    ms_btn = f"🆘 Suporte: {'Manutenção ⛔' if maint_suporte == '1' else 'Online ✅'}"
    
    buttons = [
        [InlineKeyboardButton("🔄 Atualizar", callback_data="adm_main")],
        [InlineKeyboardButton(ms_btn, callback_data="adm_maint_suporte")],
        [InlineKeyboardButton("👑 Administradores", callback_data="adm_admins")],
        [InlineKeyboardButton("🛒 Configurar vendas", callback_data="adm_vendas"),
         InlineKeyboardButton("💠 Configurar Pix", callback_data="adm_pix")],
        [InlineKeyboardButton("💰 Configurar saldos/gift", callback_data="adm_saldos"),
         InlineKeyboardButton("👤 Configurar usuários", callback_data="adm_users")],
        [InlineKeyboardButton("⚙️ Mais configurações", callback_data="adm_mais")],
    ]
    
    if update.callback_query:
        await safe_edit(update.callback_query, text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def adm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all admin panel callbacks"""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Sem permissão", show_alert=True)
        return
    await query.answer()
    
    data = query.data
    
    # ===== MAIN MENU =====
    if data == "adm_main":
        await admin_panel(update, context)
    
    # ===== TOGGLE MAINTENANCE VENDAS =====
    elif data == "adm_maint_vendas":
        current = get_config('maintenance') or '0'
        new_val = '0' if current == '1' else '1'
        set_config('maintenance', new_val)
        status = "ATIVADA 🔧" if new_val == '1' else "DESATIVADA ✅"
        await query.answer(f"🛒 Bot Vendas manutenção: {status}", show_alert=True)
        await admin_panel(update, context)
    
    # ===== TOGGLE MAINTENANCE SUPORTE =====
    elif data == "adm_maint_suporte":
        current = get_config('maintenance_suporte') or '0'
        new_val = '0' if current == '1' else '1'
        
        # Toggle via support web API
        support_api = get_config('support_api_url') or SUPPORT_API_URL
        action = "on" if new_val == '1' else "off"
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{support_api}/api/maintenance",
                    json={"action": action, "secret": "lkstore2026"},
                    timeout=aiohttp.ClientTimeout(total=5)
                )
                result = await resp.json()
                is_on = result.get('maintenance', False)
                new_val = '1' if is_on else '0'
        except:
            pass
        
        set_config('maintenance_suporte', new_val)
        status = "ATIVADA 🔧" if new_val == '1' else "DESATIVADA ✅"
        await query.answer(f"🆘 Bot Suporte manutenção: {status}", show_alert=True)
        await admin_panel(update, context)
    
    # ===== ADMINS =====
    elif data == "adm_admins":
        admins_text = "\n".join(f"  · <code>{a}</code>" for a in ADMIN_IDS)
        text = (
            f"👑 <b>Administradores</b>\n\n"
            f"{admins_text}\n\n"
            f"Adicionar admin: <code>/addadmin ID</code>\n"
            f"Remover admin: <code>/removeadmin ID</code>"
        )
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Atualizar", callback_data="adm_admins")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_main")]
        ]))
    
    # ===== CONFIGURAR VENDAS =====
    elif data == "adm_vendas":
        conn = get_db()
        total_stock = conn.execute("SELECT COUNT(*) as c FROM products WHERE sold = 0").fetchone()['c']
        products = conn.execute(
            "SELECT name, price, COUNT(*) as qty FROM products WHERE sold = 0 GROUP BY name, price ORDER BY name"
        ).fetchall()
        conn.close()
        
        stock_detail = ""
        for p in products:
            stock_detail += f"  📦 {p['name']} - R${p['price']:.0f} ({p['qty']} un)\n"
        if not stock_detail:
            stock_detail = "  Vazio!\n"
        
        text = (
            f"🛒 <b>Configuração de Vendas</b>\n\n"
            f"🛒 Comando para ativar/desativar módulo de logins:\n"
            f"<code>/modulologins 1 ou 0</code>\n\n"
            f"📦 <b>{total_stock} logins no estoque</b>\n\n"
            f"{stock_detail}"
        )
        
        buttons = [
            [InlineKeyboardButton("🔄 Atualizar", callback_data="adm_vendas")],
            [InlineKeyboardButton(f"🛒 {total_stock} logins no estoque", callback_data="adm_estoque_detail")],
            [InlineKeyboardButton("🛒 Adicionar logins", callback_data="adm_add_logins")],
            [InlineKeyboardButton("🛒 Alterar modo de vendas", callback_data="adm_modo_vendas")],
            [InlineKeyboardButton("💰 Alterar preços", callback_data="adm_alterar_precos")],
            [InlineKeyboardButton("😀 Alterar nome dos logins", callback_data="adm_alterar_nomes")],
            [InlineKeyboardButton("💚 Baixar todos logins no estoque", callback_data="adm_baixar_estoque")],
            [InlineKeyboardButton("📅 Baixar vendas por data", callback_data="adm_baixar_vendas_menu")],
            [InlineKeyboardButton("💚 Baixar logins vendidos por nome", callback_data="adm_baixar_vendidos_nome")],
            [InlineKeyboardButton("🗑 Deletar todos logins do estoque", callback_data="adm_deletar_estoque")],
            [InlineKeyboardButton("🗑 Deletar login específico", callback_data="adm_deletar_login")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_main")]
        ]
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(buttons))
    
    # ===== CONFIGURAR PIX =====
    elif data == "adm_pix":
        mp_token = get_config('mp_token') or MP_ACCESS_TOKEN
        pix_min = get_config('pix_min') or '1'
        pix_max = get_config('pix_max') or '500'
        pix_manual_key = get_config('pix_manual_key') or 'Não definido'
        pix_manual_name = get_config('pix_manual_name') or 'Não definido'
        pix_method = get_config('pix_method') or 'MERCADOPAGO'
        
        text = (
            f"💠 <b>Configurações de Pix</b>\n\n"
            f"🔹 Pix Atual: <b>{pix_method}</b>\n"
            f"- Ranking de pix inseridos: /rankingpix\n\n"
            f"💠 <b>Configuração de Limites de Recarga</b>\n"
            f"- Pix Mínimo Atual: <b>{pix_min}</b>\n"
            f"- Pix Máximo Atual: <b>{pix_max}</b>\n"
            f"- Comando para alterar o pix mínimo: <code>/recargaminima Valor</code>\n"
            f"- Pix Máximo: <code>/recargamaxima Valor</code>\n\n"
            f"💠 <b>Verificação de Pendências no Pix</b>\n"
            f"- Listar Todos os Pix Pendentes: /listarpix\n"
            f"- Cancelar Pix por ID: <code>/cancelarpix Id</code>\n\n"
            f"💠 <b>Configurações Específicas por Banco</b>\n\n"
            f"🔵 <b>Pix MERCADO PAGO:</b>\n"
            f"- Token atual: <code>{mp_token[:20]}...</code>\n"
            f"- Definir Token: <code>/definirtokenmp TOKEN</code>\n\n"
            f"⚫️ <b>Pix MANUAL:</b>\n"
            f"- Chave pix manual atual: <b>{pix_manual_key}</b>\n"
            f"- Nome pix manual atual: <b>{pix_manual_name}</b>\n"
            f"- Definir Chave Pix: <code>/chavepix CHAVE</code>\n"
            f"- Definir Nome: <code>/nomepix NOME</code>"
        )
        
        buttons = [
            [InlineKeyboardButton("🔄 Atualizar", callback_data="adm_pix")],
            [InlineKeyboardButton("🔵 Usar Mercado Pago", callback_data="adm_pix_mp")],
            [InlineKeyboardButton("⚫️ Usar Pix Manual", callback_data="adm_pix_manual")],
            [InlineKeyboardButton("📋 Listar Pix Pendentes", callback_data="adm_listar_pix")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_main")]
        ]
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(buttons))
    
    elif data == "adm_pix_mp":
        set_config('pix_method', 'MERCADOPAGO')
        await query.answer("✅ Pix alterado para MERCADO PAGO", show_alert=True)
        # Refresh pix menu
        query.data = "adm_pix"
        await adm_callback(update, context)
    
    elif data == "adm_pix_manual":
        set_config('pix_method', 'MANUAL')
        await query.answer("✅ Pix alterado para MANUAL", show_alert=True)
        query.data = "adm_pix"
        await adm_callback(update, context)
    
    elif data == "adm_listar_pix":
        conn = get_db()
        pending = conn.execute(
            "SELECT * FROM payments WHERE status = 'pending' ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        conn.close()
        
        if not pending:
            text = "📋 Nenhum PIX pendente."
        else:
            text = "📋 <b>PIX PENDENTES</b>\n\n"
            for p in pending:
                text += (f"🔹 ID: <code>{p['mp_payment_id']}</code>\n"
                        f"   User: {p['telegram_id']} | R${p['amount']:.2f}\n"
                        f"   📅 {p['created_at']}\n\n")
        
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Atualizar", callback_data="adm_listar_pix")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_pix")]
        ]))
    
    # ===== CONFIGURAR SALDOS/GIFT =====
    elif data == "adm_saldos":
        saldo_dobro = get_config('saldo_dobro') or '0'
        saldo_dobro_min = get_config('saldo_dobro_min') or '0'
        bonus_registro = get_config('bonus_registro') or '0'
        porcent_recarga = get_config('porcent_recarga') or '0'
        porcent_recarga_min = get_config('porcent_recarga_min') or '0'
        porcent_recarga_val = get_config('porcent_recarga_porcent') or '0'
        protecao_saldo = get_config('protecao_saldo') or '0'
        
        sdobro_status = "Ativado ✅" if saldo_dobro == '1' else "Desativado ❌"
        porcent_status = "Ativado ✅" if porcent_recarga == '1' else "Desativado ❌"
        
        text = (
            f"💰 <b>Configuração de Saldo e Gifts</b>\n\n"
            f"◆ <b>Saldo em Dobro:</b>\n"
            f"· Status Atual: {sdobro_status}\n"
            f"· Valor Mínimo para Ativar: {saldo_dobro_min}\n"
            f"· Comando para Alterar: <code>/saldoemdobro SITUAÇÃO VALOR</code>\n"
            f"(Situação: 1=LIGADO, 0=DESLIGADO | Valor: Mínimo para dobrar o saldo)\n\n"
            f"🎊 <b>Porcentagem por Recarga:</b>\n"
            f"· Status Atual: {porcent_status}\n"
            f"· Valor Mínimo para Ativar: {porcent_recarga_min}\n"
            f"· Porcentagem: {porcent_recarga_val}%\n"
            f"· Comando para Alterar: <code>/porcentagemrecarga SITUAÇÃO VALOR PORCENTAGEM</code>\n\n"
            f"💰 <b>Bônus de Registro:</b>\n"
            f"· Bônus atual: R${bonus_registro}\n"
            f"· Comando para Alterar: <code>/bonusregistro VALOR</code>\n\n"
            f"🎁 <b>Configuração de Gifts:</b>\n"
            f"· Gerar Gift: <code>/gift VALOR</code>\n"
            f"· Gerar Múltiplos Gifts: <code>/gengifts QUANTIDADE VALOR</code>\n"
            f"· Resgatar Gift: <code>/resgatar CÓDIGO</code>\n"
            f"· Adicionar Saldo: <code>/addsaldo ID_TELEGRAM VALOR</code>\n"
            f"· Proteção de saldo: <code>/protecaosaldo VALOR</code>\n"
            f"· Remover Saldo: <code>/removesaldo ID_TELEGRAM VALOR</code>"
        )
        
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Atualizar", callback_data="adm_saldos")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_main")]
        ]))
    
    # ===== CONFIGURAR USUARIOS =====
    elif data == "adm_users":
        force_username = get_config('force_username') or '0'
        force_channel = get_config('force_channel') or '0'
        channel_link = get_config('channel_link') or 'Não definido'
        
        fu_status = "True" if force_username == '1' else "False"
        fc_status = "True" if force_channel == '1' else "False"
        
        text = (
            f"👤 <b>Configuração de usuários</b>\n\n"
            f"- Forçar usuário a criar um username para utilizar o bot: <b>{fu_status}</b>\n\n"
            f"- Forçar usuário a entrar em seu canal para utilizar o bot: <b>{fc_status}</b>\n"
            f"  – {channel_link}\n"
            f"  (O canal deve ser público e o bot deve ser admin no canal.)\n\n"
            f"- Alterar link do canal: <code>/linkcanal LINK</code>\n\n"
            f"- Ver informações e banir/desbanir usuários: <code>/userinfo ID_TELEGRAM</code>\n\n"
            f"💬 <b>Spam para usuários</b>\n\n"
            f"- Comando para enviar spam: <code>/enviarspam TEXTO</code>\n"
            f"(Você também pode enviar fotos e texto no chat do bot para spam com foto e texto, seja imediato ou agendado.)"
        )
        
        buttons = [
            [InlineKeyboardButton("🔄 Atualizar", callback_data="adm_users")],
            [InlineKeyboardButton(f"{'DESATIVAR' if force_username == '1' else 'ATIVAR'} forçar username", callback_data="adm_toggle_username")],
            [InlineKeyboardButton(f"{'DESATIVAR' if force_channel == '1' else 'ATIVAR'} forçar entrar em canal", callback_data="adm_toggle_channel")],
            [InlineKeyboardButton("🔽 Baixar usuários cadastrados", callback_data="adm_baixar_users")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_main")]
        ]
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(buttons))
    
    elif data == "adm_toggle_username":
        current = get_config('force_username') or '0'
        set_config('force_username', '0' if current == '1' else '1')
        query.data = "adm_users"
        await adm_callback(update, context)
    
    elif data == "adm_toggle_channel":
        current = get_config('force_channel') or '0'
        set_config('force_channel', '0' if current == '1' else '1')
        query.data = "adm_users"
        await adm_callback(update, context)
    
    elif data == "adm_baixar_users":
        conn = get_db()
        users = conn.execute("SELECT telegram_id, username, first_name, balance, created_at FROM users ORDER BY created_at DESC").fetchall()
        conn.close()
        
        content = "ID_TELEGRAM|USERNAME|NOME|SALDO|CADASTRO\n"
        for u in users:
            content += f"{u['telegram_id']}|{u['username'] or ''}|{u['first_name'] or ''}|{u['balance']:.2f}|{u['created_at']}\n"
        
        buf = BytesIO(content.encode('utf-8'))
        buf.name = "usuarios_cadastrados.txt"
        await query.message.reply_document(document=buf, filename="usuarios_cadastrados.txt",
            caption=f"👥 {len(users)} usuários cadastrados")
    
    # ===== MAIS CONFIGURAÇÕES =====
    elif data == "adm_mais":
        text = "⚙️ <b>Mais configurações</b>"
        buttons = [
            [InlineKeyboardButton("🤖 Testar bot", callback_data="adm_testar"),
             InlineKeyboardButton("💬 Alterar textos/fotos", callback_data="adm_textos")],
            [InlineKeyboardButton("🗑 Limpar cache do bot", callback_data="adm_limpar_cache")],
            [InlineKeyboardButton("📊 Estatísticas", callback_data="adm_stats")],
            [InlineKeyboardButton("🔄 Reiniciar LKLogins", callback_data="adm_restart_lklogins")],
            [InlineKeyboardButton("🔄 Reiniciar Suporte", callback_data="adm_restart_suporte")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_main")]
        ]
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(buttons))
    
    elif data == "adm_testar":
        await query.message.reply_text("🤖 Bot está funcionando normalmente! ✅")
    
    elif data == "adm_restart_lklogins":
        await safe_edit(query, "🔄 <b>Reiniciando LKLogins...</b>\n\nAguarde...", reply_markup=None)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://lklogins-production.up.railway.app/api/self-restart",
                    headers={"X-Restart-Secret": "lk3401restart", "Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        await safe_edit(query, "🔄 <b>LKLogins reiniciando...</b>\n\n⏳ Monitorando — te aviso quando voltar online.", reply_markup=None)
                        asyncio.create_task(_monitor_lklogins_restart(context, query.message.chat_id, query.message.message_id))
                    else:
                        await safe_edit(query, f"❌ <b>Erro ao reiniciar:</b> HTTP {resp.status}", reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔄 Tentar novamente", callback_data="adm_restart_lklogins")],
                            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_mais")]
                        ]))
        except Exception as e:
            logger.error(f"LKLogins restart error: {e}")
            await safe_edit(query, f"❌ <b>Erro de conexão:</b>\n<code>{str(e)[:200]}</code>", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Tentar novamente", callback_data="adm_restart_lklogins")],
                [InlineKeyboardButton("🔙 Voltar", callback_data="adm_mais")]
            ]))
    
    elif data == "adm_restart_suporte":
        await safe_edit(query, "🔄 <b>Reiniciando Bot de Suporte...</b>\n\nAguarde...", reply_markup=None)
        try:
            async with aiohttp.ClientSession() as session:
                # Restart the web (Flask) — triggers Railway auto-restart
                async with session.post(
                    "https://web-production-d061f.up.railway.app/api/self-restart",
                    headers={"X-Restart-Secret": "lk3401restart", "Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        await safe_edit(query, "✅ <b>Bot de Suporte reiniciado!</b>\n\nVai voltar em ~1 minuto.", reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_mais")]
                        ]))
                    else:
                        await safe_edit(query, f"❌ <b>Erro ao reiniciar:</b> HTTP {resp.status}", reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔄 Tentar novamente", callback_data="adm_restart_suporte")],
                            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_mais")]
                        ]))
        except Exception as e:
            logger.error(f"Suporte restart error: {e}")
            await safe_edit(query, f"❌ <b>Erro de conexão:</b>\n<code>{str(e)[:200]}</code>", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Tentar novamente", callback_data="adm_restart_suporte")],
                [InlineKeyboardButton("🔙 Voltar", callback_data="adm_mais")]
            ]))
    
    elif data == "adm_limpar_cache":
        await query.answer("✅ Cache limpo!", show_alert=True)
    
    elif data == "adm_stats":
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
        
        text = (
            f"📊 <b>ESTATÍSTICAS</b>\n\n"
            f"👥 Usuários: {total_users}\n"
            f"🛒 Vendas total: {total_sales}\n"
            f"💰 Receita total: R${total_revenue:.2f}\n"
            f"📦 Estoque: {total_stock}\n"
            f"💎 Saldo total users: R${total_balance:.2f}\n\n"
            f"📅 <b>HOJE:</b>\n"
            f"🛒 Vendas: {today_sales['c']}\n"
            f"💰 Receita: R${today_sales['s']:.2f}"
        )
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Atualizar", callback_data="adm_stats")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_mais")]
        ]))
    
    # ===== ALTERAR TEXTOS/FOTOS =====
    elif data == "adm_textos":
        text = "💬 <b>Alterar textos</b>"
        buttons = [
            [InlineKeyboardButton("💬 Textos tela inicial", callback_data="adm_texto_inicio")],
            [InlineKeyboardButton("💬 Mensagem após a compra", callback_data="adm_texto_compra")],
            [InlineKeyboardButton("📌 Link de suporte", callback_data="adm_link_suporte")],
            [InlineKeyboardButton("📸 Foto de boas-vindas", callback_data="adm_foto_welcome")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_mais")]
        ]
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(buttons))
    
    elif data == "adm_texto_inicio":
        current = get_config('welcome_text') or 'Padrão'
        text = (
            f"💬 <b>Texto da tela inicial</b>\n\n"
            f"Texto atual:\n<i>{current[:500]}</i>\n\n"
            f"Para alterar use:\n<code>/setwelcome SEU TEXTO AQUI</code>"
        )
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_textos")]
        ]))
    
    elif data == "adm_texto_compra":
        current = get_config('msg_pos_compra') or '𝑁𝐴̃𝑂 𝑆𝐸 𝑃𝑅𝐸𝑂𝐶𝑈𝑃𝐸,𝐴𝑂 𝑉𝐸𝑁𝐶𝐸𝑅 𝑁𝑂𝑇𝐼𝐹𝐼𝐶𝐴𝑅𝐸𝑀𝑂𝑆 𝑉𝑂𝐶𝐸̂!😃🚀'
        text = (
            f"💬 <b>Mensagem após a compra</b>\n\n"
            f"Mensagem atual:\n<i>{current[:500]}</i>\n\n"
            f"Para alterar use:\n<code>/setmsgcompra SUA MENSAGEM</code>"
        )
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_textos")]
        ]))
    
    elif data == "adm_link_suporte":
        text = (
            f"📌 <b>Link de suporte</b>\n\n"
            f"Atual: {SUPPORT_BOT}\n\n"
            f"Para alterar use:\n<code>/setsuporte LINK</code>"
        )
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_textos")]
        ]))
    
    elif data == "adm_foto_welcome":
        text = (
            "📸 <b>Foto de boas-vindas</b>\n\n"
            "Para alterar: envie uma foto no chat e responda a ela com /setphoto"
        )
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_textos")]
        ]))
    
    # ===== VENDAS - SUB ACTIONS =====
    elif data == "adm_add_logins":
        text = (
            "🛒 <b>Adicionar Logins</b>\n\n"
            "Modo 1 - Um por um:\n"
            "<code>/addlogin PRODUTO===PREÇO===email senha===VALIDADE===MENSAGEM</code>\n\n"
            "Modo 2 - Importar arquivo:\n"
            "Envie um arquivo .txt e responda com /importar\n\n"
            "Formato do arquivo (cada linha):\n"
            "<code>PRODUTO|PREÇO|email senha||00|VALIDADE|MSG</code>"
        )
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_vendas")]
        ]))
    
    elif data == "adm_estoque_detail":
        conn = get_db()
        products = conn.execute(
            "SELECT name, price, COUNT(*) as qty FROM products WHERE sold = 0 GROUP BY name, price ORDER BY name"
        ).fetchall()
        conn.close()
        
        text = "📦 <b>ESTOQUE DETALHADO</b>\n\n"
        for p in products:
            text += f"📦 {p['name']} - R${p['price']:.0f} → {p['qty']} un\n"
        if not products:
            text += "Vazio!"
        
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Atualizar", callback_data="adm_estoque_detail")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_vendas")]
        ]))
    
    elif data == "adm_modo_vendas":
        mode = get_config('venda_mode') or 'automatico'
        text = (
            f"🛒 <b>Modo de Vendas</b>\n\n"
            f"Modo atual: <b>{mode.upper()}</b>\n\n"
            f"<code>/modovenda automatico</code> - Entrega automática\n"
            f"<code>/modovenda manual</code> - Entrega manual"
        )
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_vendas")]
        ]))
    
    elif data == "adm_alterar_precos":
        conn = get_db()
        products = conn.execute(
            "SELECT DISTINCT name, price FROM products WHERE sold = 0 ORDER BY name"
        ).fetchall()
        conn.close()
        
        text = "💰 <b>Alterar Preços</b>\n\n"
        for p in products:
            text += f"📦 {p['name']} - R${p['price']:.0f}\n"
        text += "\nComando: <code>/alterarpreco PRODUTO NOVO_PRECO</code>"
        
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_vendas")]
        ]))
    
    elif data == "adm_alterar_nomes":
        conn = get_db()
        products = conn.execute("SELECT DISTINCT name FROM products WHERE sold = 0 ORDER BY name").fetchall()
        conn.close()
        
        text = "😀 <b>Alterar Nome dos Logins</b>\n\nNomes atuais:\n"
        for p in products:
            text += f"  · {p['name']}\n"
        text += "\nComando: <code>/alterarnome NOME_ATUAL===NOVO_NOME</code>"
        
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_vendas")]
        ]))
    
    elif data == "adm_baixar_estoque":
        conn = get_db()
        items = conn.execute("SELECT name, price, credentials, validity FROM products WHERE sold = 0 ORDER BY name").fetchall()
        conn.close()
        
        if not items:
            await query.answer("📦 Estoque vazio!", show_alert=True)
            return
        
        content = "PRODUTO|PREÇO|CREDENCIAIS|VALIDADE\n"
        for i in items:
            content += f"{i['name']}|{i['price']}|{i['credentials']}|{i['validity']}\n"
        
        buf = BytesIO(content.encode('utf-8'))
        buf.name = "estoque_completo.txt"
        await query.message.reply_document(document=buf, filename="estoque_completo.txt",
            caption=f"📦 {len(items)} logins no estoque")
    
    elif data == "adm_baixar_vendas_menu":
        # Menu principal de download de vendas
        conn = get_db()
        total = conn.execute("SELECT count(*) FROM sales").fetchone()[0]
        hoje = conn.execute("SELECT count(*) FROM sales WHERE date(created_at) = date('now','localtime')").fetchone()[0]
        semana = conn.execute("SELECT count(*) FROM sales WHERE date(created_at) >= date('now','localtime','-7 days')").fetchone()[0]
        mes = conn.execute("SELECT count(*) FROM sales WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now','localtime')").fetchone()[0]
        conn.close()
        
        text = (
            f"📅 <b>BAIXAR VENDAS</b>\n\n"
            f"📊 Hoje: <b>{hoje}</b> vendas\n"
            f"📊 Semana: <b>{semana}</b> vendas\n"
            f"📊 Mês: <b>{mes}</b> vendas\n"
            f"📊 Total: <b>{total}</b> vendas\n\n"
            f"Escolha o período:"
        )
        buttons = [
            [InlineKeyboardButton("📅 Hoje", callback_data="adm_baixar_vendidos_hoje"),
             InlineKeyboardButton("📅 Ontem", callback_data="adm_baixar_vendidos_ontem")],
            [InlineKeyboardButton("📅 Últimos 7 dias", callback_data="adm_baixar_vendidos_semana"),
             InlineKeyboardButton("📅 Últimos 15 dias", callback_data="adm_baixar_vendidos_15d")],
            [InlineKeyboardButton("📅 Este mês", callback_data="adm_baixar_vendidos_mes"),
             InlineKeyboardButton("📅 Mês passado", callback_data="adm_baixar_vendidos_mes_passado")],
            [InlineKeyboardButton("📅 Escolher dia específico", callback_data="adm_baixar_vendidos_dia")],
            [InlineKeyboardButton("📅 Escolher período (de/até)", callback_data="adm_baixar_vendidos_periodo")],
            [InlineKeyboardButton("📥 Baixar TUDO", callback_data="adm_baixar_vendidos_total")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_vendas")]
        ]
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(buttons))
    
    elif data == "adm_baixar_vendidos_hoje":
        conn = get_db()
        items = conn.execute(
            "SELECT s.*, u.username FROM sales s LEFT JOIN users u ON s.telegram_id = u.telegram_id WHERE date(s.created_at) = date('now','localtime') ORDER BY s.created_at DESC"
        ).fetchall()
        conn.close()
        today_str = datetime.now(BRT).strftime("%d-%m-%Y")
        await _send_sales_file(query, items, f"vendas_{today_str}.txt", f"Vendas de Hoje ({today_str})")
    
    elif data == "adm_baixar_vendidos_ontem":
        conn = get_db()
        items = conn.execute(
            "SELECT s.*, u.username FROM sales s LEFT JOIN users u ON s.telegram_id = u.telegram_id WHERE date(s.created_at) = date('now','localtime','-1 day') ORDER BY s.created_at DESC"
        ).fetchall()
        conn.close()
        yesterday_str = (datetime.now(BRT) - timedelta(days=1)).strftime("%d-%m-%Y")
        await _send_sales_file(query, items, f"vendas_{yesterday_str}.txt", f"Vendas de Ontem ({yesterday_str})")
    
    elif data == "adm_baixar_vendidos_semana":
        conn = get_db()
        items = conn.execute(
            "SELECT s.*, u.username FROM sales s LEFT JOIN users u ON s.telegram_id = u.telegram_id WHERE date(s.created_at) >= date('now','localtime','-7 days') ORDER BY s.created_at DESC"
        ).fetchall()
        conn.close()
        await _send_sales_file(query, items, "vendas_ultimos_7_dias.txt", "Vendas Últimos 7 Dias")
    
    elif data == "adm_baixar_vendidos_15d":
        conn = get_db()
        items = conn.execute(
            "SELECT s.*, u.username FROM sales s LEFT JOIN users u ON s.telegram_id = u.telegram_id WHERE date(s.created_at) >= date('now','localtime','-15 days') ORDER BY s.created_at DESC"
        ).fetchall()
        conn.close()
        await _send_sales_file(query, items, "vendas_ultimos_15_dias.txt", "Vendas Últimos 15 Dias")
    
    elif data == "adm_baixar_vendidos_mes":
        conn = get_db()
        items = conn.execute(
            "SELECT s.*, u.username FROM sales s LEFT JOIN users u ON s.telegram_id = u.telegram_id WHERE strftime('%Y-%m', s.created_at) = strftime('%Y-%m', 'now','localtime') ORDER BY s.created_at DESC"
        ).fetchall()
        conn.close()
        mes_str = datetime.now(BRT).strftime("%m-%Y")
        await _send_sales_file(query, items, f"vendas_mes_{mes_str}.txt", f"Vendas do Mês ({mes_str})")
    
    elif data == "adm_baixar_vendidos_mes_passado":
        conn = get_db()
        items = conn.execute(
            "SELECT s.*, u.username FROM sales s LEFT JOIN users u ON s.telegram_id = u.telegram_id WHERE strftime('%Y-%m', s.created_at) = strftime('%Y-%m', 'now','localtime','-1 month') ORDER BY s.created_at DESC"
        ).fetchall()
        conn.close()
        mes_passado = (datetime.now(BRT).replace(day=1) - timedelta(days=1))
        mes_str = mes_passado.strftime("%m-%Y")
        await _send_sales_file(query, items, f"vendas_mes_{mes_str}.txt", f"Vendas do Mês Passado ({mes_str})")
    
    elif data == "adm_baixar_vendidos_dia":
        # Mostra últimos 14 dias como botões pra escolher
        buttons = []
        now = datetime.now(BRT)
        for i in range(14):
            d = now - timedelta(days=i)
            label = d.strftime("%d/%m/%Y")
            if i == 0:
                label += " (hoje)"
            elif i == 1:
                label += " (ontem)"
            cb = f"adm_baixar_dia_{d.strftime('%Y-%m-%d')}"
            buttons.append([InlineKeyboardButton(f"📅 {label}", callback_data=cb)])
        buttons.append([InlineKeyboardButton("📝 Digitar data específica", callback_data="adm_baixar_dia_manual")])
        buttons.append([InlineKeyboardButton("🔙 Voltar", callback_data="adm_baixar_vendas_menu")])
        await safe_edit(query, "📅 <b>Escolha o dia:</b>", reply_markup=InlineKeyboardMarkup(buttons))
    
    elif data.startswith("adm_baixar_dia_") and data != "adm_baixar_dia_manual":
        target_date = data.replace("adm_baixar_dia_", "")  # YYYY-MM-DD
        conn = get_db()
        items = conn.execute(
            "SELECT s.*, u.username FROM sales s LEFT JOIN users u ON s.telegram_id = u.telegram_id WHERE date(s.created_at) = ? ORDER BY s.created_at DESC",
            (target_date,)
        ).fetchall()
        conn.close()
        try:
            dt = datetime.strptime(target_date, "%Y-%m-%d")
            label = dt.strftime("%d-%m-%Y")
        except:
            label = target_date
        await _send_sales_file(query, items, f"vendas_{label}.txt", f"Vendas de {label}")
    
    elif data == "adm_baixar_dia_manual":
        context.user_data['adm_step'] = 'baixar_por_data'
        await safe_edit(query,
            "📅 <b>Digite a data no formato DD/MM/AAAA</b>\n\n"
            "Exemplo: <code>15/04/2026</code>\n\n"
            "Ou digite um período: <code>10/04/2026-20/04/2026</code>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar", callback_data="adm_baixar_vendas_menu")]])
        )
    
    elif data == "adm_baixar_vendidos_periodo":
        context.user_data['adm_step'] = 'baixar_por_periodo'
        await safe_edit(query,
            "📅 <b>Digite o período no formato:</b>\n\n"
            "<code>DD/MM/AAAA-DD/MM/AAAA</code>\n\n"
            "Exemplo: <code>01/04/2026-15/04/2026</code>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar", callback_data="adm_baixar_vendas_menu")]])
        )
    
    elif data == "adm_baixar_vendidos_total":
        conn = get_db()
        items = conn.execute(
            "SELECT s.*, u.username FROM sales s LEFT JOIN users u ON s.telegram_id = u.telegram_id ORDER BY s.created_at DESC"
        ).fetchall()
        conn.close()
        await _send_sales_file(query, items, "vendas_total.txt", "Todas as Vendas")
    
    elif data == "adm_baixar_vendidos_nome":
        context.user_data['adm_step'] = 'baixar_por_nome'
        await safe_edit(query,
            "📝 Digite o nome do produto para baixar os vendidos:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar", callback_data="adm_vendas")]])
        )
    
    elif data == "adm_deletar_estoque":
        text = (
            "🗑 <b>ATENÇÃO!</b>\n\n"
            "Isso vai deletar TODOS os logins do estoque!\n\n"
            "Tem certeza?"
        )
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ SIM, DELETAR TUDO", callback_data="adm_deletar_estoque_confirm")],
            [InlineKeyboardButton("❌ Cancelar", callback_data="adm_vendas")]
        ]))
    
    elif data == "adm_deletar_estoque_confirm":
        conn = get_db()
        deleted = conn.execute("DELETE FROM products WHERE sold = 0").rowcount
        conn.commit()
        conn.close()
        await query.answer(f"🗑 {deleted} logins deletados!", show_alert=True)
        query.data = "adm_vendas"
        await adm_callback(update, context)
    
    elif data.startswith("adm_ban_"):
        uid = data.replace("adm_ban_", "")
        conn = get_db()
        conn.execute("UPDATE users SET banned = 1 WHERE telegram_id = ?", (uid,))
        conn.commit()
        conn.close()
        await query.answer(f"⛔ Usuário {uid} banido!", show_alert=True)
    
    elif data.startswith("adm_unban_"):
        uid = data.replace("adm_unban_", "")
        conn = get_db()
        conn.execute("UPDATE users SET banned = 0 WHERE telegram_id = ?", (uid,))
        conn.commit()
        conn.close()
        await query.answer(f"✅ Usuário {uid} desbanido!", show_alert=True)
    
    elif data == "adm_deletar_login":
        context.user_data['adm_step'] = 'deletar_login'
        conn = get_db()
        products = conn.execute(
            "SELECT DISTINCT name, COUNT(*) as qty FROM products WHERE sold = 0 GROUP BY name"
        ).fetchall()
        conn.close()
        
        text = "🗑 <b>Deletar Login Específico</b>\n\nDigite o nome do produto para deletar:\n\n"
        for p in products:
            text += f"  · {p['name']} ({p['qty']} un)\n"
        text += "\nOu use: <code>/removelogin NOME</code>"
        
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Voltar", callback_data="adm_vendas")]
        ]))


async def _send_sales_file(query, items, filename, title, message=None):
    """Helper to send sales as file. Accepts query (callback) or message (text handler)."""
    reply_target = message or (query.message if query else None)
    
    if not items:
        if query:
            await query.answer(f"📭 Nenhuma venda encontrada!", show_alert=True)
        elif reply_target:
            await reply_target.reply_text("📭 Nenhuma venda encontrada!")
        return
    
    content = ""
    total_valor = 0
    for i in items:
        creds = i['credentials'].rstrip(':')
        # Format date to DD/MM/YYYY - HH:MM
        date_str = i['created_at'] or ''
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            date_str = dt.strftime("%d/%m/%Y - %H:%M")
        except:
            pass
        content += f"{i['product_name']}|{int(i['price'])}|{creds}||{i['telegram_id']}|{date_str}\n"
        total_valor += i['price']
    
    buf = BytesIO(content.encode('utf-8'))
    buf.name = filename
    caption = f"📊 {title}: {len(items)} vendas | R${total_valor:,.0f}"
    await reply_target.reply_document(document=buf, filename=filename, caption=caption)


# ===== NEW ADMIN COMMANDS FOR PIX/SALDO CONFIGS =====

async def recargaminima(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Use: /recargaminima VALOR")
        return
    set_config('pix_min', context.args[0])
    await update.message.reply_text(f"✅ Pix mínimo alterado para R${context.args[0]}")

async def recargamaxima(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Use: /recargamaxima VALOR")
        return
    set_config('pix_max', context.args[0])
    await update.message.reply_text(f"✅ Pix máximo alterado para R${context.args[0]}")

async def definirtokenmp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Use: /definirtokenmp TOKEN")
        return
    token = ' '.join(context.args)
    set_config('mp_token', token)
    global sdk
    sdk = mercadopago.SDK(token)
    await update.message.reply_text("✅ Token Mercado Pago atualizado!")

async def chavepix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Use: /chavepix CHAVE")
        return
    set_config('pix_manual_key', ' '.join(context.args))
    await update.message.reply_text("✅ Chave PIX manual atualizada!")

async def nomepix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Use: /nomepix NOME")
        return
    set_config('pix_manual_name', ' '.join(context.args))
    await update.message.reply_text("✅ Nome do PIX manual atualizado!")

async def cancelarpix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Use: /cancelarpix ID_PAGAMENTO")
        return
    mp_id = context.args[0]
    conn = get_db()
    conn.execute("UPDATE payments SET status = 'cancelled' WHERE mp_payment_id = ?", (mp_id,))
    conn.commit()
    conn.close()
    # Remove check job
    jobs = context.job_queue.get_jobs_by_name(f"payment_{mp_id}")
    for job in jobs:
        job.schedule_removal()
    await update.message.reply_text(f"✅ PIX {mp_id} cancelado!")

async def listarpix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    conn = get_db()
    pending = conn.execute("SELECT * FROM payments WHERE status = 'pending' ORDER BY created_at DESC LIMIT 30").fetchall()
    conn.close()
    
    if not pending:
        await update.message.reply_text("📋 Nenhum PIX pendente.")
        return
    
    text = "📋 <b>PIX PENDENTES</b>\n\n"
    for p in pending:
        text += f"🔹 ID: <code>{p['mp_payment_id']}</code> | User: {p['telegram_id']} | R${p['amount']:.2f} | {p['created_at']}\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def saldoemdobro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if len(context.args) < 2:
        await update.message.reply_text("Use: /saldoemdobro SITUAÇÃO VALOR\nEx: /saldoemdobro 1 50")
        return
    set_config('saldo_dobro', context.args[0])
    set_config('saldo_dobro_min', context.args[1])
    status = "ATIVADO ✅" if context.args[0] == '1' else "DESATIVADO ❌"
    await update.message.reply_text(f"✅ Saldo em dobro: {status}\nValor mínimo: R${context.args[1]}")

async def porcentagemrecarga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if len(context.args) < 3:
        await update.message.reply_text("Use: /porcentagemrecarga SITUAÇÃO VALOR PORCENTAGEM\nEx: /porcentagemrecarga 1 50 10")
        return
    set_config('porcent_recarga', context.args[0])
    set_config('porcent_recarga_min', context.args[1])
    set_config('porcent_recarga_porcent', context.args[2])
    status = "ATIVADO ✅" if context.args[0] == '1' else "DESATIVADO ❌"
    await update.message.reply_text(f"✅ Porcentagem por recarga: {status}\nMínimo: R${context.args[1]}\nPorcentagem: {context.args[2]}%")

async def bonusregistro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Use: /bonusregistro VALOR")
        return
    set_config('bonus_registro', context.args[0])
    await update.message.reply_text(f"✅ Bônus de registro: R${context.args[0]}")

async def protecaosaldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Use: /protecaosaldo VALOR")
        return
    set_config('protecao_saldo', context.args[0])
    await update.message.reply_text(f"✅ Proteção de saldo: R${context.args[0]}")

async def gengifts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if len(context.args) < 2:
        await update.message.reply_text("Use: /gengifts QUANTIDADE VALOR\nEx: /gengifts 5 10")
        return
    qty = int(context.args[0])
    amount = float(context.args[1])
    
    conn = get_db()
    codes = []
    for _ in range(qty):
        code = 'LK' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        conn.execute("INSERT INTO gifts (code, amount, created_by) VALUES (?, ?, ?)",
                     (code, amount, str(update.effective_user.id)))
        codes.append(code)
    conn.commit()
    conn.close()
    
    text = f"🎁 <b>{qty} GIFTS GERADOS!</b>\n💰 Valor: R${amount:.2f} cada\n\n"
    for c in codes:
        text += f"<code>/resgatar {c}</code>\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Use: /userinfo ID_TELEGRAM")
        return
    uid = context.args[0]
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    sales_count = conn.execute("SELECT COUNT(*) as c FROM sales WHERE telegram_id = ?", (uid,)).fetchone()['c']
    total_spent = conn.execute("SELECT COALESCE(SUM(price), 0) as s FROM sales WHERE telegram_id = ?", (uid,)).fetchone()['s']
    conn.close()
    
    if not user:
        await update.message.reply_text("❌ Usuário não encontrado!")
        return
    
    banned_text = "⛔ BANIDO" if user['banned'] else "✅ Normal"
    text = (
        f"👤 <b>Info do Usuário</b>\n\n"
        f"🆔 ID: <code>{user['telegram_id']}</code>\n"
        f"👤 Nome: {user['first_name'] or 'N/A'}\n"
        f"📛 Username: @{user['username'] or 'N/A'}\n"
        f"💰 Saldo: R${user['balance']:.2f}\n"
        f"🛒 Compras: {sales_count}\n"
        f"💸 Total gasto: R${total_spent:.2f}\n"
        f"📅 Cadastro: {user['created_at']}\n"
        f"🔒 Status: {banned_text}"
    )
    
    buttons = []
    if user['banned']:
        buttons.append([InlineKeyboardButton("✅ Desbanir", callback_data=f"adm_unban_{uid}")])
    else:
        buttons.append([InlineKeyboardButton("⛔ Banir", callback_data=f"adm_ban_{uid}")])
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))

async def enviarspam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    text = update.message.text.replace('/enviarspam', '', 1).strip()
    if not text:
        await update.message.reply_text("Use: /enviarspam TEXTO")
        return
    
    await update.message.reply_text("🚀 Enviando spam...")
    sent, failed = await send_spam(context, 'text', None, text)
    await update.message.reply_text(f"✅ Spam enviado!\n📨 {sent} enviados | ❌ {failed} falhas")

async def alterarpreco(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    text = update.message.text.replace('/alterarpreco', '', 1).strip()
    parts = text.rsplit(' ', 1)
    if len(parts) < 2:
        await update.message.reply_text("Use: /alterarpreco NOME_PRODUTO NOVO_PRECO")
        return
    name = parts[0].strip()
    new_price = float(parts[1])
    conn = get_db()
    updated = conn.execute("UPDATE products SET price = ? WHERE name = ? AND sold = 0", (new_price, name)).rowcount
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ {updated} logins de '{name}' alterados para R${new_price:.0f}")

async def alterarnome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    text = update.message.text.replace('/alterarnome', '', 1).strip()
    parts = text.split('===')
    if len(parts) < 2:
        await update.message.reply_text("Use: /alterarnome NOME_ATUAL===NOVO_NOME")
        return
    old_name = parts[0].strip()
    new_name = parts[1].strip()
    conn = get_db()
    updated = conn.execute("UPDATE products SET name = ? WHERE name = ?", (new_name, old_name)).rowcount
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ {updated} logins renomeados de '{old_name}' para '{new_name}'")

async def setmsgcompra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    text = update.message.text.replace('/setmsgcompra', '', 1).strip()
    if not text:
        await update.message.reply_text("Use: /setmsgcompra MENSAGEM")
        return
    set_config('msg_pos_compra', text)
    await update.message.reply_text("✅ Mensagem pós-compra atualizada!")

async def setsuporte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    text = update.message.text.replace('/setsuporte', '', 1).strip()
    if not text:
        await update.message.reply_text("Use: /setsuporte LINK")
        return
    set_config('support_link', text)
    global SUPPORT_BOT
    SUPPORT_BOT = text
    await update.message.reply_text(f"✅ Link de suporte atualizado: {text}")

async def fixadd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: fix all products/sales with /add in credentials."""
    if not is_admin(update.effective_user.id):
        return
    
    conn = get_db()
    
    # 1) Products with ONLY "/add" or "/ADD" (no email) — DELETE
    only_add = conn.execute(
        "SELECT id, name, credentials, sold FROM products WHERE LOWER(TRIM(credentials)) IN ('/add', '/add ')"
    ).fetchall()
    
    # 2) Products with "/ADD email@xxx" — CLEAN the /ADD prefix
    like_add = conn.execute(
        "SELECT id, name, credentials, sold FROM products WHERE (credentials LIKE '/add %' OR credentials LIKE '/ADD %') AND credentials LIKE '%@%'"
    ).fetchall()
    
    # 3) Sales with /add in credentials
    sales_add = conn.execute(
        "SELECT id, credentials FROM sales WHERE LOWER(TRIM(credentials)) IN ('/add', '/add ') OR credentials LIKE '/add %' OR credentials LIKE '/ADD %'"
    ).fetchall()
    
    report = f"🔧 <b>FIX /ADD</b>\n\n"
    
    # Delete products with only /add
    deleted = 0
    for p in only_add:
        conn.execute("DELETE FROM products WHERE id = ?", (p['id'],))
        deleted += 1
    report += f"🗑️ Deletados: <b>{deleted}</b> produtos (só /ADD, sem email)\n"
    
    # Clean /ADD prefix from products with email
    cleaned = 0
    for p in like_add:
        old_cred = p['credentials']
        import re
        new_cred = re.sub(r'^/[Aa][Dd][Dd]\s+', '', old_cred).strip()
        # Normalize multiple spaces
        new_cred = re.sub(r'\s+', ' ', new_cred)
        conn.execute("UPDATE products SET credentials = ? WHERE id = ?", (new_cred, p['id']))
        cleaned += 1
    report += f"✏️ Limpos: <b>{cleaned}</b> produtos (removido /ADD, mantido email)\n"
    
    # Clean sales
    sales_cleaned = 0
    for s in sales_add:
        old_cred = s['credentials']
        if old_cred.strip().lower() in ['/add', '/add ']:
            new_cred = '(credencial não disponível)'
        else:
            new_cred = re.sub(r'^/[Aa][Dd][Dd]\s+', '', old_cred).strip()
            new_cred = re.sub(r'\s+', ' ', new_cred)
        conn.execute("UPDATE sales SET credentials = ? WHERE id = ?", (new_cred, s['id']))
        sales_cleaned += 1
    report += f"📋 Vendas corrigidas: <b>{sales_cleaned}</b>\n"
    
    conn.commit()
    conn.close()
    
    report += f"\n✅ Total: {deleted + cleaned + sales_cleaned} registros corrigidos"
    await update.message.reply_text(report, parse_mode=ParseMode.HTML)
#        trigger_backup_async("fixadd")  # REMOVED - backup only every 1h


async def setsuporteapi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        current = get_config('support_api_url') or 'Não definido'
        await update.message.reply_text(f"URL atual: {current}\n\nUse: /setsuporteapi URL\nEx: /setsuporteapi https://trocasdolk-production.up.railway.app")
        return
    url = context.args[0].rstrip('/')
    set_config('support_api_url', url)
    await update.message.reply_text(f"✅ URL da API de suporte: {url}")

async def linkcanal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Use: /linkcanal LINK")
        return
    set_config('channel_link', ' '.join(context.args))
    await update.message.reply_text("✅ Link do canal atualizado!")

async def rankingpix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    conn = get_db()
    ranking = conn.execute(
        "SELECT p.telegram_id, u.username, u.first_name, SUM(p.amount) as total, COUNT(*) as qty "
        "FROM payments p LEFT JOIN users u ON p.telegram_id = u.telegram_id "
        "WHERE p.status = 'approved' GROUP BY p.telegram_id ORDER BY total DESC LIMIT 20"
    ).fetchall()
    conn.close()
    
    if not ranking:
        await update.message.reply_text("📊 Nenhum PIX aprovado ainda.")
        return
    
    text = "🏆 <b>RANKING DE PIX</b>\n\n"
    for i, r in enumerate(ranking, 1):
        name = r['first_name'] or r['username'] or 'N/A'
        text += f"{i}. {name} - R${r['total']:.2f} ({r['qty']} pix)\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ===== GENERIC MESSAGE HANDLER =====
async def adminmsg_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin clica em 'Enviar Mensagem' na notificação de compra"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        return
    
    target_user_id = query.data.replace("adminmsg_", "")
    context.user_data['adminmsg_target'] = target_user_id
    
    await query.message.reply_text(
        f"📩 <b>Enviar mensagem para o usuário <code>{target_user_id}</code></b>\n\n"
        f"Digite a mensagem que deseja enviar:",
        parse_mode=ParseMode.HTML
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    
    msg = update.message
    user_id = update.effective_user.id if update.effective_user else 0
    
    # Admin define novo produto para /add
    if is_admin(user_id) and context.user_data.get('add_awaiting_product') and msg.text:
        context.user_data['add_awaiting_product'] = False
        parts = msg.text.strip().split('===')
        if len(parts) != 2:
            await msg.reply_text("❌ Formato: NOME===PREÇO\nExemplo: TELA EXTRA===10")
            return
        product_name = parts[0].strip()
        try:
            price = float(parts[1].strip())
        except:
            await msg.reply_text("❌ Preço inválido!")
            return
        logins = context.user_data.get('add_logins', [])
        if not logins:
            await msg.reply_text("❌ Nenhum login pendente.")
            return
        added, dupes = await _insert_logins(logins, product_name, price, str(user_id))
        context.user_data.pop('add_logins', None)
        dupe_text = f"\n🔄 Duplicatas substituídas: {dupes}" if dupes > 0 else ""
        await msg.reply_text(
            f"✅ <b>{added} login(s) adicionados!</b>\n\n"
            f"📦 Produto: {product_name}\n"
            f"💵 Preço: R${price:.2f}\n"
            f"⏱ Validade: {DEFAULT_VALIDITY}\n"
            f"🔢 Total: {added}{dupe_text}",
            parse_mode=ParseMode.HTML)
        return
    
    # Admin envia mensagem para usuário (botão "Enviar Mensagem")
    if is_admin(user_id) and context.user_data.get('adminmsg_target'):
        target_id = context.user_data.pop('adminmsg_target')
        try:
            if msg.text:
                await context.bot.send_message(target_id,
                    f"📩 <b>Mensagem do Suporte LK Store:</b>\n\n{msg.text}",
                    parse_mode=ParseMode.HTML)
            elif msg.photo:
                await context.bot.send_photo(target_id, msg.photo[-1].file_id,
                    caption=f"📩 <b>Mensagem do Suporte LK Store:</b>\n\n{msg.caption or ''}",
                    parse_mode=ParseMode.HTML)
            elif msg.video:
                await context.bot.send_video(target_id, msg.video.file_id,
                    caption=f"📩 <b>Mensagem do Suporte LK Store:</b>\n\n{msg.caption or ''}",
                    parse_mode=ParseMode.HTML)
            await msg.reply_text(f"✅ Mensagem enviada para <code>{target_id}</code>!", parse_mode=ParseMode.HTML)
        except Exception as e:
            await msg.reply_text(f"❌ Erro ao enviar: {e}")
        return
    
    # Admin sends photo/video → auto spam flow
    if is_admin(user_id) and (msg.photo or msg.video):
        if msg.photo:
            context.user_data['spam_media_type'] = 'photo'
            context.user_data['spam_media_id'] = msg.photo[-1].file_id
            context.user_data['spam_text'] = msg.caption or ''
        elif msg.video:
            context.user_data['spam_media_type'] = 'video'
            context.user_data['spam_media_id'] = msg.video.file_id
            context.user_data['spam_text'] = msg.caption or ''
        
        context.user_data['spam_step'] = 'schedule'
        await msg.reply_text(
            "✅ Conteúdo recebido!\n\nAgora escolha:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Enviar Agora", callback_data="spam_now")],
                [InlineKeyboardButton("⏰ Programar Horários", callback_data="spam_schedule")],
                [InlineKeyboardButton("❌ Cancelar", callback_data="spam_cancel")]
            ])
        )
        return
    
    if not msg.text:
        # Check for spam media (from /spam command flow)
        if context.user_data.get('spam_step') == 'media':
            await handle_spam_media(update, context)
        return
    
    text = msg.text.strip()
    
    # Check spam steps
    if context.user_data.get('spam_step') == 'media':
        result = await handle_spam_media(update, context)
        if result:
            return
    
    if context.user_data.get('spam_step') == 'times':
        result = await handle_spam_times(update, context)
        if result:
            return
    
    # Admin step: baixar por nome
    if context.user_data.get('adm_step') == 'baixar_por_nome' and is_admin(update.effective_user.id):
        context.user_data['adm_step'] = None
        product_name = text.strip()
        conn = get_db()
        items = conn.execute(
            "SELECT s.*, u.username FROM sales s LEFT JOIN users u ON s.telegram_id = u.telegram_id WHERE s.product_name = ? ORDER BY s.created_at DESC",
            (product_name,)
        ).fetchall()
        conn.close()
        if not items:
            await update.message.reply_text(f"📭 Nenhuma venda de '{product_name}'")
        else:
            content = ""
            for i in items:
                creds = i['credentials']
                if ':' in creds:
                    email, senha = creds.split(':', 1)
                else:
                    email, senha = creds, ''
                date_str = i['created_at'] or ''
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                    date_str = dt.strftime("%d/%m/%Y - %H:%M")
                except:
                    pass
                content += f"{i['product_name']}|{int(i['price'])}|{email}|{senha}|{i['telegram_id']}|{date_str}\n"
            buf = BytesIO(content.encode('utf-8'))
            buf.name = f"vendidos_{product_name}.txt"
            await update.message.reply_document(document=buf, filename=f"vendidos_{product_name}.txt",
                caption=f"📊 {len(items)} vendas de {product_name}")
        return
    
    # Admin step: baixar vendas por data específica
    if context.user_data.get('adm_step') in ('baixar_por_data', 'baixar_por_periodo') and is_admin(update.effective_user.id):
        context.user_data['adm_step'] = None
        raw = text.strip()
        
        if '-' in raw and '/' in raw:
            # Período: DD/MM/AAAA-DD/MM/AAAA
            parts = raw.split('-')
            # Reagrupar (DD/MM/AAAA pode ter / mas - separa início e fim)
            # Formato esperado: DD/MM/AAAA-DD/MM/AAAA
            try:
                if len(parts) == 2:
                    dt_from = datetime.strptime(parts[0].strip(), "%d/%m/%Y")
                    dt_to = datetime.strptime(parts[1].strip(), "%d/%m/%Y")
                elif len(parts) == 3:
                    # 10/04/2026-20/04/2026 splits into ['10/04/2026', '20/04/2026'] if using single -
                    # But dates have / not - so this shouldn't happen with clean input
                    dt_from = datetime.strptime(parts[0].strip(), "%d/%m/%Y")
                    dt_to = datetime.strptime(parts[1].strip(), "%d/%m/%Y")
                else:
                    # Try splitting by space, comma etc
                    raise ValueError("formato inválido")
                
                date_from = dt_from.strftime("%Y-%m-%d")
                date_to = dt_to.strftime("%Y-%m-%d")
                conn = get_db()
                items = conn.execute(
                    "SELECT s.*, u.username FROM sales s LEFT JOIN users u ON s.telegram_id = u.telegram_id "
                    "WHERE date(s.created_at) >= ? AND date(s.created_at) <= ? ORDER BY s.created_at DESC",
                    (date_from, date_to)
                ).fetchall()
                conn.close()
                label_from = dt_from.strftime("%d-%m-%Y")
                label_to = dt_to.strftime("%d-%m-%Y")
                await _send_sales_file(query=None, items=items,
                    filename=f"vendas_{label_from}_a_{label_to}.txt",
                    title=f"Vendas de {label_from} a {label_to}",
                    message=update.message)
            except ValueError:
                await update.message.reply_text(
                    "❌ Formato inválido!\n\n"
                    "Use: <code>DD/MM/AAAA-DD/MM/AAAA</code>\n"
                    "Exemplo: <code>01/04/2026-15/04/2026</code>",
                    parse_mode=ParseMode.HTML)
        else:
            # Data única: DD/MM/AAAA
            try:
                dt = datetime.strptime(raw, "%d/%m/%Y")
                target_date = dt.strftime("%Y-%m-%d")
                conn = get_db()
                items = conn.execute(
                    "SELECT s.*, u.username FROM sales s LEFT JOIN users u ON s.telegram_id = u.telegram_id "
                    "WHERE date(s.created_at) = ? ORDER BY s.created_at DESC",
                    (target_date,)
                ).fetchall()
                conn.close()
                label = dt.strftime("%d-%m-%Y")
                await _send_sales_file(query=None, items=items,
                    filename=f"vendas_{label}.txt",
                    title=f"Vendas de {label}",
                    message=update.message)
            except ValueError:
                await update.message.reply_text(
                    "❌ Formato inválido!\n\n"
                    "Use: <code>DD/MM/AAAA</code>\n"
                    "Exemplo: <code>15/04/2026</code>",
                    parse_mode=ParseMode.HTML)
        return
    
    # Admin step: deletar login por nome
    if context.user_data.get('adm_step') == 'deletar_login' and is_admin(update.effective_user.id):
        context.user_data['adm_step'] = None
        product_name = text.strip()
        conn = get_db()
        deleted = conn.execute("DELETE FROM products WHERE name = ? AND sold = 0", (product_name,)).rowcount
        conn.commit()
        conn.close()
        await update.message.reply_text(f"🗑 {deleted} logins de '{product_name}' deletados!")
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
import shutil
import threading
import base64
import urllib.request

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO_VENDAS = os.environ.get("GITHUB_REPO", "netflixiptv5-hub/lkstore")

_BACKUP_TS_FILE = os.path.join(DATA_DIR, ".last_backup_ts")
_backup_lock = threading.Lock()

def _load_last_backup_time():
    """Load last backup timestamp from disk (survives restarts)."""
    try:
        with open(_BACKUP_TS_FILE, "r") as f:
            return float(f.read().strip())
    except:
        return 0

def _save_last_backup_time(ts):
    """Save last backup timestamp to disk."""
    try:
        with open(_BACKUP_TS_FILE, "w") as f:
            f.write(str(ts))
    except:
        pass

_last_backup_time = _load_last_backup_time()

def export_db_json():
    """Export all tables from SQLite as JSON."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        tables = ["products", "sales", "users", "transactions", "payments", "gifts", "bot_config", "scheduled_spam"]
        data = {"backup_date": datetime.now(BRT).isoformat(), "tables": {}}
        for table in tables:
            try:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                data["tables"][table] = [dict(r) for r in rows]
            except:
                data["tables"][table] = []
        conn.close()

        # Stats
        data["stats"] = {
            "estoque_disponivel": len([p for p in data["tables"].get("products", []) if not p.get("sold")]),
            "estoque_vendido": len([p for p in data["tables"].get("products", []) if p.get("sold")]),
            "total_vendas": len(data["tables"].get("sales", [])),
            "total_usuarios": len(data["tables"].get("users", [])),
            "total_transacoes": len(data["tables"].get("transactions", [])),
        }
        return data
    except Exception as e:
        logger.error(f"[BACKUP] Erro ao exportar DB: {e}")
        return None


def send_backup_telegram_vendas(backup_data, trigger="auto"):
    """Send backup JSON file to admin via Telegram."""
    try:
        now = datetime.now(BRT).strftime("%Y%m%d_%H%M")
        filename = f"backup_lkstore_{now}.json"
        json_bytes = json.dumps(backup_data, indent=2, ensure_ascii=False).encode("utf-8")

        stats = backup_data.get("stats", {})
        caption = (
            f"💾 BACKUP LKSTORE {'⚡' if trigger != 'auto' else '🔄'}\n"
            f"📅 {datetime.now(BRT).strftime('%d/%m/%Y %H:%M')}\n"
            f"🔹 Trigger: {trigger}\n\n"
            f"📦 Estoque disponível: {stats.get('estoque_disponivel', 0)}\n"
            f"🛒 Vendidos: {stats.get('estoque_vendido', 0)}\n"
            f"💰 Total vendas: {stats.get('total_vendas', 0)}\n"
            f"👥 Usuários: {stats.get('total_usuarios', 0)}"
        )

        boundary = "----LKBackupBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{ADMIN_IDS[0]}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
            f"Content-Type: application/json\r\n\r\n"
        ).encode("utf-8") + json_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

        url = f"https://api.telegram.org/bot{SUPPORT_BOT_TOKEN}/sendDocument"
        req = urllib.request.Request(url, data=body)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        urllib.request.urlopen(req, timeout=30)
        logger.info(f"[BACKUP] Telegram OK ({trigger})")
        return True
    except Exception as e:
        logger.error(f"[BACKUP] Telegram erro: {e}")
        return False


def send_backup_github_vendas(backup_data):
    """Push backup JSON to GitHub repo."""
    try:
        now = datetime.now(BRT).strftime("%Y%m%d_%H%M")
        filename = f"backups/backup_{now}.json"
        json_str = json.dumps(backup_data, indent=2, ensure_ascii=False)
        content_b64 = base64.b64encode(json_str.encode("utf-8")).decode("utf-8")

        # Check if latest.json exists to get SHA
        url_latest = f"https://api.github.com/repos/{GITHUB_REPO_VENDAS}/contents/backups/latest.json"
        sha_latest = None
        try:
            req = urllib.request.Request(url_latest)
            req.add_header("Authorization", f"token {GITHUB_TOKEN}")
            req.add_header("Accept", "application/vnd.github.v3+json")
            resp = urllib.request.urlopen(req, timeout=10)
            existing = json.loads(resp.read())
            sha_latest = existing.get("sha")
        except:
            pass

        # Upload timestamped backup
        url_file = f"https://api.github.com/repos/{GITHUB_REPO_VENDAS}/contents/{filename}"
        payload = {"message": f"Backup LKStore {now}", "content": content_b64}
        req = urllib.request.Request(url_file, data=json.dumps(payload).encode("utf-8"), method="PUT")
        req.add_header("Authorization", f"token {GITHUB_TOKEN}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/vnd.github.v3+json")
        urllib.request.urlopen(req, timeout=15)

        # Update latest.json
        payload_latest = {"message": f"Backup latest {now}", "content": content_b64}
        if sha_latest:
            payload_latest["sha"] = sha_latest
        req2 = urllib.request.Request(url_latest, data=json.dumps(payload_latest).encode("utf-8"), method="PUT")
        req2.add_header("Authorization", f"token {GITHUB_TOKEN}")
        req2.add_header("Content-Type", "application/json")
        req2.add_header("Accept", "application/vnd.github.v3+json")
        urllib.request.urlopen(req2, timeout=15)

        logger.info(f"[BACKUP] GitHub OK -> {filename}")
        return True
    except Exception as e:
        logger.error(f"[BACKUP] GitHub erro: {e}")
        return False


def do_backup(trigger="auto"):
    """Run full backup: export DB -> Telegram + GitHub. Thread-safe, skips if < 24h since last."""
    global _last_backup_time
    with _backup_lock:
        now = time.time()
        # Skip if last backup was less than 24h ago (survives restarts via file)
        if (now - _last_backup_time) < 86400:
            logger.info(f"[BACKUP] Skipped ({trigger}) - last backup was {int((now - _last_backup_time)/60)}min ago (need 24h)")
            return
        _last_backup_time = now
        _save_last_backup_time(now)

    backup_data = export_db_json()
    if backup_data:
        # Also keep local backup
        try:
            backup_path = os.path.join(DATA_DIR, "lkstore_backup.db")
            shutil.copy2(DB_PATH, backup_path)
        except:
            pass
        send_backup_telegram_vendas(backup_data, trigger)
        send_backup_github_vendas(backup_data)


def trigger_backup_async(trigger="venda"):
    """Trigger backup in background thread (non-blocking)."""
    t = threading.Thread(target=do_backup, args=(trigger,), daemon=True)
    t.start()


def auto_backup_db():
    """Faz backup automático a cada 24h."""
    time.sleep(300)  # 5 min after start
    logger.info("[BACKUP] Sistema de backup iniciado - a cada 24h")
    while True:
        try:
            do_backup("auto")
        except Exception as e:
            logger.error(f"[BACKUP] Erro no backup automático: {e}")
        time.sleep(86400)  # 24 horas

def main():
    init_db()

    # Inicia backup automático em background
    t = threading.Thread(target=auto_backup_db, daemon=True)
    t.start()
    logger.info("🔄 Backup automático iniciado (a cada 24h)")
    
    app = (
        Application.builder()
        .token(TOKEN)
        .connect_timeout(10)
        .read_timeout(10)
        .write_timeout(10)
        .pool_timeout(5)
        .concurrent_updates(True)
        .build()
    )
    
    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pix", pix_command))
    app.add_handler(CommandHandler("cancelar", cancelar))
    app.add_handler(CommandHandler("resgatar", resgatar_command))
    
    # Admin commands
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("adm", admin_panel))
    app.add_handler(CommandHandler("add", add_simple))
    app.add_handler(CallbackQueryHandler(add_simple_callback, pattern="^addto_"))
    app.add_handler(CommandHandler("addlogin", addlogin))
    app.add_handler(CommandHandler("addlogintelas", addlogin))
    app.add_handler(CommandHandler("estoque", estoque))
    app.add_handler(CommandHandler("limparduplicatas", limpar_duplicatas))
    app.add_handler(CommandHandler("removelogin", removelogin))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("addsaldo", addsaldo))
    app.add_handler(CommandHandler("removesaldo", removesaldo))
    app.add_handler(CommandHandler("gift", create_gift))
    app.add_handler(CommandHandler("gengifts", gengifts))
    app.add_handler(CommandHandler("historico", historico))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("setwelcome", setwelcome))
    app.add_handler(CommandHandler("setphoto", setphoto))
    app.add_handler(CommandHandler("spam", spam_command))
    app.add_handler(CommandHandler("cancelarspam", cancel_spam_command))
    app.add_handler(CommandHandler("importar", importar))
    app.add_handler(CommandHandler("importarvendas", importarvendas))
    app.add_handler(CommandHandler("enviarspam", enviarspam))
    # Pix config commands
    app.add_handler(CommandHandler("recargaminima", recargaminima))
    app.add_handler(CommandHandler("recargamaxima", recargamaxima))
    app.add_handler(CommandHandler("definirtokenmp", definirtokenmp))
    app.add_handler(CommandHandler("chavepix", chavepix))
    app.add_handler(CommandHandler("nomepix", nomepix))
    app.add_handler(CommandHandler("cancelarpix", cancelarpix))
    app.add_handler(CommandHandler("listarpix", listarpix))
    app.add_handler(CommandHandler("rankingpix", rankingpix))
    # Saldo config commands
    app.add_handler(CommandHandler("saldoemdobro", saldoemdobro))
    app.add_handler(CommandHandler("porcentagemrecarga", porcentagemrecarga))
    app.add_handler(CommandHandler("bonusregistro", bonusregistro))
    app.add_handler(CommandHandler("protecaosaldo", protecaosaldo))
    # User/vendas config commands
    app.add_handler(CommandHandler("userinfo", userinfo))
    app.add_handler(CommandHandler("alterarpreco", alterarpreco))
    app.add_handler(CommandHandler("alterarnome", alterarnome))
    app.add_handler(CommandHandler("setmsgcompra", setmsgcompra))
    app.add_handler(CommandHandler("setsuporte", setsuporte))
    app.add_handler(CommandHandler("linkcanal", linkcanal))
    app.add_handler(CommandHandler("setsuporteapi", setsuporteapi))
    app.add_handler(CommandHandler("fixadd", fixadd_cmd))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(adm_callback, pattern="^adm_"))
    app.add_handler(CallbackQueryHandler(buy_callback, pattern="^buy$"))
    app.add_handler(CallbackQueryHandler(product_callback, pattern="^product_"))
    app.add_handler(CallbackQueryHandler(confirm_buy, pattern="^confirm_buy$"))
    app.add_handler(CallbackQueryHandler(balance_callback, pattern="^balance$"))
    app.add_handler(CallbackQueryHandler(pix_callback, pattern="^pix_"))
    app.add_handler(CallbackQueryHandler(check_payment_callback, pattern="^check_"))
    app.add_handler(CallbackQueryHandler(cancel_pix_callback, pattern="^cancelpix_"))
    app.add_handler(CallbackQueryHandler(orders_callback, pattern="^orders$"))
    app.add_handler(CallbackQueryHandler(gift_callback, pattern="^gift$"))
    app.add_handler(CallbackQueryHandler(tutorial_compra_callback, pattern="^tutorial_compra$"))
    app.add_handler(CallbackQueryHandler(tutorial_suporte_callback, pattern="^tutorial_suporte$"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(spam_action_callback, pattern="^spam_"))
    app.add_handler(CallbackQueryHandler(adminmsg_callback, pattern="^adminmsg_"))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Spam checker — roda a cada 30s, verifica banco e envia nos horários certos
    app.job_queue.run_repeating(
        spam_checker_job,
        interval=30,
        first=10,
        name="spam_checker"
    )
    logger.info("⏰ Spam checker iniciado (a cada 30s)")
    
    logger.info("🚀 LK Store Bot started!")
    app.run_polling(drop_pending_updates=True, poll_interval=0.5, timeout=10)

if __name__ == "__main__":
    # Import sales history on first run
    try:
        import import_sales
        import_sales.run()
    except Exception as e:
        print(f"Sales import error (non-fatal): {e}")
    # Import users+balances from old bot
    try:
        import import_users
        import_users.run()
    except Exception as e:
        print(f"Users import error (non-fatal): {e}")
    main()
