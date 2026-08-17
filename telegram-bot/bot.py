import io
import os
import json
import hashlib
import random
import asyncio
import logging
import threading
import warnings
import zipfile
import time
import uuid
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.error import BadRequest, Conflict, RetryAfter, TimedOut, NetworkError

warnings.filterwarnings("ignore", message=".*per_message=False.*CallbackQueryHandler.*")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7706183809"))
PAYPAL_LINK = "https://paypal.me/Eliyas2005"

DATA_DIR       = Path("data")
USERS_FILE     = DATA_DIR / "users.json"
COINS_FILE     = DATA_DIR / "coins.json"
REFERRALS_FILE = DATA_DIR / "referrals.json"
VIDEOS_FILE    = DATA_DIR / "videos.json"
ORDERS_FILE    = DATA_DIR / "orders.json"
COUPONS_FILE   = DATA_DIR / "coupons.json"
SETTINGS_FILE  = DATA_DIR / "settings.json"
TRASH_FILE     = DATA_DIR / "trash.json"

BACKUP_ALLOWED_FILES = {
    "users.json": dict,
    "coins.json": dict,
    "referrals.json": dict,
    "videos.json": list,
    "orders.json": list,
    "coupons.json": dict,
    "settings.json": dict,
    "trash.json": list,
}
MAX_RESTORE_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_RESTORE_UNCOMPRESSED_BYTES = 40 * 1024 * 1024

COINS_PER_SHEKEL = 10
ORDERS_PER_PAGE  = 10

PACKAGES = [
    {"price": 2,   "videos": 1,   "coins": 20,   "label_paypal": "₪2 – 1 סרטון",       "label_coins": "🪙20 – 1 סרטון"},
    {"price": 9,   "videos": 5,   "coins": 90,   "label_paypal": "₪9 – 5 סרטונים",      "label_coins": "🪙90 – 5 סרטונים"},
    {"price": 16,  "videos": 10,  "coins": 160,  "label_paypal": "₪16 – 10 סרטונים",    "label_coins": "🪙160 – 10 סרטונים"},
    {"price": 30,  "videos": 20,  "coins": 300,  "label_paypal": "₪30 – 20 סרטונים",    "label_coins": "🪙300 – 20 סרטונים"},
    {"price": 65,  "videos": 50,  "coins": 650,  "label_paypal": "₪65 – 50 סרטונים",    "label_coins": "🪙650 – 50 סרטונים"},
    {"price": 85,  "videos": 70,  "coins": 850,  "label_paypal": "₪85 – 70 סרטונים",    "label_coins": "🪙850 – 70 סרטונים"},
    {"price": 110, "videos": 100, "coins": 1100, "label_paypal": "₪110 – 100 סרטונים",  "label_coins": "🪙1100 – 100 סרטונים"},
    {"price": 180, "videos": 200, "coins": 1800, "label_paypal": "₪180 – 200 סרטונים",  "label_coins": "🪙1800 – 200 סרטונים"},
]

VIP_LEVELS = [
    {"name": "ברונזה", "min_purchases": 0,   "discount": 0.0,  "icon": "🥉"},
    {"name": "כסף",   "min_purchases": 20,  "discount": 0.15, "icon": "🥈"},
    {"name": "זהב",   "min_purchases": 50,  "discount": 0.30, "icon": "🥇"},
    {"name": "יהלום", "min_purchases": 100, "discount": 0.50, "icon": "💎"},
]

(
    ADMIN_SEND_MSG,
    ADMIN_SEND_ID,
    ADMIN_APPROVE_COUNT,
    ADMIN_APPROVE_ID,
    ADMIN_CHECK_USER,
    ADMIN_COINS_ID,
    ADMIN_COINS_AMOUNT,
    ADMIN_BROADCAST,
    ADMIN_BROADCAST_BTN,
    ADMIN_BROADCAST_DELAY,
    SUPPORT_WAITING_MSG,
    SUPPORT_REPLY_MSG,
    ADMIN_COUPON_CODE,
    ADMIN_COUPON_COINS,
    ADMIN_COUPON_EXPIRY,
    ADMIN_COUPON_LIMIT,
    COUPON_REDEEM,
    ADMIN_MULTIPLIER,
    ADMIN_RESTORE,
    ADMIN_GLOBAL_RESET_CONFIRM,
    ADMIN_VIDEO_SEARCH,
    ADMIN_VIDEO_CAT,
    ADMIN_VIDEO_PREVIEW,
    ADMIN_BROADCAST_MEDIA,
    ADMIN_VIP_ID,
    ADMIN_VIP_LEVEL,
    ADMIN_RESTORE_CONFIRM,
    ADMIN_VIDEO_SEARCH_SECONDS,
    ADMIN_VIDEO_CAT_ADD,
    ADMIN_VIDEO_CAT_SORT,
) = range(30)

def ensure_data_files():
    DATA_DIR.mkdir(exist_ok=True)
    defaults = [
        (USERS_FILE,     {}),
        (COINS_FILE,     {}),
        (REFERRALS_FILE, {}),
        (VIDEOS_FILE,    []),
        (ORDERS_FILE,    []),
        (COUPONS_FILE,   {}),
        (SETTINGS_FILE,  {"referral_multiplier": 1.0, "maintenance": False}),
        (TRASH_FILE,     []),
    ]
    for filepath, default in defaults:
        if not filepath.exists():
            save_json(filepath, default)

def load_json(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "videos.json" in str(filepath) and isinstance(data, list) and data and isinstance(data[0], str):
                new_data = []
                for fid in data:
                    new_data.append({"file_id": fid, "duration": 0, "preview": None})
                return new_data
            return data
    except (json.JSONDecodeError, FileNotFoundError):
        if "videos" in str(filepath) or "orders" in str(filepath):
            return []
        return {}

def save_json(filepath, data):
    tmp = Path(str(filepath) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(filepath)

def load_videos_with_entry_ids():
    videos = load_json(VIDEOS_FILE)
    if not isinstance(videos, list):
        return []
    used_ids = set()
    changed = False
    for video in videos:
        entry_id = video.get("entry_id") if isinstance(video, dict) else None
        if not isinstance(entry_id, str) or not entry_id or entry_id in used_ids:
            entry_id = uuid.uuid4().hex
            while entry_id in used_ids:
                entry_id = uuid.uuid4().hex
            video["entry_id"] = entry_id
            changed = True
        used_ids.add(entry_id)
    if changed:
        save_json(VIDEOS_FILE, videos)
    return videos

async def send_admin_video_with_delete_button(bot, file_id, entry_id, max_attempts=5):
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 מחק סרטון זה", callback_data=f"del_eid_{entry_id}")]])
    for attempt in range(1, max_attempts + 1):
        try:
            return await bot.send_video(chat_id=ADMIN_ID, video=file_id, reply_markup=markup)
        except RetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
        except (TimedOut, NetworkError):
            await asyncio.sleep(min(2 ** attempt, 20))
        except Exception:
            return None
    return None

def load_settings() -> dict:
    s = load_json(SETTINGS_FILE)
    if not isinstance(s, dict): s = {}
    s.setdefault("referral_multiplier", 1.0)
    s.setdefault("maintenance", False)
    return s

def save_settings(s: dict):
    save_json(SETTINGS_FILE, s)

DUPLICATE_REVIEWED_KEY = "reviewed_non_duplicate_groups"

def duplicate_group_signature(group: list[dict]) -> str:
    duration = group[0].get("duration", 0) if group else 0
    entry_ids = sorted(str(video.get("entry_id", "")) for video in group)
    payload = json.dumps({"duration": duration, "entry_ids": entry_ids}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def reviewed_non_duplicate_signatures() -> set[str]:
    settings = load_settings()
    reviewed = settings.get(DUPLICATE_REVIEWED_KEY, [])
    return {signature for signature in reviewed if isinstance(signature, str)}

def mark_group_as_not_duplicate(group: list[dict]) -> bool:
    signature = duplicate_group_signature(group)
    settings = load_settings()
    reviewed = settings.get(DUPLICATE_REVIEWED_KEY, [])
    if not isinstance(reviewed, list): reviewed = []
    if signature in reviewed: return False
    reviewed.append(signature)
    settings[DUPLICATE_REVIEWED_KEY] = reviewed
    save_settings(settings)
    return True

def clear_not_duplicate_marks() -> int:
    settings = load_settings()
    reviewed = settings.get(DUPLICATE_REVIEWED_KEY, [])
    count = len(reviewed) if isinstance(reviewed, list) else 0
    settings[DUPLICATE_REVIEWED_KEY] = []
    save_settings(settings)
    return count

def get_user_vip(user_id: str) -> dict:
    users = load_json(USERS_FILE)
    u = users.get(user_id, {})
    purchases = u.get("purchases", 0)
    current_vip = VIP_LEVELS[0]
    for level in VIP_LEVELS:
        if purchases >= level["min_purchases"]:
            current_vip = level
    return current_vip

def register_user(user, ref_id=None):
    users = load_json(USERS_FILE)
    uid   = str(user.id)
    if uid not in users:
        users[uid] = {
            "id": user.id,
            "username": user.username or "None",
            "first_name": user.first_name or "User",
            "joined": str(date.today()),
            "purchases": 0,
            "spent": 0.0,
            "ref_by": ref_id if ref_id and ref_id != uid else None
        }
        save_json(USERS_FILE, users)
        if ref_id and ref_id != uid:
            coins = load_json(COINS_FILE)
            settings = load_settings()
            bonus = int(5 * settings.get("referral_multiplier", 1.0))
            coins[ref_id] = coins.get(ref_id, 0) + bonus
            save_json(COINS_FILE, coins)
            refs = load_json(REFERRALS_FILE)
            refs.setdefault(ref_id, []).append(uid)
            save_json(REFERRALS_FILE, refs)
            return True
    return False

def get_coins(user_id):
    coins = load_json(COINS_FILE)
    return coins.get(str(user_id), 0)

def update_coins(user_id, amount):
    coins = load_json(COINS_FILE)
    uid = str(user_id)
    coins[uid] = max(0, coins.get(uid, 0) + amount)
    save_json(COINS_FILE, coins)

def get_main_keyboard(user_id):
    vip = get_user_vip(str(user_id))
    return ReplyKeyboardMarkup([
        [KeyboardButton("🎬 קנה סרטונים"), KeyboardButton("🪙 טען מטבעות")],
        [KeyboardButton("👤 הפרופיל שלי"), KeyboardButton("🤝 תוכנית שותפים")],
        [KeyboardButton("🎟 מימוש קופון"), KeyboardButton("💬 תמיכה")],
        [KeyboardButton(f"💎 דרגת VIP: {vip['icon']} {vip['name']}")]
    ], resize_keyboard=True)

def get_admin_inline_keyboard():
    settings    = load_settings()
    maint_label = "🔧 כבה תחזוקה" if settings.get("maintenance") else "🔧 מצב תחזוקה"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 סטטיסטיקה", callback_data="admin_stats"), InlineKeyboardButton("🧾 הזמנות", callback_data="admin_orders_page_0")],
        [InlineKeyboardButton("🔍 בדוק משתמש", callback_data="admin_check"), InlineKeyboardButton("👥 רשימת משתמשים", callback_data="users_page_0")],
        [InlineKeyboardButton("📩 שלח למשתמש", callback_data="admin_send"), InlineKeyboardButton("✅ אישור תשלום", callback_data="admin_approve")],
        [InlineKeyboardButton("🎬 גלריית סרטונים", callback_data="admin_gallery"), InlineKeyboardButton("🏷 קטגוריות", callback_data="admin_categories")],
        [InlineKeyboardButton("👯 מצא כפילויות", callback_data="admin_dup_scan"), InlineKeyboardButton("🗑 סל מיחזור", callback_data="admin_trash_page_0")],
        [InlineKeyboardButton("💰 ניהול מטבעות", callback_data="admin_coins"), InlineKeyboardButton("💎 ניהול VIP", callback_data="admin_vip")],
        [InlineKeyboardButton("🎟 קופונים", callback_data="admin_coupons"), InlineKeyboardButton("📢 הודעה לכולם", callback_data="admin_broadcast")],
        [InlineKeyboardButton("✖️ מכפיל הפניות", callback_data="admin_multiplier"), InlineKeyboardButton("💾 גיבוי/שחזור", callback_data="admin_backup")],
        [InlineKeyboardButton(maint_label, callback_data="admin_maintenance"), InlineKeyboardButton("🔥 איפוס מלא", callback_data="admin_global_reset")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref_id = context.args[0] if context.args else None
    is_new = register_user(user, ref_id)
    welcome_text = f"ברוך הבא לבוט הסרטונים שלנו! 🎬\n\n💰 המאזן שלך: {get_coins(user.id)} מטבעות.\n🎁 הזמנת חברים מקנה לך מטבעות מתנה!\n\nהשתמש בתפריט למטה כדי להתחיל 👇"
    if is_new and ref_id:
        try: await context.bot.send_message(chat_id=ref_id, text="🎁 חבר חדש הצטרף דרכך! קיבלת בונוס מטבעות.")
        except: pass
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard(user.id))

async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await context.bot.send_message(chat_id=query.message.chat_id, text="תפריט ראשי:", reply_markup=get_main_keyboard(query.from_user.id))

async def coins_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = f"💰 *טעינת מטבעות*\n\nהמאזן שלך: {get_coins(user_id)} מטבעות\n\nבחר שיטת תשלום:"
    btns = [[InlineKeyboardButton("💳 PayPal (אוטומטי)", callback_data="paypal_menu")], [InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def profile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    u = load_json(USERS_FILE).get(uid, {})
    vip = get_user_vip(uid)
    text = f"👤 *הפרופיל שלי*\n\n🆔 מזהה: `{uid}`\n💰 יתרה: {get_coins(uid)} מטבעות\n🛍 רכישות: {u.get('purchases', 0)} סרטונים\n💎 דרגה: {vip['icon']} {vip['name']}"
    await update.message.reply_text(text, parse_mode="Markdown")

async def referrals_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    refs = load_json(REFERRALS_FILE).get(uid, [])
    link = f"https://t.me/{(await context.bot.get_me()).username}?start={uid}"
    text = f"🤝 *תוכנית שותפים*\n\n👥 חברים שהצטרפו דרכך: {len(refs)}\n🔗 קישור ההפניה שלך:\n`{link}`"
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("🛠 *פאנל ניהול*", parse_mode="Markdown", reply_markup=get_admin_inline_keyboard())

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = f"📊 *סטטיסטיקה*\n\n👥 משתמשים: {len(load_json(USERS_FILE))}\n🎬 סרטונים: {len(load_json(VIDEOS_FILE))}\n🧾 הזמנות: {len(load_json(ORDERS_FILE))}"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

async def users_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("users_page_")[1])
    users = list(load_json(USERS_FILE).values())
    start_idx = page * 10
    end_idx = start_idx + 10
    text = f"👥 *רשימת משתמשים (דף {page+1})*\n\n" + "\n".join([f"• `{u['id']}` - {u['first_name']}" for u in users[start_idx:end_idx]])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"users_page_{page-1}"))
    if end_idx < len(users): nav.append(InlineKeyboardButton("➡️", callback_data=f"users_page_{page+1}"))
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([nav, [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

async def admin_orders_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("admin_orders_page_")[1])
    orders = load_json(ORDERS_FILE)[::-1]
    start_idx = page * 10
    end_idx = start_idx + 10
    text = f"🧾 *הזמנות אחרונות (דף {page+1})*\n\n" + "\n".join([f"• `{o['user_id']}` | {o['amount']} | {o['date']}" for o in orders[start_idx:end_idx]])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"admin_orders_page_{page-1}"))
    if end_idx < len(orders): nav.append(InlineKeyboardButton("➡️", callback_data=f"admin_orders_page_{page+1}"))
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([nav, [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

async def admin_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    videos = load_json(VIDEOS_FILE)
    text = f"🎬 *ניהול גלריית סרטונים*\n\nסה\"כ סרטונים: {len(videos)}"
    btns = [
        [InlineKeyboardButton("📖 עיון בספרייה", callback_data="vid_page_0")],
        [InlineKeyboardButton("🔢 חיפוש לפי מספר", callback_data="admin_video_search")],
        [InlineKeyboardButton("⏱ חיפוש לפי זמן", callback_data="admin_search_sec_start")],
        [InlineKeyboardButton("📤 שלח את כל המאגר", callback_data="vid_send_all")],
        [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]
    ]
    if update.callback_query: await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    else: await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def clear_sent_duplicate_group_media(context: ContextTypes.DEFAULT_TYPE):
    msg_ids = context.user_data.get("dup_sent_media_message_ids", [])
    for msg_id in msg_ids:
        try: await context.bot.delete_message(chat_id=ADMIN_ID, message_id=msg_id)
        except: pass
    context.user_data["dup_sent_media_message_ids"] = []

async def admin_gallery_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=None):
    query = update.callback_query
    page = page if page is not None else int(query.data.split("vid_page_")[1])
    await clear_sent_duplicate_group_media(context)
    videos = load_json(VIDEOS_FILE)
    if not videos: return
    page = max(0, min(page, len(videos) - 1))
    v = videos[page]
    text = f"🎬 *גלריית סרטונים ({page+1}/{len(videos)})*\n\n📁 קטגוריה: {v.get('category', 'כללי')}\n⏱ אורך: {v.get('duration', 0)} שניות"
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"vid_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{len(videos)}", callback_data="noop"))
    if page < len(videos) - 1: nav.append(InlineKeyboardButton("➡️", callback_data=f"vid_page_{page+1}"))
    btns = [nav, [InlineKeyboardButton("🗑 מחק סרטון", callback_data=f"vid_del_{page}")], [InlineKeyboardButton("🔙 חזרה", callback_data="admin_gallery")]]
    try: await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    except: pass
    sent = await context.bot.send_video(chat_id=ADMIN_ID, video=v["file_id"])
    if sent: context.user_data["dup_sent_media_message_ids"] = [sent.message_id]

async def admin_gallery_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    videos = load_json(VIDEOS_FILE)
    trash = load_json(TRASH_FILE)
    v = None
    if data.startswith("vid_del_"): v = videos.pop(int(data.split("_")[2]))
    elif data.startswith("del_eid_"):
        eid = data.split("_")[2]
        for i, item in enumerate(videos):
            if item.get("entry_id") == eid: v = videos.pop(i); break
    if v:
        v["deleted_at"] = str(datetime.now())
        trash.append(v)
        save_json(VIDEOS_FILE, videos)
        save_json(TRASH_FILE, trash)
        await query.answer("✅ הועבר לסל המיחזור")
        if data.startswith("vid_del_"): await admin_gallery(update, context)

async def admin_gallery_send_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    videos = load_videos_with_entry_ids()
    sorted_v = sorted(videos, key=lambda x: x.get("duration", 0))
    status = await query.edit_message_text(f"🚀 שולח {len(sorted_v)} סרטונים...")
    for v in sorted_v:
        await send_admin_video_with_delete_button(context.bot, v["file_id"], v["entry_id"])
        await asyncio.sleep(0.1)
    try: await status.delete()
    except: pass
    await context.bot.send_message(chat_id=ADMIN_ID, text=f"✅ סיימתי לשלוח את כל המאגר!")

async def admin_video_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("🔢 שלח מספר סרטון:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="admin_gallery")]]))
    return ADMIN_VIDEO_SEARCH

async def admin_video_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    videos = load_json(VIDEOS_FILE)
    try:
        idx = int(update.message.text.strip()) - 1
        v = videos[idx]
        await send_admin_video_with_delete_button(context.bot, v["file_id"], v.get("entry_id", uuid.uuid4().hex))
    except: await update.message.reply_text("❌ מספר לא תקין.")
    return ADMIN_VIDEO_SEARCH

def parse_smart_time(text: str) -> int:
    text = text.strip()
    if ":" in text:
        parts = text.split(":")
        try: return int(parts[0]) * 60 + int(parts[1])
        except: return -1
    try: return int(text)
    except: return -1

async def admin_search_sec_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("⏱ שלח זמן (למשל 26 או 1:20):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="admin_gallery")]]))
    return ADMIN_VIDEO_SEARCH_SECONDS

async def admin_search_sec_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sec = parse_smart_time(update.message.text)
    videos = load_videos_with_entry_ids()
    results = [v for v in videos if v.get("duration") == sec]
    if results:
        for v in results:
            await send_admin_video_with_delete_button(context.bot, v["file_id"], v["entry_id"])
            await asyncio.sleep(0.1)
    else: await update.message.reply_text("🔍 לא נמצאו סרטונים.")
    return ConversationHandler.END

async def admin_dup_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    groups = find_duplicate_groups(False)
    context.user_data["dup_groups"] = groups
    if not groups: await query.edit_message_text("✅ אין כפילויות.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_gallery")]]))
    else: await admin_dup_page(update, context, 0)

async def admin_dup_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=None):
    query = update.callback_query
    page = page if page is not None else int(query.data.split("dup_page_")[1])
    groups = context.user_data.get("dup_groups", [])
    if not groups or page >= len(groups): return
    await clear_sent_duplicate_group_media(context)
    group = groups[page]
    text = f"🔎 *חשד לכפילות ({page+1}/{len(groups)})*\n\n⏱ אורך: {group[0].get('duration', 0)} שניות\n👥 כמות: {len(group)}"
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"dup_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{len(groups)}", callback_data="noop"))
    if page < len(groups) - 1: nav.append(InlineKeyboardButton("➡️", callback_data=f"dup_page_{page+1}"))
    btns = [[InlineKeyboardButton("📺 שלח חשדות", callback_data=f"dup_send_{page}")], nav, [InlineKeyboardButton("✅ סמן כלא כפולים", callback_data=f"dup_mark_{page}")], [InlineKeyboardButton("🔙 חזרה", callback_data="admin_gallery")]]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def admin_dup_send_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    group = context.user_data.get("dup_groups", [])[int(query.data.split("dup_send_")[1])]
    ids = []
    for v in group:
        sent = await send_admin_video_with_delete_button(context.bot, v["file_id"], v["entry_id"])
        if sent: ids.append(sent.message_id)
        await asyncio.sleep(0.1)
    context.user_data["dup_sent_media_message_ids"] = ids

async def admin_dup_mark_not_duplicate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    page = int(query.data.split("dup_mark_")[1])
    group = context.user_data.get("dup_groups", [])[page]
    mark_group_as_not_duplicate(group)
    await query.answer("✅ סומן")
    await admin_dup_page(update, context, page + 1)

async def admin_categories_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = load_settings().get("categories", ["כללי"])
    text = "🏷 *ניהול קטגוריות*\n\n" + "\n".join([f"• {c}" for c in cats])
    btns = [[InlineKeyboardButton("➕ הוסף קטגוריה", callback_data="admin_cat_add")], [InlineKeyboardButton("🏷 מיון סרטונים", callback_data="admin_cat_sort_start")], [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def admin_cat_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("✍️ שלח שם קטגוריה:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="admin_categories")]]))
    return ADMIN_VIDEO_CAT_ADD

async def admin_cat_add_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    s = load_settings()
    cats = s.get("categories", ["כללי"])
    if name not in cats: cats.append(name); s["categories"] = cats; save_settings(s)
    await update.message.reply_text(f"✅ נוספה קטגוריה '{name}'", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_cat_sort_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    videos = load_json(VIDEOS_FILE)
    if not videos: return
    page = max(0, min(page, len(videos) - 1))
    v = videos[page]
    await clear_sent_duplicate_group_media(context)
    text = f"🏷 *מיון סרטונים ({page+1}/{len(videos)})*\n\nקטגוריה: {v.get('category', 'כללי')}"
    cats = load_settings().get("categories", ["כללי"])
    btns = []
    row = []
    for c in cats:
        row.append(InlineKeyboardButton(c, callback_data=f"cat_assign_{page}_{c}"))
        if len(row) == 2: btns.append(row); row = []
    if row: btns.append(row)
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"cat_sort_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{len(videos)}", callback_data="noop"))
    if page < len(videos) - 1: nav.append(InlineKeyboardButton("➡️", callback_data=f"cat_sort_page_{page+1}"))
    btns.append(nav)
    btns.append([InlineKeyboardButton("🔙 סיום", callback_data="admin_categories")])
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    sent = await context.bot.send_video(chat_id=ADMIN_ID, video=v["file_id"])
    if sent: context.user_data["dup_sent_media_message_ids"] = [sent.message_id]

async def admin_cat_assign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    page = int(parts[2])
    cat = "_".join(parts[3:])
    videos = load_json(VIDEOS_FILE)
    if 0 <= page < len(videos):
        videos[page]["category"] = cat
        save_json(VIDEOS_FILE, videos)
        await query.answer(f"✅ שונה ל-{cat}")
        await admin_cat_sort_page(update, context, page + 1)

async def admin_trash_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    page = int(query.data.split("admin_trash_page_")[1])
    trash = load_json(TRASH_FILE)
    if not trash: await query.edit_message_text("סל המיחזור ריק.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]])); return
    page = max(0, min(page, len(trash) - 1))
    v = trash[page]
    text = f"🗑 *סל מיחזור ({page+1}/{len(trash)})*\n\nאורך: {v.get('duration', 0)} שניות"
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"admin_trash_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{len(trash)}", callback_data="noop"))
    if page < len(trash) - 1: nav.append(InlineKeyboardButton("➡️", callback_data=f"admin_trash_page_{page+1}"))
    btns = [nav, [InlineKeyboardButton("♻️ שחזר", callback_data=f"trash_restore_{page}"), InlineKeyboardButton("🔥 מחק", callback_data=f"trash_perm_{page}")], [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    await clear_sent_duplicate_group_media(context)
    sent = await context.bot.send_video(chat_id=ADMIN_ID, video=v["file_id"])
    if sent: context.user_data["dup_sent_media_message_ids"] = [sent.message_id]

async def admin_trash_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    idx = int(query.data.split("_")[2])
    trash = load_json(TRASH_FILE)
    videos = load_json(VIDEOS_FILE)
    if 0 <= idx < len(trash):
        v = trash.pop(idx); v.pop("deleted_at", None); videos.append(v)
        save_json(TRASH_FILE, trash); save_json(VIDEOS_FILE, videos)
        await query.answer("✅ שוחזר")
        await admin_trash_page(update, context)

async def admin_trash_perm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    idx = int(query.data.split("_")[2])
    trash = load_json(TRASH_FILE)
    if 0 <= idx < len(trash):
        trash.pop(idx); save_json(TRASH_FILE, trash)
        await query.answer("🔥 נמחק")
        await admin_trash_page(update, context)

async def admin_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in DATA_DIR.iterdir():
            if f.suffix == ".json": zf.write(f, f.name)
    buf.seek(0)
    await context.bot.send_document(chat_id=ADMIN_ID, document=buf, filename=f"backup_{date.today()}.zip")
    await update.callback_query.edit_message_text("✅ הגיבוי נשלח.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

async def admin_global_reset_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("🔥 *איפוס מלא?*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ כן", callback_data="admin_global_reset_confirm")], [InlineKeyboardButton("❌ לא", callback_data="back_admin")]]))

async def admin_global_reset_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for f in DATA_DIR.iterdir():
        if f.suffix == ".json": save_json(f, [] if any(x in f.name for x in ["videos", "orders", "trash"]) else {})
    await update.callback_query.edit_message_text("🔥 אופס.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

async def admin_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("שלח מזהה משתמש:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="back_admin")]]))
    return ADMIN_CHECK_USER

async def admin_check_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = load_json(USERS_FILE).get(update.message.text.strip(), {})
    if u: await update.message.reply_text(f"👤 {u['first_name']}\n💰 {get_coins(u['id'])}\n🛍 {u.get('purchases', 0)}", reply_markup=get_admin_inline_keyboard())
    else: await update.message.reply_text("❌ לא נמצא.", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("שלח מזהה משתמש:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="back_admin")]]))
    return ADMIN_SEND_ID

async def admin_send_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["target_uid"] = update.message.text.strip()
    await update.message.reply_text("מה ההודעה?")
    return ADMIN_SEND_MSG

async def admin_send_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_message(chat_id=context.user_data["target_uid"], text=f"📩 הודעה מהמנהל:\n\n{update.message.text}")
        await update.message.reply_text("✅ נשלחה.", reply_markup=get_admin_inline_keyboard())
    except: await update.message.reply_text("❌ שגיאה.", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("שלח מזהה משתמש:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="back_admin")]]))
    return ADMIN_APPROVE_ID

async def admin_approve_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["target_uid"] = update.message.text.strip()
    await update.message.reply_text("כמה להוסיף?")
    return ADMIN_APPROVE_COUNT

async def admin_approve_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amt = int(update.message.text.strip())
        update_coins(context.user_data["target_uid"], amt)
        await context.bot.send_message(chat_id=context.user_data["target_uid"], text=f"✅ המנהל אישר תשלום של {amt} מטבעות!")
        await update.message.reply_text(f"✅ נוספו {amt}.", reply_markup=get_admin_inline_keyboard())
    except: await update.message.reply_text("❌ שגיאה.", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_maintenance_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = load_settings(); s["maintenance"] = not s.get("maintenance", False); save_settings(s)
    await update.callback_query.answer(f"🔧 תחזוקה: {'פעיל' if s['maintenance'] else 'כבוי'}")
    await admin_panel(update, context)

async def back_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer(); await update.callback_query.edit_message_text("🛠 *פאנל ניהול*", parse_mode="Markdown", reply_markup=get_admin_inline_keyboard())

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("בוטל.", reply_markup=get_main_keyboard(update.effective_user.id))
    return ConversationHandler.END

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    v = update.message.video
    context.user_data["new_video"] = {"file_id": v.file_id, "duration": v.duration, "category": "כללי"}
    cats = load_settings().get("categories", ["כללי"])
    btns = [[InlineKeyboardButton(c, callback_data=f"cat_sel_{c}")] for c in cats]
    await update.message.reply_text("🎬 בחר קטגוריה:", reply_markup=InlineKeyboardMarkup(btns))
    return ADMIN_VIDEO_CAT

async def admin_video_cat_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat = update.callback_query.data.split("cat_sel_")[1]
    v = context.user_data["new_video"]; v["category"] = cat; v["entry_id"] = uuid.uuid4().hex
    videos = load_json(VIDEOS_FILE); videos.append(v); save_json(VIDEOS_FILE, videos)
    await update.callback_query.edit_message_text(f"✅ נוסף ל-{cat}")
    return ConversationHandler.END

def find_duplicate_groups(include_reviewed: bool) -> list[list[dict]]:
    videos = load_videos_with_entry_ids()
    groups = {}
    for v in videos:
        d = v.get("duration", 0)
        if d: groups.setdefault(d, []).append(v)
    dups = [g for g in groups.values() if len(g) > 1]
    if include_reviewed: return dups
    reviewed = reviewed_non_duplicate_signatures()
    return [g for g in dups if duplicate_group_signature(g) not in reviewed]

def main():
    ensure_data_files()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_check, pattern="^admin_check$")], states={ADMIN_CHECK_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_check_input)]}, fallbacks=[CallbackQueryHandler(back_admin, pattern="^back_admin$")], per_message=False))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_send, pattern="^admin_send$")], states={ADMIN_SEND_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_id)], ADMIN_SEND_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_msg)]}, fallbacks=[CallbackQueryHandler(back_admin, pattern="^back_admin$")], per_message=False))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_approve, pattern="^admin_approve$")], states={ADMIN_APPROVE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_approve_id)], ADMIN_APPROVE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_approve_count)]}, fallbacks=[CallbackQueryHandler(back_admin, pattern="^back_admin$")], per_message=False))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_video_search_start, pattern="^admin_video_search$")], states={ADMIN_VIDEO_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_video_search_input)]}, fallbacks=[CallbackQueryHandler(admin_gallery, pattern="^admin_gallery$")], per_message=False))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_search_sec_start, pattern="^admin_search_sec_start$")], states={ADMIN_VIDEO_SEARCH_SECONDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_search_sec_input)]}, fallbacks=[CallbackQueryHandler(admin_gallery, pattern="^admin_gallery$")], per_message=False))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_cat_add_start, pattern="^admin_cat_add$")], states={ADMIN_VIDEO_CAT_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_cat_add_input)]}, fallbacks=[CallbackQueryHandler(admin_categories_menu, pattern="^admin_categories$")], per_message=False))
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.VIDEO, handle_video)], states={ADMIN_VIDEO_CAT: [CallbackQueryHandler(admin_video_cat_sel, pattern="^cat_sel_")]}, fallbacks=[CommandHandler("cancel", cancel)], per_message=False))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🛠 פאנל אדמין$"), admin_panel))
    app.add_handler(MessageHandler(filters.Regex("^🪙 טען מטבעות$"), coins_menu))
    app.add_handler(MessageHandler(filters.Regex("^👤 הפרופיל שלי$"), profile_menu))
    app.add_handler(MessageHandler(filters.Regex("^🤝 תוכנית שותפים$"), referrals_menu))

    cbs = [
        ("^noop$", noop_callback), ("^back_main$", back_main), ("^admin_stats$", admin_stats), (r"^admin_orders_page_\d+$", admin_orders_page),
        (r"^users_page_\d+$", users_page), ("^admin_gallery$", admin_gallery), ("^admin_dup_scan$", admin_dup_scan), (r"^dup_page_\d+$", admin_dup_page),
        (r"^dup_mark_\d+$", admin_dup_mark_not_duplicate), (r"^dup_send_\d+$", admin_dup_send_group), (r"^vid_page_\d+$", admin_gallery_page),
        (r"^vid_del_\d+$", admin_gallery_delete), (r"^del_eid_", admin_gallery_delete), ("^vid_send_all$", admin_gallery_send_all),
        ("^admin_categories$", admin_categories_menu), (r"^cat_sort_page_\d+$", admin_cat_sort_page), ("^admin_cat_sort_start$", lambda u, c: admin_cat_sort_page(u, c, 0)),
        (r"^cat_assign_", admin_cat_assign), (r"^admin_trash_page_\d+$", admin_trash_page), (r"^trash_restore_\d+$", admin_trash_restore),
        (r"^trash_perm_\d+$", admin_trash_perm), ("^trash_empty$", admin_trash_empty), ("^admin_maintenance$", admin_maintenance_toggle),
        ("^back_admin$", back_admin), ("^admin_backup$", admin_backup), ("^admin_global_reset$", admin_global_reset_start), ("^admin_global_reset_confirm$", admin_global_reset_confirm),
    ]
    for p, h in cbs: app.add_handler(CallbackQueryHandler(h, pattern=p))

    async def run_application():
        await app.initialize()
        await app.start()
        
        async def handle_polling_error(error):
            if isinstance(error, Conflict):
                logger.info("Conflict error: waiting 10s...")
                await asyncio.sleep(10)
                return
            logger.error("Polling error: %s", error)

        # Start polling in a way that handles conflict by waiting
        while True:
            try:
                await app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
                # Keep the async task alive
                while app.updater.running:
                    await asyncio.sleep(1)
            except Conflict:
                logger.info("Conflict detected, waiting to retry...")
                await asyncio.sleep(10)
            except Exception as e:
                logger.error("Fatal polling error: %s", e)
                await asyncio.sleep(5)

    if sys.version_info >= (3, 11):
        try: asyncio.run(run_application())
        except KeyboardInterrupt: pass
    else: app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)

if __name__ == "__main__":
    main()
