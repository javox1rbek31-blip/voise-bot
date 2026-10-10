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
PENDING = {}

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
    
    # Bot egasini (Owner) har doim tasdiqlangan Admin qilib saqlash
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

# --- ZAYAVKALAR VA TASDIQLASH ---
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
        
        # FOYDALANUVCHIGA TASDIQLANISH HABARI YUBORISH
        try:
            await bot.send_message(
                uid, 
                "✅ Ваша заявка принята! Теперь вы можете пользоваться ботом.", 
                reply_markup=menu(uid)
            )
        except Exception:
            mark += "\n⚠️ Не удалось отправить сообщение (пользователь заблокировал бота или не нажал /start)."
    else:
        q("UPDATE users SET status='rejected', role='user' WHERE id=?", (uid,))
        mark = "❌ Отклонён"
        try:
            await bot.send_message(uid, "❌ Ваша заявка на доступ отклонена.")
        except Exception:
            pass
            
    return r, mark

@dp.callback_query(F.data.regexp(r"^(ok|oa|no):\d+$"))
async def cb_decide(c: CallbackQuery):
    if not adm(c.from_user.id):
        return await c.answer("Нет доступа", show_alert=True)
    act, uid = c.data.split(":")
    r, mark = await decide(int(uid), act)
    if not r:
        return await c.answer()
    try:
        await c.message.edit_text(f"👤 {r['name']}\nID: {uid}\n\n{mark}")
    except TelegramBadRequest:
        pass
    await c.answer()

# --- MENU VA TUGMALAR ---
def menu(uid):
    rows = [
        [B(text="📜 Список имён", callback_data="list")],
        [B(text="🔊 Получить голосовое", callback_data="get:0")],
    ]
    # Adminlar uchun 2 ta alohida boshqaruv tugmasi
    if adm(uid):
        rows += [
            [B(text="👥 Пользователи (Доступ)", callback_data="us:0")],
            [B(text="👑 Администрация", callback_data="adm_list:0")],
            [B(text="🗑 Удалить имя", callback_data="dl:0")],
        ]
    return M(inline_keyboard=rows)

BACK = M(inline_keyboard=[[B(text="⬅️ Назад", callback_data="menu")]])

async def show(c, text, kb):
    try:
        await c.message.edit_text(text[:4000], reply_markup=kb)
    except TelegramBadRequest:
        pass
    await c.answer()

def pager(kind, rows, page, label, cb):
    pages = max(1, (len(rows) + PER - 1) // PER)
    page = min(max(page, 0), pages - 1)
    kb = [[B(text=label(r), callback_data=cb(r))] for r in rows[page * PER : (page + 1) * PER]]
    nav = []
    if page > 0:
        nav.append(B(text="⬅️", callback_data=f"{kind}:{page - 1}"))
    nav.append(B(text=f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(B(text="➡️", callback_data=f"{kind}:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([B(text="⬅️ Назад", callback_data="menu")])
    return M(inline_keyboard=kb)

ICON = {"pending": "⏳ ", "rejected": "🚫 "}

def user_label(r):
    if r["status"] == "approved":
        return ("👑 " if r["role"] == "admin" else "👤 ") + r["name"]
    return ICON.get(r["status"], "") + r["name"]

async def render(c, kind, p):
    if kind == "us":
        rows = q("SELECT * FROM users ORDER BY CASE status WHEN 'pending' THEN 1 WHEN 'approved' THEN 2 ELSE 3 END, name")
        text = f"👥 Все пользователи ({len(rows)}):"
        kb = pager("us", rows, p, user_label, lambda r: f"u:{r['id']}")
    elif kind == "adm_list":
        rows = q("SELECT * FROM users WHERE role='admin' OR id=?", (OWNER,))
        text = f"👑 Администрация ({len(rows)}):"
        kb = pager("adm_list", rows, p, user_label, lambda r: f"u:{r['id']}")
    elif kind == "get":
        rows = q("SELECT * FROM voices ORDER BY name")
        text = f"🔊 Выберите имя ({len(rows)}):"
        kb = pager("get", rows, p, lambda r: f"🔊 {r['name']} ({r['dur']} сек)", lambda r: f"g:{r['id']}")
    else:
        rows = q("SELECT * FROM voices ORDER BY name")
        text = f"🗑 Какое имя удалить? ({len(rows)})"
        kb = pager("dl", rows, p, lambda r: f"❌ {r['name']}", lambda r: f"d:{r['id']}")
    
    if not rows:
        text = "Список пуст."
        kb = BACK
    await show(c, text, kb)

@dp.callback_query(F.data == "noop")
async def cb_noop(c: CallbackQuery):
    await c.answer()

@dp.callback_query(F.data == "menu")
async def cb_menu(c: CallbackQuery):
    if not ok(c.from_user.id):
        return await c.answer("У вас нет доступа", show_alert=True)
    await show(c, "Главное меню:", menu(c.from_user.id))

@dp.callback_query(F.data == "list")
async def cb_list(c: CallbackQuery):
    if not ok(c.from_user.id):
        return await c.answer("У вас нет доступа", show_alert=True)
    rows = q("SELECT name, dur FROM voices ORDER BY name")
    if rows:
        t = f"📜 Всего имён: {len(rows)}\n\n" + "\n".join(
            f"{i}. {r['name']} — {r['dur']} сек" for i, r in enumerate(rows, 1)
        )
    else:
        t = "Список пуст."
    await show(c, t, BACK)

@dp.callback_query(F.data.regexp(r"^(get|dl|us|adm_list):\d+$"))
async def cb_pages(c: CallbackQuery):
    kind, p = c.data.split(":")
    if not ok(c.from_user.id):
        return await c.answer("У вас нет доступа", show_alert=True)
    if kind != "get" and not adm(c.from_user.id):
        return await c.answer("Нет доступа", show_alert=True)
    await render(c, kind, int(p))

@dp.callback_query(F.data.regexp(r"^g:\d+$"))
async def cb_get(c: CallbackQuery):
    if not ok(c.from_user.id):
        return await c.answer("У вас нет доступа", show_alert=True)
    v = q("SELECT * FROM voices WHERE id=?", (int(c.data[2:]),), "one")
    if v:
        # CAPTION OLIB TASHALGAN - Toza ovoz boradi
        await c.message.answer_voice(v["file_id"])
        await c.answer()
    else:
        await c.answer("Это имя уже удалено", show_alert=True)

@dp.callback_query(F.data.regexp(r"^d:\d+$"))
async def cb_del(c: CallbackQuery):
    if not adm(c.from_user.id):
        return await c.answer("Нет доступа", show_alert=True)
    q("DELETE FROM voices WHERE id=?", (int(c.data[2:]),))
    await render(c, "dl", 0)

# --- USER CARD ---
async def card(c, uid):
    r = q("SELECT * FROM users WHERE id=?", (uid,), "one")
    if not r:
        return await render(c, "us", 0)
    st = r["status"]
    if st == "pending":
        rows = [
            [
                B(text="✅ Принять", callback_data=f"cok:{uid}"),
                B(text="👑 Принять как админа", callback_data=f"coa:{uid}"),
            ],
            [B(text="❌ Отклонить", callback_data=f"cno:{uid}")],
        ]
        state = "ожидает решения"
    elif st == "approved":
        is_a = r["role"] == "admin"
        rows = []
