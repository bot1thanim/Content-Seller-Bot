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
# HTTP request URLs contain the Bot API token; never log them in production.
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

# Only these JSON data files may be restored from an administrator backup.
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
    ADMIN_VIDEO_CAT,          # 21
    ADMIN_VIDEO_PREVIEW,      # 22
    ADMIN_BROADCAST_MEDIA,    # 23
    ADMIN_VIP_ID,             # 24
    ADMIN_VIP_LEVEL,         # 25
    ADMIN_RESTORE_CONFIRM,   # 26
    ADMIN_VIDEO_SEARCH_SECONDS, # 27
    ADMIN_VIDEO_CAT_ADD,     # 28
    ADMIN_VIDEO_CAT_SORT,    # 29
) = range(30)


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
        (TRASH_FILE,     []),
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


def normalize_restored_videos(videos):
    """Convert the legacy list-of-file_id backup format to current video records."""
    if videos and all(isinstance(item, str) for item in videos):
        return [
            {"file_id": item, "duration": 0, "preview": None, "entry_id": uuid.uuid4().hex}
            for item in videos
        ]
    return videos


def parse_restore_archive(raw_bytes: bytes) -> dict:
    """Validate a ZIP fully in memory and return only allowed data-file payloads."""
    if len(raw_bytes) > MAX_RESTORE_ARCHIVE_BYTES:
        raise ValueError("קובץ הגיבוי גדול מדי (מגבלה: 20MB).")

    payloads = {}
    total_uncompressed = 0
    with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_RESTORE_UNCOMPRESSED_BYTES:
                raise ValueError("תוכן הגיבוי גדול מדי לאחר חילוץ.")
            filename = Path(info.filename).name
            if filename not in BACKUP_ALLOWED_FILES:
                continue
            if filename in payloads:
                raise ValueError(f"הקובץ {filename} מופיע יותר מפעם אחת בגיבוי.")
            try:
                decoded = json.loads(archive.read(info).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"הקובץ {filename} אינו JSON תקין.") from exc
            expected_type = BACKUP_ALLOWED_FILES[filename]
            if not isinstance(decoded, expected_type):
                raise ValueError(f"המבנה של {filename} אינו תקין.")
            payloads[filename] = decoded

    if not payloads:
        raise ValueError("לא נמצאו קובצי נתונים מוכרים בגיבוי.")
    if "videos.json" in payloads:
        payloads["videos.json"] = normalize_restored_videos(payloads["videos.json"])
    return payloads


def restore_summary(payloads: dict) -> str:
    labels = {
        "users.json": "משתמשים",
        "videos.json": "סרטונים",
        "orders.json": "הזמנות",
        "coins.json": "יתרות מטבעות",
        "referrals.json": "הפניות",
        "coupons.json": "קופונים",
        "settings.json": "הגדרות",
        "trash.json": "סל מיחזור",
    }
    parts = []
    for filename, data in payloads.items():
        count = len(data) if isinstance(data, (list, dict)) else 0
        parts.append(f"• {labels[filename]}: {count}")
    return "\n".join(parts)


def apply_restore_payloads(payloads: dict) -> None:
    """Write only validated data files; callers must create a rollback snapshot first."""
    DATA_DIR.mkdir(exist_ok=True)
    for filename, data in payloads.items():
        save_json(DATA_DIR / filename, data)
    if "videos.json" in payloads:
        load_videos_with_entry_ids()


def load_videos_with_entry_ids():
    """Load video records and permanently assign a unique entry_id to every record."""
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
        logger.info("Assigned unique entry_id values to existing video records")
    return videos


async def send_admin_video_with_delete_button(bot, file_id, entry_id, max_attempts=5):
    """Send one video with a unique delete button and return its Telegram message on success."""
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(
        "🗑 מחק סרטון זה", callback_data=f"del_eid_{entry_id}"
    )]])

    for attempt in range(1, max_attempts + 1):
        try:
            return await bot.send_video(chat_id=ADMIN_ID, video=file_id, reply_markup=markup)
        except RetryAfter as exc:
            retry_after = exc.retry_after
            delay = retry_after.total_seconds() if hasattr(retry_after, "total_seconds") else float(retry_after)
            logger.warning(f"Telegram rate limit while sending video; retrying in {delay:.1f}s (attempt {attempt}/{max_attempts})")
            await asyncio.sleep(delay + 1)
        except (TimedOut, NetworkError) as exc:
            delay = min(2 ** attempt, 20)
            logger.warning(f"Temporary Telegram error while sending video: {exc}; retrying in {delay}s (attempt {attempt}/{max_attempts})")
            await asyncio.sleep(delay)
        except Exception as exc:
            logger.exception(f"Unable to send video with entry_id {entry_id}: {exc}")
            return None

    logger.error(f"Giving up on video with entry_id {entry_id} after {max_attempts} attempts")
    return None

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


DUPLICATE_REVIEWED_KEY = "reviewed_non_duplicate_groups"


def duplicate_group_signature(group: list[dict]) -> str:
    """Return a stable identity for the exact set of videos in a duplicate-review group."""
    duration = group[0].get("duration", 0) if group else 0
    entry_ids = sorted(str(video.get("entry_id", "")) for video in group)
    payload = json.dumps({"duration": duration, "entry_ids": entry_ids}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def reviewed_non_duplicate_signatures() -> set[str]:
    settings = load_settings()
    reviewed = settings.get(DUPLICATE_REVIEWED_KEY, [])
    return {signature for signature in reviewed if isinstance(signature, str)}


def mark_group_as_not_duplicate(group: list[dict]) -> bool:
    """Persist a manual review decision for this exact duplicate-group snapshot."""
    signature = duplicate_group_signature(group)
    settings = load_settings()
    reviewed = settings.get(DUPLICATE_REVIEWED_KEY, [])
    if not isinstance(reviewed, list):
        reviewed = []
    if signature in reviewed:
        return False
    reviewed.append(signature)
    settings[DUPLICATE_REVIEWED_KEY] = reviewed
    save_settings(settings)
    return True


def clear_not_duplicate_marks() -> int:
    """Clear manual duplicate-review decisions so a full scan includes every group again."""
    settings = load_settings()
    reviewed = settings.get(DUPLICATE_REVIEWED_KEY, [])
    count = len(reviewed) if isinstance(reviewed, list) else 0
    settings[DUPLICATE_REVIEWED_KEY] = []
    save_settings(settings)
    return count

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
            "username": user.username or "None",
            "first_name": user.first_name or "User",
            "joined": today,
            "purchases": 0,
            "spent": 0.0,
            "ref_by": ref_id if ref_id and ref_id != uid else None
        }
        save_json(USERS_FILE, users)
        
        # Referral bonus
        if ref_id and ref_id != uid:
            coins = load_json(COINS_FILE)
            settings = load_settings()
            mult = settings.get("referral_multiplier", 1.0)
            bonus = int(5 * mult)
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
    coins[uid] = coins.get(uid, 0) + amount
    if coins[uid] < 0: coins[uid] = 0
    save_json(COINS_FILE, coins)

# ─── Keyboards ───────────────────────────────────────────────────────────────

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
            InlineKeyboardButton("🏷 קטגוריות",        callback_data="admin_categories"),
        ],
        [
            InlineKeyboardButton("👯 מצא כפילויות",    callback_data="admin_dup_scan"),
            InlineKeyboardButton("🗑 סל מיחזור",       callback_data="admin_trash_page_0"),
        ],
        [
            InlineKeyboardButton("💰 ניהול מטבעות",    callback_data="admin_coins"),
            InlineKeyboardButton("💎 ניהול VIP",       callback_data="admin_vip"),
        ],
        [
            InlineKeyboardButton("🎟 קופונים",         callback_data="admin_coupons"),
            InlineKeyboardButton("📢 הודעה לכולם",     callback_data="admin_broadcast"),
        ],
        [
            InlineKeyboardButton("✖️ מכפיל הפניות",   callback_data="admin_multiplier"),
            InlineKeyboardButton("💾 גיבוי/שחזור",     callback_data="admin_backup"),
        ],
        [
            InlineKeyboardButton(maint_label,         callback_data="admin_maintenance"),
            InlineKeyboardButton("🔥 איפוס מלא",       callback_data="admin_global_reset"),
        ]
    ])

# ─── User Handlers ───────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    ref_id = args[0] if args else None
    
    is_new = register_user(user, ref_id)
    
    welcome_text = f"""ברוך הבא לבוט הסרטונים שלנו! 🎬

כאן תוכל למצוא מגוון רחב של סרטונים איכותיים לרכישה.

💡 *איך זה עובד?*
1. טוענים מטבעות (בפייפאל או באישור מנהל).
2. בוחרים חבילת סרטונים.
3. מקבלים את הסרטונים ישירות לצאט!

💰 המאזן שלך: {get_coins(user.id)} מטבעות.
🎁 הזמנת חברים מקנה לך 5 מטבעות על כל חבר!

השתמש בתפריט למטה כדי להתחיל 👇"""

    if is_new and ref_id:
        try:
            await context.bot.send_message(
                chat_id=ref_id,
                text=f"🎁 חבר חדש הצטרף דרכך! קיבלת בונוס מטבעות."
            )
        except: pass

    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard(user.id))

async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "חזרת לתפריט הראשי. השתמש במקלדת למטה:",
        reply_markup=None
    )
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="תפריט ראשי:",
        reply_markup=get_main_keyboard(query.from_user.id)
    )

# ─── Payment & Coins ─────────────────────────────────────────────────────────

async def coins_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = f"💰 *טעינת מטבעות*\n\nהמאזן שלך: {get_coins(user_id)} מטבעות\n\nבחר שיטת תשלום:"
    btns = [
        [InlineKeyboardButton("💳 PayPal (אוטומטי)", callback_data="paypal_menu")],
        [InlineKeyboardButton("🏦 העברה/מזומן (אישור ידני)", callback_data="admin_support_payment")],
        [InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def paypal_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "💳 *טעינה דרך PayPal*\n\nבחר חבילה לטעינה (המחיר בשקלים):"
    btns = []
    for i, p in enumerate(PACKAGES):
        btns.append([InlineKeyboardButton(p["label_paypal"], callback_data=f"pp_{i}")])
    btns.append([InlineKeyboardButton("🔙 חזרה", callback_data="coins_menu")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def paypal_package_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    p = PACKAGES[idx]
    
    text = f"""💎 *חבילה נבחרת: {p['label_paypal']}*

כדי להשלים את הרכישה:
1. לחץ על הכפתור למטה למעבר לפייפאל.
2. שלח את הסכום המדויק ({p['price']} ₪).
3. לאחר התשלום, צלם מסך ושלח אותו לתמיכה שלנו.

⚠️ *חשוב:* המטבעות יתווספו לאחר אישור ידני של המנהל."""
    
    btns = [
        [InlineKeyboardButton("🔗 מעבר לתשלום ב-PayPal", url=PAYPAL_LINK)],
        [InlineKeyboardButton("💬 שלח צילום מסך לתמיכה", callback_data="support")],
        [InlineKeyboardButton("🔙 חזרה", callback_data="paypal_menu")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

# ─── Profile & Referrals ─────────────────────────────────────────────────────

async def profile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid  = str(user.id)
    users = load_json(USERS_FILE)
    u = users.get(uid, {})
    vip = get_user_vip(uid)
    
    text = f"""👤 *הפרופיל שלי*

🆔 מזהה: `{uid}`
📅 הצטרפת ב: {u.get('joined', 'Unknown')}
💰 יתרה: {get_coins(uid)} מטבעות
🛍 רכישות: {u.get('purchases', 0)} סרטונים
💎 דרגה: {vip['icon']} {vip['name']} ({int(vip['discount']*100)}% הנחה)

נשמח לראות אותך ממשיך להשתמש בבוט!"""
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def referrals_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid  = str(user.id)
    refs = load_json(REFERRALS_FILE).get(uid, [])
    settings = load_settings()
    mult = settings.get("referral_multiplier", 1.0)
    bonus = int(5 * mult)
    
    link = f"https://t.me/{(await context.bot.get_me()).username}?start={uid}"
    
    text = f"""🤝 *תוכנית שותפים*

הזמן חברים וקבל מטבעות מתנה!
על כל חבר חדש שיצטרף דרך הקישור שלך, תקבל *{bonus} מטבעות*.

👥 חברים שהצטרפו דרכך: {len(refs)}
🔗 קישור ההפניה שלך:
`{link}`

(לחץ על הקישור כדי להעתיק)"""
    
    await update.message.reply_text(text, parse_mode="Markdown")

# ─── Admin: Stats & Users ────────────────────────────────────────────────────

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("🛠 *פאנל ניהול*", parse_mode="Markdown", reply_markup=get_admin_inline_keyboard())

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    users   = load_json(USERS_FILE)
    videos  = load_json(VIDEOS_FILE)
    orders  = load_json(ORDERS_FILE)
    coins   = load_json(COINS_FILE)
    total_c = sum(coins.values())
    
    text = f"""📊 *סטטיסטיקה*

👥 משתמשים: {len(users)}
🎬 סרטונים במאגר: {len(videos)}
🧾 סה"כ הזמנות: {len(orders)}
🪙 מטבעות במערכת: {total_c}"""
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

async def users_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("users_page_")[1])
    users = list(load_json(USERS_FILE).values())
    total = len(users)
    
    if total == 0:
        await query.edit_message_text("אין משתמשים.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))
        return
    
    start_idx = page * 10
    end_idx   = start_idx + 10
    current_users = users[start_idx:end_idx]
    
    text = f"👥 *רשימת משתמשים (דף {page+1})*\n\n"
    for u in current_users:
        text += f"• `{u['id']}` - {u['first_name']} (@{u['username']})\n"
        
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️ הקודם", callback_data=f"users_page_{page-1}"))
    if end_idx < total: nav.append(InlineKeyboardButton("הבא ➡️", callback_data=f"users_page_{page+1}"))
    
    btns = [nav, [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

# ─── Admin: Orders & Approval ────────────────────────────────────────────────

async def admin_orders_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("admin_orders_page_")[1])
    orders = load_json(ORDERS_FILE)
    total = len(orders)
    
    if total == 0:
        await query.edit_message_text("אין הזמנות.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))
        return
    
    orders.reverse() # Show newest first
    start_idx = page * ORDERS_PER_PAGE
    end_idx   = start_idx + ORDERS_PER_PAGE
    current_orders = orders[start_idx:end_idx]
    
    text = f"🧾 *הזמנות אחרונות (דף {page+1})*\n\n"
    for o in current_orders:
        text += f"• `{o['user_id']}` | {o['amount']} סרטונים | {o['date']}\n"
        
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️ הקודם", callback_data=f"admin_orders_page_{page-1}"))
    if end_idx < total: nav.append(InlineKeyboardButton("הבא ➡️", callback_data=f"admin_orders_page_{page+1}"))
    
    btns = [nav, [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

# ─── Admin: Gallery Management ───────────────────────────────────────────────

async def admin_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    
    videos = load_json(VIDEOS_FILE)
    text = f"🎬 *ניהול גלריית סרטונים*\n\nסה\"כ סרטונים: {len(videos)}\nבחר פעולה:"
    btns = [
        [InlineKeyboardButton("📖 עיון בספרייה", callback_data="vid_page_0")],
        [InlineKeyboardButton("🔢 חיפוש לפי מספר", callback_data="admin_video_search")],
        [InlineKeyboardButton("⏱ חיפוש לפי זמן", callback_data="admin_search_sec_start")],
        [InlineKeyboardButton("📤 שלח את כל המאגר", callback_data="vid_send_all")],
        [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]
    ]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def clear_sent_duplicate_group_media(context: ContextTypes.DEFAULT_TYPE):
    """Delete all media messages sent during a duplicate review or gallery navigation."""
    msg_ids = context.user_data.get("dup_sent_media_message_ids", [])
    for msg_id in msg_ids:
        try:
            await context.bot.delete_message(chat_id=ADMIN_ID, message_id=msg_id)
        except Exception:
            pass
    context.user_data["dup_sent_media_message_ids"] = []

async def admin_gallery_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=None):
    query = update.callback_query
    if page is None:
        page = int(query.data.split("vid_page_")[1])
    
    # Cleanup previous media
    await clear_sent_duplicate_group_media(context)
    
    videos = load_json(VIDEOS_FILE)
    total  = len(videos)
    if total == 0:
        await query.edit_message_text("אין סרטונים במאגר.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_gallery")]]))
        return

    page = max(0, min(page, total - 1))
    v = videos[page]
    text = f"""🎬 *גלריית סרטונים ({page+1}/{total})*

📁 קטגוריה: {v.get('category', 'כללי')}
⏱ אורך: {v.get('duration', 0)} שניות
🖼 דוגמה: {'יש' if v.get('preview') else 'אין'}"""
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"vid_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total}", callback_data="noop"))
    if page < total - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"vid_page_{page+1}"))
        
    btns = [
        nav,
        [InlineKeyboardButton("🗑 מחק סרטון זה", callback_data=f"vid_del_{page}")],
        [InlineKeyboardButton("🔙 חזרה לגלריה", callback_data="admin_gallery")]
    ]
    
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    except Exception:
        pass
    
    sent_msg = await context.bot.send_video(chat_id=ADMIN_ID, video=v["file_id"])
    if sent_msg:
        context.user_data["dup_sent_media_message_ids"] = [sent_msg.message_id]

async def admin_gallery_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Format can be vid_del_INDEX or del_eid_ENTRYID
    data = query.data
    videos = load_json(VIDEOS_FILE)
    trash = load_json(TRASH_FILE)
    
    deleted_video = None
    if data.startswith("vid_del_"):
        idx = int(data.split("_")[2])
        if 0 <= idx < len(videos):
            deleted_video = videos.pop(idx)
    elif data.startswith("del_eid_"):
        eid = data.split("_")[2]
        for i, v in enumerate(videos):
            if v.get("entry_id") == eid:
                deleted_video = videos.pop(i)
                break
    
    if deleted_video:
        deleted_video["deleted_at"] = str(datetime.now())
        trash.append(deleted_video)
        save_json(VIDEOS_FILE, videos)
        save_json(TRASH_FILE, trash)
        
        # Try to delete the video message if possible
        try:
            await query.message.delete()
        except: pass
        
        await context.bot.send_message(chat_id=ADMIN_ID, text="✅ הסרטון הועבר לסל המיחזור.")
        # If it was from gallery page, refresh gallery
        if data.startswith("vid_del_"):
            await admin_gallery(update, context)

async def admin_gallery_send_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """שליחה מהירה ורציפה של כל המאגר ללא הודעות ביניים."""
    query = update.callback_query
    await query.answer()
    
    videos = load_videos_with_entry_ids()
    if not videos:
        await query.edit_message_text("אין סרטונים במאגר.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_gallery")]]))
        return

    # מיון לפי אורך
    sorted_videos = sorted(videos, key=lambda v: v.get("duration", 0))
    total = len(sorted_videos)
    
    # הודעת התחלה (תימחק מיד בסיום)
    status_msg = await query.edit_message_text(f"🚀 שולח כעת את כל {total} הסרטונים ברצף...")
    
    success = 0
    for v in sorted_videos:
        try:
            sent = await send_admin_video_with_delete_button(context.bot, v["file_id"], v["entry_id"])
            if sent:
                success += 1
            await asyncio.sleep(0.1) # מהירות מקסימלית בטוחה
        except Exception:
            await asyncio.sleep(1.0)
            
    try:
        await status_msg.delete()
    except: pass

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"✅ סיימתי לשלוח את המאגר! ({success}/{total} נשלחו בהצלחה)",
        reply_markup=get_admin_inline_keyboard()
    )

# ─── Admin: Video Search ─────────────────────────────────────────────────────

async def admin_video_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔢 *חיפוש סרטון לפי מספר*\n\nשלח את מספר הסרטון (למשל `5`):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="admin_gallery")]])
    )
    return ADMIN_VIDEO_SEARCH

async def admin_video_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    videos = load_json(VIDEOS_FILE)
    text = update.message.text.strip()
    
    try:
        num = int(text)
        if num < 1 or num > len(videos): raise ValueError
    except ValueError:
        await update.message.reply_text(f"❌ מספר לא תקין. בחר בין 1 ל-{len(videos)}.")
        return ADMIN_VIDEO_SEARCH
        
    idx = num - 1
    v = videos[idx]
    await update.message.reply_text(f"🎬 סרטון {num}/{len(videos)}:")
    await send_admin_video_with_delete_button(context.bot, v["file_id"], v.get("entry_id", uuid.uuid4().hex))
    await update.message.reply_text("חפש עוד מספר או לחץ ביטול:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="admin_gallery")]]))
    return ADMIN_VIDEO_SEARCH

def parse_smart_time(text: str) -> int:
    """Parse '26' as 26s, '1:20' as 80s, '20:30' as 1230s."""
    text = text.strip()
    if ":" in text:
        parts = text.split(":")
        if len(parts) == 2:
            try:
                m, s = int(parts[0]), int(parts[1])
                return m * 60 + s
            except ValueError: return -1
    try:
        return int(text)
    except ValueError:
        return -1

async def admin_search_sec_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "⏱ *חיפוש סרטונים לפי זמן*\n\nשלח זמן (למשל `26` לשניות או `1:20` לדקות):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="admin_gallery")]])
    )
    return ADMIN_VIDEO_SEARCH_SECONDS

async def admin_search_sec_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    target_sec = parse_smart_time(update.message.text)
    
    if target_sec < 0:
        await update.message.reply_text("❌ פורמט זמן לא תקין. נסה שוב (למשל `30` או `1:15`).")
        return ADMIN_VIDEO_SEARCH_SECONDS
        
    videos = load_videos_with_entry_ids()
    results = [v for v in videos if v.get("duration") == target_sec]
    
    if not results:
        await update.message.reply_text(f"🔍 לא נמצאו סרטונים באורך {target_sec} שניות.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_gallery")]]))
        return ConversationHandler.END
        
    await update.message.reply_text(f"🔍 נמצאו {len(results)} סרטונים באורך {target_sec} שניות:")
    for v in results:
        await send_admin_video_with_delete_button(context.bot, v["file_id"], v["entry_id"])
        await asyncio.sleep(0.1)
        
    await update.message.reply_text("חיפוש הושלם.", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

# ─── Admin: Duplicate Detection ──────────────────────────────────────────────

def find_duplicate_groups(include_reviewed: bool) -> list[list[dict]]:
    videos = load_videos_with_entry_ids()
    groups = {}
    for video in videos:
        duration = video.get("duration", 0)
        if not duration: continue
        groups.setdefault(duration, []).append(video)

    duplicate_groups = [group for group in groups.values() if len(group) > 1]
    duplicate_groups.sort(key=lambda group: (group[0].get("duration", 0), len(group)))
    
    if include_reviewed:
        return duplicate_groups

    reviewed = reviewed_non_duplicate_signatures()
    return [group for group in duplicate_groups if duplicate_group_signature(group) not in reviewed]

async def admin_dup_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_duplicate_scan(update, context, include_reviewed=False)

async def admin_dup_rescan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clear_not_duplicate_marks()
    await show_duplicate_scan(update, context, include_reviewed=True)

async def show_duplicate_scan(update: Update, context: ContextTypes.DEFAULT_TYPE, include_reviewed: bool):
    query = update.callback_query
    duplicate_groups = find_duplicate_groups(include_reviewed=include_reviewed)
    context.user_data["dup_groups"] = duplicate_groups

    if not duplicate_groups:
        text = "✅ לא נמצאו סרטונים כפולים לפי אורך."
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_gallery")]]))
        return

    await admin_dup_page(update, context, 0)

async def admin_dup_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=None):
    query = update.callback_query
    if page is None:
        page = int(query.data.split("dup_page_")[1])
    
    groups = context.user_data.get("dup_groups", [])
    if not groups or page >= len(groups):
        await query.edit_message_text("הסריקה הסתיימה.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_gallery")]]))
        return

    # Cleanup previous media
    await clear_sent_duplicate_group_media(context)
    
    group = groups[page]
    duration = group[0].get("duration", 0)
    
    text = f"""🔎 *חשד לכפילות ({page+1}/{len(groups)})*

⏱ אורך: {duration} שניות
👥 מספר סרטונים בקבוצה: {len(group)}

לחץ על 'שלח חשדות' כדי לראות את הסרטונים ולמחוק כפילויות."""

    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"dup_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{len(groups)}", callback_data="noop"))
    if page < len(groups) - 1: nav.append(InlineKeyboardButton("➡️", callback_data=f"dup_page_{page+1}"))

    btns = [
        [InlineKeyboardButton("📺 שלח חשדות", callback_data=f"dup_send_{page}")],
        nav,
        [InlineKeyboardButton("✅ סמן כלא כפולים", callback_data=f"dup_mark_{page}")],
        [InlineKeyboardButton("🔄 סרוק הכל מחדש", callback_data="admin_dup_rescan")],
        [InlineKeyboardButton("🔙 חזרה", callback_data="admin_gallery")]
    ]
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def admin_dup_send_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("dup_send_")[1])
    groups = context.user_data.get("dup_groups", [])
    if not groups or page >= len(groups): return
    
    group = groups[page]
    sent_ids = []
    for v in group:
        sent = await send_admin_video_with_delete_button(context.bot, v["file_id"], v["entry_id"])
        if sent: sent_ids.append(sent.message_id)
        await asyncio.sleep(0.1)
    
    context.user_data["dup_sent_media_message_ids"] = sent_ids

async def admin_dup_mark_not_duplicate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    page = int(query.data.split("dup_mark_")[1])
    groups = context.user_data.get("dup_groups", [])
    if not groups or page >= len(groups): return
    
    mark_group_as_not_duplicate(groups[page])
    await query.answer("✅ סומן כלא כפול")
    
    # Move to next group
    if page < len(groups) - 1:
        await admin_dup_page(update, context, page + 1)
    else:
        await query.edit_message_text("✅ סיימת לעבור על כל החשדות!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_gallery")]]))

async def admin_dup_back_to_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await clear_sent_duplicate_group_media(context)
    await admin_gallery(update, context)

# ─── Admin: Categories ───────────────────────────────────────────────────────

async def admin_categories_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = load_settings()
    cats = settings.get("categories", ["כללי"])
    text = "🏷 *ניהול קטגוריות*\n\nקטגוריות קיימות:\n" + "\n".join([f"• {c}" for c in cats])
    btns = [
        [InlineKeyboardButton("➕ הוסף קטגוריה", callback_data="admin_cat_add")],
        [InlineKeyboardButton("🏷 מיון סרטונים לקטגוריות", callback_data="admin_cat_sort_start")],
        [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]
    ]
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def admin_cat_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("✍️ שלח את שם הקטגוריה החדשה:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="admin_categories")]]))
    return ADMIN_VIDEO_CAT_ADD

async def admin_cat_add_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    settings = load_settings()
    cats = settings.get("categories", ["כללי"])
    if name not in cats:
        cats.append(name)
        settings["categories"] = cats
        save_settings(settings)
        await update.message.reply_text(f"✅ הקטגוריה '{name}' נוספה בהצלחה!", reply_markup=get_admin_inline_keyboard())
    else:
        await update.message.reply_text(f"⚠️ הקטגוריה '{name}' כבר קיימת.", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_cat_sort_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    videos = load_json(VIDEOS_FILE)
    if not videos:
        await update.callback_query.edit_message_text("אין סרטונים למיון.", reply_markup=get_admin_inline_keyboard())
        return
    
    page = max(0, min(page, len(videos) - 1))
    v = videos[page]
    await clear_sent_duplicate_group_media(context)
    
    text = f"🏷 *מיון סרטונים ({page+1}/{len(videos)})*\n\nקטגוריה נוכחית: {v.get('category', 'כללי')}\nבחר קטגוריה חדשה:"
    
    settings = load_settings()
    cats = settings.get("categories", ["כללי"])
    btns = []
    row = []
    for c in cats:
        row.append(InlineKeyboardButton(c, callback_data=f"cat_assign_{page}_{c}"))
        if len(row) == 2:
            btns.append(row)
            row = []
    if row: btns.append(row)
    
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"cat_sort_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{len(videos)}", callback_data="noop"))
    if page < len(videos) - 1: nav.append(InlineKeyboardButton("➡️", callback_data=f"cat_sort_page_{page+1}"))
    btns.append(nav)
    btns.append([InlineKeyboardButton("🔙 סיום", callback_data="admin_categories")])
    
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    sent = await context.bot.send_video(chat_id=ADMIN_ID, video=v["file_id"])
    if sent:
        context.user_data["dup_sent_media_message_ids"] = [sent.message_id]

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
        
        if page < len(videos) - 1:
            await admin_cat_sort_page(update, context, page + 1)
        else:
            await query.edit_message_text("✅ סיימת למיין!", reply_markup=get_admin_inline_keyboard())

# ─── Admin: Trash Management ─────────────────────────────────────────────────

async def admin_trash_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("admin_trash_page_")[1])
    trash = load_json(TRASH_FILE)
    total = len(trash)
    
    if total == 0:
        await query.edit_message_text("סל המיחזור ריק.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))
        return
        
    page = max(0, min(page, total - 1))
    v = trash[page]
    text = f"🗑 *סל מיחזור ({page+1}/{total})*\n\nנמחק ב: {v.get('deleted_at', 'Unknown')}\nאורך: {v.get('duration', 0)} שניות"
    
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"admin_trash_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total}", callback_data="noop"))
    if page < total - 1: nav.append(InlineKeyboardButton("➡️", callback_data=f"admin_trash_page_{page+1}"))
    
    btns = [
        nav,
        [InlineKeyboardButton("♻️ שחזר סרטון", callback_data=f"trash_restore_{page}")],
        [InlineKeyboardButton("🔥 מחק לצמיתות", callback_data=f"trash_perm_{page}")],
        [InlineKeyboardButton("🧹 רוקן סל מיחזור", callback_data="trash_empty")],
        [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]
    ]
    
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
        v = trash.pop(idx)
        v.pop("deleted_at", None)
        videos.append(v)
        save_json(TRASH_FILE, trash)
        save_json(VIDEOS_FILE, videos)
        await query.answer("✅ הסרטון שוחזר")
        await admin_trash_page(update, context)

async def admin_trash_perm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    idx = int(query.data.split("_")[2])
    trash = load_json(TRASH_FILE)
    
    if 0 <= idx < len(trash):
        trash.pop(idx)
        save_json(TRASH_FILE, trash)
        await query.answer("🔥 נמחק לצמיתות")
        await admin_trash_page(update, context)

async def admin_trash_empty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_json(TRASH_FILE, [])
    await update.callback_query.answer("🧹 סל המיחזור רוקן")
    await back_admin(update, context)

# ─── Admin: Backup & Reset ───

async def admin_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in DATA_DIR.iterdir():
            if f.suffix == ".json":
                zf.write(f, f.name)
    buf.seek(0)
    await context.bot.send_document(chat_id=ADMIN_ID, document=buf, filename=f"backup_{date.today()}.zip")
    await query.edit_message_text("✅ הגיבוי נשלח כקובץ ZIP.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

async def admin_global_reset_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("🔥 *איפוס מלא של המערכת*\n\nפעולה זו תמחק את כל המשתמשים, ההזמנות והסרטונים!\nהאם אתה בטוח?", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ כן, אני בטוח", callback_data="admin_global_reset_confirm")],
        [InlineKeyboardButton("❌ ביטול", callback_data="back_admin")]
    ]))

async def admin_global_reset_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_data_files()
    for f in DATA_DIR.iterdir():
        if f.suffix == ".json":
            save_json(f, [] if "videos" in f.name or "orders" in f.name or "trash" in f.name else {})
    await update.callback_query.edit_message_text("🔥 המערכת אופסה לחלוטין.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

# ─── Other Admin functions ───

async def admin_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("שלח מזהה משתמש לבדיקה:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="back_admin")]]))
    return ADMIN_CHECK_USER

async def admin_check_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.text.strip()
    users = load_json(USERS_FILE)
    if uid in users:
        u = users[uid]
        await update.message.reply_text(f"👤 משתמש: {u['first_name']}\n💰 יתרה: {get_coins(uid)}\n🛍 רכישות: {u.get('purchases', 0)}", reply_markup=get_admin_inline_keyboard())
    else:
        await update.message.reply_text("❌ משתמש לא נמצא.", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("שלח מזהה משתמש שאליו תרצה לשלוח הודעה:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="back_admin")]]))
    return ADMIN_SEND_ID

async def admin_send_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["target_uid"] = update.message.text.strip()
    await update.message.reply_text("מה ההודעה שתרצה לשלוח?")
    return ADMIN_SEND_MSG

async def admin_send_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["target_uid"]
    msg = update.message.text
    try:
        await context.bot.send_message(chat_id=uid, text=f"📩 הודעה מהמנהל:\n\n{msg}")
        await update.message.reply_text("✅ ההודעה נשלחה.", reply_markup=get_admin_inline_keyboard())
    except:
        await update.message.reply_text("❌ שגיאה בשליחה.", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("שלח מזהה משתמש לאישור תשלום:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="back_admin")]]))
    return ADMIN_APPROVE_ID

async def admin_approve_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["target_uid"] = update.message.text.strip()
    await update.message.reply_text("כמה מטבעות להוסיף?")
    return ADMIN_APPROVE_COUNT

async def admin_approve_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data["target_uid"]
    try:
        amount = int(update.message.text.strip())
        update_coins(uid, amount)
        await context.bot.send_message(chat_id=uid, text=f"✅ המנהל אישר את התשלום שלך! נוספו {amount} מטבעות לחשבונך.")
        await update.message.reply_text(f"✅ נוספו {amount} מטבעות למשתמש {uid}.", reply_markup=get_admin_inline_keyboard())
    except:
        await update.message.reply_text("❌ שגיאה בתהליך.", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_maintenance_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = load_settings()
    settings["maintenance"] = not settings.get("maintenance", False)
    save_settings(settings)
    await update.callback_query.answer(f"🔧 מצב תחזוקה: {'פעיל' if settings['maintenance'] else 'כבוי'}")
    await admin_panel(update, context)

async def back_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("🛠 *פאנל ניהול*", parse_mode="Markdown", reply_markup=get_admin_inline_keyboard())

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("הפעולה בוטלה.", reply_markup=get_main_keyboard(update.effective_user.id))
    return ConversationHandler.END

# ─── Upload Handler ──────────────────────────────────────────────────────────

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    video = update.message.video
    file_id = video.file_id
    duration = video.duration
    
    context.user_data["new_video"] = {"file_id": file_id, "duration": duration, "category": "כללי", "preview": None}
    
    settings = load_settings()
    cats = settings.get("categories", ["כללי"])
    btns = []
    for c in cats:
        btns.append([InlineKeyboardButton(c, callback_data=f"cat_sel_{c}")])
    
    await update.message.reply_text("🎬 סרטון התקבל! בחר קטגוריה:", reply_markup=InlineKeyboardMarkup(btns))
    return ADMIN_VIDEO_CAT

async def admin_video_cat_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cat = query.data.split("cat_sel_")[1]
    context.user_data["new_video"]["category"] = cat
    
    videos = load_json(VIDEOS_FILE)
    v = context.user_data["new_video"]
    v["entry_id"] = uuid.uuid4().hex
    videos.append(v)
    save_json(VIDEOS_FILE, videos)
    
    await query.edit_message_text(f"✅ הסרטון נוסף בהצלחה לקטגוריה '{cat}'!")
    return ConversationHandler.END

# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    ensure_data_files()
    app = Application.builder().token(TOKEN).build()

    # Conversation Handlers
    check_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_check, pattern="^admin_check$")],
        states={ADMIN_CHECK_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_check_input)]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_admin, pattern="^back_admin$")],
        per_message=False, per_chat=True
    )
    
    send_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_send, pattern="^admin_send$")],
        states={
            ADMIN_SEND_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_id)],
            ADMIN_SEND_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_msg)]
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_admin, pattern="^back_admin$")],
        per_message=False, per_chat=True
    )
    
    approve_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_approve, pattern="^admin_approve$")],
        states={
            ADMIN_APPROVE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_approve_id)],
            ADMIN_APPROVE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_approve_count)]
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_admin, pattern="^back_admin$")],
        per_message=False, per_chat=True
    )

    video_search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_video_search_start, pattern="^admin_video_search$")],
        states={ADMIN_VIDEO_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_video_search_input)]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(admin_gallery, pattern="^admin_gallery$")],
        per_message=False, per_chat=True
    )

    video_search_sec_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_search_sec_start, pattern="^admin_search_sec_start$")],
        states={ADMIN_VIDEO_SEARCH_SECONDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_search_sec_input)]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(admin_gallery, pattern="^admin_gallery$")],
        per_message=False, per_chat=True
    )

    cat_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_cat_add_start, pattern="^admin_cat_add$")],
        states={ADMIN_VIDEO_CAT_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_cat_add_input)]},
        fallbacks=[CallbackQueryHandler(admin_categories_menu, pattern="^admin_categories$")],
        per_message=False, per_chat=True
    )

    video_upload_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.VIDEO, handle_video)],
        states={ADMIN_VIDEO_CAT: [CallbackQueryHandler(admin_video_cat_sel, pattern="^cat_sel_")]},
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False, per_chat=True
    )

    app.add_handler(check_conv)
    app.add_handler(send_conv)
    app.add_handler(approve_conv)
    app.add_handler(video_search_conv)
    app.add_handler(video_search_sec_conv)
    app.add_handler(cat_add_conv)
    app.add_handler(video_upload_conv)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🛠 פאנל אדמין$"), admin_panel))
    app.add_handler(MessageHandler(filters.Regex("^🪙 טען מטבעות$"), coins_menu))
    app.add_handler(MessageHandler(filters.Regex("^👤 הפרופיל שלי$"), profile_menu))
    app.add_handler(MessageHandler(filters.Regex("^🤝 תוכנית שותפים$"), referrals_menu))

    # Callback handlers
    cbs = [
        ("^noop$",                      noop_callback),
        ("^back_main$",                 back_main),
        ("^admin_stats$",               admin_stats),
        (r"^admin_orders_page_\d+$",    admin_orders_page),
        (r"^users_page_\d+$",           users_page),
        ("^admin_gallery$",             admin_gallery),
        ("^admin_dup_scan$",            admin_dup_scan),
        ("^admin_dup_rescan$",          admin_dup_rescan),
        (r"^dup_page_\d+$",             admin_dup_page),
        (r"^dup_mark_\d+$",             admin_dup_mark_not_duplicate),
        (r"^dup_send_\d+$",             admin_dup_send_group),
        (r"^vid_page_\d+$",             admin_gallery_page),
        (r"^vid_del_\d+$",              admin_gallery_delete),
        (r"^del_eid_",                  admin_gallery_delete),
        ("^vid_send_all$",              admin_gallery_send_all),
        ("^admin_categories$",          admin_categories_menu),
        (r"^cat_sort_page_\d+$",        admin_cat_sort_page),
        ("^admin_cat_sort_start$",      lambda u, c: admin_cat_sort_page(u, c, 0)),
        (r"^cat_assign_",               admin_cat_assign),
        (r"^admin_trash_page_\d+$",     admin_trash_page),
        (r"^trash_restore_\d+$",        admin_trash_restore),
        (r"^trash_perm_\d+$",           admin_trash_perm),
        ("^trash_empty$",               admin_trash_empty),
        ("^admin_maintenance$",         admin_maintenance_toggle),
        ("^back_admin$",                back_admin),
        ("^admin_backup$",              admin_backup),
        ("^admin_global_reset$",        admin_global_reset_start),
        ("^admin_global_reset_confirm$", admin_global_reset_confirm),
    ]
    for pattern, handler in cbs:
        app.add_handler(CallbackQueryHandler(handler, pattern=pattern))

    async def run_application():
        await app.initialize()
        await app.start()
        
        def handle_polling_error(error):
            if isinstance(error, Conflict):
                logger.info("Polling conflict: exiting cleanly.")
                os._exit(0)
            logger.error("Recoverable polling error: %s", error)

        await app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
            error_callback=handle_polling_error,
        )
        
        while True:
            await asyncio.sleep(3600)

    try:
        if sys.version_info >= (3, 11):
            asyncio.run(run_application())
        else:
            app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
    except Exception as e:
        logger.error(f"Error starting bot: {e}")

if __name__ == "__main__":
    main()
