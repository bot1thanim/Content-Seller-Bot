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
# VIP Levels
VIP_LEVELS = [
    {"name": "ברונזה", "min_purchases": 0,   "discount": 0.0,  "icon": "🥉"},
    {"name": "כסף",   "min_purchases": 20,  "discount": 0.15, "icon": "🥈"},
    {"name": "זהב",   "min_purchases": 50,  "discount": 0.30, "icon": "🥇"},
    {"name": "יהלום", "min_purchases": 100, "discount": 0.50, "icon": "💎"},
]
# ── Conversation states ────────────────────────────────────────────────────────
(
    ADMIN_SEND_MSG,           # 0
    ADMIN_SEND_ID,            # 1
    ADMIN_APPROVE_COUNT,      # 2
    ADMIN_APPROVE_ID,         # 3
    ADMIN_CHECK_USER,         # 4
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
    ADMIN_RESTORE,            # 18
    ADMIN_GLOBAL_RESET_CONFIRM, # 19
    ADMIN_VIDEO_SEARCH,       # 20
    ADMIN_VIDEO_CAT,          # 21
    ADMIN_VIDEO_PREVIEW,      # 22
    ADMIN_BROADCAST_MEDIA,    # 23
    ADMIN_VIP_ID,             # 24
    ADMIN_VIP_LEVEL,          # 25
) = range(23)
# ─── Data helpers ─────────────────────────────────────────────────────────────
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
                coins       = load_json(COINS_FILE)
                coins[ref_key] = coins.get(ref_key, 0) + 1
                save_json(COINS_FILE, coins)
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
        [
            InlineKeyboardButton(f"{vip['icon']} רמת {vip['name']}", callback_data="vip_info"),
        ],
        [
            InlineKeyboardButton("💳 תשלום",       callback_data="payment_method"),
        ],
        [
            InlineKeyboardButton("👥 הפניות שלי",   callback_data="referrals"),
            InlineKeyboardButton("🎟 מימוש קופון",  callback_data="coupon_redeem"),
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
        ],
        [
            InlineKeyboardButton("💎 ניהול דרגות",    callback_data="admin_vip"),
        ],
        [
            InlineKeyboardButton("🎟 ניהול קופונים",  callback_data="admin_coupons"),
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
    bonus_amount = 1
    user_data["last_bonus_ts"] = now_ts
    user_data["last_bonus"] = str(date.today()) # Keep for legacy compatibility
    users[uid] = user_data
    save_json(USERS_FILE, users)
    coins = load_json(COINS_FILE)
    old_balance = coins.get(uid, 0)
    new_balance = old_balance + bonus_amount
    coins[uid] = new_balance
    save_json(COINS_FILE, coins)
    await query.answer(
        f"🎁 קיבלת {bonus_amount} מטבע מתנה!\n\n"
        f"💰 יתרה קודמת: {old_balance}\n"
        f"🆕 יתרה חדשה: {new_balance}",
        show_alert=True
    )
    await back_main(update, context)
# ─── VIP Info ────────────────────────────────────────────────────────────────
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
    text += "\n_ההנחה חלה באופן אוטומטי על תשלום בפייפאל ובמטבעות._"
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
    coins   = load_json(COINS_FILE)
    balance = coins.get(str(query.from_user.id), 0)
    await query.edit_message_text(
        "💰 *בחר אמצעי תשלום:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 תשלום בפייפאל",                        callback_data="paypal_menu")],
            [InlineKeyboardButton("🔙 חזרה",                                 callback_data="back_main")],
        ]),
    )
async def paypal_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await maintenance_gate(update):
        return
    uid = str(query.from_user.id)
    vip = get_user_vip(uid)
    btns = []
    for i, p in enumerate(PACKAGES):
        discounted = round(p["price"] * (1 - vip["discount"]), 2)
        label = f"₪{discounted} – {p['videos']} סרטונים"
        if vip["discount"] > 0:
            label += f" ({int(vip['discount']*100)}% הנחה)"
        btns.append([InlineKeyboardButton(label, callback_data=f"pp_{i}")])
    btns.append([InlineKeyboardButton("🔙 חזרה", callback_data="payment_method")])
    await query.edit_message_text(
        "💳 *תשלום בפייפאל*\n\nבחר חבילה לרכישה:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns),
    )
async def paypal_package_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    vip = get_user_vip(uid)
    idx = int(query.data.split("_")[1])
    pkg = PACKAGES[idx]
    price = round(pkg["price"] * (1 - vip["discount"]), 2)
    # Generate PayPal link with price
    final_link = f"{PAYPAL_LINK}/{price}"
    text = (
        f"✅ בחרת חבילה של *{pkg['videos']} סרטונים*\n"
        f"💰 מחיר לאחר הנחה ({int(vip['discount']*100)}%): *₪{price}*\n\n"
        f"1️⃣ לחץ על הכפתור למעבר לתשלום.\n"
        f"2️⃣ לאחר התשלום, שלח צילום מסך של האישור למנהל דרך כפתור ה'תמיכה'.\n"
        f"3️⃣ המנהל יאשר את הרכישה והסרטונים יישלחו אליך."
    )
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 מעבר לתשלום בפייפאל", url=final_link)],
            [InlineKeyboardButton("💬 שלח אישור למנהל",      callback_data="support")],
            [InlineKeyboardButton("🔙 חזרה",                 callback_data="paypal_menu")],
        ]),
    )
async def coins_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await maintenance_gate(update):
        return
    uid = str(query.from_user.id)
    vip = get_user_vip(uid)
    coins_d = load_json(COINS_FILE)
    balance = coins_d.get(uid, 0)
    btns = []
    for i, p in enumerate(PACKAGES):
        discounted = int(p["coins"] * (1 - vip["discount"]))
        label = f"🪙{discounted} – {p['videos']} סרטונים"
        if vip["discount"] > 0:
            label += f" ({int(vip['discount']*100)}% הנחה)"
        btns.append([InlineKeyboardButton(label, callback_data=f"coin_{i}")])
    btns.append([InlineKeyboardButton("🔙 חזרה", callback_data="payment_method")])
    await query.edit_message_text(
        f"🪙 *רכישה באמצעות מטבעות*\n\nהיתרה שלך: *{balance}*\nבחר חבילה:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns),
    )
async def coin_package_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid   = str(query.from_user.id)
    vip   = get_user_vip(uid)
    idx   = int(query.data.split("_")[1])
    pkg   = PACKAGES[idx]
    cost  = int(pkg["coins"] * (1 - vip["discount"]))
    coins = load_json(COINS_FILE)
    bal   = coins.get(uid, 0)
    if bal < cost:
        await query.answer(f"❌ אין לך מספיק מטבעות. חסרים {cost - bal}.", show_alert=True)
        return
    coins[uid] = bal - cost
    save_json(COINS_FILE, coins)
    sent = await send_videos_to_user(context, query.from_user.id, pkg["videos"])
    if sent > 0:
        record_order(query.from_user.id, 0, sent, "coins")
        await query.message.reply_text(f"✅ רכישה הושלמה! {sent} סרטונים נשלחו אליך. תהנה!")
        await alert_admin(context, f"🪙 *רכישה במטבעות*\n👤 {query.from_user.first_name} (`{uid}`)\n🎬 סרטונים: {sent}\n💰 עלות: {cost}")
    else:
        coins[uid] = bal # Refund
        save_json(COINS_FILE, coins)
        await query.message.reply_text("❌ מצטערים, אין מספיק סרטונים במאגר כרגע. המטבעות הוחזרו.")
    await back_main(update, context)
# ─── Referrals ────────────────────────────────────────────────────────────────
async def referrals_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await maintenance_gate(update):
        return
    uid       = str(query.from_user.id)
    referrals = load_json(REFERRALS_FILE)
    data      = referrals.get(uid, {"count": 0, "referred_ids": []})
    bot_username = (await context.bot.get_me()).username
    ref_link     = f"https://t.me/{bot_username}?start=ref_{uid}"
    await query.edit_message_text(
        f"👥 *מערכת הפניות*\n\n"
        f"על כל חבר שיצטרף דרכך תקבל *1 מטבע* 🪙\n\n"
        f"📈 חברים שהצטרפו: *{data['count']}*\n\n"
        f"🔗 קישור ההפניה שלך:\n`{ref_link}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]),
    )
