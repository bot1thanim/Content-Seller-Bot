import requests
import io
import os
import json
import random
import asyncio
import logging
import threading
import warnings
import zipfile
import time
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
    PreCheckoutQueryHandler,
)

warnings.filterwarnings("ignore", message=".*per_message=False.*CallbackQueryHandler.*")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7706183809"))
# PAYPAL_LINK removed for automation
PAYPAL_CLIENT_ID = "BAAd231NK9V9yCPlOHY57GWJDlLY_6W6G6ZZS0g3jUh8SzaLG8Q2sdfHcuE_Pi-m3kDZTvcMpahHCcEYlk"
PAYPAL_CLIENT_SECRET = "EIxM_M8lRQzoIaaxKAa3ugpK_VnFg3wqCoxgUivq-TnLxxGbtVn6m-7c4OWaeAb_tqnOVyd4khvx2LSI"
PAYPAL_API_BASE = "https://api-m.paypal.com"


DATA_DIR       = Path("data")
USERS_FILE     = DATA_DIR / "users.json"
REFERRALS_FILE = DATA_DIR / "referrals.json"
VIDEOS_FILE    = DATA_DIR / "videos.json"
ORDERS_FILE    = DATA_DIR / "orders.json"
SETTINGS_FILE  = DATA_DIR / "settings.json"

ORDERS_PER_PAGE  = 10

PACKAGES = [
    {"price": 0.1, "videos": 1, "stars": 1, "label_paypal": "₪0.1 – בדיקה", "label_stars": "⭐1 – חבילת בדיקה"},

    {"price": 2,   "videos": 1,   "stars": 22,   "label_paypal": "₪2 – 1 סרטון",       "label_stars": "⭐22 – 1 סרטון"},
    {"price": 9,   "videos": 5,   "stars": 101,  "label_paypal": "₪9 – 5 סרטונים",      "label_stars": "⭐101 – 5 סרטונים"},
    {"price": 16,  "videos": 10,  "stars": 180,  "label_paypal": "₪16 – 10 סרטונים",    "label_stars": "⭐180 – 10 סרטונים"},
    {"price": 30,  "videos": 20,  "stars": 337,  "label_paypal": "₪30 – 20 סרטונים",    "label_stars": "⭐337 – 20 סרטונים"},
    {"price": 65,  "videos": 50,  "stars": 730,  "label_paypal": "₪65 – 50 סרטונים",    "label_stars": "⭐730 – 50 סרטונים"},
    {"price": 85,  "videos": 70,  "stars": 955,  "label_paypal": "₪85 – 70 סרטונים",    "label_stars": "⭐955 – 70 סרטונים"},
    {"price": 110, "videos": 100, "stars": 1236, "label_paypal": "₪110 – 100 סרטונים",  "label_stars": "⭐1236 – 100 סרטונים"},
    {"price": 185, "videos": 200, "stars": 2079, "label_paypal": "₪185 – 200 סרטונים",  "label_stars": "⭐2079 – 200 סרטונים"},
]

# VIP Levels
VIP_LEVELS = [
    {"name": "ברונזה", "min_purchases": 0,  "discount": 0.0,  "icon": "🥉"},
    {"name": "כסף",   "min_purchases": 6,  "discount": 0.10, "icon": "🥈"},
    {"name": "זהב",   "min_purchases": 16, "discount": 0.25, "icon": "🥇"},
    {"name": "יהלום", "min_purchases": 31, "discount": 0.40, "icon": "💎"},
]

# ── Conversation states ──
(
    ADMIN_SEND_MSG,
    ADMIN_SEND_ID,
    ADMIN_APPROVE_COUNT,
    ADMIN_APPROVE_ID,
    ADMIN_CHECK_USER,
    ADMIN_BROADCAST,
    ADMIN_BROADCAST_BTN,
    ADMIN_BROADCAST_DELAY,
    SUPPORT_WAITING_MSG,
    SUPPORT_REPLY_MSG,
    ADMIN_MULTIPLIER,
    ADMIN_RESTORE,
    ADMIN_GLOBAL_RESET_CONFIRM,
    ADMIN_VIDEO_SEARCH,
    ADMIN_VIDEO_CAT,
    ADMIN_VIDEO_PREVIEW,
    ADMIN_BROADCAST_MEDIA,
    ADMIN_VIP_ID,
    ADMIN_VIP_LEVEL
) = range(19)


# ─── Data helpers ─────────────────────────────────────────────────────────────

def ensure_data_files():
    DATA_DIR.mkdir(exist_ok=True)
    defaults = [
        (USERS_FILE,     {}),
        (REFERRALS_FILE, {}),
        (VIDEOS_FILE,    []),
        (ORDERS_FILE,    []),
        (SETTINGS_FILE,  {"referral_multiplier": 1.0, "maintenance": False}),
    ]
    for filepath, default in defaults:
        if not filepath.exists():
            save_json(filepath, default)

def load_json(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Migration: convert old video list to new structure
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

def load_settings() -> dict:
    s = load_json(SETTINGS_FILE)
    if not isinstance(s, dict):
        s = {}
    s.setdefault("referral_multiplier", 1.0)
    s.setdefault("maintenance", False)
    s.setdefault("waiting_users", [])
    return s

def save_settings(s: dict):
    save_json(SETTINGS_FILE, s)

def is_maintenance() -> bool:
    return load_settings().get("maintenance", False)

# ─── VIP & Discount logic ────────────────────────────────────────────────────

def get_user_vip(user_id: str) -> dict:
    users = load_json(USERS_FILE)
    u = users.get(user_id, {})
    purchases = u.get("purchases", 0)
    current_vip = VIP_LEVELS[0]
    for level in VIP_LEVELS:
        if purchases >= level["min_purchases"]:
            current_vip = level
    return current_vip

def get_discounted_price(user_id: str, original_price: float) -> float:
    vip = get_user_vip(user_id)
    discount = vip["discount"]
    return round(original_price * (1 - discount), 2)

# ─── Business logic ───────────────────────────────────────────────────────────

def register_user(user, ref_id=None):
    users = load_json(USERS_FILE)
    uid   = str(user.id)
    today = str(date.today())
    if uid not in users:
        users[uid] = {
            "id": user.id,
            "first_name": user.first_name,
            "username": user.username,
            "joined": today,
            "purchases": 0,
            "total_spent": 0,
            "seen_videos": [],
            "last_bonus": None
        }
        save_json(USERS_FILE, users)
        if ref_id and str(ref_id) != uid:
            referrals = load_json(REFERRALS_FILE)
            ref_key = str(ref_id)
            if ref_key not in referrals:
                referrals[ref_key] = {"count": 0, "referred_ids": []}
            if uid not in referrals[ref_key]["referred_ids"]:
                referrals[ref_key]["count"] += 1
                referrals[ref_key]["referred_ids"].append(uid)
                save_json(REFERRALS_FILE, referrals)
                
    return users.get(uid, {})

async def send_videos_to_user(context, user_id: int, count: int) -> int:
    all_videos = load_json(VIDEOS_FILE)
    users = load_json(USERS_FILE)
    uid = str(user_id)
    user_data = users.get(uid, {})
    seen = user_data.get("seen_videos", [])
    
    pool = all_videos
        
    # Sort pool by duration (ascending)
    pool.sort(key=lambda x: x.get("duration", 0))
    
    # Duplicate prevention: find unseen videos
    unseen = [v for v in pool if v["file_id"] not in seen]
    
    if len(unseen) >= count:
        # Randomly select from unseen videos
        selected = random.sample(unseen, count)
    else:
        # If not enough unseen, take all unseen and fill the rest from seen (randomly)
        remaining_count = count - len(unseen)
        seen_pool = [v for v in pool if v["file_id"] in seen]
        
        # Take all unseen
        selected = unseen
        
        # Add random videos from seen pool if possible
        if seen_pool and remaining_count > 0:
            selected += random.sample(seen_pool, min(remaining_count, len(seen_pool)))
        
    sent = 0
    for v in selected:
        try:
            file_id = v["file_id"]
            await context.bot.send_video(chat_id=user_id, video=file_id)
            if file_id not in seen:
                seen.append(file_id)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
            
    user_data["seen_videos"] = seen
    users[uid] = user_data
    save_json(USERS_FILE, users)
    return sent


def get_paypal_access_token():
    try:
        res = requests.post(
            f"{PAYPAL_API_BASE}/v1/oauth2/token",
            auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
            data={"grant_type": "client_credentials"},
            timeout=10
        )
        if res.status_code == 200:
            return res.json()["access_token"]
    except Exception as e:
        logger.error(f"PayPal Token Error: {e}")
    return None

def create_paypal_order(amount, currency="ILS"):
    token = get_paypal_access_token()
    if not token: return None
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    payload = {"intent": "CAPTURE", "purchase_units": [{"amount": {"currency_code": currency, "value": str(amount)}}]}
    try:
        res = requests.post(f"{PAYPAL_API_BASE}/v2/checkout/orders", headers=headers, json=payload, timeout=10)
        if res.status_code == 201:
            data = res.json()
            approve_link = next(link["href"] for link in data["links"] if link["rel"] == "approve")
            return data["id"], approve_link
    except Exception as e:
        logger.error(f"PayPal Create Order Error: {e}")
    return None

def capture_paypal_order(order_id):
    token = get_paypal_access_token()
    if not token: return False, "Token Error"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    try:
        res = requests.post(f"{PAYPAL_API_BASE}/v2/checkout/orders/{order_id}/capture", headers=headers, timeout=10)
        if res.status_code in [200, 201]:
            data = res.json()
            if data.get("status") == "COMPLETED":
                return True, "Success"
        return False, f"Status: {res.status_code}"
    except Exception as e:
        logger.error(f"PayPal Capture Error: {e}")
        return False, str(e)

def record_order(user_id: int, amount: float, videos_count: int, order_type: str):
    orders = load_json(ORDERS_FILE)
    orders.append({
        "user_id":      user_id,
        "amount":       amount,
        "videos_count": videos_count,
        "date":         str(date.today()),
        "type":         order_type,
    })
    save_json(ORDERS_FILE, orders)
    users = load_json(USERS_FILE)
    uid   = str(user_id)
    if uid in users:
        users[uid]["purchases"]   = users[uid].get("purchases", 0) + 1
        users[uid]["total_spent"] = users[uid].get("total_spent", 0) + amount
        save_json(USERS_FILE, users)

async def alert_admin(context, text: str):
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="Markdown")
    except Exception:
        pass

def build_zip_of_data() -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in DATA_DIR.iterdir():
            if f.suffix == ".json":
                zf.write(f, f.name)
    buf.seek(0)
    return buf

# ─── Keyboard builders ────────────────────────────────────────────────────────

def get_main_keyboard(user_id):
    vip = get_user_vip(str(user_id))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{vip['icon']} רמת {vip['name']}", callback_data="vip_info")],
        [
            InlineKeyboardButton("💳 תשלום",       callback_data="payment_method"),
            InlineKeyboardButton("👥 הפניות שלי",   callback_data="referrals"),
        ],
        
        [InlineKeyboardButton("💬 תמיכה",           callback_data="support")],
    ])

def get_admin_reply_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🛠 פאנל אדמין")]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )

def get_admin_inline_keyboard():
    settings    = load_settings()
    maint_status = "🟠 תחזוקה" if settings.get("maintenance") else "🟢 פעיל"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📡 סטטוס בוט: {maint_status}", callback_data="admin_maintenance")],
        [
            InlineKeyboardButton("📊 סטטיסטיקה",      callback_data="admin_stats"),
            InlineKeyboardButton("🧾 הזמנות",          callback_data="admin_orders_page_0"),
        ],
        [
            InlineKeyboardButton("🔍 בדוק משתמש",     callback_data="admin_check"),
            InlineKeyboardButton("👥 רשימת משתמשים",  callback_data="users_page_0"),
        ],
        [
            InlineKeyboardButton("📩 שלח למשתמש",     callback_data="admin_send"),
            InlineKeyboardButton("✅ אישור תשלום",     callback_data="admin_approve"),
        ],
        [
            InlineKeyboardButton("🎬 גלריית סרטונים",  callback_data="admin_gallery"),
            InlineKeyboardButton("🔢 חיפוש סרטון",    callback_data="admin_video_search"),
        ],
        [
            InlineKeyboardButton("📢 הודעה לכולם",    callback_data="admin_broadcast"),
            InlineKeyboardButton("🪙 ניהול מטבעות",   callback_data="admin_coins"),
        ],
        [
            InlineKeyboardButton("💎 ניהול דרגות",    callback_data="admin_vip"),
        ],
        [
            InlineKeyboardButton("🎟 ניהול קופונים",  callback_data="admin_coupons"),
            InlineKeyboardButton("💱 ערך מטבע",       callback_data="admin_multiplier"),
        ],
        [
            InlineKeyboardButton("💾 גיבוי ZIP",      callback_data="admin_backup"),
            InlineKeyboardButton("📥 שחזור גיבוי",   callback_data="admin_restore"),
        ],
        [
            InlineKeyboardButton("🔄 איפוס נתונים",  callback_data="admin_global_reset"),
            InlineKeyboardButton("🧹 מחק סרטונים",   callback_data="admin_delete"),
        ],
        [InlineKeyboardButton("🔧 ניהול מצב תחזוקה",    callback_data="admin_maintenance")],
    ])

# ─── Maintenance gate ─────────────────────────────────────────────────────────

async def maintenance_gate(update: Update) -> bool:
    if update.effective_user and update.effective_user.id == ADMIN_ID:
        return False
    if not is_maintenance():
        return False
    
    # רישום המשתמש ברשימת ההמתנה אם הוא לא שם
    if update.effective_user:
        settings = load_settings()
        uid = update.effective_user.id
        if uid not in settings.get("waiting_users", []):
            settings["waiting_users"].append(uid)
            save_settings(settings)
            
    msg = "🔧 *הבוט בשיפוצים*\n\nנחזור בקרוב! 🙏\n\n*אל דאגה! רשמנו אותך, ונשלח לך הודעה ברגע שנחזור לפעילות!* ✅"
    if update.callback_query:
        await update.callback_query.answer("הבוט בשיפוצים, חזרו בקרוב! נשלח לך הודעה כשנחזור.", show_alert=True)
    elif update.message:
        await update.message.reply_text(msg, parse_mode="Markdown")
    return True

# ─── /start ───────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.date:
        age = (datetime.now(timezone.utc) - update.message.date).total_seconds()
        if age > 30:
            return

    if await maintenance_gate(update):
        return

    user   = update.effective_user
    ref_id = None
    args   = context.args or []
    if args and args[0].startswith("ref_"):
        try:
            ref_id = int(args[0].split("ref_")[1])
        except ValueError:
            pass

    register_user(user, ref_id)

    if user.id == ADMIN_ID:
        await update.message.reply_text("👋 ברוך הבא אדמין!", reply_markup=get_admin_reply_keyboard())

    vip = get_user_vip(str(user.id))
    await update.message.reply_text(
        f"שלום {user.first_name} 👋\n"
        f"דרגה: {vip['icon']} *{vip['name']}* ({int(vip['discount']*100)}% הנחה)\n"
        f"ברוכים הבאים לבוט התכנים האסורים 🤫\nבחר אפשרות:",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(user.id),
    )

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user  = query.from_user
    vip = get_user_vip(str(user.id))
    await query.edit_message_text(
        f"שלום {user.first_name} 👋\n"
        f"דרגה: {vip['icon']} *{vip['name']}* ({int(vip['discount']*100)}% הנחה)\n"
        f"ברוכים הבאים לבוט התכנים האסורים 🤫\nבחר אפשרות:",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(user.id),
    )

# ─── Daily Bonus ─────────────────────────────────────────────────────────────

async def vip_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    user_vip = get_user_vip(uid)
    users = load_json(USERS_FILE)
    purchases = users.get(uid, {}).get("purchases", 0)
    
    text = f"👑 *מערכת דרגות VIP*\n\n"
    text += f"הדרגה שלך: {user_vip['icon']} *{user_vip['name']}*\n"
    text += f"רכישות שביצעת: *{purchases}*\n\n"
    text += "📊 *טבלת דרגות:*\n"
    for level in VIP_LEVELS:
        text += f"{level['icon']} *{level['name']}*: {level['min_purchases']}+ רכישות | {int(level['discount']*100)}% הנחה\n"
    
    text += "\n_ההנחה חלה באופן אוטומטי על תשלום בפייפאל ._"
    
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]),
    )

# ─── Payment ──────────────────────────────────────────────────────────────────

async def payment_method_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await maintenance_gate(update):
        return
    await query.edit_message_text(
        "💰 *בחר אמצעי תשלום:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ שלם בכוכבי טלגרם",                    callback_data="stars_menu")],
            [InlineKeyboardButton("💳 תשלום בפייפאל",                        callback_data="paypal_menu")],
            [InlineKeyboardButton("💬 תשלום אחר (פנייה לתמיכה)",             callback_data="support")],
            [InlineKeyboardButton("🔙 חזרה",                                 callback_data="back_main")],
        ]),
    )

async def wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    if await maintenance_gate(update):
        return
    

    await update.message.reply_text(
        f"✅ *קופון מומש!*\n\n🪙 קיבלת *{reward} מטבעות*\n💰 יתרה כעת: *{coins[user_id]}*",
        parse_mode="Markdown",
        reply_markup=back_btn,
    )
    await alert_admin(context,
        f"🎟 *מימוש קופון*\n👤 ID: `{user_id}`\n🎫 קוד: `{code}`\n🪙 מטבעות: {reward}")
    return ConversationHandler.END

# ─── Support ──────────────────────────────────────────────────────────────────

async def support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await maintenance_gate(update):
        return
    await query.edit_message_text(
        "💬 *תמיכה*\n\nכתוב את הודעתך ואנחנו נחזור אליך בהקדם 👇",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]),
    )
    return SUPPORT_WAITING_MSG

async def support_receive_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user     = update.effective_user
    username = f"@{user.username}" if user.username else "ללא יוזרנייים"
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📩 *הודעת תמיכה חדשה*\n\n👤 {user.first_name}\n🔗 {username}\n🆔 `{user.id}`\n\n💬 {update.message.text}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"↩️ תשובה ל-{user.id}", callback_data=f"support_reply_{user.id}")
        ]]),
    )
    await update.message.reply_text(
        "✅ ההודעה נשלחה למנהל! נחזור אליך בהקדם 🙏",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]),
    )
    return ConversationHandler.END

# ─── Admin: support reply ─────────────────────────────────────────────────────

async def admin_support_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    target = query.data.replace("support_reply_", "")
    context.user_data["support_reply_target"] = target
    await query.message.reply_text(f"✏️ תשובה ל-`{target}`:\n\nכתוב את ההודעה:", parse_mode="Markdown")
    return SUPPORT_REPLY_MSG

async def admin_support_reply_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    target = context.user_data.get("support_reply_target")
    try:
        await context.bot.send_message(chat_id=int(target), text=f"📬 *תשובה מהמנהל:*\n\n{update.message.text}", parse_mode="Markdown")
        await update.message.reply_text(f"✅ נשלח למשתמש {target}!")
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {e}")
    return ConversationHandler.END

# ─── Admin: panel ─────────────────────────────────────────────────────────────

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    settings = load_settings()
    maint_status = "🟠 *מצב תחזוקה פעיל*" if settings.get("maintenance") else "🟢 *הבוט פעיל כרגיל*"
    await update.message.reply_text(
        f"🛠 *פאנל אדמין*\n\nסטטוס נוכחי: {maint_status}\n\nבחר פעולה:",
        parse_mode="Markdown",
        reply_markup=get_admin_inline_keyboard(),
    )

async def back_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    settings = load_settings()
    maint_status = "🟠 *מצב תחזוקה פעיל*" if settings.get("maintenance") else "🟢 *הבוט פעיל כרגיל*"
    await query.edit_message_text(
        f"🛠 *פאנל אדמין*\n\nסטטוס נוכחי: {maint_status}\n\nבחר פעולה:",
        parse_mode="Markdown",
        reply_markup=get_admin_inline_keyboard(),
    )

# ─── Admin: stats ─────────────────────────────────────────────────────────────

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    users    = load_json(USERS_FILE)
    orders   = load_json(ORDERS_FILE)
    videos   = load_json(VIDEOS_FILE)
    
    action = "נוספו ➕" if amount >= 0 else "הוסרו ➖"
    await update.message.reply_text(
        f"✅ עודכן!\n🪙 {abs(amount)} מטבעות {action}\n💰 יתרה חדשה: {new_bal}",
        reply_markup=get_admin_inline_keyboard(),
    )
    return ConversationHandler.END

# ─── Admin: coupon management ─────────────────────────────────────────────────

async def admin_coupons_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    coupons = load_json(COUPONS_FILE)
    lines   = ["🎟 *ניהול קופונים*\n"]
    if coupons:
        for code, c in coupons.items():
            uses  = len(c.get("used_by", []))
            max_u = c.get("max_uses", "∞")
            exp   = c.get("expires", "ללא הגבלה")
            lines.append(f"• `{code}` — 🪙{c['coins']} | {uses}/{max_u} | תפוגה: {exp}")
    else:
        lines.append("אין קופונים עדיין.")
    btns = [[InlineKeyboardButton("➕ צור קופון חדש", callback_data="admin_coupon_new")]]
    for code in list(coupons.keys())[:10]:
        btns.append([InlineKeyboardButton(f"🗑 מחק {code}", callback_data=f"coupon_del_{code}")])
    btns.append([InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")])
    await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def admin_coupon_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    code    = query.data.replace("coupon_del_", "")
    coupons = load_json(COUPONS_FILE)
    if code in coupons:
        del coupons[code]
        save_json(COUPONS_FILE, coupons)
    await admin_coupons_menu(update, context)

async def admin_coupon_new_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("🎟 *קופון חדש*\n\nשלח את *קוד הקופון* (אותיות/מספרים):", parse_mode="Markdown")
    return ADMIN_COUPON_CODE

async def admin_coupon_get_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    code = update.message.text.strip().upper()
    if not code.replace("_", "").replace("-", "").isalnum():
        await update.message.reply_text("❌ קוד לא תקין. רק אותיות ומספרים.")
        return ADMIN_COUPON_CODE
    coupons = load_json(COUPONS_FILE)
    if code in coupons:
        await update.message.reply_text("❌ קוד כבר קיים.")
        return ADMIN_COUPON_CODE
    context.user_data["new_coupon_code"] = code
    await update.message.reply_text(f"✅ קוד: `{code}`\n\nכמה 🪙 מטבעות?", parse_mode="Markdown")
    return ADMIN_COUPON_COINS

async def admin_coupon_get_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        val = int(update.message.text.strip())
        if val <= 0:
            raise ValueError
        context.user_data["new_coupon_coins"] = val
    except ValueError:
        await update.message.reply_text("❌ מספר לא תקין.")
        return ADMIN_COUPON_COINS
    await update.message.reply_text("📅 תאריך תפוגה? (`YYYY-MM-DD` או `skip`):", parse_mode="Markdown")
    return ADMIN_COUPON_EXPIRY

async def admin_coupon_get_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    raw = update.message.text.strip()
    if raw.lower() == "skip":
        context.user_data["new_coupon_expiry"] = None
    else:
        try:
            datetime.strptime(raw, "%Y-%m-%d")
            context.user_data["new_coupon_expiry"] = raw
        except ValueError:
            await update.message.reply_text("❌ פורמט לא תקין. נסה `YYYY-MM-DD` או `skip`.", parse_mode="Markdown")
            return ADMIN_COUPON_EXPIRY
    await update.message.reply_text("👥 מגבלת שימושים? (מספר או `skip`):", parse_mode="Markdown")
    return ADMIN_COUPON_LIMIT

async def admin_coupon_get_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    raw      = update.message.text.strip()
    max_uses = None
    if raw.lower() != "skip":
        try:
            max_uses = int(raw)
            if max_uses <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ מספר לא תקין.")
            return ADMIN_COUPON_LIMIT
    code  = context.user_data["new_coupon_code"]
    coins_val = context.user_data["new_coupon_coins"]
    expiry    = context.user_data.get("new_coupon_expiry")
    coupons   = load_json(COUPONS_FILE)
    coupons[code] = {"coins": coins_val, "expires": expiry, "max_uses": max_uses, "used_by": []}
    save_json(COUPONS_FILE, coupons)
    await update.message.reply_text(
        f"✅ *קופון נוצר!*\n\n🎟 `{code}`\n🪙 {coins_val} מטבעות\n📅 תפוגה: {expiry or 'ללא'}\n👥 מגבלה: {max_uses or 'ללא'}",
        parse_mode="Markdown",
        reply_markup=get_admin_inline_keyboard(),
    )
    return ConversationHandler.END

# ─── Admin: currency multiplier ───────────────────────────────────────────────

async def admin_multiplier_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    settings = load_settings()
    current  = settings.get("referral_multiplier", 1.0)
    await query.edit_message_text(
        f"💱 *ערך מטבעות*\n\nהמכפיל הנוכחי: *{current}x*\n(על כל הפניה מקבלים 1 * המכפיל)\n\nשלח מכפיל חדש (למשל 1.5):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="back_admin")]]),
    )
    return ADMIN_MULTIPLIER

async def admin_multiplier_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        new_mult = float(update.message.text.strip())
        if new_mult <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ מספר לא תקין.")
        return ADMIN_MULTIPLIER
    settings = load_settings()
    old_mult = settings.get("referral_multiplier", 1.0)
    ratio    = new_mult / old_mult
    
    settings["referral_multiplier"] = new_mult
    save_settings(settings)
    await update.message.reply_text(f"✅ *מכפיל עודכן:* {old_mult}x → {new_mult}x\n\nשולח הודעה לכל המשתמשים...", parse_mode="Markdown")
    users = load_json(USERS_FILE)
    sent  = 0
    for uid in users:
        bal = coins.get(uid, 0)
        try:
            await context.bot.send_message(
                chat_id=int(uid),
                text=f"💱 *ערך המטבעות השתנה!*\n\nיתרתך עודכנה ל-*{bal} מטבעות*.\nכעת תקבל *{new_mult}x* מטבעות על כל הפניה! 🎉",
                parse_mode="Markdown",
            )
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await update.message.reply_text(f"✅ הודעות נשלחו ל-{sent} משתמשים.", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

# ─── Admin: backup ZIP ────────────────────────────────────────────────────────

async def admin_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    await query.edit_message_text("💾 *יוצר גיבוי ZIP...*", parse_mode="Markdown")
    buf = build_zip_of_data()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    await context.bot.send_document(
        chat_id=ADMIN_ID,
        document=buf,
        filename=f"backup_{stamp}.zip",
        caption=f"💾 גיבוי מלא — {stamp}",
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text="✅ הגיבוי הושלם!", reply_markup=get_admin_inline_keyboard())

# ─── Admin: restore from ZIP ─────────────────────────────────────────────────

async def admin_restore_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text(
        "📥 *שחזור מגיבוי*\n\n⚠️ זה ישכתב את הנתונים הקיימים!\n\nשלח קובץ ZIP של הגיבוי:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="back_admin")]]),
    )
    return ADMIN_RESTORE

async def admin_restore_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    doc = update.message.document
    if not doc or not doc.file_name.endswith(".zip"):
        await update.message.reply_text("❌ שלח קובץ ZIP בלבד.")
        return ADMIN_RESTORE

    await update.message.reply_text("⏳ מחלץ גיבוי...")
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        buf     = io.BytesIO()
        await tg_file.download_to_memory(buf)
        buf.seek(0)
        with zipfile.ZipFile(buf, "r") as zf:
            names = zf.namelist()
            json_files = [n for n in names if n.endswith(".json")]
            if not json_files:
                await update.message.reply_text("❌ לא נמצאו קבצי JSON בארכיון.", reply_markup=get_admin_inline_keyboard())
                return ConversationHandler.END
            DATA_DIR.mkdir(exist_ok=True)
            for name in json_files:
                data = json.loads(zf.read(name).decode("utf-8"))
                save_json(DATA_DIR / name, data)
        await update.message.reply_text(
            f"✅ *שחזור הושלמה!*\nשוחזרו: {', '.join(json_files)}",
            parse_mode="Markdown",
            reply_markup=get_admin_inline_keyboard(),
        )
    except zipfile.BadZipFile:
        await update.message.reply_text("❌ קובץ ZIP פגום.", reply_markup=get_admin_inline_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה בשחזור: {e}", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

# ─── Admin: global reset ──────────────────────────────────────────────────────

async def admin_global_reset_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END

    users  = len(load_json(USERS_FILE))
    orders = len(load_json(ORDERS_FILE))
    videos = len(load_json(VIDEOS_FILE))
    coins  = len(load_json(COINS_FILE))

    await query.edit_message_text(
        f"🔄 *איפוס נתונים כולל*\n\n"
        f"⚠️ פעולה זו תמחק לצמיתות:\n"
        f"• {users} משתמשים\n"
        f"• {orders} הזמנות\n"
        f"• {videos} סרטונים\n"
        f"• {coins} יתרות מטבעות\n"
        f"• קופונים והפניות\n\n"
        f"💾 *מומלץ מאוד לבצע גיבוי לפני!*\n\n"
        f"לאישור ראשוני:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ המשך לאישור סופי", callback_data="admin_global_reset_step2")],
            [InlineKeyboardButton("❌ ביטול",              callback_data="back_admin")],
        ]),
    )

async def admin_global_reset_step2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text(
        "🔴 *אישור סופי*\n\nהקלד *מאשר* כדי למחוק את כל הנתונים:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="back_admin")]]),
    )
    return ADMIN_GLOBAL_RESET_CONFIRM

async def admin_global_reset_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    if update.message.text.strip() != "מאשר":
        await update.message.reply_text("❌ ביטול — הטקסט לא תאם. שלח 'מאשר' בדיוק.")
        return ADMIN_GLOBAL_RESET_CONFIRM

    for filepath, default in [
        (USERS_FILE,     {}),
        (REFERRALS_FILE, {}),
        (VIDEOS_FILE,    []),
        (ORDERS_FILE,    []),
        ]:
        save_json(filepath, default)

    await update.message.reply_text(
        "✅ *כל הנתונים נמחקו בהצלחה!*\nהגדרות המערכת (settings.json) נשמרו.",
        parse_mode="Markdown",
        reply_markup=get_admin_inline_keyboard(),
    )
    return ConversationHandler.END

# ─── Admin: delete all videos ────────────────────────────────────────────────

async def admin_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    videos = load_json(VIDEOS_FILE)
    await query.edit_message_text(
        f"🧹 *מחיקת כל הסרטונים*\n\nיש {len(videos)} סרטונים.\nהאם אתה בטוח?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ כן, מחק", callback_data="admin_delete_confirm"),
            InlineKeyboardButton("❌ ביטול",   callback_data="back_admin"),
        ]]),
    )

async def admin_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    save_json(VIDEOS_FILE, [])
    await query.edit_message_text(
        "✅ כל הסרטונים נמחקו!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")]]),
    )

# ─── Admin: maintenance mode ──────────────────────────────────────────────────

async def admin_maintenance_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    
    data = query.data
    settings = load_settings()
    
    if data == "maint_on":
        settings["maintenance"] = True
        # איפוס רשימת המשתמשים המחכים כשמפעילים תחזוקה מחדש
        settings["waiting_users"] = []
        save_settings(settings)
        await query.answer("✅ מצב תחזוקה הופעל", show_alert=True)
    elif data == "maint_off":
        settings["maintenance"] = False
        waiting = settings.get("waiting_users", [])
        save_settings(settings)
        await query.answer("✅ מצב תחזוקה כובה", show_alert=True)
        
        # שליחת הודעה לכל המשתמשים שחיכו
        if waiting:
            count_sent = 0
            for uid in waiting:
                try:
                    await context.bot.send_message(
                        chat_id=uid,
                        text="🎉 *הבוט חזר לפעילות!*\n\nמוזמנים להמשיך להשתמש בבוט וליהנות! 🚀",
                        parse_mode="Markdown"
                    )
                    count_sent += 1
                except Exception:
                    pass
            # איפוס הרשימה לאחר השליחה
            settings = load_settings()
            settings["waiting_users"] = []
            save_settings(settings)
            await query.message.reply_text(f"📢 נשלחה הודעת חזרה לפעילות ל-{count_sent} משתמשים שחיכו.")
        
    status = "🟠 *פעיל (הבוט חסום למשתמשים)*" if settings.get("maintenance") else "🟢 *כבוי (הבוט פתוח לכולם)*"
    
    text = (
        "🔧 *ניהול מצב תחזוקה*\n\n"
        "💡 *מה זה אומר?*\n"
        "• *פועל:* רק האדמין יכול להשתמש בבוט. משתמשים רגילים יראו הודעת תחזוקה.\n"
        "• *כבוי:* הבוט פתוח לשימוש מלא לכל המשתמשים.\n\n"
        f"סטטוס נוכחי: {status}"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🟢 הפעל בוט (כבה תחזוקה)", callback_data="maint_off"),
        ],
        [
            InlineKeyboardButton("🟠 השבת בוט (הפעל תחזוקה)", callback_data="maint_on"),
        ],
        [InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")]
    ]
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ─── Video upload ─────────────────────────────────────────────────────────────

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """מטפל בסרטון שנשלח על ידי האדמין - מכניס ישירות למערכת ללא שאלות."""
    if update.effective_user.id != ADMIN_ID:
        return
    video = update.message.video
    if not video:
        return

    file_id = video.file_id
    duration = video.duration

    videos = load_json(VIDEOS_FILE)
    videos.append({
        "file_id": file_id,
        "duration": duration,
        "category": "כללי",
        "preview": None
    })
    save_json(VIDEOS_FILE, videos)

    await update.message.reply_text(
        f"✅ סרטון נשמר! (אורך: {duration} שניות | סה\"כ בספרייה: {len(videos)})"
    )

async def admin_video_cat_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat = query.data.replace("cat_sel_", "")
    context.user_data["last_upload_cat"] = cat
    await query.edit_message_text(
        f"✅ קטגוריה: *{cat}* נשמרה.",
        parse_mode="Markdown"
    )

async def admin_video_preview_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """פונקציה שמורה לתאימות אחורה - לא בשימוש פעיל."""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    return ConversationHandler.END

# ─── Utility ──────────────────────────────────────────────────────────────────

async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ בוטל.", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

# ─── Health server ────────────────────────────────────────────────────────────

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass

def _start_health_server():
    import socket
    port = int(os.environ.get("PORT", "10000"))
    try:
        server = HTTPServer(("0.0.0.0", port), _HealthHandler)
        server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        logger.info(f"Health server on port {port}")
        server.serve_forever()
    except OSError as e:
        logger.warning(f"Health server could not start: {e}")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    ensure_data_files()

    threading.Thread(target=_start_health_server, daemon=True).start()

    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN לא הוגדר!")
        return

    app = Application.builder().token(TOKEN).build()
    # IMPORTANT: Payment handlers must be registered early
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))


    # ── Conversation handlers ─────────────────────────────────────────────────

    check_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_check_start, pattern="^admin_check$")],
        states={ADMIN_CHECK_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_check_user)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False, per_chat=True,
    )
    send_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_send_start, pattern="^admin_send$")],
        states={
            ADMIN_SEND_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_msg)],
            ADMIN_SEND_ID:  [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False, per_chat=True,
    )
    approve_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_approve_start, pattern="^admin_approve$")],
        states={
            ADMIN_APPROVE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_approve_count)],
            ADMIN_APPROVE_ID:    [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_approve_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False, per_chat=True,
    )
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast$")],
        states={
            ADMIN_BROADCAST:       [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_get_msg)],
            ADMIN_BROADCAST_MEDIA: [
                MessageHandler(filters.PHOTO | filters.VIDEO, admin_broadcast_get_media),
                MessageHandler(filters.Regex("^skip$"), admin_broadcast_get_media)
            ],
            ADMIN_BROADCAST_BTN:   [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_get_btn)],
            ADMIN_BROADCAST_DELAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_get_delay)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False, per_chat=True,
    )
    coins_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_coins_start, pattern="^admin_coins$")],
        states={
            ADMIN_COINS_ID:     [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coins_id)],
            ADMIN_COINS_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coins_amount)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False, per_chat=True,
    )
    vip_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_vip_start, pattern="^admin_vip$")],
        states={
            ADMIN_VIP_ID:    [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_vip_id)],
            ADMIN_VIP_LEVEL: [CallbackQueryHandler(admin_vip_level, pattern="^set_vip_")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(back_admin, pattern="^back_admin$"),
        ],
        per_message=False, per_chat=True,
    )
    coupon_new_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_coupon_new_start, pattern="^admin_coupon_new$")],
        states={
            ADMIN_COUPON_CODE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coupon_get_code)],
            ADMIN_COUPON_COINS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coupon_get_coins)],
            ADMIN_COUPON_EXPIRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coupon_get_expiry)],
            ADMIN_COUPON_LIMIT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coupon_get_limit)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False, per_chat=True,
    )
    multiplier_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_multiplier_start, pattern="^admin_multiplier$")],
        states={ADMIN_MULTIPLIER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_multiplier_apply)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False, per_chat=True,
    )
    restore_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_restore_start, pattern="^admin_restore$")],
        states={ADMIN_RESTORE: [MessageHandler(filters.Document.ALL, admin_restore_receive)]},
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(back_admin, pattern="^back_admin$"),
        ],
        per_message=False, per_chat=True,
    )
    global_reset_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_global_reset_step2, pattern="^admin_global_reset_step2$")],
        states={ADMIN_GLOBAL_RESET_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_global_reset_execute)]},
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(back_admin, pattern="^back_admin$"),
        ],
        per_message=False, per_chat=True,
    )
    video_search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_video_search_start, pattern="^admin_video_search$")],
        states={ADMIN_VIDEO_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_video_search_input)]},
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(back_admin, pattern="^back_admin$"),
        ],
        per_message=False, per_chat=True,
    )
    support_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(support_menu, pattern="^support$")],
        states={
            SUPPORT_WAITING_MSG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive_msg),
                CallbackQueryHandler(back_main, pattern="^back_main$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(back_main, pattern="^back_main$"),
        ],
        per_message=False, per_chat=True,
    )
    coupon_redeem_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(coupon_redeem_start, pattern="^coupon_redeem$")],
        states={COUPON_REDEEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, coupon_redeem_input)]},
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(back_main, pattern="^back_main$"),
        ],
        per_message=False, per_chat=True,
    )
    support_reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_support_reply_start, pattern=r"^support_reply_\d+$")],
        states={SUPPORT_REPLY_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_support_reply_send)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False, per_chat=True,
    )
    # ── Register handlers ─────────────────────────────────────────────────────────────────────────────
    for conv in [
        check_conv, send_conv, approve_conv, broadcast_conv, coins_conv, vip_conv,
        coupon_new_conv, multiplier_conv, restore_conv, global_reset_conv,
        video_search_conv, support_conv, coupon_redeem_conv, support_reply_conv,
    ]:
        app.add_handler(conv)

    # טיפול ישיר בסרטונים שנשלחים על ידי האדמין - ללא ConversationHandler כדי לתמוך בשליחת מספר סרטונים בו-זמנית
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))

    # Telegram Stars payment handlers

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🛠 פאנל אדמין$"), admin_panel))

    # Callback handlers (standalone)
    cbs = [
        ("^noop$",                      noop_callback),
        ("^payment_method$",            payment_method_menu),
        ("^paypal_menu$",               paypal_menu),
        (r"^pp_\d+$",                   paypal_package_selected),
        (r"^ppverify_\d+_",           paypal_verify_payment),
        ("^coins_menu$",                coins_menu),
        (r"^coin_\d+$",                 coin_package_buy),
        ("^stars_menu$",                stars_menu),
        (r"^star_\d+$",                 star_package_buy),
        ("^referrals$",                 referrals_menu),
        ("^wallet$",                    wallet_menu),
        ("^daily_bonus$",               daily_bonus),
        ("^vip_info$",                  vip_info),
        ("^back_main$",                 back_main),
        ("^admin_stats$",               admin_stats),
        (r"^admin_orders_page_\d+$",    admin_orders_page),
        (r"^users_page_\d+$",           users_page),
        ("^admin_gallery$",             admin_gallery),
        (r"^vid_page_\d+$",             admin_gallery_page),
        (r"^vid_del_\d+$",              admin_gallery_delete),
        ("^vid_send_all$",              admin_gallery_send_all),
        ("^admin_coupons$",             admin_coupons_menu),
        (r"^coupon_del_",               admin_coupon_delete),
        ("^admin_backup$",              admin_backup),
        ("^admin_delete$",              admin_delete_start),
        ("^admin_delete_confirm$",      admin_delete_confirm),
        ("^admin_global_reset$",        admin_global_reset_start),
                ("^admin_maintenance$",          admin_maintenance_toggle),
        ("^maint_on$",                   admin_maintenance_toggle),
        ("^maint_off$",                  admin_maintenance_toggle),
        ("^back_admin$",                back_admin),
    ]
    for pattern, handler in cbs:
        app.add_handler(CallbackQueryHandler(handler, pattern=pattern))

    logger.info("הבוט מופעל... 🚀")
    
    import asyncio
    import sys

    async def run_application():
        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        while True:
            await asyncio.sleep(3600)

    try:
        if sys.version_info >= (3, 11):
            asyncio.run(run_application())
        else:
            app.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Error starting bot: {e}")

if __name__ == "__main__":
    main()
