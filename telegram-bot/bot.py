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
)

warnings.filterwarnings("ignore", message=".*per_message=False.*CallbackQueryHandler.*")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

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
    {"name": "ברונזה", "min_purchases": 0,  "discount": 0.0,  "icon": "🥉"},
    {"name": "כסף",   "min_purchases": 6,  "discount": 0.10, "icon": "🥈"},
    {"name": "זהב",   "min_purchases": 16, "discount": 0.25, "icon": "🥇"},
    {"name": "יהלום", "min_purchases": 31, "discount": 0.40, "icon": "💎"},
]

(
    ADMIN_SEND_MSG,           # 0
    ADMIN_SEND_ID,            # 1
    ADMIN_APPROVE_COUNT,      # 2
    ADMIN_APPROVE_ID,         # 3
    ADMIN_CHECK_USER,         # 4
    ADMIN_COINS_ID,           # 5
    ADMIN_COINS_AMOUNT,       # 6
    ADMIN_BROADCAST,          # 7
    ADMIN_BROADCAST_BTN,      # 8
    ADMIN_BROADCAST_DELAY,    # 9
    SUPPORT_WAITING_MSG,      # 10
    SUPPORT_REPLY_MSG,        # 11
    ADMIN_COUPON_CODE,        # 12
    ADMIN_COUPON_COINS,       # 13
    ADMIN_COUPON_EXPIRY,      # 14
    ADMIN_COUPON_LIMIT,       # 15
    COUPON_REDEEM,            # 16
    ADMIN_MULTIPLIER,         # 17
    ADMIN_RESTORE,            # 18
    ADMIN_GLOBAL_RESET_CONFIRM, # 19
    ADMIN_VIDEO_SEARCH,       # 20
    ADMIN_VIDEO_PREVIEW,      # 21
    ADMIN_BROADCAST_MEDIA,    # 22
    ADMIN_SET_WELCOME_TEXT,   # 23
    ADMIN_SET_WELCOME_MEDIA,  # 24
) = range(25)

def ensure_data_files():
    DATA_DIR.mkdir(exist_ok=True)
    defaults = [
        (USERS_FILE,     {}),
        (COINS_FILE,     {}),
        (REFERRALS_FILE, {}),
        (VIDEOS_FILE,    []),
        (ORDERS_FILE,    []),
        (COUPONS_FILE,   {}),
        (SETTINGS_FILE,  {
            "referral_multiplier": 1.0, 
            "maintenance": False, 
            "welcome_text": "שלום {name} 👋\nברוכים הבאים לבוט התכנים האסורים 🤫",
            "welcome_media": None # (type, file_id)
        }),
    ]
    for filepath, default in defaults:
        if not filepath.exists():
            save_json(filepath, default)

def load_json(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
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
    if not isinstance(s, dict): s = {}
    s.setdefault("referral_multiplier", 1.0)
    s.setdefault("maintenance", False)
    s.setdefault("welcome_text", "שלום {name} 👋\nברוכים הבאים לבוט התכנים האסורים 🤫")
    s.setdefault("welcome_media", None)
    return s

def save_settings(s: dict):
    save_json(SETTINGS_FILE, s)

def is_maintenance() -> bool:
    return load_settings().get("maintenance", False)

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
            "first_name": user.first_name,
            "username": user.username,
            "joined": str(date.today()),
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
                coins = load_json(COINS_FILE)
                coins[ref_key] = coins.get(ref_key, 0) + 1
                save_json(COINS_FILE, coins)
    return users.get(uid, {})

async def send_videos_to_user(context, user_id: int, count: int) -> int:
    all_videos = load_json(VIDEOS_FILE)
    # Sort by duration ascending (shortest to longest)
    all_videos.sort(key=lambda x: x.get("duration", 0))
    
    users = load_json(USERS_FILE)
    uid = str(user_id)
    user_data = users.get(uid, {})
    seen = user_data.get("seen_videos", [])
    
    unseen = [v for v in all_videos if v["file_id"] not in seen]
    
    if len(unseen) >= count:
        selected = unseen[:count]
    else:
        # If not enough unseen, take all unseen and fill the rest from seen
        selected = unseen + random.sample([v for v in all_videos if v["file_id"] in seen], min(count - len(unseen), len(all_videos) - len(unseen)))
        
    sent = 0
    for v in selected:
        try:
            file_id = v["file_id"]
            # Find index in all_videos for numbering (1-based)
            idx = 0
            for i, vid in enumerate(all_videos):
                if vid["file_id"] == file_id:
                    idx = i + 1
                    break
            
            caption = f"🎬 סרטון #{idx}"
            await context.bot.send_video(chat_id=user_id, video=file_id, caption=caption)
            if file_id not in seen:
                seen.append(file_id)
            sent += 1
            await asyncio.sleep(0.1)
        except Exception:
            pass
            
    user_data["seen_videos"] = seen
    users[uid] = user_data
    save_json(USERS_FILE, users)
    return sent

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

def get_main_keyboard(user_id):
    vip = get_user_vip(str(user_id))
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{vip['icon']} רמת {vip['name']}", callback_data="vip_info"),
            InlineKeyboardButton("🎁 מתנה יומית", callback_data="daily_bonus"),
        ],
        [
            InlineKeyboardButton("💳 תשלום",       callback_data="payment_method"),
            InlineKeyboardButton("👥 הפניות שלי",   callback_data="referrals"),
        ],
        [
            InlineKeyboardButton("💰 ארנק מטבעות", callback_data="wallet"),
            InlineKeyboardButton("🎟 מימוש קופון",  callback_data="coupon_redeem"),
        ],
        [InlineKeyboardButton("💬 תמיכה",           callback_data="support")],
    ])

def get_admin_reply_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("🛠 פאנל אדמין")]], resize_keyboard=True)

def get_admin_inline_keyboard():
    settings    = load_settings()
    maint_label = "🔧 כבה תחזוקה" if settings.get("maintenance") else "🔧 מצב תחזוקה"
    return InlineKeyboardMarkup([
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
            InlineKeyboardButton("🎟 ניהול קופונים",  callback_data="admin_coupons"),
            InlineKeyboardButton("👋 הודעת ברוכים הבאים", callback_data="admin_welcome_set"),
        ],
        [
            InlineKeyboardButton("💾 גיבוי ZIP",      callback_data="admin_backup"),
            InlineKeyboardButton("📥 שחזור גיבוי",   callback_data="admin_restore"),
        ],
        [
            InlineKeyboardButton("🔄 איפוס נתונים",  callback_data="admin_global_reset"),
            InlineKeyboardButton("🧹 מחק סרטונים",   callback_data="admin_delete"),
        ],
        [InlineKeyboardButton(maint_label,             callback_data="admin_maintenance")],
    ])

async def maintenance_gate(update: Update) -> bool:
    if update.effective_user and update.effective_user.id == ADMIN_ID: return False
    if not is_maintenance(): return False
    msg = "🔧 *הבוט בשיפוצים*\n\nנחזור בקרוב! 🙏"
    if update.callback_query: await update.callback_query.answer("הבוט בשיפוצים!", show_alert=True)
    elif update.message: await update.message.reply_text(msg, parse_mode="Markdown")
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await maintenance_gate(update): return
    user = update.effective_user
    ref_id = None
    if context.args and context.args[0].startswith("ref_"):
        try: ref_id = int(context.args[0].split("ref_")[1])
        except: pass
    register_user(user, ref_id)
    if user.id == ADMIN_ID:
        await update.message.reply_text("👋 ברוך הבא אדמין!", reply_markup=get_admin_reply_keyboard())
    
    settings = load_settings()
    vip = get_user_vip(str(user.id))
    welcome_text = settings["welcome_text"].format(name=user.first_name)
    welcome_text += f"\n\nדרגה: {vip['icon']} *{vip['name']}* ({int(vip['discount']*100)}% הנחה)"
    
    media = settings.get("welcome_media")
    if media:
        m_type, f_id = media
        if m_type == "photo":
            await context.bot.send_photo(chat_id=user.id, photo=f_id, caption=welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard(user.id))
        else:
            await context.bot.send_video(chat_id=user.id, video=f_id, caption=welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard(user.id))
    else:
        await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard(user.id))

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    settings = load_settings()
    vip = get_user_vip(str(user.id))
    welcome_text = settings["welcome_text"].format(name=user.first_name)
    welcome_text += f"\n\nדרגה: {vip['icon']} *{vip['name']}* ({int(vip['discount']*100)}% הנחה)"
    await query.edit_message_text(welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard(user.id))

async def daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    users = load_json(USERS_FILE)
    user_data = users.get(uid, {})
    if user_data.get("last_bonus") == str(date.today()):
        await query.answer("❌ כבר קיבלת היום!", show_alert=True)
        return
    user_data["last_bonus"] = str(date.today())
    users[uid] = user_data
    save_json(USERS_FILE, users)
    coins = load_json(COINS_FILE)
    coins[uid] = coins.get(uid, 0) + 1
    save_json(COINS_FILE, coins)
    await query.answer("🎁 קיבלת 1 מטבע!", show_alert=True)
    await back_main(update, context)

async def vip_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    user_vip = get_user_vip(uid)
    purchases = load_json(USERS_FILE).get(uid, {}).get("purchases", 0)
    text = f"👑 *מערכת דרגות VIP*\n\nהדרגה שלך: {user_vip['icon']} *{user_vip['name']}*\nרכישות: *{purchases}*\n\n"
    for level in VIP_LEVELS:
        text += f"{level['icon']} *{level['name']}*: {level['min_purchases']}+ רכישות | {int(level['discount']*100)}% הנחה\n"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]))

async def payment_method_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await maintenance_gate(update): return
    balance = load_json(COINS_FILE).get(str(query.from_user.id), 0)
    await query.edit_message_text("💰 *בחר אמצעי תשלום:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 פייפאל", callback_data="paypal_menu")],
        [InlineKeyboardButton(f"🪙 מטבעות ({balance})", callback_data="coins_menu")],
        [InlineKeyboardButton("🔙 חזרה", callback_data="back_main")],
    ]))

async def paypal_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    vip = get_user_vip(str(query.from_user.id))
    btns = []
    for i, p in enumerate(PACKAGES):
        price = round(p["price"] * (1 - vip["discount"]), 2)
        btns.append([InlineKeyboardButton(f"₪{price} – {p['videos']} סרטונים", callback_data=f"pp_{i}")])
    btns.append([InlineKeyboardButton("🔙 חזרה", callback_data="payment_method")])
    await query.edit_message_text("💳 *פייפאל* - בחר חבילה:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def paypal_package_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    pkg = PACKAGES[idx]
    vip = get_user_vip(str(query.from_user.id))
    price = round(pkg["price"] * (1 - vip["discount"]), 2)
    text = f"✅ חבילת *{pkg['videos']} סרטונים*\n💰 מחיר: *₪{price}*\n\n1️⃣ שלם בקישור.\n2️⃣ שלח צילום מסך לתמיכה."
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 מעבר לתשלום", url=f"{PAYPAL_LINK}/{price}")],
        [InlineKeyboardButton("💬 שלח אישור", callback_data="support")],
        [InlineKeyboardButton("🔙 חזרה", callback_data="paypal_menu")],
    ]))

async def coins_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    vip = get_user_vip(uid)
    balance = load_json(COINS_FILE).get(uid, 0)
    btns = []
    for i, p in enumerate(PACKAGES):
        cost = int(p["coins"] * (1 - vip["discount"]))
        btns.append([InlineKeyboardButton(f"🪙{cost} – {p['videos']} סרטונים", callback_data=f"coin_{i}")])
    btns.append([InlineKeyboardButton("🔙 חזרה", callback_data="payment_method")])
    await query.edit_message_text(f"🪙 *מטבעות* - יתרה: *{balance}*\nבחר חבילה:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def coin_package_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    idx = int(query.data.split("_")[1])
    pkg = PACKAGES[idx]
    cost = int(pkg["coins"] * (1 - get_user_vip(uid)["discount"]))
    coins = load_json(COINS_FILE)
    if coins.get(uid, 0) < cost:
        await query.answer("❌ אין מספיק מטבעות!", show_alert=True)
        return
    coins[uid] -= cost
    save_json(COINS_FILE, coins)
    sent = await send_videos_to_user(context, query.from_user.id, pkg["videos"])
    if sent > 0:
        record_order(query.from_user.id, 0, sent, "coins")
        await query.message.reply_text(f"✅ נשלחו {sent} סרטונים!")
    else:
        coins[uid] += cost # refund
        save_json(COINS_FILE, coins)
        await query.message.reply_text("❌ המאגר ריק.")
    await back_main(update, context)

async def referrals_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    count = load_json(REFERRALS_FILE).get(uid, {}).get("count", 0)
    bot_user = (await context.bot.get_me()).username
    await query.edit_message_text(f"👥 *הפניות*\n\nחברים שהצטרפו: *{count}*\nקישור שלך:\n`https://t.me/{bot_user}?start=ref_{uid}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]))

async def wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    balance = load_json(COINS_FILE).get(str(query.from_user.id), 0)
    await query.edit_message_text(f"💰 *ארנק*\n\nיתרה: *{balance} מטבעות*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]))

async def coupon_redeem_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🎟 *מימוש קופון*\nהזן קוד:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]))
    return COUPON_REDEEM

async def coupon_redeem_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    code = update.message.text.strip().upper()
    coupons = load_json(COUPONS_FILE)
    c = coupons.get(code)
    if not c or uid in c.get("used_by", []):
        await update.message.reply_text("❌ קוד לא תקין או כבר נוצל.")
    else:
        c.setdefault("used_by", []).append(uid)
        save_json(COUPONS_FILE, coupons)
        coins = load_json(COINS_FILE)
        coins[uid] = coins.get(uid, 0) + c["coins"]
        save_json(COINS_FILE, coins)
        await update.message.reply_text(f"✅ קיבלת {c['coins']} מטבעות!")
    return ConversationHandler.END

async def support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("💬 *תמיכה*\nכתוב הודעה:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]))
    return SUPPORT_WAITING_MSG

async def support_receive_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await context.bot.send_message(ADMIN_ID, f"📩 *תמיכה*\n👤 {user.first_name} (`{user.id}`)\n💬 {update.message.text}", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"↩️ תשובה", callback_data=f"support_reply_{user.id}")]]))
    await update.message.reply_text("✅ נשלח!")
    return ConversationHandler.END

async def admin_support_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    target = query.data.replace("support_reply_", "")
    context.user_data["support_reply_target"] = target
    await query.message.reply_text(f"✏️ תשובה ל-`{target}`:")
    return SUPPORT_REPLY_MSG

async def admin_support_reply_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = context.user_data.get("support_reply_target")
    try:
        await context.bot.send_message(int(target), f"📬 *תשובה מהמנהל:*\n\n{update.message.text}", parse_mode="Markdown")
        await update.message.reply_text("✅ נשלח!")
    except: await update.message.reply_text("❌ נכשל.")
    return ConversationHandler.END

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("🛠 *פאנל אדמין*", parse_mode="Markdown", reply_markup=get_admin_inline_keyboard())

async def back_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🛠 *פאנל אדמין*", parse_mode="Markdown", reply_markup=get_admin_inline_keyboard())

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    users = load_json(USERS_FILE)
    videos = load_json(VIDEOS_FILE)
    orders = load_json(ORDERS_FILE)
    rev = sum(o.get("amount", 0) for o in orders)
    await query.edit_message_text(f"📊 *סטטיסטיקה*\n👤 משתמשים: {len(users)}\n🎬 סרטונים: {len(videos)}\n💰 הכנסות: ₪{rev}", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

async def admin_orders_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("admin_orders_page_")[1])
    orders = list(reversed(load_json(ORDERS_FILE)))
    chunk = orders[page*10:(page+1)*10]
    text = f"🧾 *הזמנות (עמוד {page+1}):*\n" + "\n".join([f"`{o['user_id']}` | ₪{o['amount']} | 🎬 {o['videos_count']}" for o in chunk])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"admin_orders_page_{page-1}"))
    if len(orders) > (page+1)*10: nav.append(InlineKeyboardButton("➡️", callback_data=f"admin_orders_page_{page+1}"))
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([nav, [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

async def users_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("users_page_")[1])
    u_ids = list(load_json(USERS_FILE).keys())
    if not u_ids: return
    uid = u_ids[idx]
    u = load_json(USERS_FILE)[uid]
    text = f"👤 *משתמש {idx+1}/{len(u_ids)}*\n📛 {u['first_name']}\n🆔 `{uid}`\n🛒 רכישות: {u['purchases']}"
    nav = []
    if idx > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"users_page_{idx-1}"))
    if idx < len(u_ids)-1: nav.append(InlineKeyboardButton("➡️", callback_data=f"users_page_{idx+1}"))
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([nav, [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

async def admin_check_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔍 שלח ID לבדיקה:")
    return ADMIN_CHECK_USER

async def admin_check_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = load_json(USERS_FILE).get(update.message.text.strip())
    if not u: await update.message.reply_text("❌ לא נמצא.")
    else: await update.message.reply_text(f"👤 {u['first_name']}\n🛒 רכישות: {u['purchases']}", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_send_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📩 כמה סרטונים?")
    return ADMIN_SEND_MSG

async def admin_send_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["send_v_count"] = int(update.message.text)
    await update.message.reply_text("שלח ID:")
    return ADMIN_SEND_ID

async def admin_send_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.text.strip()
    sent = await send_videos_to_user(context, int(uid), context.user_data["send_v_count"])
    await update.message.reply_text(f"✅ נשלחו {sent}!", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_approve_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✅ כמה סרטונים לאשר?")
    return ADMIN_APPROVE_COUNT

async def admin_approve_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["app_v_count"] = int(update.message.text)
    await update.message.reply_text("שלח ID:")
    return ADMIN_APPROVE_ID

async def admin_approve_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.text.strip()
    sent = await send_videos_to_user(context, int(uid), context.user_data["app_v_count"])
    if sent > 0:
        record_order(int(uid), 0, sent, "manual")
        await update.message.reply_text(f"✅ אושר ונשלח!", reply_markup=get_admin_inline_keyboard())
    else: await update.message.reply_text("❌ נכשל.")
    return ConversationHandler.END

async def admin_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await admin_gallery_page(update, context, 0)

async def admin_gallery_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=None):
    query = update.callback_query
    if page is None: page = int(query.data.split("vid_page_")[1])
    videos = load_json(VIDEOS_FILE)
    # Ensure sorted by duration
    videos.sort(key=lambda x: x.get("duration", 0))
    if not videos:
        await query.edit_message_text("ריק.", reply_markup=get_admin_inline_keyboard())
        return
    v = videos[page]
    text = f"🎬 *סרטון {page+1}/{len(videos)}*\n⏱ אורך: {v.get('duration', 0)} שניות"
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"vid_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}", callback_data="noop"))
    if page < len(videos)-1: nav.append(InlineKeyboardButton("➡️", callback_data=f"vid_page_{page+1}"))
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([nav, [InlineKeyboardButton("🗑 מחק", callback_data=f"vid_del_{page}")], [InlineKeyboardButton("📤 שלח הכל", callback_data="vid_send_all")], [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))
    await context.bot.send_video(ADMIN_ID, v["file_id"])

async def admin_gallery_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("vid_del_")[1])
    videos = load_json(VIDEOS_FILE)
    if 0 <= idx < len(videos):
        videos.pop(idx)
        save_json(VIDEOS_FILE, videos)
        await query.answer("נמחק!")
        await admin_gallery(update, context)

async def admin_gallery_send_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    videos = load_json(VIDEOS_FILE)
    videos.sort(key=lambda x: x.get("duration", 0))
    for i, v in enumerate(videos):
        await context.bot.send_video(ADMIN_ID, v["file_id"], caption=f"🎬 סרטון #{i+1}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 מחק", callback_data=f"vid_del_{i}")]]))
        await asyncio.sleep(0.1)
    await context.bot.send_message(ADMIN_ID, "✅ הסתיים.", reply_markup=get_admin_inline_keyboard())

async def admin_video_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔢 שלח מספר סרטון (1 עד X):")
    return ADMIN_VIDEO_SEARCH

async def admin_video_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    videos = load_json(VIDEOS_FILE)
    videos.sort(key=lambda x: x.get("duration", 0))
    try:
        idx = int(update.message.text) - 1
        v = videos[idx]
        await context.bot.send_video(ADMIN_ID, v["file_id"], caption=f"🎬 סרטון #{idx+1}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 מחק", callback_data=f"vid_del_{idx}")]]))
    except: await update.message.reply_text("❌ לא תקין.")
    return ConversationHandler.END

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📢 שלח הודעה (טקסט):")
    return ADMIN_BROADCAST

async def admin_broadcast_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["br_msg"] = update.message.text
    await update.message.reply_text("🖼 שלח מדיה או `skip`:")
    return ADMIN_BROADCAST_MEDIA

async def admin_broadcast_get_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo: context.user_data["br_media"] = ("photo", update.message.photo[-1].file_id)
    elif update.message.video: context.user_data["br_media"] = ("video", update.message.video.file_id)
    else: context.user_data["br_media"] = None
    await update.message.reply_text("🔗 שלח `טקסט|קישור` או `skip`:")
    return ADMIN_BROADCAST_BTN

async def admin_broadcast_get_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text
    markup = None
    if raw.lower() != "skip" and "|" in raw:
        t, u = raw.split("|", 1)
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(t.strip(), url=u.strip())]])
    context.user_data["br_markup"] = markup
    await update.message.reply_text("⏰ השהייה בדקות (0 למיידי):")
    return ADMIN_BROADCAST_DELAY

async def admin_broadcast_get_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    delay = int(update.message.text)
    if delay > 0: await asyncio.sleep(delay*60)
    msg = context.user_data["br_msg"]
    media = context.user_data["br_media"]
    markup = context.user_data["br_markup"]
    users = load_json(USERS_FILE)
    sent = 0
    for uid in users:
        try:
            if media:
                if media[0] == "photo": await context.bot.send_photo(int(uid), media[1], caption=msg, reply_markup=markup)
                else: await context.bot.send_video(int(uid), media[1], caption=msg, reply_markup=markup)
            else: await context.bot.send_message(int(uid), msg, reply_markup=markup)
            sent += 1
            await asyncio.sleep(0.05)
        except: pass
    await update.message.reply_text(f"✅ נשלח ל-{sent}!", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_coins_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🪙 שלח ID:")
    return ADMIN_COINS_ID

async def admin_coins_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["c_uid"] = update.message.text.strip()
    await update.message.reply_text("כמה להוסיף/להוריד?")
    return ADMIN_COINS_AMOUNT

async def admin_coins_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["c_uid"]
    coins = load_json(COINS_FILE)
    coins[uid] = max(0, coins.get(uid, 0) + int(update.message.text))
    save_json(COINS_FILE, coins)
    await update.message.reply_text(f"✅ יתרה חדשה: {coins[uid]}", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_welcome_set_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("👋 שלח את טקסט הברוכים הבאים (השתמש ב-{name} לשם המשתמש):")
    return ADMIN_SET_WELCOME_TEXT

async def admin_welcome_set_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_welcome_text"] = update.message.text
    await update.message.reply_text("🖼 שלח סרטון/תמונה להודעה, או `skip` ללא מדיה:")
    return ADMIN_SET_WELCOME_MEDIA

async def admin_welcome_set_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    media = None
    if update.message.photo: media = ("photo", update.message.photo[-1].file_id)
    elif update.message.video: media = ("video", update.message.video.file_id)
    
    settings = load_settings()
    settings["welcome_text"] = context.user_data["new_welcome_text"]
    settings["welcome_media"] = media
    save_settings(settings)
    await update.message.reply_text("✅ הודעת ברוכים הבאים עודכנה!", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_coupons_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coupons = load_json(COUPONS_FILE)
    text = "🎟 *קופונים:*\n" + "\n".join([f"`{k}` - {v['coins']} מטבעות" for k,v in coupons.items()])
    btns = [[InlineKeyboardButton("➕ חדש", callback_data="admin_coupon_new")]]
    for k in list(coupons.keys())[:5]: btns.append([InlineKeyboardButton(f"🗑 {k}", callback_data=f"coupon_del_{k}")])
    btns.append([InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def admin_coupon_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    code = query.data.replace("coupon_del_", "")
    coupons = load_json(COUPONS_FILE)
    if code in coupons: del coupons[code]; save_json(COUPONS_FILE, coupons)
    await admin_coupons_menu(update, context)

async def admin_coupon_new_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🎟 קוד קופון:")
    return ADMIN_COUPON_CODE

async def admin_coupon_get_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["c_code"] = update.message.text.strip().upper()
    await update.message.reply_text("כמה מטבעות?")
    return ADMIN_COUPON_COINS

async def admin_coupon_get_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = context.user_data["c_code"]
    coupons = load_json(COUPONS_FILE)
    coupons[code] = {"coins": int(update.message.text), "used_by": []}
    save_json(COUPONS_FILE, coupons)
    await update.message.reply_text(f"✅ קופון `{code}` נוצר!", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in DATA_DIR.glob("*.json"): zf.write(f, f.name)
    buf.seek(0)
    await context.bot.send_document(ADMIN_ID, buf, filename="backup.zip")
    await query.answer("✅ נשלח!")

async def admin_restore_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📥 שלח קובץ ZIP:")
    return ADMIN_RESTORE

async def admin_restore_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    f = await context.bot.get_file(doc.file_id)
    buf = io.BytesIO()
    await f.download_to_memory(buf)
    buf.seek(0)
    with zipfile.ZipFile(buf, "r") as zf:
        for n in zf.namelist():
            if n.endswith(".json"): save_json(DATA_DIR / n, json.loads(zf.read(n)))
    await update.message.reply_text("✅ שוחזר!", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_global_reset_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔄 בטוח? הקלד 'מאשר':")
    return ADMIN_GLOBAL_RESET_CONFIRM

async def admin_global_reset_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "מאשר":
        for f in DATA_DIR.glob("*.json"):
            if f.name != "settings.json": save_json(f, [] if "videos" in f.name or "orders" in f.name else {})
        await update.message.reply_text("✅ אופס!", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🧹 מחק הכל? ", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("כן", callback_data="admin_delete_confirm"), InlineKeyboardButton("לא", callback_data="back_admin")]]))

async def admin_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_json(VIDEOS_FILE, [])
    await update.callback_query.answer("✅ נמחק!")
    await back_admin(update, context)

async def admin_maintenance_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = load_settings()
    s["maintenance"] = not s["maintenance"]
    save_settings(s)
    await update.callback_query.answer(f"תחזוקה: {'פעיל' if s['maintenance'] else 'כבוי'}")
    await back_admin(update, context)

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    v = update.message.video
    context.user_data["v_fid"] = v.file_id
    context.user_data["v_dur"] = v.duration
    await update.message.reply_text("🎬 סרטון התקבל! שלח תמונה/סרטון דוגמה או `skip`:")
    return ADMIN_VIDEO_PREVIEW

async def admin_video_preview_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    preview = None
    if update.message.photo: preview = ("photo", update.message.photo[-1].file_id)
    elif update.message.video: preview = ("video", update.message.video.file_id)
    videos = load_json(VIDEOS_FILE)
    videos.append({"file_id": context.user_data["v_fid"], "duration": context.user_data["v_dur"], "preview": preview})
    save_json(VIDEOS_FILE, videos)
    await update.message.reply_text("✅ נשמר!")
    return ConversationHandler.END

async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.callback_query.answer()
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE): return ConversationHandler.END

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args): pass

def _start_health_server():
    port = int(os.environ.get("PORT", "10000"))
    try: HTTPServer(("0.0.0.0", port), _HealthHandler).serve_forever()
    except: pass

def main():
    ensure_data_files()
    threading.Thread(target=_start_health_server, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🛠 פאנל אדמין$"), admin_panel))
    
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_check_start, pattern="^admin_check$")], states={ADMIN_CHECK_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_check_user)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_send_start, pattern="^admin_send$")], states={ADMIN_SEND_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_msg)], ADMIN_SEND_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_id)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_approve_start, pattern="^admin_approve$")], states={ADMIN_APPROVE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_approve_count)], ADMIN_APPROVE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_approve_id)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast$")], states={ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_get_msg)], ADMIN_BROADCAST_MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO | filters.Regex("^skip$"), admin_broadcast_get_media)], ADMIN_BROADCAST_BTN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_get_btn)], ADMIN_BROADCAST_DELAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_get_delay)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_coins_start, pattern="^admin_coins$")], states={ADMIN_COINS_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coins_id)], ADMIN_COINS_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coins_amount)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_coupon_new_start, pattern="^admin_coupon_new$")], states={ADMIN_COUPON_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coupon_get_code)], ADMIN_COUPON_COINS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coupon_get_coins)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_welcome_set_start, pattern="^admin_welcome_set$")], states={ADMIN_SET_WELCOME_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_welcome_set_text)], ADMIN_SET_WELCOME_MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO | filters.Regex("^skip$"), admin_welcome_set_media)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_restore_start, pattern="^admin_restore$")], states={ADMIN_RESTORE: [MessageHandler(filters.Document.ALL, admin_restore_receive)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_global_reset_start, pattern="^admin_global_reset$")], states={ADMIN_GLOBAL_RESET_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_global_reset_execute)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_video_search_start, pattern="^admin_video_search$")], states={ADMIN_VIDEO_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_video_search_input)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(support_menu, pattern="^support$")], states={SUPPORT_WAITING_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive_msg)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(coupon_redeem_start, pattern="^coupon_redeem$")], states={COUPON_REDEEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, coupon_redeem_input)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_support_reply_start, pattern=r"^support_reply_\d+$")], states={SUPPORT_REPLY_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_support_reply_send)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.VIDEO, handle_video)], states={ADMIN_VIDEO_PREVIEW: [MessageHandler(filters.PHOTO | filters.VIDEO | filters.Regex("^skip$"), admin_video_preview_receive)]}, fallbacks=[]))

    cbs = [
        ("^noop$", noop_callback), ("^payment_method$", payment_method_menu), ("^paypal_menu$", paypal_menu), (r"^pp_\d+$", paypal_package_selected),
        ("^coins_menu$", coins_menu), (r"^coin_\d+$", coin_package_buy), ("^referrals$", referrals_menu), ("^wallet$", wallet_menu),
        ("^daily_bonus$", daily_bonus), ("^vip_info$", vip_info), ("^back_main$", back_main), ("^admin_stats$", admin_stats),
        (r"^admin_orders_page_\d+$", admin_orders_page), (r"^users_page_\d+$", users_page), ("^admin_gallery$", admin_gallery),
        (r"^vid_page_\d+$", admin_gallery_page), (r"^vid_del_\d+$", admin_gallery_delete), ("^vid_send_all$", admin_gallery_send_all),
        ("^admin_coupons$", admin_coupons_menu), (r"^coupon_del_", admin_coupon_delete), ("^admin_backup$", admin_backup),
        ("^admin_delete$", admin_delete_start), ("^admin_delete_confirm$", admin_delete_confirm), ("^admin_maintenance$", admin_maintenance_toggle), ("^back_admin$", back_admin),
    ]
    for p, h in cbs: app.add_handler(CallbackQueryHandler(h, pattern=p))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(_run_app(app))
    loop.run_forever()

async def _run_app(app):
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    while True: await asyncio.sleep(3600)

if __name__ == "__main__":
    main()
