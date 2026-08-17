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
    except Exception as e:
        logger.warning(f"Failed to alert admin: {e}")

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
            InlineKeyboardButton("🔢 חיפוש (לפי מס')", callback_data="admin_video_search"),
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

    # Welcome guide for new users
    users = load_json(USERS_FILE)
    if str(user.id) not in users or not users[str(user.id)].get("seen_guide"):
        guide = """📖 *מדריך קצר למשתמש החדש:*

💰 *איך משיגים סרטונים?*
• לוחצים על '🎁 מתנה יומית' ומקבלים בונוס כל יום!
• מזמינים חברים דרך '👥 הפניות שלי' ומקבלים מטבע על כל חבר.
• קונים חבילות מטבעות דרך '💳 תשלום'.

🎬 *איך צופים בתכנים?*
ברגע שיש לך מספיק מטבעות, תוכל לרכוש סרטונים והם יישלחו אליך מיד כאן בצ'אט!

💬 בכל שאלה, כפתור 'תמיכה' זמין עבורך."""
        await update.message.reply_text(guide, parse_mode="Markdown")
        if str(user.id) in users:
            users[str(user.id)]["seen_guide"] = True
            save_json(USERS_FILE, users)


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

async def daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    users = load_json(USERS_FILE)
    user_data = users.get(uid, {})
    
    # Use timestamp for exact 24h timer
    last_bonus_ts = user_data.get("last_bonus_ts", 0)
    now_ts = time.time()
    
    if now_ts - last_bonus_ts < 24 * 3600:
        remaining = int(24 * 3600 - (now_ts - last_bonus_ts))
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        await query.answer(f"⏳ נשאר עוד {hours} שעות ו-{minutes} דקות לקבלת המתנה הבאה!", show_alert=True)
        return
        
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
            [InlineKeyboardButton(f"🪙 שלם במטבעות (יתרה: {balance})",      callback_data="coins_menu")],
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
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזור", callback_data="back_admin")]]),
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
    buttons.append([InlineKeyboardButton("🔙 חזור", callback_data="back_admin")])
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
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזור", callback_data="back_admin")]]),
        )
        return

    idx = max(0, min(idx, total - 1))
    uid = uid_list[idx]
    u   = users[uid]
    bal = coins_d.get(uid, 0)
    vip = get_user_vip(uid)
    ref_cnt   = refs.get(uid, {}).get("count", 0)
    user_ords = [o for o in orders if str(o.get("user_id")) == uid]
    spent     = sum(o.get("amount", 0) for o in user_ords)

    text = (
        f"👤 *כרטיס משתמש {idx+1}/{total}*\n\n"
        f"📛 שם: {u.get('first_name', 'N/A')}\n"
        f"🆔 ID: `{uid}`\n"
        f"👑 דרגה: {vip['icon']} {vip['name']}\n"
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
    buttons.append([InlineKeyboardButton("🔙 חזור", callback_data="back_admin")])
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
    vip       = get_user_vip(target_id)
    ref_cnt   = refs.get(target_id, {}).get("count", 0)
    user_ords = [o for o in orders if str(o.get("user_id")) == target_id]
    coin_n    = sum(1 for o in user_ords if o.get("type") == "coins")
    await update.message.reply_text(
        f"🔍 *דוח משתמש*\n\n👤 {u.get('first_name')}\n"
        f"🆔 ID: `{target_id}`\n"
        f"👑 דרגה: {vip['icon']} {vip['name']}\n"
        f"🪙 מטבעות: {bal}\n"
        f"👥 הפניות: {ref_cnt}\n"
        f"🛒 רכישות: {len(user_ords)} ({coin_n} במטבעות)\n"
        f"💰 סה\"כ הוציא: ₪{sum(o.get('amount',0) for o in user_ords)}",
        reply_markup=get_admin_inline_keyboard()
    )
    return ConversationHandler.END

# ─── Admin: send videos to user ──────────────────────────────────────────────

async def admin_send_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("📩 *שליחת הודעה למשתמש*\n\nרשום את ההודעה שברצונך לשלוח:", parse_mode="Markdown")
    return ADMIN_SEND_MSG

async def admin_send_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    msg = update.message.text.strip()
    context.user_data["admin_msg_text"] = msg
    await update.message.reply_text("שלח את ה-ID של המשתמש אליו תישלח ההודעה:")
    return ADMIN_SEND_ID

async def admin_send_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    uid   = update.message.text.strip()
    msg   = context.user_data.get("admin_msg_text", "")
    
    try:
        await context.bot.send_message(chat_id=int(uid), text=f"📩 *הודעה מהמנהל:*\n\n{msg}", parse_mode="Markdown")
        await update.message.reply_text(f"✅ ההודעה נשלחה בהצלחה למשתמש {uid}!", reply_markup=get_admin_inline_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ השליחה נכשלה: {str(e)}", reply_markup=get_admin_inline_keyboard())
    
    return ConversationHandler.END

# ─── Admin: approve payment ───────────────────────────────────────────────────

async def admin_approve_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("✅ *אישור תשלום ידני*\n\nכמה סרטונים לשלוח למשתמש?", parse_mode="Markdown")
    return ADMIN_APPROVE_COUNT

async def admin_approve_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        count = int(update.message.text.strip())
        context.user_data["approve_v_count"] = count
    except ValueError:
        await update.message.reply_text("❌ מספר לא תקין.")
        return ADMIN_APPROVE_COUNT
    await update.message.reply_text("שלח את ה-ID של המשתמש לאישור:")
    return ADMIN_APPROVE_ID

async def admin_approve_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    uid   = update.message.text.strip()
    count = context.user_data.get("approve_v_count", 0)
    
    try:
        sent = await send_videos_to_user(context, int(uid), count)
        if sent > 0:
            record_order(int(uid), 0, sent, "manual")
            await update.message.reply_text(f"✅ בוצע! {sent} סרטונים נשלחו למשתמש {uid}.", reply_markup=get_admin_inline_keyboard())
            try:
                await context.bot.send_message(chat_id=int(uid), text=f"✅ התשלום שלך אושר! {sent} סרטונים נשלחו אליך. תהנה!")
            except Exception:
                pass
        else:
            await update.message.reply_text("❌ השליחה נכשלה. וודא שה-ID תקין ויש סרטונים במאגר.", reply_markup=get_admin_inline_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {str(e)}", reply_markup=get_admin_inline_keyboard())
        
    return ConversationHandler.END

# ─── Admin: gallery ───────────────────────────────────────────────────────────


# ─── Admin: Enhanced Gallery & Media Management ────────────────────────────────

async def admin_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    
    keyboard = [
        [InlineKeyboardButton("🎬 עיון בספריה", callback_data="vid_page_0")],
        [
            InlineKeyboardButton("🔎 מצא כפילויות", callback_data="admin_dup_scan"),
            InlineKeyboardButton("🔄 מצא כפילויות מחדש", callback_data="admin_dup_rescan"),
        ],
        [InlineKeyboardButton("🗑 סל מיחזור", callback_data="admin_trash_page_0")],
        [InlineKeyboardButton("📤 שלח את כל הסרטונים", callback_data="vid_send_all")],
        [InlineKeyboardButton("🔍 חיפוש לפי שניות", callback_data="admin_search_sec_start")],
        [InlineKeyboardButton("🔙 חזור לפאנל", callback_data="back_admin")]
    ]
    
    text = """🎬 *ניהול גלריה ומדיה*

כאן תוכל לנהל את כל הסרטונים בבוט, לאתר כפילויות ולשחזר סרטונים שנמחקו."""
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_gallery_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=None):
    query = update.callback_query
    if page is None:
        page = int(query.data.split("vid_page_")[1])
    
    # Cleanup previous review media
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

def find_duplicate_groups(include_reviewed: bool) -> list[list[dict]]:
    """Find duration-based duplicate groups, optionally including manually reviewed groups."""
    videos = load_videos_with_entry_ids()
    groups = {}
    for video in videos:
        duration = video.get("duration", 0)
        if not duration:
            continue
        groups.setdefault(duration, []).append(video)

    duplicate_groups = [group for group in groups.values() if len(group) > 1]
    duplicate_groups.sort(key=lambda group: (group[0].get("duration", 0), len(group)))
    if include_reviewed:
        return duplicate_groups

    reviewed = reviewed_non_duplicate_signatures()
    return [group for group in duplicate_groups if duplicate_group_signature(group) not in reviewed]


async def show_duplicate_scan(update: Update, context: ContextTypes.DEFAULT_TYPE, include_reviewed: bool):
    query = update.callback_query
    duplicate_groups = find_duplicate_groups(include_reviewed=include_reviewed)
    context.user_data["dup_groups"] = duplicate_groups

    if not duplicate_groups:
        if include_reviewed:
            text = "✅ לא נמצאו סרטונים חשודים ככפולים לפי אורך, גם לאחר סריקה מלאה מחדש."
        else:
            text = "✅ לא נמצאו חשדות חדשים. קבוצות שסומנו ידנית כלא כפולות אינן מוצגות כאן."
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לגלריה", callback_data="admin_gallery")]]),
        )
        return

    await admin_dup_page(update, context, 0)


async def admin_dup_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_duplicate_scan(update, context, include_reviewed=False)


async def admin_dup_rescan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cleared_count = clear_not_duplicate_marks()
    await show_duplicate_scan(update, context, include_reviewed=True)
    if cleared_count:
        logger.info(f"Full duplicate rescan cleared {cleared_count} manual non-duplicate marks")


async def admin_dup_mark_not_duplicate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    page = int(query.data.replace("dup_mark_", ""))
    groups = context.user_data.get("dup_groups", [])
    if not (0 <= page < len(groups)):
        await query.answer("הקבוצה כבר אינה זמינה. יש לבצע סריקה מחדש.", show_alert=True)
        return

    group = groups[page]
    was_added = mark_group_as_not_duplicate(group)
    await clear_sent_duplicate_group_media(context)
    groups.pop(page)
    context.user_data["dup_groups"] = groups
    await query.answer(
        "✅ הקבוצה סומנה כלא כפולה ולא תופיע בסריקה הרגילה."
        if was_added else "✅ הקבוצה כבר סומנה כלא כפולה.",
        show_alert=True,
    )

    if groups:
        await admin_dup_page(update, context, min(page, len(groups) - 1))
        return

    await query.edit_message_text(
        "✅ הקבוצה סומנה כלא כפולה. אין עוד חשדות פתוחים בסריקה זו.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לגלריה", callback_data="admin_gallery")]]),
    )

def duplicate_group_keyboard(page: int, total: int) -> InlineKeyboardMarkup:
    """Build the same management controls for a duplicate group before and after media delivery."""
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton("⬅️ הקודם", callback_data=f"dup_page_{page - 1}"))
    navigation.append(InlineKeyboardButton(f"{page + 1}/{total}", callback_data="noop"))
    if page < total - 1:
        navigation.append(InlineKeyboardButton("הבא ➡️", callback_data=f"dup_page_{page + 1}"))

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 שלח חשדות (סרטונים אלו)", callback_data=f"dup_send_{page}")],
        [
            InlineKeyboardButton("✅ סמן כלא כפול", callback_data=f"dup_mark_{page}"),
            InlineKeyboardButton("🔙 חזרה לגלריה", callback_data="dup_back_gallery"),
        ],
        navigation,
    ])


async def clear_sent_duplicate_group_media(context: ContextTypes.DEFAULT_TYPE) -> int:
    """Remove only videos previously sent for the duplicate group being left."""
    message_ids = context.user_data.pop("dup_sent_media_message_ids", [])
    removed_count = 0
    for message_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=ADMIN_ID, message_id=message_id)
            removed_count += 1
        except Exception as exc:
            # A video may already have been deleted manually; this must not block navigation.
            logger.info(f"Could not remove prior duplicate-review media message {message_id}: {exc}")
    return removed_count


async def admin_dup_back_to_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await clear_sent_duplicate_group_media(context)
    await admin_gallery(update, context)


async def admin_dup_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=None):
    query = update.callback_query
    if page is None:
        page = int(query.data.split("dup_page_")[1])
        # Moving back or forward means the previous group is no longer being reviewed.
        await clear_sent_duplicate_group_media(context)

    groups = context.user_data.get("dup_groups", [])
    total = len(groups)

    if page >= total:
        await query.edit_message_text(
            "✅ סיימת לעבור על כל הכפילויות!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="dup_back_gallery")]]),
        )
        return

    group = groups[page]
    duration = group[0].get("duration", 0)
    text = f"""🔎 *חשד לכפילות ({page + 1}/{total})*

⏱ אורך משותף: {duration} שניות
👥 מספר סרטונים בקבוצה: {len(group)}

לחץ על הכפתור למטה כדי לשלוח את הסרטונים לבדיקה ולמחיקה."""
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=duplicate_group_keyboard(page, total),
    )


async def admin_dup_send_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.replace("dup_send_", ""))
    groups = context.user_data.get("dup_groups", [])
    if not (0 <= page < len(groups)):
        await context.bot.send_message(chat_id=ADMIN_ID, text="⚠️ קבוצת הכפילויות כבר אינה זמינה. יש לבצע 'מצא כפילויות' מחדש.")
        return

    # Re-sending the same group must not leave an older copy of its review videos in the chat.
    await clear_sent_duplicate_group_media(context)
    group = groups[page]
    success_count = 0
    failed_count = 0
    sent_message_ids = []

    for video in group:
        entry_id = video.get("entry_id")
        if not entry_id:
            logger.error("Duplicate group contains a video without entry_id")
            failed_count += 1
            continue

        sent_message = await send_admin_video_with_delete_button(context.bot, video["file_id"], entry_id)
        if sent_message:
            success_count += 1
            sent_message_ids.append(sent_message.message_id)
        else:
            failed_count += 1
        # Stay below the per-chat delivery rate and let the retry handler manage 429 responses.
        await asyncio.sleep(1.1)

    context.user_data["dup_sent_media_message_ids"] = sent_message_ids
    status = f"✅ נשלחו {success_count}/{len(group)} סרטונים חשודים. לחץ על סרטון כדי למחוק אותו, או המשך לקבוצה הבאה."
    if failed_count:
        status += f" ⚠️ {failed_count} סרטונים לא נשלחו; הפרטים נרשמו בלוגים."
    await query.edit_message_text(
        status,
        reply_markup=duplicate_group_keyboard(page, len(groups)),
    )

async def admin_gallery_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    videos = load_json(VIDEOS_FILE)
    trash = load_json(TRASH_FILE)
    
    if "del_eid_" in query.data:
        eid = query.data.replace("del_eid_", "")
        to_delete = [v for v in videos if v.get("entry_id") == eid]
        if to_delete:
            target_v = to_delete[0]
            # Remove ONLY this specific entry by entry_id
            videos = [vi for vi in videos if vi.get("entry_id") != eid]
            target_v["deleted_at"] = str(datetime.now())
            trash.append(target_v)
            save_json(VIDEOS_FILE, videos)
            save_json(TRASH_FILE, trash)
            await query.answer("הסרטון הספציפי הועבר לסל המיחזור!", show_alert=True)
            try: await query.message.delete() 
            except: pass
    elif "vid_del_id_" in query.data or "del_v_" in query.data:
        fid_part = query.data.replace("vid_del_id_", "").replace("del_v_", "")
        to_delete = [v for v in videos if v["file_id"].startswith(fid_part)]
        if to_delete:
            target_v = to_delete[0]
            # Remove ONLY the first matching instance to avoid wiping all identical file_ids
            removed = False
            new_videos = []
            for vi in videos:
                if not removed and vi["file_id"] == target_v["file_id"]:
                    removed = True
                else:
                    new_videos.append(vi)
            videos = new_videos
            target_v["deleted_at"] = str(datetime.now())
            trash.append(target_v)
            save_json(VIDEOS_FILE, videos)
            save_json(TRASH_FILE, trash)
            await query.answer("הסרטון הועבר לסל המיחזור!", show_alert=True)
            try: await query.message.delete() 
            except: pass
    else:
        idx = int(query.data.split("vid_del_")[1])
        if 0 <= idx < len(videos):
            v = videos.pop(idx)
            v["deleted_at"] = str(datetime.now())
            trash.append(v)
            save_json(VIDEOS_FILE, videos)
            save_json(TRASH_FILE, trash)
            await query.answer("הסרטון הועבר לסל המיחזור!", show_alert=True)
            if videos: await admin_gallery_page(update, context, 0)
            else: await query.edit_message_text("המאגר ריק.", reply_markup=get_admin_inline_keyboard())

async def admin_trash_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=None):
    query = update.callback_query
    await query.answer()
    if page is None:
        page = int(query.data.split("admin_trash_page_")[1])
    
    # Cleanup previous video
    await clear_sent_duplicate_group_media(context)
    
    trash = load_json(TRASH_FILE)
    total = len(trash)
    
    if total == 0:
        await query.edit_message_text("🗑 סל המיחזור ריק.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_gallery")]]))
        return

    page = max(0, min(page, total - 1))
    v = trash[page]
    
    text = f"""🗑 *סל מיחזור ({page+1}/{total})*

⏱ אורך: {v.get('duration', 0)} שניות
📅 נמחק ב: {v.get('deleted_at', 'לא ידוע')}"""
    
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"admin_trash_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total}", callback_data="noop"))
    if page < total - 1: nav.append(InlineKeyboardButton("➡️", callback_data=f"admin_trash_page_{page+1}"))
    
    btns = [
        nav,
        [InlineKeyboardButton("♻️ שחזר סרטון", callback_data=f"trash_restore_{page}")],
        [InlineKeyboardButton("🗑 מחק לצמיתות", callback_data=f"trash_perm_{page}")],
        [InlineKeyboardButton("🧹 רוקן סל מיחזור", callback_data="trash_empty")],
        [InlineKeyboardButton("🔙 חזרה", callback_data="admin_gallery")]
    ]
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    sent_msg = await context.bot.send_video(chat_id=ADMIN_ID, video=v["file_id"])
    if sent_msg:
        context.user_data["dup_sent_media_message_ids"] = [sent_msg.message_id]

async def admin_trash_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    idx = int(query.data.replace("trash_restore_", ""))
    trash = load_json(TRASH_FILE)
    videos = load_json(VIDEOS_FILE)
    
    if 0 <= idx < len(trash):
        v = trash.pop(idx)
        videos.append(v)
        save_json(TRASH_FILE, trash)
        save_json(VIDEOS_FILE, videos)
        await query.answer("✅ הסרטון שוחזר בהצלחה!", show_alert=True)
        await admin_trash_page(update, context, 0)

async def admin_trash_perm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    idx = int(query.data.replace("trash_perm_", ""))
    trash = load_json(TRASH_FILE)
    
    if 0 <= idx < len(trash):
        trash.pop(idx)
        save_json(TRASH_FILE, trash)
        await query.answer("🗑 נמחק לצמיתות.", show_alert=True)
        await admin_trash_page(update, context, 0)

async def admin_trash_empty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_json(TRASH_FILE, [])
    await update.callback_query.answer("🧹 סל המיחזור רוקן!", show_alert=True)
    await admin_gallery(update, context)

# At most one manual "send all" run may operate for a chat at the same time.
SEND_ALL_TASKS: dict[int, asyncio.Task] = {}


async def run_send_all_videos(bot, chat_id: int):
    """Send the complete library with per-video delete controls in the bot's running event loop."""
    success_count = 0
    total = 0
    try:
        videos = load_videos_with_entry_ids()
        if not videos:
            await bot.send_message(chat_id=chat_id, text="אין סרטונים במאגר.")
            return

        sorted_videos = sorted(videos, key=lambda video: video.get("duration", 0))
        total = len(sorted_videos)
        logger.info(f"Manual send-all started for chat {chat_id}: {total} videos")
        await bot.send_message(
            chat_id=chat_id,
            text=f"📤 התחילה שליחה ידנית של {total} סרטונים. תקבל עדכון כל 50 סרטונים ובסיום.",
        )
        await asyncio.sleep(1.1)

        for index, video in enumerate(sorted_videos, start=1):
            try:
                if await send_admin_video_with_delete_button(bot, video["file_id"], video["entry_id"]):
                    success_count += 1
                await asyncio.sleep(1.1)

                if index % 50 == 0:
                    await bot.send_message(chat_id=chat_id, text=f"⏳ התקדמות: נשלחו {index} מתוך {total} סרטונים...")
                    logger.info(f"Manual send-all progress for chat {chat_id}: {index}/{total}")
                    await asyncio.sleep(1.1)
            except Exception as exc:
                logger.exception(f"Failed to send video {index}/{total} during manual send-all: {exc}")
                await asyncio.sleep(2.0)

        await bot.send_message(
            chat_id=chat_id,
            text=f"✅ סיימתי לשלוח את כל הסרטונים! (נשלחו בהצלחה: {success_count}/{total})",
            reply_markup=get_admin_inline_keyboard(),
        )
        logger.info(f"Manual send-all completed for chat {chat_id}: {success_count}/{total} videos")
    except Exception as exc:
        logger.exception(f"Manual send-all stopped unexpectedly for chat {chat_id}: {exc}")
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ שליחת כל הסרטונים נעצרה לאחר {success_count}/{total} סרטונים. ניתן להפעיל אותה שוב ידנית.",
            )
        except Exception:
            pass
    finally:
        SEND_ALL_TASKS.pop(chat_id, None)


async def admin_gallery_send_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """שליחה מהירה ורציפה של כל המאגר לפי אורך (מהקצר לארוך)."""
    query = update.callback_query
    await query.answer()
    
    videos = load_videos_with_entry_ids()
    if not videos:
        await query.edit_message_text("אין סרטונים במאגר.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_gallery")]]))
        return

    sorted_videos = sorted(videos, key=lambda v: v.get("duration", 0))
    total = len(sorted_videos)
    
    await query.edit_message_text(f"🚀 שולח כעת את כל {total} הסרטונים ברצף מהיר מהקצר לארוך...")
    
    success = 0
    for v in sorted_videos:
        try:
            sent = await send_admin_video_with_delete_button(context.bot, v["file_id"], v["entry_id"])
            if sent:
                success += 1
            await asyncio.sleep(0.15) # שליחה מהירה אך בטוחה
        except Exception:
            await asyncio.sleep(1.0)
            
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"✅ סיימתי לשלוח את המאגר! ({success}/{total} נשלחו בהצלחה)",
        reply_markup=get_admin_inline_keyboard()
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
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול / חזור", callback_data="back_admin")]]),
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
    v = videos[idx]
    await update.message.reply_text(f"🎬 סרטון {num}/{len(videos)}:")
    await context.bot.send_video(
        chat_id=ADMIN_ID,
        video=v["file_id"],
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"🗑 מחק סרטון {num}", callback_data=f"vid_del_{idx}")]]),
    )
    await update.message.reply_text("חפש עוד סרטון או לחץ ביטול:", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

# ─── Admin: video search by seconds ───────────────────────────────────────────

async def admin_search_sec_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text(
        "⏱ *חיפוש סרטונים לפי שניות*\n\nשלח את מספר השניות לחיפוש (למשל `26`):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="admin_gallery")]])
    )
    return ADMIN_VIDEO_SEARCH_SECONDS

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

async def admin_search_sec_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    text = update.message.text.strip()
    if text.lower() == "cancel":
        await update.message.reply_text("החיפוש בוטל.", reply_markup=get_admin_inline_keyboard())
        return ConversationHandler.END
        
    seconds = parse_smart_time(text)
    if seconds < 0:
        await update.message.reply_text("❌ פורמט לא תקין. השתמש במספר (שניות) או דקות:שניות (למשל 1:20).")
        return ADMIN_VIDEO_SEARCH_SECONDS
    
    videos = load_json(VIDEOS_FILE)
    results = [v for v in videos if v.get("duration") == seconds]
    
    if not results:
        await update.message.reply_text(f"❌ לא נמצאו סרטונים באורך {text}.", reply_markup=get_admin_inline_keyboard())
        return ConversationHandler.END
    
    await update.message.reply_text(f"🔎 נמצאו {len(results)} סרטונים באורך {text}. שולח כעת...")
    
    success = 0
    for v in results:
        try:
            sent = await send_admin_video_with_delete_button(context.bot, v["file_id"], v["entry_id"])
            if sent:
                success += 1
            await asyncio.sleep(0.3)
        except Exception:
            pass
    
    await update.message.reply_text(f"✅ סיימתי לשלוח את תוצאות החיפוש ({success}/{len(results)} נשלחו).", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

# ─── Admin: Category Management ───────────────────────────────────────────

async def admin_categories_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    settings = load_settings()
    cats = settings.get("categories", ["כללי"])
    text = "📂 *ניהול קטגוריות*\n\nהקטגוריות הקיימות:\n" + "\n".join([f"• {c}" for c in cats])
    
    btns = [
        [InlineKeyboardButton("➕ הוסף קטגוריה", callback_data="admin_cat_add")],
        [InlineKeyboardButton("🏷 מיון סרטונים", callback_data="admin_cat_sort_start")],
        [InlineKeyboardButton("🔙 חזרה לגלריה", callback_data="admin_gallery")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def admin_cat_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✍️ שלח את שם הקטגוריה החדשה:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="admin_categories")]]))
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
    
    text = f"🏷 *מיון סרטונים ({page+1}/{len(videos)})*\n\nקטגוריה נוכחית: {v.get('category', 'כללי')}\nבחר קטגוריה חדשה לסרטון זה:"
    
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
    # Format: cat_assign_PAGE_CATNAME
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
            await query.edit_message_text("✅ סיימת למיין את כל הסרטונים!", reply_markup=get_admin_inline_keyboard())


# ─── Admin: broadcast (enhanced + media) ──────────────────────────────────────

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("📢 *הודעה לכולם*\n\nשלח את תוכן ההודעה (טקסט בלבד):", parse_mode="Markdown")
    return ADMIN_BROADCAST

async def admin_broadcast_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    context.user_data["broadcast_msg"] = update.message.text
    await update.message.reply_text(
        "🖼 *הוספת מדיה (אופציונלי)*\n\nשלח תמונה או סרטון, או שלח `skip` לדלג:",
        parse_mode="Markdown",
    )
    return ADMIN_BROADCAST_MEDIA

async def admin_broadcast_get_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    if update.message.photo:
        context.user_data["broadcast_media"] = ("photo", update.message.photo[-1].file_id)
    elif update.message.video:
        context.user_data["broadcast_media"] = ("video", update.message.video.file_id)
    else:
        context.user_data["broadcast_media"] = None
        
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
    media  = context.user_data.get("broadcast_media")
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
            if media:
                m_type, f_id = media
                if m_type == "photo":
                    await context.bot.send_photo(chat_id=int(uid), photo=f_id, caption=msg, reply_markup=markup)
                else:
                    await context.bot.send_video(chat_id=int(uid), video=f_id, caption=msg, reply_markup=markup)
            else:
                await context.bot.send_message(chat_id=int(uid), text=msg, reply_markup=markup)
            sent += 1
        except Exception as e:
            logger.warning(f"Failed to send broadcast to {uid}: {e}")
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

# ─── Admin: VIP management ────────────────────────────────────────────────────

async def admin_vip_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("💎 *ניהול דרגות VIP*\n\nשלח את ה-ID של המשתמש:", parse_mode="Markdown")
    return ADMIN_VIP_ID

async def admin_vip_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        uid = str(int(update.message.text.strip()))
        context.user_data["vip_target_id"] = uid
    except ValueError:
        await update.message.reply_text("❌ ID לא תקין.")
        return ConversationHandler.END
    
    users = load_json(USERS_FILE)
    if uid not in users:
        await update.message.reply_text("❌ משתמש לא נמצא במערכת.")
        return ConversationHandler.END
        
    user = users[uid]
    vip  = get_user_vip(uid)
    
    keyboard = []
    for i, level in enumerate(VIP_LEVELS):
        keyboard.append([InlineKeyboardButton(f"{level['icon']} {level['name']}", callback_data=f"set_vip_{i}")])
    keyboard.append([InlineKeyboardButton("❌ ביטול / חזור", callback_data="back_admin")])
    
    await update.message.reply_text(
        f"👤 *משתמש:* {user.get('first_name', 'לא ידוע')}\n"
        f"🆔 *ID:* `{uid}`\n"
        f"💎 *דרגה נוכחית:* {vip['icon']} {vip['name']}\n"
        f"🧾 *רכישות:* {user.get('purchases', 0)}\n\n"
        "בחר את הדרגה החדשה:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ADMIN_VIP_LEVEL

async def admin_vip_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
        
    try:
        level_idx = int(query.data.replace("set_vip_", ""))
        new_level = VIP_LEVELS[level_idx]
        uid = context.user_data.get("vip_target_id")
        
        users = load_json(USERS_FILE)
        if uid in users:
            # שינוי הדרגה מתבצע על ידי עדכון מספר הרכישות המינימלי הנדרש לאותה דרגה
            users[uid]["purchases"] = new_level["min_purchases"]
            save_json(USERS_FILE, users)
            
            await query.edit_message_text(
                f"✅ הדרגה של משתמש `{uid}` עודכנה בהצלחה ל-*{new_level['icon']} {new_level['name']}*!",
                parse_mode="Markdown",
                reply_markup=get_admin_inline_keyboard()
            )
            
            # שליחת הודעה למשתמש על עדכון הדרגה
            try:
                await context.bot.send_message(
                    chat_id=int(uid),
                    text=f"🎊 *חדשות טובות!*\nהמנהל עדכן את הדרגה שלך ל-*{new_level['icon']} {new_level['name']}*!\n\nתהנה מההנחות וההטבות החדשות! 💎",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
        else:
            await query.edit_message_text("❌ שגיאה: המשתמש לא נמצא.")
            
    except Exception as e:
        await query.edit_message_text(f"❌ שגיאה בעדכון הדרגה: {str(e)}")
        
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
    btns.append([InlineKeyboardButton("🔙 חזור", callback_data="back_admin")])
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
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול / חזור", callback_data="back_admin")]]),
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
    context.user_data.pop("pending_restore", None)
    await query.edit_message_text(
        "📥 *שחזור מגיבוי*\n\n"
        "שלח קובץ ZIP של גיבוי הנתונים. הקובץ ייבדק תחילה ולא ישכתב דבר עד שתאשר ידנית.\n"
        "מגבלת קובץ: 20MB.\n\n"
        "⚠️ לאחר האישור, הנתונים הקיימים יוחלפו — אך תקבל קודם גיבוי חירום של המצב הנוכחי.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול / חזור", callback_data="back_admin")]]),
    )
    return ADMIN_RESTORE


async def admin_restore_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    doc = update.message.document
    if not doc or not (doc.file_name or "").lower().endswith(".zip"):
        await update.message.reply_text("❌ שלח קובץ ZIP בלבד.")
        return ADMIN_RESTORE
    if doc.file_size and doc.file_size > MAX_RESTORE_ARCHIVE_BYTES:
        await update.message.reply_text("❌ קובץ הגיבוי גדול מדי. המגבלה היא 20MB.")
        return ADMIN_RESTORE

    await update.message.reply_text("⏳ בודק את הגיבוי — עדיין לא בוצע שחזור...")
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        payloads = parse_restore_archive(buf.getvalue())
        context.user_data["pending_restore"] = payloads
        await update.message.reply_text(
            "🔎 *תצוגה מקדימה של הגיבוי*\n\n"
            f"{restore_summary(payloads)}\n\n"
            "הנתונים עדיין לא שונו. לחץ על *אשר שחזור* כדי לבצע החלפה, או על ביטול.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ אשר שחזור", callback_data="admin_restore_apply")],
                [InlineKeyboardButton("❌ ביטול / חזור", callback_data="back_admin")],
            ]),
        )
        return ADMIN_RESTORE_CONFIRM
    except zipfile.BadZipFile:
        await update.message.reply_text("❌ קובץ ZIP פגום.")
    except ValueError as exc:
        await update.message.reply_text(f"❌ הגיבוי לא התקבל: {exc}")
    except Exception as exc:
        logger.exception("Backup preview failed")
        await update.message.reply_text("❌ אירעה שגיאה בבדיקת הגיבוי. הנתונים לא שונו.")
    return ADMIN_RESTORE


async def admin_restore_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    payloads = context.user_data.get("pending_restore")
    if not isinstance(payloads, dict) or not payloads:
        await query.edit_message_text(
            "❌ אין גיבוי מוכן לשחזור. שלח את קובץ הגיבוי מחדש.",
            reply_markup=get_admin_inline_keyboard(),
        )
        return ConversationHandler.END

    try:
        snapshot = build_zip_of_data()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        await context.bot.send_document(
            chat_id=ADMIN_ID,
            document=snapshot,
            filename=f"before_restore_{stamp}.zip",
            caption="💾 גיבוי חירום אוטומטי לפני שחזור",
        )
        apply_restore_payloads(payloads)
        context.user_data.pop("pending_restore", None)
        await query.edit_message_text(
            "✅ *השחזור הושלם בהצלחה!*\n\n"
            f"{restore_summary(payloads)}\n\n"
            "נשלח אליך גם גיבוי חירום של המצב שהיה לפני השחזור.",
            parse_mode="Markdown",
            reply_markup=get_admin_inline_keyboard(),
        )
    except Exception:
        logger.exception("Backup restore failed")
        await query.edit_message_text(
            "❌ אירעה שגיאה בשחזור. הנתונים לא אושרו כהושלמו; השתמש בגיבוי החירום שנשלח לפני הפעולה אם נדרש.",
            reply_markup=get_admin_inline_keyboard(),
        )
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
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול / חזור", callback_data="back_admin")]]),
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
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזור", callback_data="back_admin")]]),
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
                        text="📢 *הבוט חזר לפעילות!*\n\nלחצו על /start כדי להתחיל! ✅",
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
        [InlineKeyboardButton("🔙 חזור", callback_data="back_admin")]
    ]
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ─── Video upload ─────────────────────────────────────────────────────────────

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """העלאת סרטון עם בחירת קטגוריה ודוגמה."""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    video = update.message.video
    if not video:
        return ConversationHandler.END

    context.user_data["last_upload_fid"] = video.file_id
    context.user_data["last_upload_fuid"] = getattr(video, "file_unique_id", None)
    context.user_data["last_upload_dur"] = video.duration
    context.user_data["last_upload_size"] = video.file_size
    
    settings = load_settings()
    cats = settings.get("categories", ["כללי"])
    btns = []
    row = []
    for c in cats:
        row.append(InlineKeyboardButton(c, callback_data=f"cat_sel_{c}"))
        if len(row) == 2:
            btns.append(row)
            row = []
    if row: btns.append(row)
    
    await update.message.reply_text(
        f"🎬 סרטון התקבל! (אורך: {video.duration} שניות)\nבחר קטגוריה לסרטון:",
        reply_markup=InlineKeyboardMarkup(btns)
    )
    return ADMIN_VIDEO_CAT

async def admin_video_cat_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat = query.data.replace("cat_sel_", "")
    context.user_data["last_upload_cat"] = cat
    
    await query.edit_message_text(
        f"✅ קטגוריה: *{cat}*\n\nשלח עכשיו תמונה או סרטון קצר כ**דוגמה (Preview)**, או שלח `skip` לדלג:",
        parse_mode="Markdown"
    )
    return ADMIN_VIDEO_PREVIEW

async def admin_video_preview_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    preview = None
    if update.message.photo:
        preview = ("photo", update.message.photo[-1].file_id)
    elif update.message.video:
        preview = ("video", update.message.video.file_id)
    
    fid = context.user_data.get("last_upload_fid")
    fuid = context.user_data.get("last_upload_fuid")
    dur = context.user_data.get("last_upload_dur")
    size = context.user_data.get("last_upload_size")
    cat = context.user_data.get("last_upload_cat")
    
    videos = load_json(VIDEOS_FILE)
    entry_id = str(uuid.uuid4())[:8]
    videos.append({
        "entry_id": entry_id,
        "file_id": fid,
        "file_unique_id": fuid,
        "duration": dur,
        "file_size": size,
        "category": cat,
        "preview": preview,
        "added_at": str(datetime.now())
    })
    save_json(VIDEOS_FILE, videos)
    
    await update.message.reply_text(
        f"✅ הסרטון נשמר בהצלחה!\n📂 קטגוריה: {cat}\n⏱ אורך: {dur} שניות\n🖼 דוגמה: {'כן' if preview else 'לא'}",
        reply_markup=get_admin_inline_keyboard()
    )
    return ConversationHandler.END

# ─── Utility ──────────────────────────────────────────────────────────────────

async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Ignore Telegram's harmless no-op edit error while retaining actionable errors."""
    error = context.error
    if isinstance(error, BadRequest) and "Message is not modified" in str(error):
        logger.info("Ignored a no-op Telegram message edit request")
        return
    logger.error("Unhandled Telegram update error", exc_info=error)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ בוטל.", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END


# ─── Automated Admin Backup Scheduler ──────────────────────────────────────────

async def send_automated_backup(context: ContextTypes.DEFAULT_TYPE):
    try:
        buf = build_zip_of_data()
        today_str = str(date.today())
        await context.bot.send_document(
            chat_id=ADMIN_ID,
            document=buf,
            filename=f"bot_backup_{today_str}.zip",
            caption=f"""💾 *גיבוי אוטומתי יומי*
📅 תאריך: {today_str}""",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning(f"Failed to send automated backup: {e}")

def setup_automated_backup(application):
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(send_automated_backup, interval=86400, first=10)

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
    logger.info("Starting main function...")
    logger.info(f"Token present: {bool(TOKEN)}")
    logger.info(f"Admin ID: {ADMIN_ID}")
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
        states={
            ADMIN_RESTORE: [MessageHandler(filters.Document.ALL, admin_restore_receive)],
            ADMIN_RESTORE_CONFIRM: [
                CallbackQueryHandler(admin_restore_apply, pattern="^admin_restore_apply$"),
                CallbackQueryHandler(back_admin, pattern="^back_admin$"),
            ],
        },
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
    video_search_sec_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_search_sec_start, pattern="^admin_search_sec_start$")],
        states={ADMIN_VIDEO_SEARCH_SECONDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_search_sec_input)]},
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(admin_gallery, pattern="^admin_gallery$"),
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
    cat_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_cat_add_start, pattern="^admin_cat_add$")],
        states={ADMIN_VIDEO_CAT_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_cat_add_input)]},
        fallbacks=[CallbackQueryHandler(admin_categories_menu, pattern="^admin_categories$")],
        per_message=False, per_chat=True,
    )
    video_upload_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.VIDEO, handle_video)],
        states={
            ADMIN_VIDEO_CAT: [CallbackQueryHandler(admin_video_cat_sel, pattern="^cat_sel_")],
            ADMIN_VIDEO_PREVIEW: [
                MessageHandler(filters.PHOTO | filters.VIDEO | filters.Regex("^skip$"), admin_video_preview_receive)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False, per_chat=True,
    )
    
    for conv in [
        check_conv, send_conv, approve_conv, broadcast_conv, coins_conv, vip_conv,
        coupon_new_conv, multiplier_conv, restore_conv, global_reset_conv,
        video_search_conv, video_search_sec_conv, support_conv, coupon_redeem_conv, support_reply_conv,
        cat_add_conv, video_upload_conv
    ]:
        app.add_handler(conv)

    app.add_error_handler(telegram_error_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🛠 פאנל אדמין$"), admin_panel))

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
        ("^daily_bonus$",               daily_bonus),
        ("^vip_info$",                  vip_info),
        ("^back_main$",                 back_main),
        ("^admin_stats$",               admin_stats),
        (r"^admin_orders_page_\d+$",    admin_orders_page),
        (r"^users_page_\d+$",           users_page),
        ("^admin_gallery$",             admin_gallery),
        ("^admin_dup_scan$",             admin_dup_scan),
        ("^admin_dup_rescan$",           admin_dup_rescan),
        (r"^dup_page_\d+$",            admin_dup_page),
        (r"^dup_mark_\d+$",            admin_dup_mark_not_duplicate),
        ("^dup_back_gallery$",           admin_dup_back_to_gallery),
        (r"^dup_send_\d+$",            admin_dup_send_group),
        (r"^vid_del_id_",               admin_gallery_delete),
        (r"^del_v_",                    admin_gallery_delete),
        (r"^del_eid_",                  admin_gallery_delete),
        (r"^admin_trash_page_\d+$",    admin_trash_page),
        (r"^trash_restore_\d+$",       admin_trash_restore),
        (r"^trash_perm_\d+$",          admin_trash_perm),
        ("^trash_empty$",               admin_trash_empty),

        (r"^vid_page_\d+$",             admin_gallery_page),
        (r"^vid_del_\d+$",              admin_gallery_delete),
        ("^vid_send_all$",              admin_gallery_send_all),
        ("^admin_categories$",          admin_categories_menu),
        (r"^cat_sort_page_\d+$",       admin_cat_sort_page),
        ("^admin_cat_sort_start$",      lambda u, c: admin_cat_sort_page(u, c, 0)),
        (r"^cat_assign_",               admin_cat_assign),
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
        
        # הודעת חזרה לפעילות למשתמשים ששלחו הודעות כשהבוט היה כבוי
        async def notify_back_online():
            try:
                # נחכה רגע שהפולניג יתחיל לקבל עדכונים
                await asyncio.sleep(5)
                settings = load_settings()
                waiting = settings.get("waiting_users", [])
                if waiting:
                    logger.info(f"Sending restart notification to {len(waiting)} users...")
                    count_sent = 0
                    for uid in waiting:
                        try:
                            await app.bot.send_message(
                                chat_id=uid,
                                text="📢 *הבוט חזר לפעילות!*\n\nלחצו על /start כדי להתחיל! ✅",
                                parse_mode="Markdown"
                            )
                            count_sent += 1
                            await asyncio.sleep(0.05) # מניעת ספאם
                        except Exception:
                            pass
                    
                    # איפוס הרשימה
                    settings = load_settings()
                    settings["waiting_users"] = []
                    save_settings(settings)
                    logger.info(f"Restart notification sent to {count_sent} users.")
            except Exception as e:
                logger.error(f"Error in notify_back_online: {e}")

        def handle_polling_error(error):
            """Exit a superseded Render instance instead of leaving it alive without polling."""
            if isinstance(error, Conflict):
                logger.info(
                    "Polling conflict: this instance was superseded by another deployment; "
                    "exiting cleanly."
                )
                # During a Render rolling deployment Telegram terminates the previous long-poll.
                # A hard exit is deliberate: it prevents an HTTP-only process that no longer handles updates.
                time.sleep(10)
            logger.error("Recoverable polling error: %s", error)

        # הפעלת הפולינג

        # Start polling in a way that handles conflict by waiting
        while True:
            try:
                await app.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=False,
                )
                # Keep the async task alive
                while app.updater.running:
                    await asyncio.sleep(1)
            except Conflict:
                logger.info("Conflict detected, waiting 10s to retry...")
                await asyncio.sleep(10)
            except Exception as e:
                logger.error("Fatal polling error: %s", e)
                await asyncio.sleep(5)

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
