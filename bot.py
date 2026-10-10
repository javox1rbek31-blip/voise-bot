import asyncio
import logging
import os
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton as B,
    InlineKeyboardMarkup as M,
    Message,
    ErrorEvent
)

# Ma'lumotlar va Sozlamalar
TOKEN = os.environ.get("BOT_TOKEN", "8608877100:AAF9kNtDxRmtMwFjGlNGUT0Zi7X8mQ9AJOI").strip()
OWNER = int(os.environ.get("OWNER_ID", "8785896869"))
DB = os.environ.get("DB_PATH", "/data/bot.db")
PER = 8
PENDING = {}  # Ovozli xabarni almashtirish uchun xotira

logging.basicConfig(level=logging.INFO)
bot = Bot(TOKEN)
dp = Dispatcher()

# --- BAZA BILAN ISHLASH ---
def q(sql, args=(), fetch="all"):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        cur = c.execute(sql, args)
        c.commit()
        return cur.fetchone() if fetch == "one" else cur.fetchall()
    finally:
        c.close()

def init():
    folder = os.path.dirname(DB)
    if folder:
        os.makedirs(folder, exist_ok=True)
    q("CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT)")
    q("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, status TEXT, role TEXT)")
    q("CREATE TABLE IF NOT EXISTS voices (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, name TEXT, file_id TEXT, uid TEXT, dur INTEGER, added_by INTEGER)")
    
    # Bot egasini avtomatik Bosh Admin va tasdiqlangan holatda saqlash
    q("""
        INSERT INTO users (id, name, status, role)
        VALUES (?, 'Owner', 'approved', 'admin')
        ON CONFLICT (id) DO UPDATE SET status='approved', role='admin'
    """, (OWNER,))

def get_log():
    try:
        r = q("SELECT v FROM settings WHERE k='log_chat'", (), "one")
        return int(r["v"]) if r else None
    except Exception:
        return None

def set_log(chat_id):
    q("INSERT INTO settings (k,v) VALUES('log_chat',?) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v", (str(chat_id),))

async def notify(text, kb=None):
    for t in [get_log(), OWNER]:
        if t is None:
            continue
        try:
            await bot.send_message(t, text[:4000], reply_markup=kb)
            return
        except Exception:
            logging.exception("notify failed")

def ok(uid):
    if uid == OWNER:
        return True
    r = q("SELECT status FROM users WHERE id=?", (uid,), "one")
    return bool(r and r["status"] == "approved")

def adm(uid):
    if uid == OWNER:
        return True
    r = q("SELECT status, role FROM users WHERE id=?", (uid,), "one")
    return bool(r and r["status"] == "approved" and r["role"] == "admin")

def uname(u):
    return u.full_name + (f" (@{u.username})" if u.username else "")

# --- ZAYAVKALAR ---
def req_kb(uid):
    return M(inline_keyboard=[
        [
            B(text="✅ Принять", callback_data=f"ok:{uid}"),
            B(text="👑 Принять как админа", callback_data=f"oa:{uid}"),
        ],
        [B(text="❌ Отклонить", callback_data=f"no:{uid}")],
    ])

async def request_access(u):
    if u.id == OWNER:
        return
    if q("SELECT 1 FROM users WHERE id=?", (u.id,), "one"):
        return
    q("INSERT INTO users (id, name, status, role) VALUES (?, ?, 'pending', 'user')", (u.id, uname(u)))
    await notify(f"📩 Новый пользователь запрашивает доступ:\n{uname(u)}\nID: {u.id}", kb=req_kb(u.id))

async def decide(uid, act):
    r = q("SELECT * FROM users WHERE id=?", (uid,), "one")
    if not r:
        return None, ""
    if act in ("ok", "oa"):
        role = "admin" if act == "oa" else "user"
        q("UPDATE users SET status='approved', role=? WHERE id=?", (role, uid))
        mark = "✅ Принят как админ" if role == "admin" else "✅ Принят"
        try:
            await bot.send_message(uid, "Ваша заявка принята.", reply_markup=menu(uid))
        except Exception:
            mark += "\n⚠️ Пользователь ещё не открывал бота (пусть нажмёт /start)"
    else:
        q("UPDATE users SET status='rejected', role='user' WHERE id=?", (uid,))
        mark = "❌ Отклонён"
    return r, mark

@dp.callback_query(F.data
