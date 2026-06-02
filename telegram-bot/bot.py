import io
import os
import json
import random
import asyncio
import logging
import threading
import warnings
import zipfile
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
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

# ── Conversation states ────────────────────────────────────────────────────────
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
) = range(21)


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
    if not isinstance(s, dict):
        s = {}
    s.setdefault("referral_multiplier", 1.0)
    s.setdefault("maintenance", False)
    return s


def save_settings(s: dict):
    save_json(SETTINGS_FILE, s)


def is_maintenance() -> bool:
    return load_settings().get("maintenance", False)


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
    videos = load_json(VIDEOS_FILE)
    if len(videos) < count:
        return -1
    selected = random.sample(videos, count)
    sent = 0
    for file_id in selected:
        try:
            await context.bot.send_video(chat_id=user_id, video=file_id)
            sent += 1
        except Exception:
            pass
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
    return InlineKeyboardMarkup([
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
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🛠 פאנל אדמין")]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


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
        [InlineKeyboardButton(maint_label,             callback_data="admin_maintenance")],
    ])


# ─── Maintenance gate ─────────────────────────────────────────────────────────

async def maintenance_gate(update: Update) -> bool:
    """Returns True if blocked (maintenance mode and not admin)."""
    if update.effective_user and update.effective_user.id == ADMIN_ID:
        return False
    if not is_maintenance():
        return False
    msg = "🔧 *הבוט בשיפוצים*\n\nנחזור בקרוב! 🙏"
    if update.callback_query:
        await update.callback_query.answer("הבוט בשיפוצים, חזרו בקרוב!", show_alert=True)
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

    await update.message.reply_text(
        f"שלום {user.first_name} 👋\nברוכים הבאים לבוט התכנים האסורים 🤫\nבחר אפשרות:",
        reply_markup=get_main_keyboard(user.id),
    )


# ─── back_main ────────────────────────────────────────────────────────────────

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user  = query.from_user
    await query.edit_message_text(
        f"שלום {user.first_name} 👋\nברוכים הבאים לבוט התכנים האסורים 🤫\nבחר אפשרות:",
        reply_markup=get_main_keyboard(user.id),
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
            [InlineKeyboardButton(f"🪙 שלם במטבעות (יתרה: {balance})",      callback_data="coins_menu")],
            [InlineKeyboardButton("🔙 חזרה",                                 callback_data="back_main")],
        ]),
    )


async def paypal_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await maintenance_gate(update):
        return
    btns = [[InlineKeyboardButton(p["label_paypal"], callback_data=f"pp_{i}")] for i, p in enumerate(PACKAGES)]
    btns.append([InlineKeyboardButton("🔙 חזרה", callback_data="payment_method")])
    await query.edit_message_text(
        "💳 *תשלום בפייפאל – בחר חבילה:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns),
    )


async def paypal_package_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await maintenance_gate(update):
        return
    idx     = int(query.data.split("pp_")[1])
    pkg     = PACKAGES[idx]
    user_id = query.from_user.id
    url     = f"{PAYPAL_LINK}/{pkg['price']}ILS"
    await query.edit_message_text(
        f"📦 *חבילה נבחרת:* {pkg['label_paypal']}\n\n"
        f"🔗 [לחץ כאן לתשלום בפייפאל]({url})\n\n"
        f"לאחר השלמת התשלום שלח את ה-ID שלך `{user_id}` + צילום מסך לתמיכה ✅",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לחבילות", callback_data="paypal_menu")]]),
        disable_web_page_preview=False,
    )
    await alert_admin(context, f"🛒 *בקשת תשלום פייפאל*\n👤 ID: `{user_id}`\n📦 חבילה: {pkg['label_paypal']}")


async def coins_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await maintenance_gate(update):
        return
    uid     = str(query.from_user.id)
    coins   = load_json(COINS_FILE)
    balance = coins.get(uid, 0)
    btns    = []
    for i, pkg in enumerate(PACKAGES):
        icon = "✅ " if balance >= pkg["coins"] else "🔒 "
        btns.append([InlineKeyboardButton(icon + pkg["label_coins"], callback_data=f"coin_{i}")])
    btns.append([InlineKeyboardButton("🔙 חזרה", callback_data="payment_method")])
    await query.edit_message_text(
        f"🪙 *תשלום במטבעות*\n\n💰 יתרתך: *{balance} מטבעות*\n\n✅ = מספיק | 🔒 = אין מספיק\nבחר חבילה:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns),
    )


async def coin_package_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await maintenance_gate(update):
        return
    idx        = int(query.data.split("coin_")[1])
    pkg        = PACKAGES[idx]
    user_id    = query.from_user.id
    uid        = str(user_id)
    coins      = load_json(COINS_FILE)
    balance    = coins.get(uid, 0)
    cost       = pkg["coins"]
    vid_count  = pkg["videos"]

    if balance < cost:
        await query.edit_message_text(
            f"❌ *אין מספיק מטבעות*\n\n🪙 יתרתך: *{balance}*\n🏷 נדרש: *{cost}*\nחסרים: *{cost - balance}*\n\nצבור מטבעות על ידי הפניות, או שלם בפייפאל 💳",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 שלם בפייפאל", callback_data="paypal_menu")],
                [InlineKeyboardButton("🔙 חזרה",        callback_data="coins_menu")],
            ]),
        )
        return

    videos = load_json(VIDEOS_FILE)
    if len(videos) < vid_count:
        await query.edit_message_text(
            f"⚠️ *אין מספיק סרטונים כרגע*\n\nיש {len(videos)}, נדרשים {vid_count}.\nנסה חבילה קטנה יותר.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="coins_menu")]]),
        )
        return

    coins[uid] = balance - cost
    save_json(COINS_FILE, coins)

    await query.edit_message_text(
        f"⏳ *מעבד...*\n🪙 נוכו {cost} מטבעות\n📤 שולח {vid_count} סרטונים...",
        parse_mode="Markdown",
    )

    sent = await send_videos_to_user(context, user_id, vid_count)
    if sent == -1:
        coins[uid] = balance
        save_json(COINS_FILE, coins)
        await context.bot.send_message(chat_id=user_id, text="❌ שגיאה: אין מספיק סרטונים. המטבעות הוחזרו.")
        return

    record_order(user_id, 0, sent, "coins")
    new_bal = load_json(COINS_FILE).get(uid, 0)
    await context.bot.send_message(
        chat_id=user_id,
        text=f"✅ *רכישה הושלמה!*\n\n🎬 קיבלת *{sent} סרטונים*\n🪙 יתרה: *{new_bal}*\n\nתהנה! 🔥",
        parse_mode="Markdown",
    )
    await alert_admin(context,
        f"💰 *רכישה במטבעות*\n👤 ID: `{user_id}`\n📦 {pkg['label_coins']}\n🪙 שולם: {cost} | יתרה: {new_bal}")


# ─── Referrals & Wallet ───────────────────────────────────────────────────────

async def referrals_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    if await maintenance_gate(update):
        return
    user     = query.from_user
    bot_info = await context.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user.id}"
    refs     = load_json(REFERRALS_FILE)
    coins    = load_json(COINS_FILE)
    ref_data = refs.get(str(user.id), {"count": 0})
    await query.edit_message_text(
        f"👥 *הפניות שלי*\n\n🔗 הקישור שלך:\n`{ref_link}`\n\n"
        f"👤 אנשים שנרשמו: *{ref_data.get('count', 0)}*\n"
        f"🪙 מטבעות שצברת: *{coins.get(str(user.id), 0)}*\n\n"
        f"שתף את הקישור – כל הצטרפות = מטבע! 🎉",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]),
    )


async def wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    if await maintenance_gate(update):
        return
    coins   = load_json(COINS_FILE)
    balance = coins.get(str(query.from_user.id), 0)
    await query.edit_message_text(
        f"💰 *הארנק שלי*\n\n🪙 יתרה: *{balance}*\n💵 שווי: *₪{balance / COINS_PER_SHEKEL:.1f}*\n\n_10 מטבעות = ₪1_\n\n💸 צבור מטבעות על ידי הפניית חברים!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🪙 קנה עם מטבעות", callback_data="coins_menu")],
            [InlineKeyboardButton("🎟 מימוש קופון",   callback_data="coupon_redeem")],
            [InlineKeyboardButton("🔙 חזרה",          callback_data="back_main")],
        ]),
    )


# ─── Coupon redeem ────────────────────────────────────────────────────────────

async def coupon_redeem_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await maintenance_gate(update):
        return
    await query.edit_message_text(
        "🎟 *מימוש קופון*\n\nהזן את קוד הקופון שלך:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]),
    )
    return COUPON_REDEEM


async def coupon_redeem_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await maintenance_gate(update):
        return ConversationHandler.END
    user_id = str(update.effective_user.id)
    code    = update.message.text.strip().upper()
    today   = str(date.today())
    coupons = load_json(COUPONS_FILE)
    coupon  = coupons.get(code)
    back_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]])

    if not coupon:
        await update.message.reply_text("❌ קוד קופון לא תקין.", reply_markup=back_btn)
        return ConversationHandler.END
    if coupon.get("expires") and coupon["expires"] < today:
        await update.message.reply_text("⏰ קוד הקופון פג תוקפו.", reply_markup=back_btn)
        return ConversationHandler.END
    used_by  = coupon.get("used_by", [])
    max_uses = coupon.get("max_uses")
    if max_uses is not None and len(used_by) >= max_uses:
        await update.message.reply_text("🚫 קוד הקופון מוצה לגמרי.", reply_markup=back_btn)
        return ConversationHandler.END
    if user_id in used_by:
        await update.message.reply_text("🔄 כבר השתמשת בקופון הזה.", reply_markup=back_btn)
        return ConversationHandler.END

    reward = coupon["coins"]
    used_by.append(user_id)
    coupon["used_by"] = used_by
    coupons[code] = coupon
    save_json(COUPONS_FILE, coupons)

    coins          = load_json(COINS_FILE)
    coins[user_id] = coins.get(user_id, 0) + reward
    save_json(COINS_FILE, coins)

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
    await update.message.reply_text(
        "🛠 *פאנל אדמין*\nבחר פעולה:",
        parse_mode="Markdown",
        reply_markup=get_admin_inline_keyboard(),
    )


async def back_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    await query.edit_message_text(
        "🛠 *פאנל אדמין*\nבחר פעולה:",
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
    coins    = load_json(COINS_FILE)
    coupons  = load_json(COUPONS_FILE)
    today    = str(date.today())
    week_ago = str(date.today() - timedelta(days=7))
    new_today  = sum(1 for u in users.values() if u.get("joined") == today)
    new_week   = sum(1 for u in users.values() if u.get("joined", "") >= week_ago)
    revenue    = sum(o.get("amount", 0) for o in orders if o.get("type") in ("manual", "paypal"))
    coin_ords  = sum(1 for o in orders if o.get("type") == "coins")
    pp_ords    = sum(1 for o in orders if o.get("type") in ("manual", "paypal"))
    total_coins= sum(coins.values())
    coupon_uses= sum(len(c.get("used_by", [])) for c in coupons.values())
    maint      = "✅ פעיל" if load_settings().get("maintenance") else "❌ כבוי"
    await query.edit_message_text(
        f"📊 *סטטיסטיקה מפורטת*\n\n"
        f"👤 סה\"כ משתמשים: *{len(users)}*\n"
        f"🆕 חדשים היום: *{new_today}*\n"
        f"📅 חדשים השבוע: *{new_week}*\n\n"
        f"💰 הכנסות פייפאל: *₪{revenue:.1f}*\n"
        f"🧾 הזמנות פייפאל: *{pp_ords}*\n"
        f"🪙 הזמנות מטבעות: *{coin_ords}*\n\n"
        f"🪙 מטבעות בשוק: *{int(total_coins)}*\n"
        f"🎟 שימושי קופונים: *{coupon_uses}*\n"
        f"🎬 סרטונים: *{len(videos)}*\n"
        f"🔧 מצב תחזוקה: {maint}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")]]),
    )


# ─── Admin: orders (paginated) ────────────────────────────────────────────────

async def admin_orders_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    page   = int(query.data.split("admin_orders_page_")[1])
    orders = load_json(ORDERS_FILE)
    total  = len(orders)
    pages  = max(1, (total + ORDERS_PER_PAGE - 1) // ORDERS_PER_PAGE)
    page   = max(0, min(page, pages - 1))
    start  = page * ORDERS_PER_PAGE
    chunk  = list(reversed(orders))[start:start + ORDERS_PER_PAGE]

    if not orders:
        text = "🧾 *הזמנות*\n\nאין הזמנות עדיין."
    else:
        lines = [f"🧾 *הזמנות (עמוד {page+1}/{pages}):*\n"]
        for o in chunk:
            icon = "🪙" if o.get("type") == "coins" else "💳"
            lines.append(f"{icon} `{o.get('user_id')}` | ₪{o.get('amount')} | 📅 {o.get('date')} | 🎬 {o.get('videos_count')}")
        text = "\n".join(lines)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ קודם", callback_data=f"admin_orders_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("הבא ➡️", callback_data=f"admin_orders_page_{page+1}"))

    buttons = [nav] if nav else []
    buttons.append([InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


# ─── Admin: user browsing (paginated) ────────────────────────────────────────

async def users_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    idx       = int(query.data.split("users_page_")[1])
    users     = load_json(USERS_FILE)
    coins_d   = load_json(COINS_FILE)
    refs      = load_json(REFERRALS_FILE)
    orders    = load_json(ORDERS_FILE)
    uid_list  = list(users.keys())
    total     = len(uid_list)

    if total == 0:
        await query.edit_message_text(
            "👥 אין משתמשים רשומים.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")]]),
        )
        return

    idx = max(0, min(idx, total - 1))
    uid = uid_list[idx]
    u   = users[uid]
    bal = coins_d.get(uid, 0)
    ref_cnt   = refs.get(uid, {}).get("count", 0)
    user_ords = [o for o in orders if str(o.get("user_id")) == uid]
    spent     = sum(o.get("amount", 0) for o in user_ords)

    text = (
        f"👤 *כרטיס משתמש {idx+1}/{total}*\n\n"
        f"📛 שם: {u.get('first_name', 'N/A')}\n"
        f"🆔 ID: `{uid}`\n"
        f"📅 הצטרף: {u.get('joined', 'N/A')}\n"
        f"🪙 מטבעות: {bal}\n"
        f"👥 הפניות: {ref_cnt}\n"
        f"🛒 רכישות: {len(user_ords)}\n"
        f"💰 סה\"כ הוציא: ₪{spent}"
    )
    nav = []
    if idx > 0:
        nav.append(InlineKeyboardButton("⬅️ קודם", callback_data=f"users_page_{idx-1}"))
    nav.append(InlineKeyboardButton(f"{idx+1}/{total}", callback_data="noop"))
    if idx < total - 1:
        nav.append(InlineKeyboardButton("הבא ➡️", callback_data=f"users_page_{idx+1}"))

    buttons = [nav] if nav else []
    buttons.append([InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


# ─── Admin: check user (by ID) ────────────────────────────────────────────────

async def admin_check_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("🔍 *בדיקת משתמש*\n\nשלח את ה-ID:", parse_mode="Markdown")
    return ADMIN_CHECK_USER


async def admin_check_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    target_id = update.message.text.strip()
    users   = load_json(USERS_FILE)
    coins   = load_json(COINS_FILE)
    refs    = load_json(REFERRALS_FILE)
    orders  = load_json(ORDERS_FILE)
    u       = users.get(target_id)
    if not u:
        await update.message.reply_text("❌ משתמש לא נמצא.", reply_markup=get_admin_inline_keyboard())
        return ConversationHandler.END
    bal       = coins.get(target_id, 0)
    ref_cnt   = refs.get(target_id, {}).get("count", 0)
    user_ords = [o for o in orders if str(o.get("user_id")) == target_id]
    coin_n    = sum(1 for o in user_ords if o.get("type") == "coins")
    await update.message.reply_text(
        f"🔍 *דוח משתמש*\n\n👤 {u.get('first_name')}\n🆔 `{target_id}`\n"
        f"📅 הצטרף: {u.get('joined')}\n🪙 מטבעות: {bal}\n👥 הפניות: {ref_cnt}\n"
        f"🛒 רכישות: {len(user_ords)} (פייפאל: {len(user_ords)-coin_n} | מטבעות: {coin_n})\n"
        f"💰 סה\"כ הוציא: ₪{sum(o.get('amount',0) for o in user_ords)}",
        parse_mode="Markdown",
        reply_markup=get_admin_inline_keyboard(),
    )
    return ConversationHandler.END


# ─── Admin: send to user ──────────────────────────────────────────────────────

async def admin_send_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("📩 *שליחה למשתמש*\n\nשלח את ההודעה:", parse_mode="Markdown")
    return ADMIN_SEND_MSG


async def admin_send_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    context.user_data["msg_to_send"] = update.message.text
    await update.message.reply_text("📲 שלח את ה-ID של המשתמש:")
    return ADMIN_SEND_ID


async def admin_send_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        target_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ ID לא תקין.", reply_markup=get_admin_inline_keyboard())
        return ConversationHandler.END
    try:
        await context.bot.send_message(chat_id=target_id, text=context.user_data.get("msg_to_send", ""))
        await update.message.reply_text("✅ נשלח!", reply_markup=get_admin_inline_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {e}", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END


# ─── Admin: approve payment ───────────────────────────────────────────────────

async def admin_approve_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("✅ *אישור תשלום*\n\nכמה סרטונים לשלוח?", parse_mode="Markdown")
    return ADMIN_APPROVE_COUNT


async def admin_approve_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        context.user_data["approve_count"] = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ מספר לא תקין.")
        return ADMIN_APPROVE_COUNT
    await update.message.reply_text(f"📲 ה-ID של המשתמש שיקבל {context.user_data['approve_count']} סרטונים?")
    return ADMIN_APPROVE_ID


async def admin_approve_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        target_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ ID לא תקין.")
        return ConversationHandler.END
    count  = context.user_data.get("approve_count", 0)
    videos = load_json(VIDEOS_FILE)
    if len(videos) < count:
        await update.message.reply_text(f"❌ יש רק {len(videos)} סרטונים.", reply_markup=get_admin_inline_keyboard())
        return ConversationHandler.END
    await update.message.reply_text(f"📤 שולח {count} סרטונים ל-`{target_id}`...", parse_mode="Markdown")
    sent = await send_videos_to_user(context, target_id, count)
    if sent == -1:
        await update.message.reply_text("❌ שגיאה.", reply_markup=get_admin_inline_keyboard())
        return ConversationHandler.END
    record_order(target_id, 0, sent, "manual")
    try:
        await context.bot.send_message(chat_id=target_id, text=f"🎉 תשלומך אושר! קיבלת {sent} סרטונים. תהנה! 🔥")
    except Exception:
        pass
    await update.message.reply_text(f"✅ נשלחו {sent} סרטונים!", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END


# ─── Admin: video gallery ─────────────────────────────────────────────────────

def _gallery_keyboard(idx: int, total: int):
    nav = []
    if idx > 0:
        nav.append(InlineKeyboardButton("⬅️ קודם", callback_data=f"vid_page_{idx-1}"))
    nav.append(InlineKeyboardButton(f"{idx+1}/{total}", callback_data="noop"))
    if idx < total - 1:
        nav.append(InlineKeyboardButton("הבא ➡️", callback_data=f"vid_page_{idx+1}"))
    return InlineKeyboardMarkup([
        nav,
        [InlineKeyboardButton("🗑 מחק סרטון זה",          callback_data=f"vid_del_{idx}")],
        [InlineKeyboardButton("📤 שלח לי את כל הסרטונים", callback_data="vid_send_all")],
        [InlineKeyboardButton("🔙 חזרה לפאנל",            callback_data="back_admin")],
    ])


async def admin_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    videos = load_json(VIDEOS_FILE)
    if not videos:
        await query.edit_message_text(
            "🎬 *גלריית סרטונים*\n\nאין סרטונים. שלח סרטון לבוט כדי להוסיף.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")]]),
        )
        return
    await query.edit_message_text(
        f"🎬 *גלריית סרטונים* — סה\"כ *{len(videos)}*\n\nסרטון 1:",
        parse_mode="Markdown",
        reply_markup=_gallery_keyboard(0, len(videos)),
    )
    await context.bot.send_video(chat_id=query.from_user.id, video=videos[0])


async def admin_gallery_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    idx    = int(query.data.split("vid_page_")[1])
    videos = load_json(VIDEOS_FILE)
    if not videos or idx >= len(videos):
        await query.answer("אין סרטון כאן.", show_alert=True)
        return
    await query.edit_message_text(
        f"🎬 *גלריית סרטונים* — סה\"כ *{len(videos)}*\n\nסרטון {idx+1}:",
        parse_mode="Markdown",
        reply_markup=_gallery_keyboard(idx, len(videos)),
    )
    await context.bot.send_video(chat_id=query.from_user.id, video=videos[idx])


async def admin_gallery_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    idx    = int(query.data.split("vid_del_")[1])
    videos = load_json(VIDEOS_FILE)
    if idx >= len(videos):
        await query.answer("הסרטון כבר נמחק.", show_alert=True)
        return
    videos.pop(idx)
    save_json(VIDEOS_FILE, videos)
    if not videos:
        await query.edit_message_text(
            "✅ נמחק. המאגר ריק כעת.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")]]),
        )
        return
    new_idx = min(idx, len(videos) - 1)
    await query.edit_message_text(
        f"✅ נמחק!\n\n🎬 *גלריה* — סה\"כ *{len(videos)}*\n\nסרטון {new_idx+1}:",
        parse_mode="Markdown",
        reply_markup=_gallery_keyboard(new_idx, len(videos)),
    )
    await context.bot.send_video(chat_id=query.from_user.id, video=videos[new_idx])


async def admin_gallery_send_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    videos = load_json(VIDEOS_FILE)
    if not videos:
        await query.answer("אין סרטונים.", show_alert=True)
        return
    await query.edit_message_text(f"📤 שולח {len(videos)} סרטונים...")
    sent = 0
    for file_id in videos:
        try:
            await context.bot.send_video(chat_id=ADMIN_ID, video=file_id)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"✅ נשלחו *{sent}/{len(videos)}* סרטונים.",
        parse_mode="Markdown",
        reply_markup=get_admin_inline_keyboard(),
    )


# ─── Admin: video search ──────────────────────────────────────────────────────

async def admin_video_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    videos = load_json(VIDEOS_FILE)
    await query.edit_message_text(
        f"🔢 *חיפוש סרטון לפי מספר*\n\nיש {len(videos)} סרטונים.\nשלח מספר (1–{len(videos)}):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="back_admin")]]),
    )
    return ADMIN_VIDEO_SEARCH


async def admin_video_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    videos = load_json(VIDEOS_FILE)
    try:
        num = int(update.message.text.strip())
        if num < 1 or num > len(videos):
            raise ValueError
    except ValueError:
        await update.message.reply_text(f"❌ מספר לא תקין. בחר בין 1 ל-{len(videos)}.")
        return ADMIN_VIDEO_SEARCH
    idx     = num - 1
    file_id = videos[idx]
    await update.message.reply_text(f"🎬 סרטון {num}/{len(videos)}:")
    await context.bot.send_video(
        chat_id=ADMIN_ID,
        video=file_id,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"🗑 מחק סרטון {num}", callback_data=f"vid_del_{idx}")]]),
    )
    await update.message.reply_text("חפש עוד סרטון או לחץ ביטול:", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END


# ─── Admin: broadcast (enhanced + delayed) ────────────────────────────────────

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("📢 *הודעה לכולם*\n\nשלח את תוכן ההודעה:", parse_mode="Markdown")
    return ADMIN_BROADCAST


async def admin_broadcast_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    context.user_data["broadcast_msg"] = update.message.text
    await update.message.reply_text(
        "🔗 *כפתור קישור (אופציונלי)*\n\nפורמט: `טקסט|https://קישור`\nאו `skip` לדלג:",
        parse_mode="Markdown",
    )
    return ADMIN_BROADCAST_BTN


async def admin_broadcast_get_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    raw    = update.message.text.strip()
    markup = None
    if raw.lower() != "skip":
        if "|" in raw:
            parts = raw.split("|", 1)
            btn_text, btn_url = parts[0].strip(), parts[1].strip()
            if btn_url.startswith("http"):
                markup = InlineKeyboardMarkup([[InlineKeyboardButton(btn_text, url=btn_url)]])
            else:
                await update.message.reply_text("❌ קישור לא תקין (חייב להתחיל ב-http).")
                return ADMIN_BROADCAST_BTN
        else:
            await update.message.reply_text("❌ פורמט לא תקין. השתמש ב-`טקסט|קישור` או `skip`.", parse_mode="Markdown")
            return ADMIN_BROADCAST_BTN
    context.user_data["broadcast_markup"] = markup
    await update.message.reply_text(
        "⏰ *השהיית שליחה (בדקות)*\n\nשלח `0` לשליחה מיידית, או מספר דקות להשהיה:",
        parse_mode="Markdown",
    )
    return ADMIN_BROADCAST_DELAY


async def admin_broadcast_get_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        delay_min = int(update.message.text.strip())
        if delay_min < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ מספר לא תקין. שלח 0 לשליחה מיידית.")
        return ADMIN_BROADCAST_DELAY

    msg    = context.user_data.get("broadcast_msg", "")
    markup = context.user_data.get("broadcast_markup")
    users  = load_json(USERS_FILE)

    if delay_min > 0:
        await update.message.reply_text(
            f"⏰ ההודעה תישלח בעוד *{delay_min} דקות* ל-{len(users)} משתמשים.",
            parse_mode="Markdown",
        )
        await asyncio.sleep(delay_min * 60)

    sent = 0
    failed = 0
    progress = await update.message.reply_text(f"📤 שולח ל-{len(users)} משתמשים...")

    for uid in users:
        try:
            await context.bot.send_message(chat_id=int(uid), text=msg, reply_markup=markup)
            sent += 1
        except Exception:
            failed += 1
        if (sent + failed) % 20 == 0:
            try:
                await progress.edit_text(f"📤 נשלח: {sent + failed}/{len(users)}...")
            except Exception:
                pass
        await asyncio.sleep(0.05)

    await update.message.reply_text(
        f"✅ *שליחה הושלמה!*\n\n✔️ הצליח: *{sent}*\n❌ נכשל: *{failed}*",
        parse_mode="Markdown",
        reply_markup=get_admin_inline_keyboard(),
    )
    return ConversationHandler.END


# ─── Admin: coins management ──────────────────────────────────────────────────

async def admin_coins_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("🪙 *ניהול מטבעות*\n\nשלח את ה-ID של המשתמש:", parse_mode="Markdown")
    return ADMIN_COINS_ID


async def admin_coins_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        uid = str(int(update.message.text.strip()))
        context.user_data["coins_target_id"] = uid
    except ValueError:
        await update.message.reply_text("❌ ID לא תקין.")
        return ConversationHandler.END
    users   = load_json(USERS_FILE)
    coins   = load_json(COINS_FILE)
    name    = users.get(uid, {}).get("first_name", "לא ידוע")
    current = coins.get(uid, 0)
    await update.message.reply_text(f"👤 {name}\n🪙 יתרה: {current}\n\nשלח כמות (+ להוסיף, - להוריד):")
    return ADMIN_COINS_AMOUNT


async def admin_coins_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        amount = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ כמות לא תקינה.")
        return ConversationHandler.END
    uid     = context.user_data.get("coins_target_id")
    coins   = load_json(COINS_FILE)
    current = coins.get(uid, 0)
    new_bal = max(0, current + amount)
    coins[uid] = new_bal
    save_json(COINS_FILE, coins)
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
        f"💱 *שינוי ערך המטבע*\n\nמכפיל נוכחי: *{current}x*\n\nשלח מכפיל חדש (לדוגמה: `1.5`).\nיתרות כל המשתמשים יוכפלו ביחס החדש.\n\n⚠️ פעולה בלתי הפיכה!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="back_admin")]]),
    )
    return ADMIN_MULTIPLIER


async def admin_multiplier_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        new_mult = float(update.message.text.strip().replace(",", "."))
        if new_mult <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ מספר לא תקין.")
        return ADMIN_MULTIPLIER
    settings = load_settings()
    old_mult = settings.get("referral_multiplier", 1.0)
    ratio    = new_mult / old_mult
    coins    = load_json(COINS_FILE)
    for uid in coins:
        coins[uid] = round(coins[uid] * ratio)
    save_json(COINS_FILE, coins)
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
            f"✅ *שחזור הושלם!*\nשוחזרו: {', '.join(json_files)}",
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
        (COINS_FILE,     {}),
        (REFERRALS_FILE, {}),
        (VIDEOS_FILE,    []),
        (ORDERS_FILE,    []),
        (COUPONS_FILE,   {}),
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
    settings = load_settings()
    new_val  = not settings.get("maintenance", False)
    settings["maintenance"] = new_val
    save_settings(settings)
    status = "🟠 פעיל" if new_val else "🟢 כבוי"
    await query.edit_message_text(
        f"🔧 *מצב תחזוקה*\n\nהסטטוס כעת: {status}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")]]),
    )


# ─── Video upload ─────────────────────────────────────────────────────────────

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    video = update.message.video
    if not video:
        return
    file_id = video.file_id
    videos  = load_json(VIDEOS_FILE)
    if file_id not in videos:
        videos.append(file_id)
        save_json(VIDEOS_FILE, videos)
        await update.message.reply_text(f"✅ הסרטון נשמר!\n📦 סה\"כ: {len(videos)}")
    else:
        await update.message.reply_text("ℹ️ סרטון זה כבר קיים.")


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

    # ── Register handlers ─────────────────────────────────────────────────────
    for conv in [
        check_conv, send_conv, approve_conv, broadcast_conv, coins_conv,
        coupon_new_conv, multiplier_conv, restore_conv, global_reset_conv,
        video_search_conv, support_conv, coupon_redeem_conv, support_reply_conv,
    ]:
        app.add_handler(conv)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🛠 פאנל אדמין$"), admin_panel))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))

    # Callback handlers (standalone)
    cbs = [
        ("^noop$",                      noop_callback),
        ("^payment_method$",            payment_method_menu),
        ("^paypal_menu$",               paypal_menu),
        (r"^pp_\d+$",                   paypal_package_selected),
        ("^coins_menu$",                coins_menu),
        (r"^coin_\d+$",                 coin_package_buy),
        ("^referrals$",                 referrals_menu),
        ("^wallet$",                    wallet_menu),
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
        ("^admin_maintenance$",         admin_maintenance_toggle),
        ("^back_admin$",                back_admin),
    ]
    for pattern, handler in cbs:
        app.add_handler(CallbackQueryHandler(handler, pattern=pattern))

    logger.info("הבוט מופעל... 🚀")
    
    # Fix for asyncio event loop issue in some environments
    import asyncio
    import sys

    async def run_application():
        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        # Keep the bot running
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
