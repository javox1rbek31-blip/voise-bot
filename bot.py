import asyncio
import logging
import os
import sqlite3
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Telegram sozlamalari
BOT_TOKEN = os.getenv("BOT_TOKEN", "8608877100:AAF9kNtDxRmtMwFjGlNGUT0Zi7X8mQ9AJOI")
OWNER_ID = int(os.getenv("OWNER_ID", "8785896869"))
DB_PATH = os.getenv("DB_PATH", "/data/bot.db")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- BAZA BILAN ISHLASH ---
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Foydalanuvchilar jadvali (is_allowed: ruxsat, is_admin: adminlik)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            username TEXT,
            is_allowed INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0
        )
    """)
    
    # Asosiy Ega (Owner) har doim ruxsatli va admin
    cursor.execute("""
        INSERT INTO users (user_id, full_name, username, is_allowed, is_admin)
        VALUES (?, 'Owner', 'owner', 1, 1)
        ON CONFLICT(user_id) DO UPDATE SET is_allowed = 1, is_admin = 1
    """, (OWNER_ID,))
    
    conn.commit()
    conn.close()

def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row and row[0] == 1)

def is_allowed(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT is_allowed FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row and row[0] == 1)

def add_user(user_id: int, full_name: str, username: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, full_name, username)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET full_name=?, username=?
    """, (user_id, full_name, username, full_name, username))
    conn.commit()
    conn.close()

def set_access_status(user_id: int, status: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_allowed = ? WHERE user_id = ?", (status, user_id))
    conn.commit()
    conn.close()

def set_admin_status(user_id: int, status: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Admin bo'lsa, avotmatik botdan foydalanishiga ham ruxsat beriladi
    cursor.execute("UPDATE users SET is_admin = ?, is_allowed = 1 WHERE user_id = ?", (status, user_id))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, full_name, username, is_allowed, is_admin FROM users")
    rows = cursor.fetchall()
    conn.close()
    return rows


# --- HANDLERLAR ---

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    user_id = message.from_user.id
    full_name = message.from_user.full_name
    username = message.from_user.username or "yo'q"
    
    add_user(user_id, full_name, username)
    
    # Adminlar uchun 2 ta ALOHIDA knopka
    if is_admin(user_id):
        keyboard = [
            [InlineKeyboardButton(text="👥 Foydalanish Ruxsatlari", callback_data="manage_access")],
            [InlineKeyboardButton(text="👑 Adminlar Boshqaruvi", callback_data="manage_admins")]
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await message.answer(f"Xush kelibsiz, Admin {full_name}!\nBoshqaruv menyusini tanlang:", reply_markup=markup)
        return

    # Oddiy foydalanuvchini tekshirish
    if not is_allowed(user_id):
        await message.answer("Sizga hali botdan foydalanish uchun ruxsat berilmagan. Adminlar tasdiqlashini kuting.")
        return

    await message.answer(f"Assalomu alaykum, {full_name}! Botdan bemalol foydalanishingiz mumkin.")


# 1. OVOZLI XABARLARNI YUBORISH (Caption/ism yo'q)
@dp.message(F.voice)
async def handle_voice(message: types.Message):
    if not is_allowed(message.from_user.id):
        await message.answer("Sizda botdan foydalanish ruxsati yo'q.")
        return

    # Ovozli xabar tagiga hech qanday yozuv qo'shilmaydi
    await message.answer_voice(voice=message.voice.file_id)


# 2. ALOHIDA KNOPKA 1: FOYDALANISH RUXSATLARINI BOSHQARISH
@dp.callback_query(F.data == "manage_access")
async def manage_access_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Sizda admin huquqi yo'q!", show_alert=True)
        return

    users = get_all_users()
    keyboard = []
    text = "<b>👥 Foydalanish Ruxsatlari Boshqaruvi:</b>\n\n"
    
    for uid, fname, uname, allowed, admin_flag in users:
        if uid == OWNER_ID:
            continue
        status_text = "✅ Ruxsat berilgan" if allowed else "❌ Ruxsat berilmagan"
        text += f"• {fname} (@{uname}) — {status_text}\n"
        
        btn_text = f"🚫 Ruxsatni olish: {fname}" if allowed else f"✅ Ruxsat berish: {fname}"
        action = f"deny_acc_{uid}" if allowed else f"allow_acc_{uid}"
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=action)])

    keyboard.append([InlineKeyboardButton(text="🔄 Yangilash", callback_data="manage_access")])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))

# Ruxsat berish / olish callback
@dp.callback_query(F.data.startswith("allow_acc_"))
async def allow_access(callback: types.CallbackQuery):
    target_id = int(callback.data.split("_")[2])
    set_access_status(target_id, 1)
    await callback.answer("Foydalanuvchiga ruxsat berildi!", show_alert=True)
    await manage_access_callback(callback)

@dp.callback_query(F.data.startswith("deny_acc_"))
async def deny_access(callback: types.CallbackQuery):
    target_id = int(callback.data.split("_")[2])
    set_access_status(target_id, 0)
    await callback.answer("Foydalanish ruxsati olib tashlandi!", show_alert=True)
    await manage_access_callback(callback)


# 3. ALOHIDA KNOPKA 2: ADMINLARNI BOSHQARISH
@dp.callback_query(F.data == "manage_admins")
async def manage_admins_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Sizda admin huquqi yo'q!", show_alert=True)
        return

    users = get_all_users()
    keyboard = []
    text = "<b>👑 Adminlar Boshqaruvi:</b>\n\n"
    
    for uid, fname, uname, allowed, admin_flag in users:
        if uid == OWNER_ID:
            text += f"• {fname} (@{uname}) — 👑 Bosh Admin (Owner)\n"
            continue
            
        role_text = "⭐ Admin" if admin_flag else "👤 Oddiy foydalanuvchi"
        text += f"• {fname} (@{uname}) — {role_text}\n"
        
        btn_text = f"❌ Adminlikni olish: {fname}" if admin_flag else f"➕ Admin qilish: {fname}"
        action = f"demote_adm_{uid}" if admin_flag else f"promote_adm_{uid}"
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=action)])

    keyboard.append([InlineKeyboardButton(text="🔄 Yangilash", callback_data="manage_admins")])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))

# Admin tayinlash / olish callback
@dp.callback_query(F.data.startswith("promote_adm_"))
async def promote_admin(callback: types.CallbackQuery):
    target_id = int(callback.data.split("_")[2])
    set_admin_status(target_id, 1)
    await callback.answer("Foydalanuvchi Admin qilindi!", show_alert=True)
    await manage_admins_callback(callback)

@dp.callback_query(F.data.startswith("demote_adm_"))
async def demote_admin(callback: types.CallbackQuery):
    target_id = int(callback.data.split("_")[2])
    set_admin_status(target_id, 0)
    await callback.answer("Adminlik huquqi olib tashlandi!", show_alert=True)
    await manage_admins_callback(callback)


# BOTNI ISHGA TUSHIRISH
async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
