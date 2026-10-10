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

# Sozlamalar
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
    
    # Owner har doim Bosh Admin qilib biriktiriladi
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
            await bot.send_message(uid, "✅ Ваша заявка принята! Вы можете пользоваться ботом.", reply_markup=menu(uid))
        except Exception:
            mark += "\n⚠️ Пользователь ещё не открывал бота (пусть нажмёт /start)"
    else:
        q("UPDATE users SET status='rejected', role='user' WHERE id=?", (uid,))
        mark = "❌ Отклонён"
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
    if adm(uid):
        rows += [
            [B(text="🗑 Удалить имя", callback_data="dl:0")],
            [B(text="👥 Управление доступом", callback_data="us:0")],
            [B(text="👑 Администраторы", callback_data="adm_list:0")],
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
        text = f"👑 Администраторы ({len(rows)}):"
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
        # Caption-siz yuborish (toza audio)
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
        if uid != OWNER:
            rows.append([B(text="👤 Снять админа" if is_a else "👑 Сделать админом", callback_data=f"adm:{uid}")])
            rows.append([B(text="🚫 Закрыть доступ", callback_data=f"rm:{uid}")])
        state = "главный владелец" if uid == OWNER else ("админ" if is_a else "пользователь")
    else:
        rows = [[B(text="🔄 Открыть доступ", callback_data=f"cok:{uid}")]]
        state = "доступ закрыт"
    
    rows.append([B(text="⬅️ Назад", callback_data="us:0")])
    await show(c, f"👤 {r['name']}\nID: {uid}\nСтатус: {state}", M(inline_keyboard=rows))

@dp.callback_query(F.data.regexp(r"^(u|adm|rm|cok|coa|cno):\d+$"))
async def cb_user(c: CallbackQuery):
    if not adm(c.from_user.id):
        return await c.answer("Нет доступа", show_alert=True)
    act, uid = c.data.split(":")
    uid = int(uid)
    
    if act == "u":
        return await card(c, uid)
    if act == "adm":
        if uid == OWNER:
            return await c.answer("Нельзя изменить статус владельца!", show_alert=True)
        r = q("SELECT role FROM users WHERE id=?", (uid,), "one")
        if r:
            new_role = "user" if r["role"] == "admin" else "admin"
            q("UPDATE users SET role=? WHERE id=?", (new_role, uid))
            try:
                msg = "👑 Вам выдан статус Администратора!" if new_role == "admin" else "👤 Ваш статус администратора снят."
                await bot.send_message(uid, msg, reply_markup=menu(uid))
            except Exception:
                pass
            return await card(c, uid)
    if act == "rm":
        if uid == OWNER:
            return await c.answer("Нельзя удалить владельца!", show_alert=True)
        q("UPDATE users SET status='rejected', role='user' WHERE id=?", (uid,))
        try:
            await bot.send_message(uid, "🚫 Ваш доступ к боту закрыт.")
        except Exception:
            pass
        return await render(c, "us", 0)
    if act in ("cok", "coa", "cno"):
        res, mark = await decide(uid, act[1:])
        return await card(c, uid)

# --- OVOZLARNI ALMASHTIRISH ---
@dp.callback_query(F.data.regexp(r"^(rp|rk):-?\d+:\d+$"))
async def cb_replace(c: CallbackQuery):
    act, chat_id, mid = c.data.split(":")
    key = (int(chat_id), int(mid))
    p = PENDING.get(key)
    if not p:
        try:
            await c.message.edit_text("Запрос устарел.")
        except TelegramBadRequest:
            pass
        return await c.answer()
    
    if c.from_user.id != p["by"] and not adm(c.from_user.id):
        return await c.answer("Нет доступа", show_alert=True)
    
    PENDING.pop(key, None)
    if act == "rp":
        q("UPDATE voices SET name=?, file_id=?, uid=?, dur=?, added_by=? WHERE key=?",
          (p["name"], p["file_id"], p["uid"], p["dur"], p["by"], p["key"]))
        text = f"🔄 Голосовое заменено: {p['name']}"
    else:
        text = "Оставлено прежнее голосовое."
    
    try:
        await c.message.edit_text(text)
    except TelegramBadRequest:
        pass
    await c.answer()

# --- SHAXSIY CHAT ---
@dp.message(CommandStart(), F.chat.type == "private")
async def start(m: Message):
    if ok(m.from_user.id):
        await m.answer("Главное меню:", reply_markup=menu(m.from_user.id))
    else:
        await m.answer("⏳ Ваша заявка отправлена администраторам. Ожидайте подтверждения.")
        await request_access(m.from_user)

@dp.message(F.chat.type == "private")
async def private_any(m: Message):
    if not ok(m.from_user.id):
        await m.answer("⏳ У вас пока нет доступа к боту. Ожидайте подтверждения.")
        return

    text = m.text.strip().lower()
    
    # Bazadan ismni qidirish
    v = q("SELECT file_id FROM voices WHERE key=?", (text,), "one")
    if v:
        # Topilsa — tagida captionsiz toza voice yuboradi
        await m.answer_voice(v["file_id"])
    else:
        # Topilmasa — rus tilida topilmadi deydi
        await m.answer("❌ Имя не найдено.")

# --- GURUH VA CHATLAR (OVOZ SAQLASH VA OLISH) ---
@dp.my_chat_member()
async def added(ev: ChatMemberUpdated):
    if ev.chat.type not in ("group", "supergroup"):
        return
    if ev.new_chat_member.status in ("member", "administrator") and ev.from_user.id == OWNER:
        set_log(ev.chat.id)
        await bot.send_message(
            ev.chat.id,
            "📌 Группа назначена главной: сюда приходят заявки и сообщения об ошибках.\n\n"
            "🎤 Как сохранить голосовое: ответьте (reply) на голосовое сообщение и напишите имя.\n"
            "🔊 Как получить: просто напишите имя.\n\nПанель управления:",
            reply_markup=menu(OWNER)
        )

@dp.message(F.chat.type.in_(("group", "supergroup")), F.text)
async def group(m: Message):
    u = m.from_user
    if not u or u.is_bot:
        return
    text = m.text.strip()
    
    if text.startswith("/"):
        if u.id == OWNER and text.split()[0].split("@")[0].lower() == "/setgroup":
            set_log(m.chat.id)
            await m.reply("📌 Эта группа назначена главной.", reply_markup=menu(OWNER))
        return
    
    r = m.reply_to_message
    if r and r.voice:
        if not ok(u.id):
            await request_access(u)
            return
        
        key = text.lower()
        if not key or len(key) > 60:
            return
        
        v = r.voice
        ex = q("SELECT uid FROM voices WHERE key=?", (key,), "one")
        if not ex:
            q("INSERT INTO voices (key, name, file_id, uid, dur, added_by) VALUES (?,?,?,?,?,?)",
              (key, text, v.file_id, v.file_unique_id, v.duration, u.id))
            await m.reply(f"✅ Сохранено: {text}")
        elif ex["uid"] == v.file_unique_id:
            await m.reply("ℹ️ Это голосовое уже сохранено под таким именем.")
        else:
            PENDING[(m.chat.id, m.message_id)] = {
                "key": key, "name": text, "file_id": v.file_id,
                "uid": v.file_unique_id, "dur": v.duration, "by": u.id
            }
            kb = M(inline_keyboard=[[
                B(text="🔄 Заменить", callback_data=f"rp:{m.chat.id}:{m.message_id}"),
                B(text="❌ Оставить прежнее", callback_data=f"rk:{m.chat.id}:{m.message_id}")
            ]])
            await m.reply(f"⚠️ Имя «{text}» уже занято. Заменить голосовое?", reply_markup=kb)
        return

    if ok(u.id):
        v = q("SELECT file_id FROM voices WHERE key=?", (text.lower(),), "one")
        if v:
            # Captionsiz audio boradi
            await m.reply_voice(v["file_id"])

# --- XATOLIKLAR VA ISHGA TUSHIRISH ---
@dp.error()
async def on_error(event: ErrorEvent):
    logging.exception("handler error", exc_info=event.exception)
    await notify(f"⚠️ Ошибка в боте:\n{type(event.exception).__name__}: {event.exception}")
    return True

async def main():
    init()
    await bot.delete_webhook(drop_pending_updates=True)
    await notify("🚀 Бот запущен и работает.")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        await notify(f"🛑 Бот остановился из-за ошибки:\n{type(e).__name__}: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
