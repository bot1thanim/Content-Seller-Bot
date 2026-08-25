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
import re
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
    ApplicationHandlerStop,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.error import BadRequest, Conflict, RetryAfter, TimedOut, NetworkError
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

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
ADMIN_ACTIONS_FILE = DATA_DIR / "admin_actions.json"
DUPLICATE_REVIEWS_FILE = DATA_DIR / "duplicate_reviews.json"
AUTO_BACKUPS_DIR = DATA_DIR / "auto_backups"
MAX_ADMIN_ACTIONS = 2000
MAX_AUTO_BACKUPS = 30

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
    "admin_actions.json": list,
    "duplicate_reviews.json": list,
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
    ADMIN_REPAIR_UPLOAD,     # 30
    ADMIN_CATEGORY_RENAME,   # 31
    ADMIN_MANAGER_ADD_ID,    # 32
    ADMIN_ASSISTANT_COMMAND, # 33
) = range(34)


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
        (SETTINGS_FILE,  {"referral_multiplier": 1.0, "daily_gift_amount": 1, "referral_reward_amount": 1, "maintenance": False}),
        (TRASH_FILE,     []),
        (ADMIN_ACTIONS_FILE, []),
        (DUPLICATE_REVIEWS_FILE, []),
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


DEFAULT_CATEGORY = "רנדומלי"
LEGACY_DEFAULT_CATEGORY = "כללי"


def normalize_category_name(category: str) -> str:
    """Map the legacy default category to the current random-category name."""
    cleaned = str(category).strip()
    return DEFAULT_CATEGORY if cleaned == LEGACY_DEFAULT_CATEGORY else cleaned


def normalize_category_list(categories, alphabetical: bool = True) -> list[str]:
    """Normalize categories, optionally preserving the manually selected order."""
    cleaned = [
        normalize_category_name(category)
        for category in (categories if isinstance(categories, list) else [])
        if isinstance(category, str) and str(category).strip()
    ]
    if DEFAULT_CATEGORY not in cleaned:
        cleaned.append(DEFAULT_CATEGORY)
    unique = list(dict.fromkeys(cleaned))
    return sorted(unique, key=lambda category: category.casefold()) if alphabetical else unique


def video_categories(video: dict) -> list[str]:
    """Return normalized private category memberships, supporting legacy single-category data."""
    if not isinstance(video, dict):
        return [DEFAULT_CATEGORY]
    raw = video.get("categories")
    if isinstance(raw, list):
        categories = [normalize_category_name(item) for item in raw if isinstance(item, str) and item.strip()]
    else:
        legacy = video.get("category", LEGACY_DEFAULT_CATEGORY)
        categories = [normalize_category_name(legacy)] if isinstance(legacy, str) and legacy.strip() else []
    # Keep order stable while removing duplicates.
    unique = list(dict.fromkeys(categories))
    return unique or [DEFAULT_CATEGORY]


def normalize_video_categories(video: dict) -> bool:
    """Persist both multi-category data and the legacy primary category for compatibility."""
    if not isinstance(video, dict):
        return False
    categories = video_categories(video)
    changed = video.get("categories") != categories or video.get("category") != categories[0]
    video["categories"] = categories
    video["category"] = categories[0]
    return changed


def display_video_categories(video: dict) -> str:
    return ", ".join(video_categories(video))


def format_duration(seconds: int | float | None) -> str:
    """Display a stored duration in a concise, human-readable seconds or minutes:seconds form."""
    try:
        total_seconds = max(0, int(seconds or 0))
    except (TypeError, ValueError):
        total_seconds = 0
    minutes, remainder = divmod(total_seconds, 60)
    return f"{minutes}:{remainder:02d}" if minutes else f"{remainder} שניות"


def normalize_restored_videos(videos):
    """Convert legacy file-id data and normalize category memberships during restore."""
    if videos and all(isinstance(item, str) for item in videos):
        videos = [
            {"file_id": item, "duration": 0, "preview": None, "entry_id": uuid.uuid4().hex}
            for item in videos
        ]
    if isinstance(videos, list):
        for video in videos:
            normalize_video_categories(video)
    return videos


def normalize_restored_settings(settings):
    """Keep old backups compatible after the default category was renamed."""
    if isinstance(settings, dict):
        order_mode = settings.get("category_order_mode", "alphabetical")
        if order_mode not in {"alphabetical", "manual"}:
            order_mode = "alphabetical"
        settings["category_order_mode"] = order_mode
        settings["categories"] = normalize_category_list(
            settings.get("categories", []), alphabetical=(order_mode == "alphabetical")
        )
        reviewed_entries = settings.get(CATEGORY_SORT_REVIEWED_KEY, [])
        settings[CATEGORY_SORT_REVIEWED_KEY] = sorted({
            str(entry_id) for entry_id in reviewed_entries
            if isinstance(entry_id, str) and entry_id
        })
    return settings


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
    if "trash.json" in payloads:
        payloads["trash.json"] = normalize_restored_videos(payloads["trash.json"])
    if "settings.json" in payloads:
        payloads["settings.json"] = normalize_restored_settings(payloads["settings.json"])

    # Review marks existed inside settings in older backups and in a dedicated file in newer ones.
    # Merge both sources so a newly-created empty review file can never erase legacy decisions.
    legacy_reviews = payloads.get("settings.json", {}).get(DUPLICATE_REVIEWED_KEY, [])
    file_reviews = payloads.get("duplicate_reviews.json", [])
    combined_reviews = sorted({
        signature for signature in list(legacy_reviews if isinstance(legacy_reviews, list) else [])
        + list(file_reviews if isinstance(file_reviews, list) else [])
        if isinstance(signature, str)
    })
    if "settings.json" in payloads:
        payloads["settings.json"][DUPLICATE_REVIEWED_KEY] = combined_reviews
    if "duplicate_reviews.json" in payloads or "settings.json" in payloads:
        payloads["duplicate_reviews.json"] = combined_reviews
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
        "admin_actions.json": "יומן פעולות מנהל",
        "duplicate_reviews.json": "סימוני לא־כפול",
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
    # Old backups kept review marks inside settings; new ones have a dedicated file too.
    if "duplicate_reviews.json" in payloads:
        save_reviewed_non_duplicate_signatures(payloads["duplicate_reviews.json"])
    elif "settings.json" in payloads:
        legacy_reviews = payloads["settings.json"].get(DUPLICATE_REVIEWED_KEY, [])
        save_reviewed_non_duplicate_signatures(legacy_reviews if isinstance(legacy_reviews, list) else [])
    if "videos.json" in payloads:
        load_videos_with_entry_ids()


def load_videos_with_entry_ids():
    """Load video records and permanently normalize IDs and category memberships."""
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
        if normalize_video_categories(video):
            changed = True

    if changed:
        save_json(VIDEOS_FILE, videos)
        logger.info("Assigned unique entry_id values to existing video records")
    return videos


def _quick_category_markup(entry_id: str, menu_open: bool = False) -> InlineKeyboardMarkup:
    """Build media-message controls for direct category assignment by stable entry ID."""
    if not menu_open:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 מחק סרטון זה", callback_data=f"del_eid_{entry_id}")],
            [InlineKeyboardButton("🏷 שיוך לקטגוריה", callback_data=f"cat_quick_menu_{entry_id}")],
        ])
    videos = load_videos_with_entry_ids()
    video = next((item for item in videos if item.get("entry_id") == entry_id), None)
    if not video:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 סגור", callback_data=f"cat_quick_done_{entry_id}")]])
    memberships = video_categories(video)
    rows = []
    row = []
    for index, category in enumerate(_admin_categories()):
        label = f"✅ {category}" if category in memberships else category
        row.append(InlineKeyboardButton(label, callback_data=f"cat_quick_toggle_{entry_id}_{index}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 סיום שיוך", callback_data=f"cat_quick_done_{entry_id}")])
    return InlineKeyboardMarkup(rows)


async def send_admin_video_with_delete_button(bot, file_id, entry_id, max_attempts=5, include_category_assignment=False):
    """Send one video with a unique delete button and optional direct category controls."""
    markup = _quick_category_markup(entry_id) if include_category_assignment else InlineKeyboardMarkup([[InlineKeyboardButton(
        "🗑 מחק סרטון זה", callback_data=f"del_eid_{entry_id}"
    )]])

    for attempt in range(1, max_attempts + 1):
        try:
            return await bot.send_video(chat_id=ADMIN_ID, video=file_id, reply_markup=markup)
        except BadRequest as e:
            if "Wrong file identifier" in str(e) or "file_id" in str(e).lower():
                logger.error(f"Invalid file_id detected for entry_id {entry_id}")
                return "INVALID_FILE_ID"
            raise e
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
    # Direct reward settings supersede the legacy multiplier without changing old balances.
    s.setdefault("daily_gift_amount", 1)
    s.setdefault("referral_reward_amount", 1)
    s.setdefault("maintenance", False)
    s.setdefault("waiting_users", [])
    order_mode = s.get("category_order_mode", "alphabetical")
    if order_mode not in {"alphabetical", "manual"}:
        order_mode = "alphabetical"
    s["category_order_mode"] = order_mode
    s["categories"] = normalize_category_list(s.get("categories", []), alphabetical=(order_mode == "alphabetical"))
    return s

def save_settings(s: dict):
    save_json(SETTINGS_FILE, s)


ADMIN_PERMISSIONS = [
    ("assistant", "🤖 שימוש בעוזר פקודות"),
    ("gallery", "🎬 גלריה, העלאה וקטגוריות"),
    ("duplicates", "🔎 כפילויות וסל מיחזור"),
    ("users", "👥 משתמשים, הזמנות ותמיכה"),
    ("user_messages", "📩 שליחה למשתמש ואישור תשלום"),
    ("broadcast", "📢 הודעה לכל המשתמשים"),
    ("coins", "🪙 מטבעות, קופונים ודרגות"),
    ("maintenance", "🔧 מצב תחזוקה"),
    ("audit_log", "📜 יומן פעולות"),
    ("backup", "💾 גיבוי ושחזור"),
    ("dangerous_delete", "🗑 מחיקה לצמיתות ואיפוס"),
]
PERMISSION_LABELS = dict(ADMIN_PERMISSIONS)

# These are a second, finer-grained permission layer for commands executed through the assistant.
# A manager must hold both the regular permission and the matching assistant capability.
ASSISTANT_CAPABILITIES = [
    ("gallery", "🎬 חיפוש, עיון ושליחת סרטונים"),
    ("duplicates", "🔎 כפילויות, סל מיחזור ומחיקה"),
    ("users", "👥 משתמשים, הזמנות וסטטיסטיקה"),
    ("user_messages", "📩 שליחה למשתמש ואישור תשלום"),
    ("broadcast", "📢 הודעה לכל המשתמשים"),
    ("coins", "🪙 מטבעות, קופונים ודרגות"),
    ("audit_log", "📜 יומן פעולות"),
    ("backup", "💾 גיבוי ושחזור"),
]
ASSISTANT_CAPABILITY_LABELS = dict(ASSISTANT_CAPABILITIES)


def is_owner(user_id: int) -> bool:
    return int(user_id) == ADMIN_ID


def admin_managers() -> dict:
    settings = load_settings()
    managers = settings.get("admin_managers", {})
    return managers if isinstance(managers, dict) else {}


def is_admin(user_id: int) -> bool:
    return is_owner(user_id) or str(user_id) in admin_managers()


def admin_permissions(user_id: int) -> set[str]:
    if is_owner(user_id):
        return set(PERMISSION_LABELS)
    record = admin_managers().get(str(user_id), {})
    permissions = record.get("permissions", []) if isinstance(record, dict) else []
    return {permission for permission in permissions if permission in PERMISSION_LABELS}


def has_admin_permission(user_id: int, permission: str) -> bool:
    return is_owner(user_id) or permission in admin_permissions(user_id)


def assistant_capabilities(user_id: int) -> set[str]:
    """Return only explicitly enabled assistant capabilities for a manager."""
    if is_owner(user_id):
        return set(ASSISTANT_CAPABILITY_LABELS)
    record = admin_managers().get(str(user_id), {})
    capabilities = record.get("assistant_capabilities", []) if isinstance(record, dict) else []
    return {capability for capability in capabilities if capability in ASSISTANT_CAPABILITY_LABELS}


def has_assistant_capability(user_id: int, capability: str) -> bool:
    """Assistant actions require the assistant switch, normal permission, and capability switch."""
    return (
        has_admin_permission(user_id, "assistant")
        and has_admin_permission(user_id, capability)
        and capability in assistant_capabilities(user_id)
    )


def callback_permission(callback_data: str) -> str | None:
    """Map private callback data to its required permission; None means owner-only/unknown."""
    if callback_data in {"admin_panel", "back_admin"}:
        return "panel"
    if callback_data == "admin_owner_assistant_settings":
        return "owner"
    if callback_data in {"admin_assistant", "admin_assistant_back"}:
        return "assistant"
    if callback_data == "admin_gallery":
        return "gallery_or_duplicates"
    if callback_data.startswith((
        "vid_", "admin_categories", "admin_cat_", "cat_", "admin_repair",
        "admin_video_search", "admin_search_sec",
    )):
        return "gallery"
    if callback_data.startswith(("admin_dup", "dup_", "admin_trash", "trash_", "del_eid_", "del_v_")):
        return "duplicates"
    if callback_data.startswith(("admin_stats", "admin_orders", "users_page", "admin_check", "support_reply")):
        return "users"
    if callback_data.startswith(("admin_send", "admin_approve")):
        return "user_messages"
    if callback_data.startswith("admin_broadcast"):
        return "broadcast"
    if callback_data.startswith(("admin_coins", "admin_coupons", "coupon_", "admin_vip", "admin_multiplier", "admin_coin_control", "admin_coin_set_")):
        return "coins"
    if callback_data.startswith(("admin_maintenance", "maint_")):
        return "maintenance"
    if callback_data.startswith("admin_menu_users") or callback_data.startswith("admin_menu_rewards") or callback_data.startswith("admin_menu_communications"):
        return "panel"
    if callback_data.startswith("admin_menu_system"):
        return "system"
    if callback_data.startswith("admin_actions"):
        return "audit_log"
    if callback_data.startswith(("admin_backup", "admin_restore")):
        return "backup"
    if callback_data.startswith(("admin_delete", "admin_global_reset")):
        return "dangerous_delete"
    if callback_data.startswith("admin_managers") or callback_data.startswith("admin_mgr_"):
        return None
    return None


async def admin_callback_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    data = query.data or ""
    # The owner is the final authority for every management callback.
    if is_owner(query.from_user.id):
        return
    permission = callback_permission(data)
    if permission == "panel":
        if is_admin(query.from_user.id):
            return
    elif permission == "owner":
        if is_owner(query.from_user.id):
            return
    elif permission == "gallery_or_duplicates":
        if is_admin(query.from_user.id) and (
            has_admin_permission(query.from_user.id, "gallery")
            or has_admin_permission(query.from_user.id, "duplicates")
        ):
            return
    elif permission == "system":
        if is_admin(query.from_user.id) and (
            is_owner(query.from_user.id)
            or bool({"audit_log", "backup", "dangerous_delete"} & admin_permissions(query.from_user.id))
        ):
            return
    elif permission:
        if is_admin(query.from_user.id) and has_admin_permission(query.from_user.id, permission):
            return
    else:
        # Callbacks not owned by this mapper are public unless they use the admin namespace.
        if not data.startswith(("admin_", "cat_", "dup_", "vid_", "trash_", "del_", "maint_", "support_reply")):
            return
        if data.startswith(("admin_managers", "admin_mgr_")) and is_owner(query.from_user.id):
            return
    await query.answer("⛔ אין לך הרשאה לפעולה זו.", show_alert=True)
    raise ApplicationHandlerStop


def create_auto_backup(reason: str, actor_id: int | None = None) -> Path | None:
    """Create a bounded JSON snapshot before a destructive data operation."""
    try:
        AUTO_BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        safe_reason = re.sub(r"[^a-zA-Z0-9_-]+", "_", reason).strip("_") or "operation"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = AUTO_BACKUPS_DIR / f"auto_{stamp}_{safe_reason}.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for filename in BACKUP_ALLOWED_FILES:
                filepath = DATA_DIR / filename
                if filepath.exists():
                    archive.write(filepath, arcname=filename)
        backups = sorted(AUTO_BACKUPS_DIR.glob("auto_*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
        for old_backup in backups[MAX_AUTO_BACKUPS:]:
            old_backup.unlink(missing_ok=True)
        logger.info("Created automatic backup %s for %s by %s", path.name, reason, actor_id)
        return path
    except Exception as exc:
        logger.exception("Automatic backup failed before %s: %s", reason, exc)
        return None


def log_admin_action(actor_id: int, action: str, details: dict | None = None) -> None:
    """Persist a bounded, non-secret audit record for admin activity."""
    records = load_json(ADMIN_ACTIONS_FILE)
    if not isinstance(records, list):
        records = []
    records.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "admin_id": int(actor_id),
        "action": str(action),
        "details": details if isinstance(details, dict) else {},
    })
    save_json(ADMIN_ACTIONS_FILE, records[-MAX_ADMIN_ACTIONS:])


DUPLICATE_REVIEWED_KEY = "reviewed_non_duplicate_groups"
CATEGORY_SORT_REVIEWED_KEY = "category_sort_reviewed_entry_ids"


def duplicate_group_signature(group: list[dict]) -> str:
    """Return a stable identity for the exact set of videos in a duplicate-review group."""
    duration = group[0].get("duration", 0) if group else 0
    entry_ids = sorted(str(video.get("entry_id", "")) for video in group)
    payload = json.dumps({"duration": duration, "entry_ids": entry_ids}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def reviewed_non_duplicate_signatures() -> set[str]:
    """Load review decisions from the dedicated backup file and legacy settings key."""
    settings = load_settings()
    legacy = settings.get(DUPLICATE_REVIEWED_KEY, [])
    stored = load_json(DUPLICATE_REVIEWS_FILE)
    values = []
    if isinstance(legacy, list):
        values.extend(legacy)
    if isinstance(stored, list):
        values.extend(stored)
    return {signature for signature in values if isinstance(signature, str)}


def save_reviewed_non_duplicate_signatures(signatures) -> None:
    """Persist review decisions both in a dedicated backup file and legacy settings."""
    cleaned = sorted({signature for signature in signatures if isinstance(signature, str)})
    save_json(DUPLICATE_REVIEWS_FILE, cleaned)
    settings = load_settings()
    settings[DUPLICATE_REVIEWED_KEY] = cleaned
    save_settings(settings)


def mark_group_as_not_duplicate(group: list[dict]) -> bool:
    """Persist a manual review decision for this exact duplicate-group snapshot.

    The dedicated review file and the legacy settings field are merged first.
    This prevents a restore containing decisions in only one location from being
    accidentally overwritten when a new group is marked.
    """
    signature = duplicate_group_signature(group)
    reviewed = reviewed_non_duplicate_signatures()
    if signature in reviewed:
        return False
    reviewed.add(signature)
    save_reviewed_non_duplicate_signatures(reviewed)
    return True


def category_sort_reviewed_entry_ids() -> set[str]:
    """Return videos already handled in the normal, incremental category sort."""
    stored = load_settings().get(CATEGORY_SORT_REVIEWED_KEY, [])
    return {str(entry_id) for entry_id in stored if isinstance(entry_id, str) and entry_id}


def save_category_sort_reviewed_entry_ids(entry_ids) -> None:
    """Persist category-sort progress in settings, which is included in every backup."""
    settings = load_settings()
    settings[CATEGORY_SORT_REVIEWED_KEY] = sorted({str(entry_id) for entry_id in entry_ids if entry_id})
    save_settings(settings)


def mark_category_sort_reviewed(entry_id: str) -> bool:
    reviewed = category_sort_reviewed_entry_ids()
    if entry_id in reviewed:
        return False
    reviewed.add(entry_id)
    save_category_sort_reviewed_entry_ids(reviewed)
    return True


def clear_category_sort_progress() -> int:
    reviewed = category_sort_reviewed_entry_ids()
    save_category_sort_reviewed_entry_ids([])
    return len(reviewed)


def clear_not_duplicate_marks() -> int:
    """Clear manual duplicate-review decisions so a full scan includes every group again."""
    count = len(reviewed_non_duplicate_signatures())
    save_reviewed_non_duplicate_signatures([])
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
                reward = max(0, int(load_settings().get("referral_reward_amount", 1)))
                coins[ref_key] = coins.get(ref_key, 0) + reward
                save_json(COINS_FILE, coins)
    return users.get(uid, {})

def count_unseen_videos(user_id: int) -> int:
    """Count usable videos that a user has not received before."""
    users = load_json(USERS_FILE)
    seen = set(users.get(str(user_id), {}).get("seen_videos", []))
    return sum(
        1
        for video in load_json(VIDEOS_FILE)
        if isinstance(video, dict)
        and video.get("file_id")
        and video.get("file_status") not in {"broken", "broken_skipped"}
        and video.get("file_id") not in seen
    )


async def send_videos_to_user(context, user_id: int, count: int) -> int:
    """Send a random selection of strictly unseen, usable videos to one user.

    The function intentionally never falls back to previously sent videos.
    """
    all_videos = load_json(VIDEOS_FILE)
    users = load_json(USERS_FILE)
    uid = str(user_id)
    user_data = users.get(uid, {})
    seen = user_data.get("seen_videos", [])
    seen_set = set(seen)

    pool = [
        video for video in all_videos
        if isinstance(video, dict)
        and video.get("file_id")
        and video.get("file_status") not in {"broken", "broken_skipped"}
    ]
    unseen = [video for video in pool if video["file_id"] not in seen_set]
    selected = random.sample(unseen, min(count, len(unseen)))

    sent = 0
    for video in selected:
        try:
            file_id = video["file_id"]
            await context.bot.send_video(chat_id=user_id, video=file_id)
            seen.append(file_id)
            seen_set.add(file_id)
            sent += 1
            await asyncio.sleep(0.05)
        except BadRequest as exc:
            # A file that fails here cannot be used by the current bot. Mark it for repair.
            if "file" in str(exc).lower() or "identifier" in str(exc).lower():
                video["file_status"] = "broken"
            logger.warning("Could not send a library video to user %s: %s", user_id, exc)
        except Exception as exc:
            logger.warning("Could not send a library video to user %s: %s", user_id, exc)

    save_json(VIDEOS_FILE, all_videos)
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
            InlineKeyboardButton("💳 תשלום", callback_data="payment_method"),
            InlineKeyboardButton("👥 הפניות שלי", callback_data="referrals"),
        ],
        [
            InlineKeyboardButton("💰 ארנק מטבעות", callback_data="wallet"),
            InlineKeyboardButton("🎟 מימוש קופון", callback_data="coupon_redeem"),
        ],
        [InlineKeyboardButton("ℹ️ איך זה עובד?", callback_data="purchase_help")],
        [InlineKeyboardButton("💬 תמיכה", callback_data="support")],
    ])

def get_admin_reply_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🛠 פאנל אדמין")]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def get_admin_inline_keyboard(user_id: int = ADMIN_ID):
    """Return the familiar detailed admin panel, filtered strictly by each manager's permissions."""
    settings = load_settings()
    maint_status = "🟠 תחזוקה" if settings.get("maintenance") else "🟢 פעיל"
    rows = []

    def add(permission: str, buttons: list[InlineKeyboardButton]):
        if has_admin_permission(user_id, permission):
            rows.append(buttons)

    # The owner can grant or remove this dedicated assistant permission for every manager.
    if has_admin_permission(user_id, "assistant"):
        rows.append([InlineKeyboardButton("🤖 עוזר פקודות", callback_data="admin_assistant")])

    # Existing day-to-day controls stay visible on the main panel.
    add("maintenance", [InlineKeyboardButton(f"📡 סטטוס בוט: {maint_status}", callback_data="admin_maintenance")])
    add("users", [InlineKeyboardButton("📊 סטטיסטיקה", callback_data="admin_stats"), InlineKeyboardButton("🧾 הזמנות", callback_data="admin_orders_page_0")])
    add("users", [InlineKeyboardButton("🔍 בדוק משתמש", callback_data="admin_check"), InlineKeyboardButton("👥 רשימת משתמשים", callback_data="users_page_0")])
    add("user_messages", [InlineKeyboardButton("📩 שלח למשתמש", callback_data="admin_send"), InlineKeyboardButton("✅ אישור תשלום", callback_data="admin_approve")])
    if has_admin_permission(user_id, "gallery") or has_admin_permission(user_id, "duplicates"):
        rows.append([InlineKeyboardButton("🎬 גלריית סרטונים", callback_data="admin_gallery")])
    add("broadcast", [InlineKeyboardButton("📢 הודעה לכולם", callback_data="admin_broadcast")])
    add("coins", [InlineKeyboardButton("🪙 מטבעות", callback_data="admin_coins_menu")])

    # Advanced tools added after the original panel are kept together here.
    advanced_permissions = {"audit_log", "backup", "dangerous_delete"}
    if is_owner(user_id) or bool(advanced_permissions & admin_permissions(user_id)):
        rows.append([InlineKeyboardButton("⚙️ מערכת, גיבויים וכלים מתקדמים", callback_data="admin_menu_system")])

    return InlineKeyboardMarkup(rows or [[InlineKeyboardButton("ℹ️ אין הרשאות פעילות", callback_data="noop")]])


def _back_to_admin_row():
    return [InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")]


def _flow_back_markup(callback_data: str = "back_admin", label: str = "🔙 חזרה לפאנל") -> InlineKeyboardMarkup:
    """Visible exit control for every text-input management step."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=callback_data)]])


async def admin_menu_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    rows = []
    if has_admin_permission(user_id, "users"):
        rows.extend([
            [InlineKeyboardButton("📊 סטטיסטיקה", callback_data="admin_stats"), InlineKeyboardButton("🧾 הזמנות", callback_data="admin_orders_page_0")],
            [InlineKeyboardButton("🔍 בדוק משתמש", callback_data="admin_check"), InlineKeyboardButton("👥 רשימת משתמשים", callback_data="users_page_0")],
        ])
    if has_admin_permission(user_id, "user_messages"):
        rows.append([InlineKeyboardButton("📩 שלח למשתמש", callback_data="admin_send"), InlineKeyboardButton("✅ אישור תשלום", callback_data="admin_approve")])
    rows.append(_back_to_admin_row())
    await query.edit_message_text("👥 *משתמשים ומכירות*\n\nבחר פעולה:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))


async def admin_menu_rewards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await admin_coins_menu(update, context)


async def admin_menu_communications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📢 *הודעות ופרסום*\n\nפעולות אלו פונות למשתמשים ולכן יש לבדוק אותן לפני שליחה.", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📢 הודעה לכולם", callback_data="admin_broadcast")], _back_to_admin_row()]),
    )


async def admin_menu_system(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    rows = []
    if has_admin_permission(user_id, "audit_log"):
        rows.append([InlineKeyboardButton("📜 יומן פעולות", callback_data="admin_actions_page_0")])
    if has_admin_permission(user_id, "backup"):
        rows.append([InlineKeyboardButton("💾 גיבוי ZIP", callback_data="admin_backup"), InlineKeyboardButton("📥 שחזור גיבוי", callback_data="admin_restore")])
    if has_admin_permission(user_id, "dangerous_delete"):
        rows.append([InlineKeyboardButton("🔄 איפוס נתונים", callback_data="admin_global_reset"), InlineKeyboardButton("🧹 מחק סרטונים", callback_data="admin_delete")])
    if is_owner(user_id):
        rows.append([InlineKeyboardButton("👑 ניהול מנהלים", callback_data="admin_managers")])
    rows.append(_back_to_admin_row())
    await query.edit_message_text("⚙️ *מערכת, גיבויים וכלים מתקדמים*\n\nבחר פעולה:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

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


    if is_admin(user.id):
        await update.message.reply_text("👋 ברוך הבא בפאנל הניהול!", reply_markup=get_admin_reply_keyboard())

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
        
    bonus_amount = max(0, int(load_settings().get("daily_gift_amount", 1)))
    user_data["last_bonus_ts"] = now_ts
    user_data["last_bonus"] = str(date.today()) # Keep for legacy compatibility
    users[uid] = user_data
    save_json(USERS_FILE, users)
    
    coins = load_json(COINS_FILE)
    old_balance = coins.get(uid, 0)
    new_balance = old_balance + bonus_amount
    coins[uid] = new_balance
    save_json(COINS_FILE, coins)
    
    await query.answer(f"🎁 קיבלת {bonus_amount} מטבע! כעת יש לך בסך הכול {new_balance} מטבעות.", show_alert=True)
    await query.edit_message_text(
        f"🎁 *המתנה היומית התקבלה!*\n\n"
        f"קיבלת עכשיו: *{bonus_amount} מטבעות*\n"
        f"💰 יש לך בסך הכול: *{new_balance} מטבעות*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לתפריט", callback_data="back_main")]]),
    )

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

async def purchase_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await maintenance_gate(update):
        return
    text = (
        "ℹ️ *איך קונים ומקבלים סרטונים?*\n\n"
        "1️⃣ בוחרים חבילה ותשלום בפייפאל או במטבעות.\n"
        "2️⃣ לאחר אישור התשלום, הבוט שולח את כמות הסרטונים שבחבילה.\n"
        "3️⃣ הסרטונים נבחרים *באקראי* מתוך המאגר. לא בוחרים סרטון ספציפי.\n"
        "4️⃣ הבוט שולח לכל משתמש רק סרטונים שעדיין לא קיבל, כך שאין חזרות.\n\n"
        "בתשלום בפייפאל יש לשלוח צילום מסך של אישור התשלום דרך התמיכה. "
        "בתשלום במטבעות הסרטונים נשלחים מיד לאחר אישור הפעולה."
    )
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 לרכישה", callback_data="payment_method")],
            [InlineKeyboardButton("🔙 חזרה", callback_data="back_main")],
        ]),
    )


async def payment_method_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await maintenance_gate(update):
        return
    coins = load_json(COINS_FILE)
    balance = coins.get(str(query.from_user.id), 0)
    await query.edit_message_text(
        "💰 *רכישת סרטונים*\n\n"
        "הסרטונים נשלחים באקראי ורק כאלה שעדיין לא קיבלת. "
        "בחר אמצעי תשלום:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 תשלום בפייפאל", callback_data="paypal_menu")],
            [InlineKeyboardButton(f"🪙 שלם במטבעות (יתרה: {balance})", callback_data="coins_menu")],
            [InlineKeyboardButton("ℹ️ הסבר על הרכישה", callback_data="purchase_help")],
            [InlineKeyboardButton("🔙 חזרה", callback_data="back_main")],
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
    
    available = count_unseen_videos(query.from_user.id)
    if available < pkg["videos"]:
        await query.edit_message_text(
            f"כרגע נשארו לך רק {available} סרטונים חדשים שעדיין לא קיבלת. "
            "כדי לא לשלוח לך כפילויות, לא ניתן להשלים את החבילה הזו כרגע. "
            "בחר חבילה קטנה יותר או חזור לאחר שיועלה תוכן חדש.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="paypal_menu")]]),
        )
        return

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

    available = count_unseen_videos(query.from_user.id)
    if available < pkg["videos"]:
        await query.answer(
            f"❌ נשארו לך רק {available} סרטונים חדשים שעדיין לא קיבלת. "
            "כדי למנוע כפילויות, בחר חבילה קטנה יותר או המתן לתוכן חדש.",
            show_alert=True,
        )
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
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    target = query.data.replace("support_reply_", "")
    context.user_data["support_reply_target"] = target
    await query.message.reply_text(f"✏️ תשובה ל-`{target}`:\n\nכתוב את ההודעה:", parse_mode="Markdown", reply_markup=_flow_back_markup())
    return SUPPORT_REPLY_MSG

async def admin_support_reply_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    target = context.user_data.get("support_reply_target")
    try:
        await context.bot.send_message(chat_id=int(target), text=f"📬 *תשובה מהמנהל:*\n\n{update.message.text}", parse_mode="Markdown")
        await update.message.reply_text(f"✅ נשלח למשתמש {target}!")
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {e}")
    return ConversationHandler.END

# ─── Owner: manager permissions ───────────────────────────────────────────────

async def admin_managers_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        return
    managers = admin_managers()
    buttons = [
        [InlineKeyboardButton("➕ הוסף מנהל", callback_data="admin_mgr_add")],
        [InlineKeyboardButton("🤖 הגדרות עוזר למנהלים", callback_data="admin_mgr_assistant_list")],
    ]
    for manager_id, record in managers.items():
        label = record.get("name") or f"מנהל {manager_id}"
        buttons.append([InlineKeyboardButton(f"👤 {label} ({manager_id})", callback_data=f"admin_mgr_pick_{manager_id}")])
    buttons.append([InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")])
    await query.edit_message_text(
        "👑 *ניהול מנהלים*\n\nבחר מנהל כדי להגדיר את ההרשאות שלו, או לחץ על *🤖 הגדרות עוזר למנהלים* כדי לבחור יכולות עוזר. רק הבעלים יכול להוסיף, לערוך או להסיר מנהלים.",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_manager_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        return ConversationHandler.END
    await query.edit_message_text(
        "שלח את *מזהה המשתמש המספרי* של המנהל החדש. לאחר ההוספה תבחר בדיוק את ההרשאות שלו.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="admin_managers")]]),
    )
    return ADMIN_MANAGER_ADD_ID


async def admin_manager_add_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    try:
        manager_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ יש לשלוח מזהה משתמש מספרי בלבד.")
        return ADMIN_MANAGER_ADD_ID
    if manager_id == ADMIN_ID:
        await update.message.reply_text("ℹ️ זהו מזהה הבעלים הראשי, ולכן אין צורך להוסיף אותו כמנהל.")
        return ConversationHandler.END
    settings = load_settings()
    managers = settings.setdefault("admin_managers", {})
    record = managers.get(str(manager_id), {})
    record.setdefault("permissions", [])
    record.setdefault("assistant_capabilities", [])
    record.setdefault("name", "")
    managers[str(manager_id)] = record
    save_settings(settings)
    context.user_data["selected_manager_id"] = str(manager_id)
    log_admin_action(update.effective_user.id, "manager_added", {"manager_id": manager_id})
    await update.message.reply_text(
        f"✅ המנהל `{manager_id}` נוסף ללא הרשאות עדיין. בחר את ההרשאות שלו:",
        parse_mode="Markdown",
        reply_markup=_manager_permissions_keyboard(str(manager_id)),
    )
    return ConversationHandler.END


def _manager_permissions_keyboard(manager_id: str) -> InlineKeyboardMarkup:
    record = admin_managers().get(manager_id, {})
    assigned = set(record.get("permissions", [])) if isinstance(record, dict) else set()
    buttons = []
    for permission, label in ADMIN_PERMISSIONS:
        mark = "✅" if permission in assigned else "⬜"
        buttons.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"admin_mgr_toggle_{permission}")])
    buttons.extend([
        [InlineKeyboardButton("🤖 הגדרות עוזר", callback_data="admin_mgr_assistant")],
        [InlineKeyboardButton("🗑 הסר מנהל", callback_data="admin_mgr_remove")],
        [InlineKeyboardButton("🔙 חזרה לרשימה", callback_data="admin_managers")],
    ])
    return InlineKeyboardMarkup(buttons)


def _manager_assistant_keyboard(manager_id: str) -> InlineKeyboardMarkup:
    record = admin_managers().get(manager_id, {})
    enabled = set(record.get("assistant_capabilities", [])) if isinstance(record, dict) else set()
    buttons = []
    for capability, label in ASSISTANT_CAPABILITIES:
        mark = "✅" if capability in enabled else "⬜"
        buttons.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"admin_mgr_assist_toggle_{capability}")])
    buttons.append([InlineKeyboardButton("🔙 חזרה להרשאות המנהל", callback_data=f"admin_mgr_pick_{manager_id}")])
    return InlineKeyboardMarkup(buttons)


async def admin_manager_assistant_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        return
    managers = admin_managers()
    buttons = [[InlineKeyboardButton(
        "👑 הגדרות העוזר שלי — בעלים", callback_data="admin_owner_assistant_settings"
    )]]
    buttons.extend([
        [InlineKeyboardButton(
            f"🤖 {record.get('name') or manager_id} ({manager_id})",
            callback_data=f"admin_mgr_assistant_pick_{manager_id}",
        )]
        for manager_id, record in managers.items()
    ])
    if not managers:
        buttons.append([InlineKeyboardButton("➕ הוסף מנהל חדש", callback_data="admin_mgr_add")])
        text = (
            "🤖 *הגדרות עוזר למנהלים*\n\n"
            "עדיין לא הוספת מנהל, ולכן אין מנהל לבחירה. לחץ על הכפתור למטה, שלח את מזהה המשתמש שלו, "
            "ואז תוכל לבחור לו הרשאות ויכולות עוזר."
        )
    else:
        text = "🤖 *הגדרות עוזר למנהלים*\n\nבחר מנהל כדי להפעיל או לבטל את יכולות העוזר שלו."
    buttons.append([InlineKeyboardButton("🔙 חזרה לניהול מנהלים", callback_data="admin_managers")])
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_owner_assistant_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        return
    await query.edit_message_text(
        "👑 *הגדרות העוזר שלי*\n\n"
        "כבעלים, כל יכולות העוזר פעילות עבורך באופן אוטומטי. אין צורך להוסיף מנהל כדי לבדוק את העוזר או להשתמש בכל פעולות הניהול.\n\n"
        "אפשר לפתוח עכשיו את העוזר ולנסות כל בקשה.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🤖 פתח את העוזר שלי", callback_data="admin_assistant")],
            [InlineKeyboardButton("🔙 חזרה לניהול מנהלים", callback_data="admin_managers")],
        ]),
    )


async def admin_manager_assistant_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        return
    manager_id = query.data.replace("admin_mgr_assistant_pick_", "")
    if manager_id not in admin_managers():
        await query.answer("המנהל אינו קיים יותר.", show_alert=True)
        await admin_manager_assistant_list(update, context)
        return
    context.user_data["selected_manager_id"] = manager_id
    await admin_manager_assistant_menu(update, context)


async def admin_manager_assistant_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        return
    manager_id = context.user_data.get("selected_manager_id")
    if not manager_id or manager_id not in admin_managers():
        await query.answer("בחר מנהל מחדש.", show_alert=True)
        await admin_managers_menu(update, context)
        return
    record = admin_managers()[manager_id]
    title = record.get("name") or manager_id
    await query.edit_message_text(
        f"🤖 *הגדרות עוזר — {title}*\n\n"
        "סמן מה העוזר רשאי לעשות עבור המנהל. פעולה תעבוד רק אם קיימת גם ההרשאה הרגילה המתאימה.",
        parse_mode="Markdown",
        reply_markup=_manager_assistant_keyboard(manager_id),
    )


async def admin_manager_assistant_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        return
    capability = query.data.replace("admin_mgr_assist_toggle_", "")
    manager_id = context.user_data.get("selected_manager_id")
    if capability not in ASSISTANT_CAPABILITY_LABELS or not manager_id:
        await query.answer("נתוני ההרשאה אינם זמינים. בחר מנהל מחדש.", show_alert=True)
        return
    settings = load_settings()
    managers = settings.get("admin_managers", {})
    record = managers.get(manager_id)
    if not isinstance(record, dict):
        await query.answer("המנהל אינו קיים יותר.", show_alert=True)
        return
    capabilities = set(record.get("assistant_capabilities", []))
    if capability in capabilities:
        capabilities.remove(capability)
    else:
        capabilities.add(capability)
    record["assistant_capabilities"] = sorted(capabilities)
    managers[manager_id] = record
    settings["admin_managers"] = managers
    save_settings(settings)
    log_admin_action(query.from_user.id, "manager_assistant_capability_changed", {
        "manager_id": manager_id,
        "capability": capability,
        "enabled": capability in capabilities,
    })
    await query.edit_message_reply_markup(reply_markup=_manager_assistant_keyboard(manager_id))


async def admin_manager_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        return
    manager_id = query.data.replace("admin_mgr_pick_", "")
    if manager_id not in admin_managers():
        await query.answer("המנהל אינו קיים יותר.", show_alert=True)
        await admin_managers_menu(update, context)
        return
    context.user_data["selected_manager_id"] = manager_id
    record = admin_managers()[manager_id]
    title = record.get("name") or manager_id
    await query.edit_message_text(
        f"👤 *מנהל: {title}*\n🆔 `{manager_id}`\n\nסמן או הסר הרשאות. השינויים נשמרים מיד.",
        parse_mode="Markdown", reply_markup=_manager_permissions_keyboard(manager_id),
    )


async def admin_manager_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        return
    permission = query.data.replace("admin_mgr_toggle_", "")
    manager_id = context.user_data.get("selected_manager_id")
    if permission not in PERMISSION_LABELS or not manager_id:
        await query.answer("נתוני ההרשאה אינם זמינים. בחר מנהל מחדש.", show_alert=True)
        return
    settings = load_settings()
    managers = settings.get("admin_managers", {})
    record = managers.get(manager_id)
    if not isinstance(record, dict):
        await query.answer("המנהל אינו קיים יותר.", show_alert=True)
        return
    permissions = set(record.get("permissions", []))
    if permission in permissions:
        permissions.remove(permission)
    else:
        permissions.add(permission)
    record["permissions"] = sorted(permissions)
    managers[manager_id] = record
    settings["admin_managers"] = managers
    save_settings(settings)
    log_admin_action(query.from_user.id, "manager_permission_changed", {"manager_id": manager_id, "permission": permission, "enabled": permission in permissions})
    await query.edit_message_reply_markup(reply_markup=_manager_permissions_keyboard(manager_id))


async def admin_manager_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        return
    manager_id = context.user_data.get("selected_manager_id")
    if not manager_id or manager_id not in admin_managers():
        await admin_managers_menu(update, context)
        return
    settings = load_settings()
    settings.get("admin_managers", {}).pop(manager_id, None)
    save_settings(settings)
    log_admin_action(query.from_user.id, "manager_removed", {"manager_id": manager_id})
    context.user_data.pop("selected_manager_id", None)
    await query.answer("🗑 המנהל הוסר והגישה נחסמה מיד.", show_alert=True)
    await admin_managers_menu(update, context)

# ─── Admin: panel ─────────────────────────────────────────────────────────────

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    settings = load_settings()
    maint_status = "🟠 *מצב תחזוקה פעיל*" if settings.get("maintenance") else "🟢 *הבוט פעיל כרגיל*"
    title = "👑 *פאנל בעלים*" if is_owner(user_id) else "🛠 *פאנל מנהל*"
    await update.message.reply_text(
        f"{title}\n\nסטטוס נוכחי: {maint_status}\n\nבחר פעולה:",
        parse_mode="Markdown",
        reply_markup=get_admin_inline_keyboard(user_id),
    )

async def back_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    settings = load_settings()
    maint_status = "🟠 *מצב תחזוקה פעיל*" if settings.get("maintenance") else "🟢 *הבוט פעיל כרגיל*"
    title = "👑 *פאנל בעלים*" if is_owner(query.from_user.id) else "🛠 *פאנל מנהל*"
    await query.edit_message_text(
        f"{title}\n\nסטטוס נוכחי: {maint_status}\n\nבחר פעולה:",
        parse_mode="Markdown",
        reply_markup=get_admin_inline_keyboard(query.from_user.id),
    )

# ─── Admin: free command assistant ────────────────────────────────────────────


def _assistant_examples(user_id: int) -> list[str]:
    """Return only examples that match the requesting administrator's permissions."""
    examples = []
    if has_assistant_capability(user_id, "gallery"):
        examples.extend([
            "• שלח סרטונים מ-10 עד 20 שניות",
            "• תשלח לי סרטונים בין 1:30 ל-2:10",
            "• חפש מספרים 10 עד 28",
            "• פתח גלריה או קטגוריות",
            "• שלח את כל הסרטונים",
        ])
    if has_assistant_capability(user_id, "duplicates"):
        examples.extend(["• מצא כפילויות", "• פתח סל מיחזור"])
    if has_assistant_capability(user_id, "users"):
        examples.extend(["• הצג סטטיסטיקה", "• פתח הזמנות", "• פתח רשימת משתמשים"])
    if has_assistant_capability(user_id, "user_messages"):
        examples.append("• שלח למשתמש או אשר תשלום")
    if has_assistant_capability(user_id, "broadcast"):
        examples.append("• הודעה לכולם")
    if has_assistant_capability(user_id, "coins"):
        examples.append("• פתח מטבעות, שליטה במטבעות, קופונים או דרגות")
    if has_assistant_capability(user_id, "backup"):
        examples.extend(["• צור גיבוי", "• שחזר גיבוי"])
    if has_assistant_capability(user_id, "audit_log"):
        examples.append("• פתח יומן פעולות")
    examples.append("• מה זה יומן פעולות? / הסבר ערך מטבע")
    return examples or ["• אין כרגע פעולות שמותר לבצע עם העוזר."]


async def admin_assistant_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not has_admin_permission(user_id, "assistant"):
        return ConversationHandler.END
    text = (
        "🤖 *עוזר פקודות חכם*\n\n"
        "כתוב לי פעולה בעברית ואבצע אותה לפי ההרשאות שלך. "
        "אפשר לכתוב `עזרה` בכל זמן או `ביטול` כדי לצאת.\n\n"
        "*דוגמאות לפעולות שלך:*\n" + "\n".join(_assistant_examples(user_id))
    )
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=_assistant_navigation_keyboard(),
    )
    return ADMIN_ASSISTANT_COMMAND


def _assistant_navigation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="admin_assistant_back")],
        [InlineKeyboardButton("❌ ביטול", callback_data="admin_assistant_back")],
    ])


async def admin_assistant_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Leave the assistant safely and return to the normal admin panel."""
    query = update.callback_query
    await _assistant_clear_previous_media(context, query.from_user.id)
    await back_admin(update, context)
    return ConversationHandler.END


async def admin_assistant_exit_to_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The persistent admin-panel keyboard must never be interpreted as an assistant command."""
    await _assistant_clear_previous_media(context, update.effective_user.id)
    await admin_panel(update, context)
    return ConversationHandler.END


def _assistant_normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower().replace("–", "-").replace("—", "-"))


_ASSISTANT_AI_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "hebrew_admin_intent",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "kind": {"type": "string", "enum": ["rewrite", "answer", "unsupported", "clarification"]},
                "canonical_text": {"type": ["string", "null"]},
                "reply": {"type": ["string", "null"]},
            },
            "required": ["kind", "canonical_text", "reply"],
        },
    },
}

_ASSISTANT_AI_PROMPT = """אתה עוזר AI חכם, ידידותי ומדויק בתוך בוט ניהול Telegram בעברית.
ענה בעברית טבעית לשאלות כלליות, לשיחות קצרות ולהסברים, גם כשהשאלה אינה פקודה או אינה קשורה ישירות לבוט.
עם זאת, אין לך גישה לאינטרנט, לקבצים, לסרטונים, לטוקנים, לסיסמאות או לנתונים אישיים, ואסור לך להמציא מידע כזה.

המערכת הקיימת יודעת לבצע רק פעולות מוגדרות: הסברים על יכולות, עזרה, שליחת סרטונים לפי טווח זמן או מספר,
שליחת כל הסרטונים, פתיחת גלריה, קטגוריות, כפילויות, סל מיחזור, סטטיסטיקה, הזמנות, משתמשים,
אישור תשלום, שליחה למשתמש, הודעה לכולם, מטבעות, קופונים, דרגות, יומן, גיבוי, שחזור ותחזוקה.

החזר JSON בלבד. אם המשתמש מבקש פעולה קיימת שאפשר לתרגם לפקודה קצרה שהמערכת מבינה, החזר kind=\"rewrite\"
ו-canonical_text. אם המשתמש שואל שאלה, מבקש הסבר כללי או מנהל שיחה, החזר kind=\"answer\" ו-reply מועיל,
קצר וישיר. אל תטען שביצעת פעולה. אם הפעולה המבוקשת אינה קיימת, החזר kind=\"unsupported\" והסבר זאת ב-reply.
אם חסר מידע הכרחי לביצוע פעולה, החזר kind=\"clarification\" ושאל רק את שאלת ההבהרה הדרושה.
אל תבצע פעולות, אל תנהל הרשאות ואל תחזיר טוקנים או מידע אישי.
"""


def _assistant_ai_enabled() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY")) and OpenAI is not None


async def _assistant_ai_rewrite(raw_text: str, user_id: int) -> tuple[str | None, str | None]:
    """Use an optional LLM only as a constrained Hebrew intent rewriter."""
    text = raw_text.strip()
    if not _assistant_ai_enabled() or len(text) < 4:
        return None, None
    try:
        client = OpenAI()
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=os.environ.get("ADMIN_ASSISTANT_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": _ASSISTANT_AI_PROMPT},
                {"role": "user", "content": text},
            ],
            response_format=_ASSISTANT_AI_SCHEMA,
            max_completion_tokens=500,
        )
        content = getattr(response.choices[0].message, "content", None)
        if not content:
            logger.warning("Optional assistant AI returned empty content; using deterministic fallback")
            return None, None
        payload = json.loads(content)
        if payload.get("kind") == "rewrite" and isinstance(payload.get("canonical_text"), str):
            return payload["canonical_text"].strip(), None
        if payload.get("kind") in {"answer", "unsupported", "clarification"}:
            return None, payload.get("reply") or "🤖 לא הצלחתי להבין את הבקשה במלואה. אפשר לנסח אותה אחרת?"
    except Exception as exc:
        logger.warning("Optional assistant AI intent layer failed; using deterministic fallback: %s", exc)
    return None, None


def _assistant_time_range(text: str) -> tuple[int, int] | None:
    """Extract a time range from Hebrew command text without accepting unrelated numbers."""
    if not any(marker in text for marker in ("שני", "זמן", "אורך", "דקה", ":")):
        return None
    match = re.search(
        r"(?:בין\s*)?(\d+(?::\d+)?)\s*(?:שניות?|דקות?)?\s*(?:-|עד|ועד|ל-?|ל)\s*(\d+(?::\d+)?)\s*(?:שניות?|דקות?)?",
        text,
    )
    if not match:
        return None
    first = parse_smart_time(match.group(1))
    last = parse_smart_time(match.group(2))
    return (first, last) if first >= 0 and last >= first else None


def _assistant_number_range(text: str) -> tuple[int, int] | None:
    """Extract a number range only when the command explicitly refers to video numbers."""
    if "מספר" not in text:
        return None
    match = re.search(r"(?:מספר(?:ים)?|מספרי סרטונים)[^\d]*(\d+)\s*(?:-|עד|ועד)\s*(\d+)", text)
    if not match:
        return None
    first, last = int(match.group(1)), int(match.group(2))
    return (first, last) if first >= 1 and last >= first else None


async def _assistant_clear_previous_media(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    message_ids = context.user_data.pop("assistant_sent_media_message_ids", [])
    for message_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass


async def _assistant_send_videos(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    selected: list[dict],
    description: str,
) -> None:
    """Send selected library videos to the requesting manager, in the supplied safe order."""
    user_id = update.effective_user.id
    await _assistant_clear_previous_media(context, user_id)
    await update.message.reply_text(f"🤖 נמצאו {len(selected)} סרטונים: {description}. שולח לפי הסדר...")
    sent_ids = []
    success = 0
    can_delete = has_assistant_capability(user_id, "duplicates")
    for video in selected:
        try:
            markup = None
            if can_delete:
                markup = InlineKeyboardMarkup([[InlineKeyboardButton(
                    "🗑 העבר לסל מיחזור", callback_data=f"del_eid_{video['entry_id']}"
                )]])
            sent = await context.bot.send_video(chat_id=user_id, video=video["file_id"], reply_markup=markup)
            if sent:
                sent_ids.append(sent.message_id)
                success += 1
            await asyncio.sleep(0.15)
        except Exception:
            logger.exception("Assistant could not send a selected video")
    context.user_data["assistant_sent_media_message_ids"] = sent_ids
    context.user_data["assistant_last_selection_ids"] = [video.get("entry_id") for video in selected if video.get("entry_id")]
    await update.message.reply_text(
        f"✅ העוזר סיים: {success}/{len(selected)} סרטונים נשלחו.",
        reply_markup=_assistant_navigation_keyboard(),
    )


def _assistant_action_button(label: str, callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=callback_data)],
        [InlineKeyboardButton("🤖 המשך עם העוזר", callback_data="admin_assistant")],
        [InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="admin_assistant_back")],
    ])


def _assistant_explanation(text: str, user_id: int) -> str | None:
    """Return a clear Hebrew explanation for bot features without viewing any media or files."""
    if "כל כפתור" in text or "כל הכפתורים" in text or "מה הבוט עושה" in text:
        return (
            "🤖 *מדריך קצר לפאנל הניהול*\n\n"
            "🎬 *גלריית סרטונים* — עיון, חיפוש לפי זמן או מספר, שליחה, קטגוריות ותיקון מזהים.\n"
            "🔎 *כפילויות וסל מיחזור* — מאתר קבוצות בעלות אותו משך, מאפשר בדיקה ידנית והחזרה של סרטון שנמחק.\n"
            "👥 *משתמשים והזמנות* — סטטיסטיקה, הזמנות, בדיקת משתמש ותמיכה.\n"
            "🪙 *מטבעות וקופונים* — ניהול איזונים, הטבות, דרגות וערך המטבע.\n"
            "⚙️ *מערכת* — גיבויים, שחזור, יומן פעולות, תחזוקה וכלי בטיחות.\n"
            "👑 *ניהול מנהלים* — הוספת מנהלים, הרשאות רגילות והגדרות העוזר שלהם.\n\n"
            "אפשר לשאול אותי על כל נושא בנפרד, למשל: `מה זה יומן פעולות?` או `הסבר ערך מטבע`."
        )
    if "יומן" in text and ("פעול" in text or "מנהל" in text):
        return (
            "📜 *יומן פעולות*\n\n"
            "היומן הוא תיעוד של פעולות ניהול חשובות: מי ביצע פעולה, מתי היא בוצעה ומה היה סוג הפעולה. "
            "הוא שימושי לבקרה, לאיתור טעויות ולבדיקה מי שינה הרשאות, מחק פריט או ביצע פעולת מערכת. "
            "הוא אינו משנה נתונים בעצמו; הוא רק מתעד אותם."
        )
    if "ערך" in text and "מטבע" in text:
        return (
            "🪙 *ערך מטבע*\n\n"
            "הגדרה זו קובעת כמה מטבעות משתמש מקבל ביחס לכל סכום תשלום או כלל ההמרה שהוגדר בבוט. "
            "שינוי הערך משפיע על חישובים עתידיים בלבד; הוא לא משנה אוטומטית את יתרות המטבעות שכבר קיימות אצל משתמשים. "
            "לפני שינוי כדאי לבדוק שהערך תואם למחירים ולכמות התוכן שאתה רוצה למכור."
        )
    if "גלר" in text or "עיון בספר" in text:
        return (
            "🎬 *גלריית סרטונים*\n\n"
            "זהו מאגר הניהול של כל הסרטונים. אפשר לעבור בין סרטונים, לחפש לפי מספר או משך, לשלוח טווח תוצאות, "
            "להציג את כל המאגר בסדר מהקצר לארוך, לנהל קטגוריות ולתקן מזהי קבצים ישנים. "
            "הגלריה מיועדת למנהלים בלבד ואינה חושפת קטגוריות או רשימת סרטונים למשתמשים קונים."
        )
    if "חיפוש" in text and ("זמן" in text or "שני" in text or "דקה" in text or "מספר" in text):
        return (
            "🔎 *חיפוש סרטונים*\n\n"
            "אפשר לחפש אורך מדויק כמו `26` או `1:20`, וטווח כמו `10-13` או `1:30-2:10`. "
            "אפשר גם לחפש מספר סרטון יחיד או טווח מספרים, למשל `10-28`. "
            "בתוצאת טווח, הסרטונים נשלחים לפי סדר עולה — מהקצר לארוך או מהמספר הנמוך לגבוה."
        )
    if "כל הסרטונים" in text or "שלח הכל" in text or "שליחה של כל" in text:
        return (
            "📤 *שליחת כל הסרטונים*\n\n"
            "פעולה זו שולחת את כל סרטוני המאגר לפי משך עולה, מהקצר לארוך. בגלל שזו פעולה גדולה, הבוט מבקש "
            "לחיצה מודעת לפני תחילתה. היא אינה מוחקת או משנה את המאגר; היא רק שולחת עותקים לצ׳אט הניהול."
        )
    if "כפיל" in text or "כפול" in text or "חשוד" in text:
        return (
            "🔎 *בדיקת כפילויות*\n\n"
            "המערכת מקבצת סרטונים בעלי אותו משך זמן כחשודים לכפילות. זו אינה מחיקה אוטומטית: אתה בודק את "
            "הקבוצה, יכול לסמן אותה כלא כפולה, לעבור לקבוצה הבאה או להעביר סרטון מסוים לסל המיחזור. "
            "אפשר לבצע סריקה מחדש כדי לכלול גם סרטונים שנוספו לאחרונה."
        )
    if "סל" in text and ("מחזור" in text or "אשפה" in text):
        return (
            "🗑 *סל מיחזור*\n\n"
            "כאשר מוחקים סרטון דרך כלי הניהול, הוא עובר קודם לסל המיחזור ולא נמחק לצמיתות. "
            "מהסל אפשר לעיין, לשחזר סרטון למאגר או למחוק אותו לצמיתות רק לאחר אישור. זה מאפשר לתקן מחיקה בטעות."
        )
    if "קטגור" in text:
        return (
            "🏷 *קטגוריות*\n\n"
            "קטגוריות הן כלי ארגון פנימי למנהלים. אפשר ליצור, לשנות שם, לשכפל, למזג ולהסיר קטגוריות, "
            "ולשייך סרטון ליותר מקטגוריה אחת. כרגע הקטגוריות אינן מוצגות למשתמשים קונים."
        )
    if "גיבוי" in text or "שחזור" in text:
        return (
            "💾 *גיבוי ושחזור*\n\n"
            "גיבוי יוצר קובץ ZIP של נתוני הבוט, כגון סרטונים, משתמשים, מטבעות, הגדרות ויומן פעולות. "
            "שחזור מציג קודם תצוגה מקדימה ורק לאחר אישור מחליף את הנתונים בקובץ הגיבוי. "
            "לפני פעולות מסוכנות הבוט יוצר גם גיבוי בטיחותי אוטומטי."
        )
    if "תחזוקה" in text:
        return (
            "🔧 *מצב תחזוקה*\n\n"
            "במצב תחזוקה אפשר לעצור זמנית פעולות רגילות של משתמשים בזמן בדיקה או סידור המערכת. "
            "המנהלים עדיין יכולים לבצע פעולות ניהול. בסיום חשוב להחזיר את מצב התחזוקה לכבוי כדי שהמשתמשים יוכלו להשתמש בבוט כרגיל."
        )
    if "קופון" in text:
        return (
            "🎟 *קופונים*\n\n"
            "קופון הוא קוד הטבה שניתן למשתמשים. בניהול קופונים אפשר ליצור קוד, להגדיר את הערך או ההטבה שלו, "
            "לעקוב אחרי השימוש בו ולבטל אותו כשאינו נחוץ יותר."
        )
    if "דרג" in text or "vip" in text:
        return (
            "💎 *דרגות VIP*\n\n"
            "דרגות מאפשרות להגדיר הטבות למשתמשים לפי רמה, למשל תנאים מיוחדים או תגמולים. "
            "ניהול הדרגות קובע את הכללים; הוא אינו בוחר עבור המשתמש איזה תוכן לצפות בו."
        )
    if "הזמנ" in text or "תשלום" in text:
        return (
            "🧾 *הזמנות ואישור תשלום*\n\n"
            "מסך ההזמנות מרכז רכישות שבוצעו או ממתינות לטיפול. אישור תשלום מיועד למנהל שמוודא תשלום ידני, "
            "ולאחר האישור הבוט מזכה או פותח את מה שהוגדר עבור אותה הזמנה."
        )
    if "משתמש" in text or "סטט" in text:
        return (
            "👥 *משתמשים וסטטיסטיקה*\n\n"
            "הסטטיסטיקה מציגה תמונת מצב של הבוט, כגון כמות משתמשים, סרטונים והזמנות. "
            "בניהול משתמשים אפשר לבדוק משתמש מסוים, להציג רשימות ולטפל בפניות, לפי ההרשאות שקיבלת."
        )
    if "הודעה לכולם" in text or "הפצה" in text or "פרסום" in text:
        return (
            "📢 *הודעה לכל המשתמשים*\n\n"
            "כלי ההפצה שולח הודעה אחת לכל המשתמשים המתאימים. לפני השליחה אפשר לחזור או לבטל, ולכן כדאי "
            "לנסח את ההודעה בזהירות ולבדוק אותה לפני אישור ההפצה."
        )
    if "מנהל" in text or "הרשאה" in text:
        return (
            "👑 *ניהול מנהלים והרשאות*\n\n"
            "רק הבעלים מוסיף או מסיר מנהלים. לכל מנהל אפשר לסמן הרשאות רגילות לפעולות הבוט, ובנפרד לבחור "
            "מה עוזר הפקודות שלו רשאי לבצע. העוזר לעולם לא עוקף הרשאה רגילה."
        )
    if "עוזר" in text or "ai" in text:
        return (
            "🤖 *עוזר הפקודות*\n\n"
            "העוזר מאפשר לכתוב בקשות בעברית במקום לנווט ידנית בין כפתורים. הוא מבין חיפושי זמן ומספרים, "
            "פעולות גלריה, כפילויות, משתמשים וכלי מערכת לפי ההרשאות שלך. הוא לא פותח או צופה בסרטונים וקבצים; "
            "הוא עובד רק עם פעולות הניהול ועם הנתונים הדרושים לביצוען."
        )
    return None


async def admin_assistant_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deterministically parse safe Hebrew manager commands without an external AI service."""
    user_id = update.effective_user.id
    if not has_admin_permission(user_id, "assistant"):
        return ConversationHandler.END
    text = _assistant_normalize(update.message.text)
    ai_rewrite, ai_reply = await _assistant_ai_rewrite(text, user_id)
    if ai_reply:
        await update.message.reply_text(ai_reply, reply_markup=_assistant_navigation_keyboard())
        return ADMIN_ASSISTANT_COMMAND
    if ai_rewrite and ai_rewrite != text:
        text = _assistant_normalize(ai_rewrite)
    explanation_markers = (
        "מה זה", "מהו", "מה עושה", "מה עושים", "מה המשמעות", "הסבר", "תסביר",
        "איך עובד", "איך משתמשים", "למה משמש", "מה כל", "על מה",
    )
    if any(marker in text for marker in explanation_markers):
        explanation = _assistant_explanation(text, user_id)
        if explanation:
            await update.message.reply_text(
                explanation,
                parse_mode="Markdown",
                reply_markup=_assistant_navigation_keyboard(),
            )
        else:
            await update.message.reply_text(
                "🤖 אני יכול להסביר על גלריה, חיפוש, כפילויות, סל מיחזור, קטגוריות, משתמשים, מטבעות, "
                "קופונים, דרגות, גיבוי, שחזור, תחזוקה, יומן פעולות, הרשאות וכל כפתורי הפאנל.",
                reply_markup=_assistant_navigation_keyboard(),
            )
        return ADMIN_ASSISTANT_COMMAND
    if text in {"ביטול", "חזור", "חזרה", "יציאה", "צא", "חזור לפאנל", "חזרה לפאנל"}:
        await _assistant_clear_previous_media(context, user_id)
        await update.message.reply_text("🤖 יצאת מעוזר הפקודות.", reply_markup=get_admin_inline_keyboard(user_id))
        return ConversationHandler.END
    if text in {"היי", "הי", "שלום", "מה קורה", "מה נשמע", "בוקר טוב", "ערב טוב"} or text.startswith(("היי ", "הי ", "שלום ")):
        await update.message.reply_text(
            "🤖 היי! הכול טוב. מה תרצה שאעשה היום? אפשר לכתוב `עזרה` כדי לראות פעולות שמתאימות להרשאות שלך.",
            reply_markup=_assistant_navigation_keyboard(),
        )
        return ADMIN_ASSISTANT_COMMAND
    if text in {"תודה", "תודה רבה", "מעולה", "סבבה"}:
        await update.message.reply_text(
            "🤖 בשמחה. אני כאן כשתרצה לבצע פעולה נוספת.",
            reply_markup=_assistant_navigation_keyboard(),
        )
        return ADMIN_ASSISTANT_COMMAND
    if text in {"עזרה", "דוגמאות", "מה אתה יודע לעשות", "מה אפשר לעשות", "מה אתה יכול לעשות"}:
        await update.message.reply_text(
            "🤖 *דוגמאות לפעולות שלך:*\n" + "\n".join(_assistant_examples(user_id)),
            parse_mode="Markdown",
            reply_markup=_assistant_navigation_keyboard(),
        )
        return ADMIN_ASSISTANT_COMMAND
    if text in {"שוב", "שלח שוב", "תשלח שוב", "שלח את הקודמים", "אותם שוב"}:
        entry_ids = set(context.user_data.get("assistant_last_selection_ids", []))
        selected = [video for video in load_json(VIDEOS_FILE) if video.get("entry_id") in entry_ids]
        if selected and has_assistant_capability(user_id, "gallery"):
            await _assistant_send_videos(update, context, selected, "מהתוצאה הקודמת")
        else:
            await update.message.reply_text(
                "🤖 אין לי תוצאה קודמת שאפשר לשלוח. כתוב טווח זמן או מספרים, למשל `שלח סרטונים מ-10 עד 20 שניות`.",
                reply_markup=_assistant_navigation_keyboard(),
            )
        return ADMIN_ASSISTANT_COMMAND
    if ("שלח" in text or "תשלח" in text or "חפש" in text) and any(word in text for word in ("סרטון", "סרטונים", "וידאו")) and not re.search(r"\d", text):
        await update.message.reply_text(
            "🤖 בשמחה. איזה סרטונים לחפש? כתוב טווח זמן, למשל `10 עד 20 שניות` או `1:30 עד 2:10`, או טווח מספרים כמו `מספרים 10 עד 28`.",
            reply_markup=_assistant_navigation_keyboard(),
        )
        return ADMIN_ASSISTANT_COMMAND

    if has_assistant_capability(user_id, "gallery"):
        if ("כל הסרטונים" in text or "כל סרטון" in text) and any(word in text for word in ("שלח", "תשלח")):
            await update.message.reply_text(
                "🤖 שליחה של כל המאגר מתחילה רק בלחיצה מודעת.",
                reply_markup=_assistant_action_button("📤 שלח את כל הסרטונים", "vid_send_all"),
            )
            return ConversationHandler.END

        time_range = _assistant_time_range(text)
        if time_range and any(word in text for word in ("שלח", "חפש", "סרטון")):
            first, last = time_range
            videos = load_json(VIDEOS_FILE)
            selected = [
                video for video in videos
                if isinstance(video, dict) and first <= int(video.get("duration", 0) or 0) <= last
            ]
            selected.sort(key=lambda video: (int(video.get("duration", 0) or 0), str(video.get("entry_id", ""))))
            if not selected:
                await update.message.reply_text(
                    f"🤖 לא נמצאו סרטונים בטווח {format_duration(first)}–{format_duration(last)}. רוצה לנסות טווח אחר?",
                    reply_markup=_assistant_navigation_keyboard(),
                )
            else:
                await _assistant_send_videos(
                    update, context, selected,
                    f"בטווח {format_duration(first)}–{format_duration(last)} מהקצר לארוך",
                )
            return ADMIN_ASSISTANT_COMMAND

        number_range = _assistant_number_range(text)
        if number_range and any(word in text for word in ("שלח", "חפש", "סרטון")):
            first, last = number_range
            videos = load_json(VIDEOS_FILE)
            if last > len(videos):
                await update.message.reply_text(
                    f"🤖 המספרים חייבים להיות בין 1 ל-{len(videos)}. כתוב טווח אחר.",
                    reply_markup=_assistant_navigation_keyboard(),
                )
            else:
                selected = videos[first - 1:last]
                await _assistant_send_videos(update, context, selected, f"במספרים {first}-{last}")
            return ADMIN_ASSISTANT_COMMAND

        if "גלר" in text or "עיון בספר" in text:
            await update.message.reply_text("🤖 פותח את הגלריה.", reply_markup=_assistant_action_button("🎬 פתח גלריית סרטונים", "admin_gallery"))
            return ConversationHandler.END
        if "קטגור" in text:
            await update.message.reply_text("🤖 פותח את הקטגוריות.", reply_markup=_assistant_action_button("🏷 פתח קטגוריות", "admin_categories"))
            return ConversationHandler.END

    if has_assistant_capability(user_id, "duplicates"):
        if ("כפיל" in text or "כפול" in text or "חשוד" in text) and any(
            word in text for word in ("מצא", "תמצא", "בדוק", "תבדוק", "סרוק", "תסרוק", "הראה", "תראה")
        ):
            callback = "admin_dup_rescan" if "מחדש" in text else "admin_dup_scan"
            label = "🔄 סרוק כפילויות מחדש" if callback.endswith("rescan") else "🔎 מצא כפילויות"
            await update.message.reply_text("🤖 הפקודה מוכנה.", reply_markup=_assistant_action_button(label, callback))
            return ConversationHandler.END
        if "סל" in text and ("מחזור" in text or "אשפה" in text):
            await update.message.reply_text("🤖 פותח את סל המיחזור.", reply_markup=_assistant_action_button("🗑 פתח סל מיחזור", "admin_trash_page_0"))
            return ConversationHandler.END

    if has_assistant_capability(user_id, "users"):
        if "סטט" in text or "נתונים" in text:
            await update.message.reply_text("🤖 פותח את הסטטיסטיקה.", reply_markup=_assistant_action_button("📊 הצג סטטיסטיקה", "admin_stats"))
            return ConversationHandler.END
        if "הזמנ" in text:
            await update.message.reply_text("🤖 פותח את ההזמנות.", reply_markup=_assistant_action_button("🧾 פתח הזמנות", "admin_orders_page_0"))
            return ConversationHandler.END
        if "משתמש" in text and any(word in text for word in ("רשימ", "הצג", "פתח")):
            await update.message.reply_text("🤖 פותח את רשימת המשתמשים.", reply_markup=_assistant_action_button("👥 פתח רשימת משתמשים", "users_page_0"))
            return ConversationHandler.END

    if has_assistant_capability(user_id, "user_messages"):
        if "אשר" in text and "תשלום" in text:
            await update.message.reply_text("🤖 פותח אישור תשלום.", reply_markup=_assistant_action_button("✅ אישור תשלום", "admin_approve"))
            return ConversationHandler.END
        if "שלח" in text and "משתמש" in text:
            await update.message.reply_text("🤖 פותח שליחה למשתמש.", reply_markup=_assistant_action_button("📩 שלח למשתמש", "admin_send"))
            return ConversationHandler.END

    if has_assistant_capability(user_id, "broadcast") and ("הודעה לכולם" in text or "שלח לכולם" in text or "פרסם" in text):
        await update.message.reply_text("🤖 פותח הודעה לכל המשתמשים.", reply_markup=_assistant_action_button("📢 הודעה לכולם", "admin_broadcast"))
        return ConversationHandler.END

    if has_assistant_capability(user_id, "coins"):
        if "שליטה במטבעות" in text or "מתנה יומית" in text or "תגמול הפניה" in text or "תגמול הפניות" in text:
            await update.message.reply_text("🤖 פותח שליטה במטבעות.", reply_markup=_assistant_action_button("🪙 שליטה במטבעות", "admin_coin_control"))
            return ConversationHandler.END
        if "קופון" in text:
            await update.message.reply_text("🤖 פותח ניהול קופונים.", reply_markup=_assistant_action_button("🎟 ניהול קופונים", "admin_coupons"))
            return ConversationHandler.END
        if "דרג" in text or "vip" in text:
            await update.message.reply_text("🤖 פותח ניהול דרגות.", reply_markup=_assistant_action_button("💎 ניהול דרגות", "admin_vip"))
            return ConversationHandler.END
        if "מטבע" in text:
            await update.message.reply_text("🤖 פותח ניהול מטבעות.", reply_markup=_assistant_action_button("🪙 ניהול מטבעות", "admin_coins"))
            return ConversationHandler.END

    if has_assistant_capability(user_id, "audit_log") and ("יומן" in text or "פעולות" in text):
        await update.message.reply_text("🤖 פותח את יומן הפעולות.", reply_markup=_assistant_action_button("📜 יומן פעולות", "admin_actions_page_0"))
        return ConversationHandler.END

    if has_assistant_capability(user_id, "backup") and ("גיבוי" in text and any(word in text for word in ("צור", "עשה", "הכן"))):
        await update.message.reply_text("🤖 יצירת הגיבוי דורשת לחיצה מודעת.", reply_markup=_assistant_action_button("💾 צור גיבוי ZIP", "admin_backup"))
        return ConversationHandler.END
    if has_assistant_capability(user_id, "backup") and ("שחזור" in text or "שחזר" in text):
        await update.message.reply_text("🤖 שחזור דורש בחירת קובץ ואישור ידני.", reply_markup=_assistant_action_button("📥 שחזור גיבוי", "admin_restore"))
        return ConversationHandler.END

    await update.message.reply_text(
        "🤖 לא הייתי בטוח למה התכוונת. נסה לנסח אחרת או כתוב `עזרה` כדי לראות פעולות שמתאימות להרשאות שלך.",
        reply_markup=_assistant_navigation_keyboard(),
    )
    return ADMIN_ASSISTANT_COMMAND


# ─── Admin: stats ─────────────────────────────────────────────────────────────

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
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
    if not is_admin(query.from_user.id):
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
    if not is_admin(query.from_user.id):
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
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    await query.edit_message_text("🔍 *בדיקת משתמש*\n\nשלח את ה-ID:", parse_mode="Markdown", reply_markup=_flow_back_markup())
    return ADMIN_CHECK_USER

async def admin_check_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
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

# ─── Admin: activity log ─────────────────────────────────────────────────────

async def admin_actions_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    try:
        page = int(query.data.rsplit("_", 1)[1])
    except ValueError:
        page = 0
    records = load_json(ADMIN_ACTIONS_FILE)
    if not isinstance(records, list) or not records:
        await query.edit_message_text(
            "📜 יומן הפעולות עדיין ריק.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]),
        )
        return
    newest_first = list(reversed(records))
    per_page = 8
    pages = max(1, (len(newest_first) + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    slice_start = page * per_page
    batch = newest_first[slice_start:slice_start + per_page]
    lines = ["📜 *יומן פעולות מנהל*\n"]
    for record in batch:
        when = str(record.get("at", ""))[:19].replace("T", " ")
        action = record.get("action", "לא ידוע")
        actor = record.get("admin_id", "?")
        lines.append(f"• `{when}` — *{action}*\n  מנהל: `{actor}`")
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton("⬅️ קודם", callback_data=f"admin_actions_page_{page - 1}"))
    navigation.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        navigation.append(InlineKeyboardButton("הבא ➡️", callback_data=f"admin_actions_page_{page + 1}"))
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            navigation,
            [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")],
        ]),
    )

# ─── Admin: send videos to user ──────────────────────────────────────────────

async def admin_send_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    await query.edit_message_text("📩 *שליחת הודעה למשתמש*\n\nרשום את ההודעה שברצונך לשלוח:", parse_mode="Markdown", reply_markup=_flow_back_markup())
    return ADMIN_SEND_MSG

async def admin_send_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    msg = update.message.text.strip()
    context.user_data["admin_msg_text"] = msg
    await update.message.reply_text("שלח את ה-ID של המשתמש אליו תישלח ההודעה:", reply_markup=_flow_back_markup())
    return ADMIN_SEND_ID

async def admin_send_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
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
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    await query.edit_message_text("✅ *אישור תשלום ידני*\n\nכמה סרטונים לשלוח למשתמש?", parse_mode="Markdown", reply_markup=_flow_back_markup())
    return ADMIN_APPROVE_COUNT

async def admin_approve_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    try:
        count = int(update.message.text.strip())
        context.user_data["approve_v_count"] = count
    except ValueError:
        await update.message.reply_text("❌ מספר לא תקין.", reply_markup=_flow_back_markup())
        return ADMIN_APPROVE_COUNT
    await update.message.reply_text("שלח את ה-ID של המשתמש לאישור:", reply_markup=_flow_back_markup())
    return ADMIN_APPROVE_ID

async def admin_approve_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    uid   = update.message.text.strip()
    count = context.user_data.get("approve_v_count", 0)
    
    try:
        available = count_unseen_videos(int(uid))
        if available < count:
            await update.message.reply_text(
                f"❌ למשתמש נותרו רק {available} סרטונים חדשים שעדיין לא קיבל. "
                "לא נשלחה חבילה כדי למנוע חזרות.",
                reply_markup=get_admin_inline_keyboard(),
            )
            return ConversationHandler.END
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
    # Leaving library, trash, or duplicate review must remove the preview video from the chat.
    await clear_sent_duplicate_group_media(context)
    # When this callback is the lower duplicate-review control, it becomes the gallery message.
    user_data = getattr(context, "user_data", None)
    if isinstance(user_data, dict):
        user_data.pop("dup_review_control_message_id", None)
    user_id = query.from_user.id
    can_manage_gallery = has_admin_permission(user_id, "gallery")
    can_manage_duplicates = has_admin_permission(user_id, "duplicates")
    if not (can_manage_gallery or can_manage_duplicates):
        return

    keyboard = []
    if can_manage_gallery:
        keyboard.extend([
            [InlineKeyboardButton("🎬 עיון בספריה", callback_data="vid_page_0")],
            [InlineKeyboardButton("🏷 קטגוריות", callback_data="admin_categories")],
            [InlineKeyboardButton("📤 שלח את כל הסרטונים", callback_data="vid_send_all")],
            [InlineKeyboardButton("🛠 תיקון מזהים שבורים", callback_data="admin_repair_start")],
        ])
    if can_manage_duplicates:
        keyboard.extend([
            [
                InlineKeyboardButton("🔎 מצא כפילויות", callback_data="admin_dup_scan"),
                InlineKeyboardButton("🔄 מצא כפילויות מחדש", callback_data="admin_dup_rescan"),
            ],
            [InlineKeyboardButton("🗑 סל מיחזור", callback_data="admin_trash_page_0")],
        ])
    keyboard.append([InlineKeyboardButton("🔙 חזור לפאנל", callback_data="back_admin")])

    text = """🎬 *ניהול גלריה ומדיה*

כאן תוכל לנהל את הסרטונים, לאתר כפילויות ולשחזר סרטונים שנמחקו — בהתאם להרשאות שקיבלת."""
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_gallery_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=None):
    query = update.callback_query
    if page is None:
        page = int(query.data.split("vid_page_")[1])
    
    # Cleanup previous review media
    await clear_sent_duplicate_group_media(context)
    
    videos = load_videos_with_entry_ids()
    total  = len(videos)
    if total == 0:
        await query.edit_message_text("אין סרטונים במאגר.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_gallery")]]))
        return

    page = max(0, min(page, total - 1))
    v = videos[page]
    text = f"""🎬 *גלריית סרטונים ({page+1}/{total})*

📁 קטגוריות: {display_video_categories(v)}
⏱ אורך: {format_duration(v.get('duration', 0))}"""
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"vid_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total}", callback_data="noop"))
    if page < total - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"vid_page_{page+1}"))
        
    btns = [
        nav,
        [
            InlineKeyboardButton("🔢 חיפוש לפי מספר", callback_data="admin_video_search"),
            InlineKeyboardButton("⏱ חיפוש לפי זמן", callback_data="admin_search_sec_start"),
        ],
        [InlineKeyboardButton("🗑 מחק סרטון זה", callback_data=f"vid_del_{page}")],
        [InlineKeyboardButton("🔙 חזרה לגלריה", callback_data="admin_gallery")]
    ]
    
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    except Exception:
        pass
    
    sent_msg = await send_admin_video_with_delete_button(
        context.bot, v["file_id"], v["entry_id"], include_category_assignment=True
    )
    if sent_msg and sent_msg != "INVALID_FILE_ID":
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

    await clear_sent_duplicate_group_media(context)
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

    await clear_sent_duplicate_group_media(context)
    context.user_data.pop("dup_review_control_message_id", None)
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
        [InlineKeyboardButton("🔁 שלח שוב את הקבוצה", callback_data=f"dup_send_{page}")],
        [
            InlineKeyboardButton("✅ סמן כלא כפול", callback_data=f"dup_mark_{page}"),
            InlineKeyboardButton("🔙 חזרה לגלריה", callback_data="dup_back_gallery"),
        ],
        navigation,
    ])


async def clear_sent_duplicate_group_media(context: ContextTypes.DEFAULT_TYPE) -> int:
    """Remove preview videos sent for the current library, trash, or duplicate-review item."""
    user_data = getattr(context, "user_data", None)
    if not isinstance(user_data, dict):
        return 0
    message_ids = user_data.pop("dup_sent_media_message_ids", [])
    removed_count = 0
    for message_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=ADMIN_ID, message_id=message_id)
            removed_count += 1
        except Exception as exc:
            # A video may already have been deleted manually; this must not block navigation.
            logger.info(f"Could not remove prior preview media message {message_id}: {exc}")
    return removed_count


async def clear_duplicate_review_control_message(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Remove the old lower action panel before placing the next group and its controls."""
    user_data = getattr(context, "user_data", None)
    if not isinstance(user_data, dict):
        return False
    message_id = user_data.pop("dup_review_control_message_id", None)
    if not message_id:
        return False
    try:
        await context.bot.delete_message(chat_id=ADMIN_ID, message_id=message_id)
        return True
    except Exception as exc:
        logger.info(f"Could not remove prior duplicate-review control message {message_id}: {exc}")
        return False


async def admin_dup_back_to_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await clear_sent_duplicate_group_media(context)
    # Keep the lower control itself: it is edited into the gallery menu by admin_gallery.
    user_data = getattr(context, "user_data", None)
    if isinstance(user_data, dict):
        user_data.pop("dup_review_control_message_id", None)
    await admin_gallery(update, context)


async def send_duplicate_group_media(context: ContextTypes.DEFAULT_TYPE, group: list[dict]) -> tuple[int, int]:
    """Send one review group immediately and remember only its media for clean navigation."""
    await clear_sent_duplicate_group_media(context)
    success_count = 0
    failed_count = 0
    sent_message_ids = []
    for video in group:
        entry_id = video.get("entry_id")
        if not entry_id:
            failed_count += 1
            continue
        sent_message = await send_admin_video_with_delete_button(context.bot, video["file_id"], entry_id)
        if sent_message and sent_message != "INVALID_FILE_ID":
            success_count += 1
            sent_message_ids.append(sent_message.message_id)
        else:
            failed_count += 1
        await asyncio.sleep(0.2)
    context.user_data["dup_sent_media_message_ids"] = sent_message_ids
    return success_count, failed_count


async def admin_dup_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=None):
    query = update.callback_query
    if page is None:
        page = int(query.data.split("dup_page_")[1])
        await query.answer()

    # A navigation click comes from the lower control message. Remove it and the old media,
    # then send the next group's controls after its videos instead of leaving them above.
    had_lower_control = bool(context.user_data.get("dup_review_control_message_id"))
    await clear_sent_duplicate_group_media(context)
    if had_lower_control:
        await clear_duplicate_review_control_message(context)

    groups = context.user_data.get("dup_groups", [])
    total = len(groups)
    if page >= total:
        completion_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="dup_back_gallery")]])
        if had_lower_control:
            await context.bot.send_message(chat_id=ADMIN_ID, text="✅ סיימת לעבור על כל הכפילויות!", reply_markup=completion_markup)
        else:
            await query.edit_message_text("✅ סיימת לעבור על כל הכפילויות!", reply_markup=completion_markup)
        return

    group = groups[page]
    duration = group[0].get("duration", 0)
    if not had_lower_control:
        await query.edit_message_text(
            f"🔎 *חשד לכפילות ({page + 1}/{total})*\n\n⏱ אורך משותף: {format_duration(duration)}\n"
            f"👥 מספר סרטונים בקבוצה: {len(group)}\n\n⏳ שולח את סרטוני הקבוצה אוטומטית לבדיקה...",
            parse_mode="Markdown",
        )

    success_count, failed_count = await send_duplicate_group_media(context, group)
    status = (
        f"✅ נשלחו אוטומטית {success_count}/{len(group)} סרטונים חשודים. "
        "אפשר למחוק סרטון, לסמן כלא כפול או לעבור לקבוצה הבאה."
    )
    if failed_count:
        status += f"\n⚠️ {failed_count} סרטונים לא נשלחו וסומנו לבדיקה."
    lower_control = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=status,
        reply_markup=duplicate_group_keyboard(page, total),
    )
    if lower_control:
        context.user_data["dup_review_control_message_id"] = lower_control.message_id


async def admin_dup_send_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resend the group while keeping its action controls below the newly sent videos."""
    query = update.callback_query
    page = int(query.data.replace("dup_send_", ""))
    groups = context.user_data.get("dup_groups", [])
    if not (0 <= page < len(groups)):
        await query.answer("הקבוצה כבר אינה זמינה. יש לבצע סריקה מחדש.", show_alert=True)
        return
    await admin_dup_page(update, context, page)

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

⏱ אורך: {format_duration(v.get('duration', 0))}
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
        backup = create_auto_backup("permanent_video_delete", query.from_user.id)
        if not backup:
            await query.answer("לא נוצר גיבוי בטיחותי ולכן המחיקה בוטלה.", show_alert=True)
            return
        removed = trash.pop(idx)
        save_json(TRASH_FILE, trash)
        log_admin_action(query.from_user.id, "video_permanently_deleted", {"entry_id": removed.get("entry_id")})
        await query.answer("🗑 נמחק לצמיתות לאחר יצירת גיבוי בטיחותי.", show_alert=True)
        await admin_trash_page(update, context, 0)

async def admin_trash_empty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    backup = create_auto_backup("empty_trash", query.from_user.id)
    if not backup:
        await query.answer("לא נוצר גיבוי בטיחותי ולכן ריקון הסל בוטל.", show_alert=True)
        return
    count = len(load_json(TRASH_FILE))
    save_json(TRASH_FILE, [])
    log_admin_action(query.from_user.id, "trash_emptied", {"removed_entries": count})
    await query.answer("🧹 סל המיחזור רוקן לאחר יצירת גיבוי בטיחותי!", show_alert=True)
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
    broken = 0
    for v in sorted_videos:
        try:
            sent = await send_admin_video_with_delete_button(context.bot, v["file_id"], v["entry_id"])
            if sent == "INVALID_FILE_ID":
                broken += 1
                v["file_status"] = "broken"
                v["file_checked_at"] = datetime.now(timezone.utc).isoformat()
            elif sent:
                success += 1
                v["file_status"] = "valid"
                v["file_checked_at"] = datetime.now(timezone.utc).isoformat()
            await asyncio.sleep(0.15) # שליחה מהירה אך בטוחה
        except Exception:
            await asyncio.sleep(1.0)

    # sorted_videos holds the same dictionaries as videos, so statuses are persisted safely.
    save_json(VIDEOS_FILE, videos)
            
    report = f"✅ סיימתי לשלוח את המאגר! ({success}/{total} נשלחו בהצלחה)"
    if broken > 0:
        report += f"\n\n⚠️ נמצאו {broken} סרטונים עם מזהים שבורים. השתמש בכלי התיקון בגלריה כדי לעדכן אותם."
        
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=report,
        reply_markup=get_admin_inline_keyboard()
    )





# ─── Admin: Database Repair & Re-upload ───────────────────────────────────────

BROKEN_FILE_STATUSES = {"broken", "broken_skipped"}


def _invalid_file_id_error(error: Exception) -> bool:
    """Return True only for Telegram errors that prove the file_id is unusable."""
    message = str(error).lower()
    markers = (
        "wrong file identifier",
        "wrong file_id",
        "file_id",
        "file identifier",
        "failed to get http url content",
    )
    return isinstance(error, BadRequest) and any(marker in message for marker in markers)


async def _check_video_file_id(bot, video: dict, attempts: int = 3):
    """Validate a stored file_id without sending media into the administrator chat.

    Returns True for a valid identifier, False for a proven broken identifier and
    None for a temporary/unknown Telegram error.
    """
    file_id = video.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        return False

    for attempt in range(1, attempts + 1):
        try:
            await bot.get_file(file_id)
            return True
        except RetryAfter as exc:
            retry_after = exc.retry_after
            delay = retry_after.total_seconds() if hasattr(retry_after, "total_seconds") else float(retry_after)
            await asyncio.sleep(delay + 1)
        except BadRequest as exc:
            if _invalid_file_id_error(exc):
                return False
            logger.warning("Unexpected Telegram validation error for video %s: %s", video.get("entry_id"), exc)
            return None
        except (TimedOut, NetworkError) as exc:
            if attempt == attempts:
                logger.warning("Temporary error validating video %s: %s", video.get("entry_id"), exc)
                return None
            await asyncio.sleep(min(2 ** attempt, 10))
        except Exception as exc:
            logger.warning("Could not validate video %s: %s", video.get("entry_id"), exc)
            return None
    return None


def _broken_video_entries(videos: list[dict]) -> list[str]:
    return [
        video.get("entry_id")
        for video in videos
        if isinstance(video, dict) and video.get("entry_id") and video.get("file_status") in BROKEN_FILE_STATUSES
    ]


async def admin_repair_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open the repair menu. This handler is deliberately standalone, not a conversation entry point."""
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    videos = load_videos_with_entry_ids()
    broken = _broken_video_entries(videos)
    lines = [
        "🛠 *תיקון סרטונים מהבוט הישן*",
        "",
        "לאחר החלפת טוקן, Telegram לא מאפשר לבוט החדש להשתמש במזהי הקבצים של הבוט הישן.",
        "הסריקה בודקת את המזהים בלי לשלוח סרטונים לצ׳אט. לאחר מכן אפשר להעלות מחדש כל קובץ חסר, והבוט מחליף רק את המזהה שלו במאגר.",
        "",
        f"📚 סרטונים במאגר: {len(videos)}",
        f"⚠️ כבר זוהו כמזהים שבורים: {len(broken)}",
    ]
    keyboard = [[InlineKeyboardButton("🔍 סריקה מלאה של המזהים", callback_data="admin_repair_scan")]]
    if broken:
        keyboard.append([InlineKeyboardButton(f"▶️ המשך תיקון של {len(broken)} סרטונים", callback_data="admin_repair_cached")])
    keyboard.append([InlineKeyboardButton("🔙 חזרה לגלריה", callback_data="admin_gallery")])
    await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_repair_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Scan all stored identifiers through getFile and begin the re-upload flow."""
    query = update.callback_query
    await query.answer("הסריקה התחילה")
    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    videos = load_videos_with_entry_ids()
    if not videos:
        await query.edit_message_text("אין סרטונים במאגר.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_gallery")]]))
        return ConversationHandler.END

    total = len(videos)
    broken_ids = []
    valid = 0
    unknown = 0
    now = datetime.now(timezone.utc).isoformat()

    await query.edit_message_text(f"🔍 בודק את מזהי הקבצים: 0/{total}")
    for index, video in enumerate(videos, start=1):
        result = await _check_video_file_id(context.bot, video)
        video["file_checked_at"] = now
        if result is True:
            video["file_status"] = "valid"
            valid += 1
        elif result is False:
            video["file_status"] = "broken"
            broken_ids.append(video["entry_id"])
        else:
            video["file_status"] = "unknown"
            unknown += 1

        if index % 20 == 0 or index == total:
            save_json(VIDEOS_FILE, videos)
            try:
                await query.edit_message_text(f"🔍 בודק את מזהי הקבצים: {index}/{total}")
            except BadRequest as exc:
                if "Message is not modified" not in str(exc):
                    raise

        # A short pause avoids a request burst while keeping a 500+ library practical.
        await asyncio.sleep(0.04)

    save_json(VIDEOS_FILE, videos)
    context.user_data["repair_list"] = broken_ids
    context.user_data["repair_index"] = 0
    context.user_data["repair_scan_summary"] = {"valid": valid, "broken": len(broken_ids), "unknown": unknown}
    return await admin_repair_show_current(update, context)


async def admin_repair_cached(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume the re-upload queue built by a prior full scan."""
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    videos = load_videos_with_entry_ids()
    broken_ids = _broken_video_entries(videos)
    if not broken_ids:
        await query.edit_message_text(
            "✅ אין כרגע סרטונים שסומנו כשבורים. אפשר לבצע סריקה מלאה חדשה.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_repair_start")]]),
        )
        return ConversationHandler.END

    context.user_data["repair_list"] = broken_ids
    context.user_data["repair_index"] = 0
    return await admin_repair_show_current(update, context)


async def admin_repair_show_current(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt for the original file of the current broken video; never sends the broken file_id."""
    repair_list = context.user_data.get("repair_list", [])
    index = int(context.user_data.get("repair_index", 0))

    while index < len(repair_list):
        entry_id = repair_list[index]
        videos = load_videos_with_entry_ids()
        video = next((item for item in videos if item.get("entry_id") == entry_id), None)
        if video and video.get("file_status") in BROKEN_FILE_STATUSES:
            break
        index += 1
        context.user_data["repair_index"] = index

    if index >= len(repair_list):
        summary = context.user_data.get("repair_scan_summary", {})
        text = "✅ *הטיפול ברשימת הסרטונים השבורים הסתיים.*"
        if summary:
            text += f"\n\nבסריקה: {summary.get('valid', 0)} תקינים, {summary.get('broken', 0)} שבורים, {summary.get('unknown', 0)} ללא תוצאה ודאית."
        text += "\n\nאם דילגת על סרטונים, אפשר לבצע סריקה מלאה שוב כדי להציג אותם מחדש."
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לגלריה", callback_data="admin_gallery")]])
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        else:
            await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
        context.user_data.pop("repair_list", None)
        context.user_data.pop("repair_index", None)
        return ConversationHandler.END

    context.user_data["repair_index"] = index
    duration = video.get("duration", 0)
    category = display_video_categories(video)
    size_bytes = video.get("file_size")
    size_text = f"\n📦 גודל: {size_bytes / (1024 * 1024):.2f} MB" if isinstance(size_bytes, (int, float)) and size_bytes else ""
    text = (
        f"⚠️ *סרטון עם מזהה שבור ({index + 1}/{len(repair_list)})*\n\n"
        f"⏱ אורך במאגר: {format_duration(duration)}\n"
        f"📁 קטגוריה: {category}{size_text}\n\n"
        "שלח עכשיו את *אותו קובץ וידאו מקורי* לבוט.\n"
        "הבוט יעדכן רק את מזהה הקובץ, וישמור את שאר פרטי הסרטון."
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ דלג כרגע", callback_data="admin_repair_skip")],
        [InlineKeyboardButton("❌ ביטול", callback_data="admin_repair_cancel")],
    ])
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
    return ADMIN_REPAIR_UPLOAD


async def admin_repair_handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the fresh file_id supplied by the administrator for the current item."""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    if not update.message.video:
        await update.message.reply_text("יש לשלוח קובץ וידאו בלבד, או ללחוץ על ׳דלג כרגע׳.")
        return ADMIN_REPAIR_UPLOAD

    repair_list = context.user_data.get("repair_list", [])
    index = int(context.user_data.get("repair_index", 0))
    if index >= len(repair_list):
        return await admin_repair_show_current(update, context)

    entry_id = repair_list[index]
    videos = load_videos_with_entry_ids()
    video = next((item for item in videos if item.get("entry_id") == entry_id), None)
    if video is None:
        context.user_data["repair_index"] = index + 1
        return await admin_repair_show_current(update, context)

    incoming = update.message.video
    video["file_id"] = incoming.file_id
    video["duration"] = incoming.duration or video.get("duration", 0)
    video["file_size"] = incoming.file_size
    video["file_status"] = "valid"
    video["file_checked_at"] = datetime.now(timezone.utc).isoformat()
    video["repaired_at"] = datetime.now(timezone.utc).isoformat()
    save_json(VIDEOS_FILE, videos)

    context.user_data["repair_index"] = index + 1
    await update.message.reply_text("✅ הסרטון עודכן ונשמר במאגר החדש.")
    return await admin_repair_show_current(update, context)


async def admin_repair_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    repair_list = context.user_data.get("repair_list", [])
    index = int(context.user_data.get("repair_index", 0))
    if index < len(repair_list):
        videos = load_videos_with_entry_ids()
        for video in videos:
            if video.get("entry_id") == repair_list[index]:
                video["file_status"] = "broken_skipped"
                break
        save_json(VIDEOS_FILE, videos)
    context.user_data["repair_index"] = index + 1
    return await admin_repair_show_current(update, context)


async def admin_repair_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("repair_list", None)
    context.user_data.pop("repair_index", None)
    await admin_gallery(update, context)
    return ConversationHandler.END


# ─── Admin: video search ──────────────────────────────────────────────────────

async def admin_video_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    if not has_admin_permission(query.from_user.id, "gallery"):
        return ConversationHandler.END
    videos = load_json(VIDEOS_FILE)
    await query.edit_message_text(
        f"🔢 *חיפוש סרטונים לפי מספר*\n\nיש {len(videos)} סרטונים.\n"
        f"שלח מספר יחיד (למשל `26`) או טווח מספרים (למשל `10-28`).\n"
        f"הטווח החוקי הוא 1–{len(videos)}.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול / חזור לעיון", callback_data="admin_gallery")]]),
    )
    return ADMIN_VIDEO_SEARCH


def parse_number_range(text: str, maximum: int) -> tuple[int, int] | None:
    """Parse one video number or an inclusive number range such as 10-28."""
    normalized = text.strip().replace("–", "-").replace("—", "-")
    parts = normalized.split("-")
    if len(parts) not in {1, 2} or any(not part.strip().isdigit() for part in parts):
        return None
    first = int(parts[0])
    last = int(parts[-1])
    if first < 1 or last < first or last > maximum:
        return None
    return first, last


async def admin_video_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_admin_permission(update.effective_user.id, "gallery"):
        return ConversationHandler.END
    videos = load_videos_with_entry_ids()
    number_range = parse_number_range(update.message.text, len(videos))
    if not number_range:
        await update.message.reply_text(
            f"❌ מספר או טווח לא תקינים. כתוב מספר בין 1 ל-{len(videos)} או טווח כמו `10-28`.",
            parse_mode="Markdown",
        )
        return ADMIN_VIDEO_SEARCH

    first, last = number_range
    selected = list(enumerate(videos[first - 1:last], start=first - 1))
    await clear_sent_duplicate_group_media(context)
    label = str(first) if first == last else f"{first}-{last}"
    await update.message.reply_text(f"🔢 נמצאו {len(selected)} סרטונים במספרים {label}. שולח לפי הסדר...")

    success = 0
    sent_message_ids = []
    for index, video in selected:
        try:
            sent = await send_admin_video_with_delete_button(
                context.bot, video["file_id"], video["entry_id"], include_category_assignment=True
            )
            if sent and sent != "INVALID_FILE_ID":
                success += 1
                sent_message_ids.append(sent.message_id)
            await asyncio.sleep(0.15)
        except Exception:
            logger.exception("Failed to send numbered search result %s", index + 1)
    context.user_data["dup_sent_media_message_ids"] = sent_message_ids
    await update.message.reply_text(
        f"✅ סיימתי לשלוח את תוצאות החיפוש ({success}/{len(selected)} נשלחו).",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 חזרה לעיון בספריה", callback_data=f"vid_page_{first - 1}")],
            [
                InlineKeyboardButton("🔢 חיפוש לפי מספר", callback_data="admin_video_search"),
                InlineKeyboardButton("⏱ חיפוש לפי זמן", callback_data="admin_search_sec_start"),
            ],
        ]),
    )
    return ConversationHandler.END

# ─── Admin: video search by seconds ───────────────────────────────────────────

async def admin_search_sec_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not has_admin_permission(query.from_user.id, "gallery"):
        return ConversationHandler.END
    await query.edit_message_text(
        "⏱ *חיפוש סרטונים לפי זמן*\n\n"
        "אפשר לחפש זמן יחיד: `26` או `1:20`.\n"
        "אפשר גם לחפש טווח כולל: `10-13` או `1:30-22:30`.\n\n"
        "הזמן מוצג בפורמט דקות:שניות, והסרטונים יישלחו מהקצר לארוך.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="admin_gallery")]])
    )
    return ADMIN_VIDEO_SEARCH_SECONDS


def parse_smart_time(text: str) -> int:
    """Parse '26' as 26s, '1:20' as 80s, and reject invalid minute:second values."""
    text = text.strip()
    if ":" in text:
        parts = text.split(":")
        if len(parts) == 2 and all(part.strip().isdigit() for part in parts):
            minutes, seconds = (int(part) for part in parts)
            if 0 <= seconds < 60:
                return minutes * 60 + seconds
        return -1
    return int(text) if text.isdigit() else -1


def parse_smart_time_range(text: str) -> tuple[int, int] | None:
    """Parse one time or an inclusive time range such as 10-13 or 1:30-22:30."""
    normalized = text.strip().replace("–", "-").replace("—", "-")
    parts = normalized.split("-")
    if len(parts) not in {1, 2}:
        return None
    first = parse_smart_time(parts[0])
    last = parse_smart_time(parts[-1])
    if first < 0 or last < first:
        return None
    return first, last


async def admin_search_sec_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_admin_permission(update.effective_user.id, "gallery"):
        return ConversationHandler.END

    text = update.message.text.strip()
    if text.lower() == "cancel":
        await update.message.reply_text("החיפוש בוטל.", reply_markup=get_admin_inline_keyboard())
        return ConversationHandler.END

    time_range = parse_smart_time_range(text)
    if not time_range:
        await update.message.reply_text(
            "❌ פורמט לא תקין. כתוב זמן יחיד כמו `26` או `1:20`, או טווח כמו `10-13` או `1:30-22:30`.",
            parse_mode="Markdown",
        )
        return ADMIN_VIDEO_SEARCH_SECONDS

    first, last = time_range
    videos = load_videos_with_entry_ids()
    results = [
        video for video in videos
        if isinstance(video, dict) and first <= int(video.get("duration", 0) or 0) <= last
    ]
    results.sort(key=lambda video: (int(video.get("duration", 0) or 0), str(video.get("entry_id", ""))))

    if not results:
        await update.message.reply_text(
            f"❌ לא נמצאו סרטונים בטווח {format_duration(first)}–{format_duration(last)}.",
            reply_markup=get_admin_inline_keyboard(),
        )
        return ConversationHandler.END

    label = format_duration(first) if first == last else f"{format_duration(first)}–{format_duration(last)}"
    await clear_sent_duplicate_group_media(context)
    await update.message.reply_text(f"🔎 נמצאו {len(results)} סרטונים בטווח {label}. שולח מהקצר לארוך...")

    success = 0
    sent_message_ids = []
    for video in results:
        try:
            sent = await send_admin_video_with_delete_button(
                context.bot, video["file_id"], video["entry_id"], include_category_assignment=True
            )
            if sent and sent != "INVALID_FILE_ID":
                success += 1
                sent_message_ids.append(sent.message_id)
            await asyncio.sleep(0.15)
        except Exception:
            logger.exception("Failed to send time-range search result")
    context.user_data["dup_sent_media_message_ids"] = sent_message_ids

    await update.message.reply_text(
        f"✅ סיימתי לשלוח את תוצאות החיפוש ({success}/{len(results)} נשלחו).",
        reply_markup=get_admin_inline_keyboard(),
    )
    return ConversationHandler.END

# ─── Admin: Private Category Management ───────────────────────────────────────


def _admin_categories() -> list[str]:
    """Return private categories in the saved alphabetical or manual order."""
    settings = load_settings()
    return normalize_category_list(
        settings.get("categories", []),
        alphabetical=(settings.get("category_order_mode", "alphabetical") == "alphabetical"),
    )


def _valid_category_name(name: str) -> str | None:
    name = name.strip()
    if not name or len(name) > 32 or "\n" in name:
        return None
    return name


async def admin_categories_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await clear_sent_duplicate_group_media(context)
    categories = _admin_categories()
    order_mode = load_settings().get("category_order_mode", "alphabetical")
    order_label = "א׳–ת׳" if order_mode == "alphabetical" else "סדר ידני"
    text = "🏷 *קטגוריות — כלי ניהול פרטי*\n\n"
    text += f"סדר נוכחי: *{order_label}*\n\nהקטגוריות הקיימות:\n" + "\n".join(f"• {category}" for category in categories)
    text += "\n\nהקטגוריות אינן מוצגות למשתמשים כרגע. בעתיד, בחירה ב׳רנדומלי׳ תבחר סרטון מתוך קטגוריה זו באופן אקראי."
    buttons = [
        [InlineKeyboardButton("📂 עיון ושליחה לפי קטגוריה", callback_data="admin_cat_browse")],
        [InlineKeyboardButton("↕️ סדר קטגוריות", callback_data="admin_cat_order")],
        [InlineKeyboardButton("✏️ עריכת קטגוריות", callback_data="admin_cat_edit")],
        [InlineKeyboardButton(f"▶️ המשך מיון ({len(_category_sort_pending_videos())})", callback_data="admin_cat_sort_continue")],
        [InlineKeyboardButton("🔄 מיון מחדש — כל הסרטונים", callback_data="admin_cat_sort_rescan")],
        [InlineKeyboardButton("🔙 חזרה לגלריה", callback_data="admin_gallery")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))



def _category_videos(category: str) -> list[dict]:
    """Return all entries of one private admin category, ordered by duration."""
    return sorted(
        [
            video for video in load_videos_with_entry_ids()
            if category in video_categories(video)
        ],
        key=lambda video: (video.get("duration", 0), video.get("added_at", "")),
    )


async def admin_cat_order_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show manual up/down controls while retaining an explicit alphabetical reset."""
    query = update.callback_query
    await query.answer()
    categories = _admin_categories()
    order_mode = load_settings().get("category_order_mode", "alphabetical")
    mode_text = "א׳–ת׳" if order_mode == "alphabetical" else "ידני"
    buttons = []
    for index, category in enumerate(categories):
        buttons.append([InlineKeyboardButton(f"{index + 1}. {category}", callback_data="noop")])
        moves = []
        if index > 0:
            moves.append(InlineKeyboardButton("⬆️ למעלה", callback_data=f"cat_order_up_{index}"))
        if index < len(categories) - 1:
            moves.append(InlineKeyboardButton("⬇️ למטה", callback_data=f"cat_order_down_{index}"))
        if moves:
            buttons.append(moves)
    buttons.extend([
        [InlineKeyboardButton("🔤 מיין מחדש לפי א׳–ת׳", callback_data="cat_order_alpha")],
        [InlineKeyboardButton("🔙 חזרה לקטגוריות", callback_data="admin_categories")],
    ])
    await query.edit_message_text(
        f"↕️ *סדר קטגוריות*\n\nהסדר הנוכחי: *{mode_text}*. "
        "אפשר להזיז כל קטגוריה למעלה או למטה, או להחזיר את כולן למיון א׳–ת׳.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_cat_order_move(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    match = re.fullmatch(r"cat_order_(up|down)_(\d+)", query.data or "")
    if not match:
        return
    direction, raw_index = match.groups()
    index = int(raw_index)
    categories = _admin_categories()
    target = index - 1 if direction == "up" else index + 1
    if not 0 <= index < len(categories) or not 0 <= target < len(categories):
        await query.answer("הקטגוריה אינה זמינה להזזה.", show_alert=True)
        return
    categories[index], categories[target] = categories[target], categories[index]
    settings = load_settings()
    settings["category_order_mode"] = "manual"
    settings["categories"] = categories
    save_settings(settings)
    await admin_cat_order_menu(update, context)


async def admin_cat_order_alphabetical(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    settings = load_settings()
    settings["category_order_mode"] = "alphabetical"
    settings["categories"] = normalize_category_list(settings.get("categories", []), alphabetical=True)
    save_settings(settings)
    await admin_cat_order_menu(update, context)


async def admin_cat_browse_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    categories = _admin_categories()
    buttons = []
    for index, category in enumerate(categories):
        count = len(_category_videos(category))
        buttons.append([
            InlineKeyboardButton(f"📁 {category} ({count})", callback_data=f"cat_browse_pick_{index}")
        ])
    buttons.append([InlineKeyboardButton("🔙 חזרה לקטגוריות", callback_data="admin_categories")])
    await query.edit_message_text(
        "📂 *עיון ושליחה לפי קטגוריה*\n\nבחר קטגוריה כדי לראות את מספר הסרטונים שבה, לעיין בהם, או לשלוח את כולם אליך.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_cat_browse_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    try:
        index = int(query.data.rsplit("_", 1)[1])
    except ValueError:
        await admin_cat_browse_menu(update, context)
        return
    categories = _admin_categories()
    if not 0 <= index < len(categories):
        await query.answer("הקטגוריה אינה זמינה. חזור ובחר מחדש.", show_alert=True)
        await admin_cat_browse_menu(update, context)
        return

    category = categories[index]
    context.user_data["category_browse_name"] = category
    videos = _category_videos(category)
    buttons = []
    if videos:
        buttons.append([InlineKeyboardButton("🎬 עיון בסרטונים", callback_data="cat_browse_page_0")])
        buttons.append([InlineKeyboardButton(f"📤 שלח את כל {len(videos)} הסרטונים בקטגוריה", callback_data="cat_browse_send_all")])
    buttons.append([InlineKeyboardButton("🔙 בחירת קטגוריה אחרת", callback_data="admin_cat_browse")])
    buttons.append([InlineKeyboardButton("🔙 חזרה לקטגוריות", callback_data="admin_categories")])

    await query.edit_message_text(
        f"📁 *קטגוריה: {category}*\n\n🎬 נמצאו *{len(videos)}* סרטונים בקטגוריה זו.\n\n"
        "אפשר לעבור עליהם אחד־אחד או לשלוח את כולם ברצף ישירות אליך.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_cat_browse_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return
    try:
        page = int(query.data.rsplit("_", 1)[1])
    except ValueError:
        page = 0

    category = context.user_data.get("category_browse_name")
    if not category:
        await admin_cat_browse_menu(update, context)
        return
    videos = _category_videos(category)
    if not videos:
        await query.answer("אין כרגע סרטונים בקטגוריה זו.", show_alert=True)
        await admin_cat_browse_category(update, context)
        return

    page = max(0, min(page, len(videos) - 1))
    video = videos[page]
    await clear_sent_duplicate_group_media(context)

    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton("⬅️ הקודם", callback_data=f"cat_browse_page_{page - 1}"))
    navigation.append(InlineKeyboardButton(f"{page + 1}/{len(videos)}", callback_data="noop"))
    if page < len(videos) - 1:
        navigation.append(InlineKeyboardButton("הבא ➡️", callback_data=f"cat_browse_page_{page + 1}"))

    buttons = [
        navigation,
        [InlineKeyboardButton("📤 שלח את כל הקטגוריה", callback_data="cat_browse_send_all")],
        [InlineKeyboardButton("🔙 חזרה לקטגוריה", callback_data="cat_browse_current")],
    ]
    await query.edit_message_text(
        f"📂 *{category}* — סרטון *{page + 1}/{len(videos)}*\n\n"
        f"⏱ אורך: {format_duration(video.get('duration', 0))}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    sent = await send_admin_video_with_delete_button(context.bot, video["file_id"], video["entry_id"])
    if sent and sent != "INVALID_FILE_ID":
        context.user_data["dup_sent_media_message_ids"] = [sent.message_id]


async def admin_cat_browse_current(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    category = context.user_data.get("category_browse_name")
    if not category:
        await admin_cat_browse_menu(update, context)
        return
    categories = _admin_categories()
    if category not in categories:
        await admin_cat_browse_menu(update, context)
        return
    query.data = f"cat_browse_pick_{categories.index(category)}"
    await admin_cat_browse_category(update, context)


async def admin_cat_browse_send_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Immediately send every video of the selected private category to the admin."""
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    category = context.user_data.get("category_browse_name")
    if not category:
        await query.answer("בחר תחילה קטגוריה.", show_alert=True)
        await admin_cat_browse_menu(update, context)
        return
    videos = _category_videos(category)
    if not videos:
        await query.answer("אין סרטונים בקטגוריה זו.", show_alert=True)
        await admin_cat_browse_category(update, context)
        return

    await clear_sent_duplicate_group_media(context)
    try:
        await query.delete_message()
    except Exception:
        pass

    success = 0
    broken = 0
    for video in videos:
        sent = await send_admin_video_with_delete_button(context.bot, video["file_id"], video["entry_id"])
        if sent == "INVALID_FILE_ID":
            video["file_status"] = "broken"
            video["file_checked_at"] = datetime.now(timezone.utc).isoformat()
            broken += 1
        elif sent:
            video["file_status"] = "valid"
            video["file_checked_at"] = datetime.now(timezone.utc).isoformat()
            success += 1
        await asyncio.sleep(0.15)

    all_videos = load_videos_with_entry_ids()
    statuses = {video.get("entry_id"): video for video in videos}
    for video in all_videos:
        updated = statuses.get(video.get("entry_id"))
        if updated:
            video.update({
                "file_status": updated.get("file_status"),
                "file_checked_at": updated.get("file_checked_at"),
            })
    save_json(VIDEOS_FILE, all_videos)

    report = f"✅ נשלחו {success}/{len(videos)} סרטונים מהקטגוריה ׳{category}׳."
    if broken:
        report += f"\n⚠️ {broken} סרטונים עם מזהים שבורים סומנו לתיקון."
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=report,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 בחירת קטגוריה", callback_data="admin_cat_browse")],
            [InlineKeyboardButton("🔙 חזרה לקטגוריות", callback_data="admin_categories")],
        ]),
    )


async def admin_cat_clone_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    categories = _admin_categories()
    buttons = [[InlineKeyboardButton(f"📋 {category}", callback_data=f"cat_clone_pick_{index}")]
               for index, category in enumerate(categories)]
    buttons.append([InlineKeyboardButton("🔙 חזרה", callback_data="admin_cat_edit")])
    await query.edit_message_text(
        "בחר קטגוריה לשכפול. תיווצר קטגוריה חדשה עם אותם שיוכי סרטונים, ואז אפשר לשנות לה שם.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_cat_clone_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        index = int(query.data.rsplit("_", 1)[1])
    except ValueError:
        return
    categories = _admin_categories()
    if not 0 <= index < len(categories):
        await query.answer("הקטגוריה אינה זמינה.", show_alert=True)
        return
    source = categories[index]
    base = f"עותק של {source}"
    cloned = base
    suffix = 2
    while cloned in categories:
        cloned = f"{base} {suffix}"
        suffix += 1
    backup = create_auto_backup("clone_category", query.from_user.id)
    if not backup:
        await query.answer("לא נוצר גיבוי בטיחותי ולכן השכפול בוטל.", show_alert=True)
        return
    settings = load_settings()
    settings["categories"] = categories + [cloned]
    save_settings(settings)
    videos = load_videos_with_entry_ids()
    copied = 0
    for video in videos:
        memberships = video_categories(video)
        if source in memberships:
            video["categories"] = memberships + [cloned]
            normalize_video_categories(video)
            copied += 1
    save_json(VIDEOS_FILE, videos)
    log_admin_action(query.from_user.id, "category_cloned", {"source": source, "clone": cloned, "videos": copied})
    await query.edit_message_text(
        f"✅ הקטגוריה ׳{source}׳ שוכפלה ל׳{cloned}׳. {copied} סרטונים שויכו גם לקטגוריה החדשה.\n"
        "אפשר לשנות את שם הקטגוריה החדשה במסך עריכת הקטגוריות.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לעריכת קטגוריות", callback_data="admin_cat_edit")]]),
    )


async def admin_cat_merge_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    categories = [category for category in _admin_categories() if category != DEFAULT_CATEGORY]
    buttons = [[InlineKeyboardButton(f"📁 {category}", callback_data=f"cat_merge_source_{index}")]
               for index, category in enumerate(categories)]
    buttons.append([InlineKeyboardButton("🔙 חזרה", callback_data="admin_cat_edit")])
    if not categories:
        await query.edit_message_text("אין עדיין שתי קטגוריות שניתן למזג.", reply_markup=InlineKeyboardMarkup(buttons))
        return
    context.user_data["merge_category_choices"] = categories
    await query.edit_message_text("בחר את קטגוריית המקור שתמוזג לתוך קטגוריה אחרת:", reply_markup=InlineKeyboardMarkup(buttons))


async def admin_cat_merge_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choices = context.user_data.get("merge_category_choices", [])
    try:
        source_index = int(query.data.rsplit("_", 1)[1])
    except ValueError:
        return
    if not 0 <= source_index < len(choices):
        await query.answer("הקטגוריה אינה זמינה.", show_alert=True)
        return
    source = choices[source_index]
    context.user_data["merge_category_source"] = source
    targets = [category for category in _admin_categories() if category != source]
    buttons = [[InlineKeyboardButton(f"➡️ {category}", callback_data=f"cat_merge_target_{index}")]
               for index, category in enumerate(targets)]
    buttons.append([InlineKeyboardButton("🔙 חזרה", callback_data="admin_cat_merge")])
    context.user_data["merge_category_targets"] = targets
    await query.edit_message_text(f"קטגוריית המקור: ׳{source}׳.\nבחר את קטגוריית היעד:", reply_markup=InlineKeyboardMarkup(buttons))


async def admin_cat_merge_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    source = context.user_data.get("merge_category_source")
    targets = context.user_data.get("merge_category_targets", [])
    try:
        target_index = int(query.data.rsplit("_", 1)[1])
    except ValueError:
        return
    if not source or not 0 <= target_index < len(targets):
        await query.answer("בחירת המיזוג אינה זמינה. התחל מחדש.", show_alert=True)
        return
    target = targets[target_index]
    context.user_data["merge_category_target"] = target
    await query.edit_message_text(
        f"הסרטונים של ׳{source}׳ ישויכו גם ל׳{target}׳.\n\nמה לעשות עם קטגוריית המקור לאחר המיזוג?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ מזג והשאר את המקור", callback_data="cat_merge_keep")],
            [InlineKeyboardButton("🗑 מזג ומחק את המקור", callback_data="cat_merge_remove")],
            [InlineKeyboardButton("❌ ביטול", callback_data="admin_cat_edit")],
        ]),
    )


async def admin_cat_merge_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    source = context.user_data.get("merge_category_source")
    target = context.user_data.get("merge_category_target")
    remove_source = query.data == "cat_merge_remove"
    categories = _admin_categories()
    if not source or not target or source == target or source not in categories or target not in categories:
        await query.answer("נתוני המיזוג אינם זמינים. התחל מחדש.", show_alert=True)
        return
    backup = create_auto_backup("merge_categories", query.from_user.id)
    if not backup:
        await query.answer("לא נוצר גיבוי בטיחותי ולכן המיזוג בוטל.", show_alert=True)
        return
    videos = load_videos_with_entry_ids()
    changed = 0
    for video in videos:
        memberships = video_categories(video)
        if source in memberships:
            updated = [item for item in memberships if not (remove_source and item == source)]
            if target not in updated:
                updated.append(target)
            video["categories"] = updated or [DEFAULT_CATEGORY]
            normalize_video_categories(video)
            changed += 1
    save_json(VIDEOS_FILE, videos)
    if remove_source:
        settings = load_settings()
        settings["categories"] = [category for category in categories if category != source]
        save_settings(settings)
    log_admin_action(query.from_user.id, "categories_merged", {"source": source, "target": target, "removed_source": remove_source, "videos": changed})
    await query.edit_message_text(
        f"✅ המיזוג הושלם: {changed} סרטונים שויכו ל׳{target}׳."
        + (f" קטגוריית המקור ׳{source}׳ הוסרה." if remove_source else f" קטגוריית המקור ׳{source}׳ נשארה."),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לעריכת קטגוריות", callback_data="admin_cat_edit")]]),
    )


async def admin_cat_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏️ *עריכת קטגוריות*\n\nאפשר להוסיף, לשנות שם, להסיר, לשכפל או למזג קטגוריות. "
        "סרטון יכול להשתייך לכמה קטגוריות במקביל.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ הוסף קטגוריה", callback_data="admin_cat_add")],
            [InlineKeyboardButton("✏️ שנה שם קטגוריה", callback_data="admin_cat_rename")],
            [InlineKeyboardButton("📋 שכפל קטגוריה", callback_data="admin_cat_clone")],
            [InlineKeyboardButton("🔀 מזג קטגוריות", callback_data="admin_cat_merge")],
            [InlineKeyboardButton("🗑 הסר קטגוריה", callback_data="admin_cat_delete")],
            [InlineKeyboardButton("🔙 חזרה", callback_data="admin_categories")],
        ]),
    )


async def admin_cat_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✍️ שלח את שם הקטגוריה החדשה:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="admin_cat_edit")]]),
    )
    return ADMIN_VIDEO_CAT_ADD


async def admin_cat_add_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = _valid_category_name(update.message.text)
    if not name:
        await update.message.reply_text("❌ שם קטגוריה לא תקין. שלח שם באורך של עד 32 תווים.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_cat_edit")]]))
        return ADMIN_VIDEO_CAT_ADD
    categories = _admin_categories()
    if name in categories:
        await update.message.reply_text("⚠️ קטגוריה בשם זה כבר קיימת.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_cat_edit")]]))
        return ADMIN_VIDEO_CAT_ADD
    categories.append(name)
    settings = load_settings()
    settings["categories"] = categories
    save_settings(settings)
    await update.message.reply_text(
        f"✅ הקטגוריה ׳{name}׳ נוספה.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לקטגוריות", callback_data="admin_categories")]]),
    )
    return ConversationHandler.END


async def admin_cat_rename_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    categories = _admin_categories()
    buttons = [
        [InlineKeyboardButton(category, callback_data=f"cat_rename_pick_{index}")]
        for index, category in enumerate(categories)
        if category != DEFAULT_CATEGORY
    ]
    buttons.append([InlineKeyboardButton("🔙 חזרה", callback_data="admin_cat_edit")])
    if len(buttons) == 1:
        await query.edit_message_text("אין עדיין קטגוריות שניתן לשנות. ׳רנדומלי׳ היא קטגוריית ברירת המחדל הקבועה.", reply_markup=InlineKeyboardMarkup(buttons))
        return ConversationHandler.END
    await query.edit_message_text("בחר קטגוריה לשינוי שם:", reply_markup=InlineKeyboardMarkup(buttons))


async def admin_cat_rename_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    index = int(query.data.rsplit("_", 1)[1])
    categories = _admin_categories()
    if not 0 <= index < len(categories) or categories[index] == DEFAULT_CATEGORY:
        await query.answer("הקטגוריה אינה זמינה לשינוי.", show_alert=True)
        return ConversationHandler.END
    context.user_data["category_rename_old"] = categories[index]
    await query.edit_message_text(
        f"שלח שם חדש עבור הקטגוריה ׳{categories[index]}׳:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="admin_cat_edit")]]),
    )
    return ADMIN_CATEGORY_RENAME


async def admin_cat_rename_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    old_name = context.user_data.get("category_rename_old")
    new_name = _valid_category_name(update.message.text)
    if not old_name:
        return ConversationHandler.END
    if not new_name:
        await update.message.reply_text("❌ שם קטגוריה לא תקין. שלח שם באורך של עד 32 תווים.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_cat_edit")]]))
        return ADMIN_CATEGORY_RENAME
    categories = _admin_categories()
    if new_name in categories:
        await update.message.reply_text("⚠️ קטגוריה בשם זה כבר קיימת.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_cat_edit")]]))
        return ADMIN_CATEGORY_RENAME
    if old_name not in categories or old_name == DEFAULT_CATEGORY:
        await update.message.reply_text("❌ לא ניתן לשנות את הקטגוריה הזו.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_cat_edit")]]))
        return ConversationHandler.END

    settings = load_settings()
    settings["categories"] = [new_name if category == old_name else category for category in categories]
    save_settings(settings)
    videos = load_json(VIDEOS_FILE)
    for video in videos:
        if isinstance(video, dict):
            memberships = video_categories(video)
            if old_name in memberships:
                video["categories"] = [new_name if item == old_name else item for item in memberships]
                normalize_video_categories(video)
    save_json(VIDEOS_FILE, videos)
    context.user_data.pop("category_rename_old", None)
    await update.message.reply_text(
        f"✅ שם הקטגוריה שונה מ׳{old_name}׳ ל׳{new_name}׳ וכל הסרטונים הקשורים עודכנו.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לקטגוריות", callback_data="admin_categories")]]),
    )
    return ConversationHandler.END


async def admin_cat_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    categories = _admin_categories()
    buttons = [
        [InlineKeyboardButton(f"🗑 {category}", callback_data=f"cat_delete_pick_{index}")]
        for index, category in enumerate(categories)
        if category != DEFAULT_CATEGORY
    ]
    buttons.append([InlineKeyboardButton("🔙 חזרה", callback_data="admin_cat_edit")])
    if len(buttons) == 1:
        await query.edit_message_text("אין עדיין קטגוריות שניתן להסיר. ׳רנדומלי׳ היא קטגוריית ברירת המחדל הקבועה.", reply_markup=InlineKeyboardMarkup(buttons))
        return
    await query.edit_message_text("בחר קטגוריה להסרה. הסרטונים שלה יעברו ל׳רנדומלי׳:", reply_markup=InlineKeyboardMarkup(buttons))


async def admin_cat_delete_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    index = int(query.data.rsplit("_", 1)[1])
    categories = _admin_categories()
    if not 0 <= index < len(categories) or categories[index] == DEFAULT_CATEGORY:
        await query.answer("הקטגוריה אינה זמינה להסרה.", show_alert=True)
        return
    category = categories[index]
    await query.edit_message_text(
        f"האם להסיר את הקטגוריה ׳{category}׳? כל הסרטונים שלה יעברו ל׳רנדומלי׳.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ כן, הסר", callback_data=f"cat_delete_confirm_{index}")],
            [InlineKeyboardButton("❌ ביטול", callback_data="admin_cat_edit")],
        ]),
    )


async def admin_cat_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    index = int(query.data.rsplit("_", 1)[1])
    categories = _admin_categories()
    if not 0 <= index < len(categories) or categories[index] == DEFAULT_CATEGORY:
        await query.answer("הקטגוריה אינה זמינה להסרה.", show_alert=True)
        return
    removed = categories[index]
    backup = create_auto_backup("delete_category", query.from_user.id)
    if not backup:
        await query.answer("לא נוצר גיבוי בטיחותי ולכן הסרת הקטגוריה בוטלה.", show_alert=True)
        return
    settings = load_settings()
    settings["categories"] = [category for category in categories if category != removed]
    save_settings(settings)
    videos = load_json(VIDEOS_FILE)
    moved = 0
    for video in videos:
        if isinstance(video, dict):
            memberships = video_categories(video)
            if removed in memberships:
                remaining = [item for item in memberships if item != removed]
                video["categories"] = remaining or [DEFAULT_CATEGORY]
                normalize_video_categories(video)
                moved += 1
    save_json(VIDEOS_FILE, videos)
    log_admin_action(query.from_user.id, "category_deleted", {"category": removed, "videos_updated": moved})
    await query.answer(f"הקטגוריה הוסרה; {moved} סרטונים עודכנו.", show_alert=True)
    await admin_categories_menu(update, context)


def _category_sort_videos() -> list[dict]:
    """Return all sortable videos from the shortest to the longest."""
    return sorted(
        load_videos_with_entry_ids(),
        key=lambda video: (int(video.get("duration", 0) or 0), str(video.get("entry_id", ""))),
    )


def _category_sort_pending_videos() -> list[dict]:
    """Return only videos not yet handled by normal category sorting."""
    reviewed = category_sort_reviewed_entry_ids()
    return [video for video in _category_sort_videos() if str(video.get("entry_id", "")) not in reviewed]


def _category_sort_context_videos(context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    """Resolve the current fixed sort session by entry ID, preserving its order safely."""
    entry_ids = context.user_data.get("cat_sort_entry_ids")
    if not isinstance(entry_ids, list):
        return _category_sort_videos()
    current = {str(video.get("entry_id", "")): video for video in load_videos_with_entry_ids()}
    return [current[entry_id] for entry_id in entry_ids if entry_id in current]


def _start_category_sort_session(context: ContextTypes.DEFAULT_TYPE, mode: str) -> list[dict]:
    """Freeze a deterministic category-sort session so navigation never skips videos."""
    videos = _category_sort_videos() if mode == "rescan" else _category_sort_pending_videos()
    context.user_data["cat_sort_mode"] = mode
    context.user_data["cat_sort_entry_ids"] = [str(video.get("entry_id", "")) for video in videos]
    return videos


async def admin_cat_sort_continue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    videos = _start_category_sort_session(context, "continue")
    if not videos:
        await query.edit_message_text(
            "✅ כל הסרטונים כבר טופלו במיון הקטגוריות. סרטונים חדשים שיועלו יופיעו כאן בהמשך המיון.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 מיון מחדש", callback_data="admin_cat_sort_rescan")],
                [InlineKeyboardButton("🔙 חזרה לקטגוריות", callback_data="admin_categories")],
            ]),
        )
        return
    await admin_cat_sort_page(update, context, 0)


async def admin_cat_sort_rescan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cleared = clear_category_sort_progress()
    _start_category_sort_session(context, "rescan")
    if cleared:
        log_admin_action(query.from_user.id, "category_sort_rescan", {"cleared_progress": cleared})
    await admin_cat_sort_page(update, context, 0)


async def admin_cat_sort_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parse the requested page from the callback so Next/Previous never fall back to page zero."""
    query = update.callback_query
    try:
        page = int((query.data or "").rsplit("_", 1)[1])
    except (IndexError, ValueError):
        page = 0
    await admin_cat_sort_page(update, context, page)


async def admin_cat_sort_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    videos = _category_sort_context_videos(context)
    query = update.callback_query
    if not videos:
        await query.edit_message_text("אין סרטונים למיון.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="admin_categories")]]))
        return

    page = max(0, min(page, len(videos) - 1))
    video = videos[page]
    await clear_sent_duplicate_group_media(context)
    current_categories = video_categories(video)
    mode = context.user_data.get("cat_sort_mode", "rescan")
    mode_label = "המשך מיון — סרטונים חדשים בלבד" if mode == "continue" else "מיון מחדש — כל הסרטונים"
    text = (
        f"🏷 *מיון לקטגוריות ({page + 1}/{len(videos)})*\n"
        f"מצב: *{mode_label}*\n\n"
        f"⏱ אורך: *{format_duration(video.get('duration', 0))}*\n"
        f"📁 קטגוריות נוכחיות: *{', '.join(current_categories)}*\n\n"
        "אפשר לסמן כמה קטגוריות. לחיצה על קטגוריה שומרת את ההתקדמות; אם לא משנים קטגוריה, לחץ על ׳סיים סרטון׳ כדי לשמור את הטיפול בו."
    )

    categories = _admin_categories()
    buttons = []
    row = []
    for index, category in enumerate(categories):
        label = f"✅ {category}" if category in current_categories else category
        row.append(InlineKeyboardButton(label, callback_data=f"cat_assign_{page}_{index}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton("⬅️ הקודם", callback_data=f"cat_sort_page_{page - 1}"))
    navigation.append(InlineKeyboardButton(f"{page + 1}/{len(videos)}", callback_data="noop"))
    if page < len(videos) - 1:
        navigation.append(InlineKeyboardButton("הבא ➡️", callback_data=f"cat_sort_page_{page + 1}"))
    buttons.append(navigation)
    done_label = "✅ סיים והבא" if page < len(videos) - 1 else "✅ סיים מיון"
    buttons.append([InlineKeyboardButton(done_label, callback_data=f"cat_sort_done_{page}")])
    buttons.append([InlineKeyboardButton("🔙 סיום", callback_data="admin_categories")])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
    sent = await send_admin_video_with_delete_button(context.bot, video["file_id"], video["entry_id"])
    if sent and sent != "INVALID_FILE_ID":
        context.user_data["dup_sent_media_message_ids"] = [sent.message_id]


async def admin_cat_assign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    if len(parts) != 4:
        return
    page = int(parts[2])
    category_index = int(parts[3])
    categories = _admin_categories()
    if not 0 <= category_index < len(categories):
        await query.answer("הקטגוריה כבר אינה זמינה. חזור ונסה שוב.", show_alert=True)
        return

    videos = _category_sort_context_videos(context)
    if not 0 <= page < len(videos):
        await query.answer("הסרטון כבר אינו זמין. חזור ונסה שוב.", show_alert=True)
        return
    selected_category = categories[category_index]
    selected_video = videos[page]
    memberships = video_categories(selected_video)
    if selected_category == DEFAULT_CATEGORY and selected_category in memberships and len(memberships) == 1:
        await query.answer("כל סרטון חייב להשתייך לפחות לקטגוריה אחת.", show_alert=True)
        return
    if selected_category in memberships:
        memberships = [item for item in memberships if item != selected_category]
    else:
        memberships.append(selected_category)
    selected_video["categories"] = memberships or [DEFAULT_CATEGORY]
    normalize_video_categories(selected_video)
    all_videos = load_videos_with_entry_ids()
    for index, video in enumerate(all_videos):
        if video.get("entry_id") == selected_video.get("entry_id"):
            all_videos[index] = selected_video
            break
    save_json(VIDEOS_FILE, all_videos)
    mark_category_sort_reviewed(str(selected_video.get("entry_id", "")))
    await admin_cat_sort_page(update, context, page)


async def admin_cat_sort_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark the displayed video as handled even when its existing categories stay unchanged."""
    query = update.callback_query
    await query.answer()
    try:
        page = int(query.data.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return
    videos = _category_sort_context_videos(context)
    if not 0 <= page < len(videos):
        await query.answer("הסרטון כבר אינו זמין. חזור ונסה שוב.", show_alert=True)
        return
    mark_category_sort_reviewed(str(videos[page].get("entry_id", "")))
    if page < len(videos) - 1:
        await admin_cat_sort_page(update, context, page + 1)
        return
    await clear_sent_duplicate_group_media(context)
    context.user_data.pop("cat_sort_entry_ids", None)
    context.user_data.pop("cat_sort_mode", None)
    await query.edit_message_text(
        "✅ סיימת את המיון הנוכחי. מיון המשך יציג בהמשך רק סרטונים חדשים שלא טופלו.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לקטגוריות", callback_data="admin_categories")]]),
    )

async def admin_quick_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    entry_id = query.data.replace("cat_quick_menu_", "")
    if not any(video.get("entry_id") == entry_id for video in load_videos_with_entry_ids()):
        await query.answer("הסרטון כבר אינו זמין.", show_alert=True)
        return
    await query.edit_message_reply_markup(reply_markup=_quick_category_markup(entry_id, menu_open=True))


async def admin_quick_category_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    payload = query.data.replace("cat_quick_toggle_", "")
    try:
        entry_id, category_index_text = payload.rsplit("_", 1)
        category_index = int(category_index_text)
    except (ValueError, AttributeError):
        return
    categories = _admin_categories()
    if not 0 <= category_index < len(categories):
        await query.answer("הקטגוריה כבר אינה זמינה. חזור ונסה שוב.", show_alert=True)
        return
    all_videos = load_videos_with_entry_ids()
    video = next((item for item in all_videos if item.get("entry_id") == entry_id), None)
    if not video:
        await query.answer("הסרטון כבר אינו זמין.", show_alert=True)
        return
    category = categories[category_index]
    memberships = video_categories(video)
    if category == DEFAULT_CATEGORY and category in memberships and len(memberships) == 1:
        await query.answer("כל סרטון חייב להשתייך לפחות לקטגוריה אחת.", show_alert=True)
        return
    if category in memberships:
        memberships = [item for item in memberships if item != category]
    else:
        memberships.append(category)
    video["categories"] = memberships or [DEFAULT_CATEGORY]
    normalize_video_categories(video)
    save_json(VIDEOS_FILE, all_videos)
    mark_category_sort_reviewed(entry_id)
    await query.edit_message_reply_markup(reply_markup=_quick_category_markup(entry_id, menu_open=True))


async def admin_quick_category_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    entry_id = query.data.replace("cat_quick_done_", "")
    if any(video.get("entry_id") == entry_id for video in load_videos_with_entry_ids()):
        mark_category_sort_reviewed(entry_id)
    await query.edit_message_reply_markup(reply_markup=_quick_category_markup(entry_id, menu_open=False))


# ─── Admin: broadcast (enhanced + media) ──────────────────────────────────────

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    await query.edit_message_text("📢 *הודעה לכולם*\n\nשלח את תוכן ההודעה (טקסט בלבד):", parse_mode="Markdown", reply_markup=_flow_back_markup())
    return ADMIN_BROADCAST

async def admin_broadcast_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data["broadcast_msg"] = update.message.text
    await update.message.reply_text(
        "🖼 *הוספת מדיה (אופציונלי)*\n\nשלח תמונה או סרטון, או שלח `skip` לדלג:",
        parse_mode="Markdown",
        reply_markup=_flow_back_markup(),
    )
    return ADMIN_BROADCAST_MEDIA

async def admin_broadcast_get_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
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
        reply_markup=_flow_back_markup(),
    )
    return ADMIN_BROADCAST_BTN

async def admin_broadcast_get_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
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
                await update.message.reply_text("❌ קישור לא תקין (חייב להתחיל ב-http).", reply_markup=_flow_back_markup())
                return ADMIN_BROADCAST_BTN
        else:
            await update.message.reply_text("❌ פורמט לא תקין. השתמש ב-`טקסט|קישור` או `skip`.", parse_mode="Markdown", reply_markup=_flow_back_markup())
            return ADMIN_BROADCAST_BTN
    context.user_data["broadcast_markup"] = markup
    await update.message.reply_text(
        "⏰ *השהיית שליחה (בדקות)*\n\nשלח `0` לשליחה מיידית, או מספר דקות להשהיה:",
        parse_mode="Markdown",
        reply_markup=_flow_back_markup(),
    )
    return ADMIN_BROADCAST_DELAY

async def admin_broadcast_get_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    try:
        delay_min = int(update.message.text.strip())
        if delay_min < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ מספר לא תקין. שלח 0 לשליחה מיידית.", reply_markup=_flow_back_markup())
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
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    await query.edit_message_text("💎 *ניהול דרגות VIP*\n\nשלח את ה-ID של המשתמש:", parse_mode="Markdown", reply_markup=_flow_back_markup())
    return ADMIN_VIP_ID

async def admin_vip_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    try:
        uid = str(int(update.message.text.strip()))
        context.user_data["vip_target_id"] = uid
    except ValueError:
        await update.message.reply_text("❌ ID לא תקין.", reply_markup=_flow_back_markup())
        return ConversationHandler.END
    
    users = load_json(USERS_FILE)
    if uid not in users:
        await update.message.reply_text("❌ משתמש לא נמצא במערכת.", reply_markup=_flow_back_markup())
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
    if not is_admin(query.from_user.id):
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


def _coins_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 שינוי יתרה למשתמש", callback_data="admin_coins")],
        [InlineKeyboardButton("🪙 שליטה במטבעות", callback_data="admin_coin_control")],
        [InlineKeyboardButton("🎟 ניהול קופונים", callback_data="admin_coupons"), InlineKeyboardButton("💎 ניהול דרגות", callback_data="admin_vip")],
        [InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")],
    ])


async def admin_coins_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not has_admin_permission(user_id, "coins"):
        return ConversationHandler.END
    settings = load_settings()
    await query.edit_message_text(
        "🪙 *מטבעות*\n\n"
        f"🎁 מתנה יומית: *{settings['daily_gift_amount']} מטבעות*\n"
        f"👥 תגמול הפניה: *{settings['referral_reward_amount']} מטבעות*\n\n"
        "בחר פעולה:",
        parse_mode="Markdown",
        reply_markup=_coins_menu_markup(),
    )


async def admin_coins_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not has_admin_permission(query.from_user.id, "coins"):
        return ConversationHandler.END
    context.user_data.pop("coins_target_id", None)
    await query.edit_message_text(
        "🪙 *שינוי יתרת מטבעות*\n\nשלח את ה־ID של משתמש רשום בבוט.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה למטבעות", callback_data="admin_coins_menu")]]),
    )
    return ADMIN_COINS_ID


async def admin_coins_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_admin_permission(user_id, "coins"):
        return ConversationHandler.END
    try:
        uid = str(int((update.message.text or "").strip()))
    except (TypeError, ValueError):
        await update.message.reply_text(
            "❌ ID לא תקין. שלח מספר ID של משתמש שכבר לחץ /start בבוט.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה למטבעות", callback_data="admin_coins_menu")]]),
        )
        return ADMIN_COINS_ID
    users = load_json(USERS_FILE)
    if uid not in users:
        await update.message.reply_text(
            "❌ ID לא תקין או שהמשתמש עדיין לא התחיל את הבוט. ניתן לשנות יתרה רק למשתמש רשום.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה למטבעות", callback_data="admin_coins_menu")]]),
        )
        return ADMIN_COINS_ID
    context.user_data["coins_target_id"] = uid
    coins = load_json(COINS_FILE)
    name = users[uid].get("first_name", "ללא שם")
    current = coins.get(uid, 0)
    await update.message.reply_text(
        f"👤 {name}\n🪙 יתרה נוכחית: {current}\n\nשלח כמות: מספר חיובי להוספה או מספר שלילי להורדה.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה למטבעות", callback_data="admin_coins_menu")]]),
    )
    return ADMIN_COINS_AMOUNT


async def admin_coins_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_admin_permission(user_id, "coins"):
        return ConversationHandler.END
    uid = context.user_data.get("coins_target_id")
    users = load_json(USERS_FILE)
    if not uid or uid not in users:
        context.user_data.pop("coins_target_id", None)
        await update.message.reply_text(
            "❌ המשתמש אינו תקין או אינו רשום בבוט. הפעולה בוטלה.",
            reply_markup=get_admin_inline_keyboard(user_id),
        )
        return ConversationHandler.END
    try:
        amount = int((update.message.text or "").strip())
    except (TypeError, ValueError):
        await update.message.reply_text(
            "❌ כמות לא תקינה. שלח מספר שלם חיובי או שלילי.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה למטבעות", callback_data="admin_coins_menu")]]),
        )
        return ADMIN_COINS_AMOUNT
    coins = load_json(COINS_FILE)
    current = coins.get(uid, 0)
    new_bal = max(0, current + amount)
    coins[uid] = new_bal
    save_json(COINS_FILE, coins)
    context.user_data.pop("coins_target_id", None)
    log_admin_action(user_id, "coins_balance_changed", {"target_user_id": uid, "amount": amount, "new_balance": new_bal})
    await update.message.reply_text(
        f"✅ יתרת המשתמש {uid} עודכנה.\n🪙 שינוי: {amount:+d}\n💰 יתרה חדשה: {new_bal}",
        reply_markup=get_admin_inline_keyboard(user_id),
    )
    return ConversationHandler.END


async def admin_coins_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("coins_target_id", None)
    await admin_coins_menu(update, context)
    return ConversationHandler.END

# ─── Admin: coupon management ─────────────────────────────────────────────────

async def admin_coupons_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
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
    if not is_admin(query.from_user.id):
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
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    await query.edit_message_text("🎟 *קופון חדש*\n\nשלח את *קוד הקופון* (אותיות/מספרים):", parse_mode="Markdown", reply_markup=_flow_back_markup())
    return ADMIN_COUPON_CODE

async def admin_coupon_get_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    code = update.message.text.strip().upper()
    if not code.replace("_", "").replace("-", "").isalnum():
        await update.message.reply_text("❌ קוד לא תקין. רק אותיות ומספרים.", reply_markup=_flow_back_markup())
        return ADMIN_COUPON_CODE
    coupons = load_json(COUPONS_FILE)
    if code in coupons:
        await update.message.reply_text("❌ קוד כבר קיים.", reply_markup=_flow_back_markup())
        return ADMIN_COUPON_CODE
    context.user_data["new_coupon_code"] = code
    await update.message.reply_text(f"✅ קוד: `{code}`\n\nכמה 🪙 מטבעות?", parse_mode="Markdown", reply_markup=_flow_back_markup())
    return ADMIN_COUPON_COINS

async def admin_coupon_get_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    try:
        val = int(update.message.text.strip())
        if val <= 0:
            raise ValueError
        context.user_data["new_coupon_coins"] = val
    except ValueError:
        await update.message.reply_text("❌ מספר לא תקין.", reply_markup=_flow_back_markup())
        return ADMIN_COUPON_COINS
    await update.message.reply_text("📅 תאריך תפוגה? (`YYYY-MM-DD` או `skip`):", parse_mode="Markdown", reply_markup=_flow_back_markup())
    return ADMIN_COUPON_EXPIRY

async def admin_coupon_get_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    raw = update.message.text.strip()
    if raw.lower() == "skip":
        context.user_data["new_coupon_expiry"] = None
    else:
        try:
            datetime.strptime(raw, "%Y-%m-%d")
            context.user_data["new_coupon_expiry"] = raw
        except ValueError:
            await update.message.reply_text("❌ פורמט לא תקין. נסה `YYYY-MM-DD` או `skip`.", parse_mode="Markdown", reply_markup=_flow_back_markup())
            return ADMIN_COUPON_EXPIRY
    await update.message.reply_text("👥 מגבלת שימושים? (מספר או `skip`):", parse_mode="Markdown", reply_markup=_flow_back_markup())
    return ADMIN_COUPON_LIMIT

async def admin_coupon_get_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    raw      = update.message.text.strip()
    max_uses = None
    if raw.lower() != "skip":
        try:
            max_uses = int(raw)
            if max_uses <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ מספר לא תקין.", reply_markup=_flow_back_markup())
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

# ─── Admin: direct coin controls ───────────────────────────────────────────────


def _coin_control_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 שינוי מתנה יומית", callback_data="admin_coin_set_daily")],
        [InlineKeyboardButton("👥 שינוי תגמול הפניות", callback_data="admin_coin_set_referral")],
        [InlineKeyboardButton("⚙️ שינוי שני הסכומים", callback_data="admin_coin_set_both")],
        [InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")],
    ])


async def admin_coin_control_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not has_admin_permission(user_id, "coins"):
        return ConversationHandler.END
    settings = load_settings()
    daily = max(0, int(settings.get("daily_gift_amount", 1)))
    referral = max(0, int(settings.get("referral_reward_amount", 1)))
    await query.edit_message_text(
        "🪙 *שליטה במטבעות*\n\n"
        f"🎁 מתנה יומית נוכחית: *{daily} מטבעות*\n"
        f"👥 תגמול הפניה נוכחי: *{referral} מטבעות*\n\n"
        "הערכים משפיעים על זיכויים עתידיים בלבד. יתרות שכבר קיימות לא משתנות.\n"
        "בחר מה לעדכן:",
        parse_mode="Markdown",
        reply_markup=_coin_control_markup(),
    )


async def admin_coin_control_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not has_admin_permission(user_id, "coins"):
        return ConversationHandler.END
    target = query.data.removeprefix("admin_coin_set_")
    if target not in {"daily", "referral", "both"}:
        return ConversationHandler.END
    context.user_data["coin_control_target"] = target
    prompts = {
        "daily": "שלח מספר שלם למתנה היומית, למשל `2`.",
        "referral": "שלח מספר שלם לתגמול על הפניה, למשל `3`.",
        "both": "שלח שני מספרים שלמים בפורמט `מתנה הפניה`, למשל `2 3`.",
    }
    await query.edit_message_text(
        f"🪙 *שליטה במטבעות*\n\n{prompts[target]}\n\n"
        "הערך ישפיע על זיכויים עתידיים בלבד.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ביטול וחזרה", callback_data="back_admin")]]),
    )
    return ADMIN_MULTIPLIER


async def admin_coin_control_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_admin_permission(user_id, "coins"):
        return ConversationHandler.END
    target = context.user_data.pop("coin_control_target", "")
    parts = re.findall(r"\d+", update.message.text or "")
    expected = 2 if target == "both" else 1
    if target not in {"daily", "referral", "both"} or len(parts) != expected:
        await update.message.reply_text(
            "❌ קלט לא תקין. שלח מספר שלם חיובי או אפס; עבור שני הערכים שלח שני מספרים, למשל `2 3`.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]),
        )
        context.user_data["coin_control_target"] = target
        return ADMIN_MULTIPLIER
    values = [int(value) for value in parts]
    settings = load_settings()
    if target in {"daily", "both"}:
        settings["daily_gift_amount"] = values[0]
    if target == "referral":
        settings["referral_reward_amount"] = values[0]
    elif target == "both":
        settings["referral_reward_amount"] = values[1]
    save_settings(settings)
    log_admin_action(user_id, "coin_reward_settings_updated", {
        "daily_gift_amount": settings["daily_gift_amount"],
        "referral_reward_amount": settings["referral_reward_amount"],
    })
    await update.message.reply_text(
        "✅ *הערכים נשמרו*\n\n"
        f"🎁 מתנה יומית: *{settings['daily_gift_amount']} מטבעות*\n"
        f"👥 תגמול הפניה: *{settings['referral_reward_amount']} מטבעות*\n\n"
        "היתרות הקיימות לא השתנו. הערכים יחולו על זיכויים עתידיים.",
        parse_mode="Markdown",
        reply_markup=get_admin_inline_keyboard(user_id),
    )
    return ConversationHandler.END

# Backward-compatible test helper; the old multiplier callback is no longer registered in the UI.
async def admin_multiplier_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        return ConversationHandler.END
    settings = load_settings()
    await query.edit_message_text(
        "💱 *מכפיל הפניות ויתרות מטבעות*\n\n"
        f"המכפיל ההיסטורי: *{settings.get('referral_multiplier', 1.0)}x*\n\n"
        "המערכת החדשה משתמשת בשליטה ישירה במתנה היומית ובתגמול ההפניות.\n"
        "מחיר PayPal והמתנה היומית הם הגדרות נפרדות, ויתרות קיימות אינן משתנות.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]),
    )
    return ADMIN_MULTIPLIER


# ─── Admin: backup ZIP ────────────────────────────────────────────────────────

async def admin_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
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
    log_admin_action(query.from_user.id, "manual_backup_created", {})
    await context.bot.send_message(chat_id=ADMIN_ID, text="✅ הגיבוי הושלם!", reply_markup=get_admin_inline_keyboard())

# ─── Admin: restore from ZIP ─────────────────────────────────────────────────

async def admin_restore_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
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
    if not is_admin(update.effective_user.id):
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
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    payloads = context.user_data.get("pending_restore")
    if not isinstance(payloads, dict) or not payloads:
        await query.edit_message_text(
            "❌ אין גיבוי מוכן לשחזור. שלח את קובץ הגיבוי מחדש.",
            reply_markup=get_admin_inline_keyboard(),
        )
        return ConversationHandler.END

    try:
        auto_snapshot = create_auto_backup("restore_data", query.from_user.id)
        if not auto_snapshot:
            raise RuntimeError("automatic backup creation failed")
        snapshot = build_zip_of_data()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        await context.bot.send_document(
            chat_id=ADMIN_ID,
            document=snapshot,
            filename=f"before_restore_{stamp}.zip",
            caption="💾 גיבוי חירום אוטומטי לפני שחזור",
        )
        apply_restore_payloads(payloads)
        log_admin_action(query.from_user.id, "backup_restored", {"files": sorted(payloads.keys())})
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
    if not is_admin(query.from_user.id):
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
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    await query.edit_message_text(
        "🔴 *אישור סופי*\n\nהקלד *מאשר* כדי למחוק את כל הנתונים:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול / חזור", callback_data="back_admin")]]),
    )
    return ADMIN_GLOBAL_RESET_CONFIRM

async def admin_global_reset_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    if update.message.text.strip() != "מאשר":
        await update.message.reply_text("❌ ביטול — הטקסט לא תאם. שלח 'מאשר' בדיוק.")
        return ADMIN_GLOBAL_RESET_CONFIRM

    backup = create_auto_backup("global_reset", update.effective_user.id)
    if not backup:
        await update.message.reply_text("❌ לא נוצר גיבוי בטיחותי ולכן האיפוס בוטל.", reply_markup=get_admin_inline_keyboard())
        return ConversationHandler.END

    for filepath, default in [
        (USERS_FILE,     {}),
        (COINS_FILE,     {}),
        (REFERRALS_FILE, {}),
        (VIDEOS_FILE,    []),
        (ORDERS_FILE,    []),
        (COUPONS_FILE,   {}),
    ]:
        save_json(filepath, default)

    log_admin_action(update.effective_user.id, "global_data_reset", {})
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
    if not is_admin(query.from_user.id):
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
    if not is_admin(query.from_user.id):
        return
    backup = create_auto_backup("delete_all_videos", query.from_user.id)
    if not backup:
        await query.answer("לא נוצר גיבוי בטיחותי ולכן המחיקה בוטלה.", show_alert=True)
        return
    count = len(load_json(VIDEOS_FILE))
    save_json(VIDEOS_FILE, [])
    log_admin_action(query.from_user.id, "all_videos_deleted", {"count": count})
    await query.edit_message_text(
        "✅ כל הסרטונים נמחקו לאחר יצירת גיבוי בטיחותי!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזור", callback_data="back_admin")]]),
    )

# ─── Admin: maintenance mode ──────────────────────────────────────────────────

async def admin_maintenance_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
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
    """Store every admin-uploaded video immediately in the private default category."""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    video = update.message.video
    if not video:
        return ConversationHandler.END

    videos = load_videos_with_entry_ids()
    entry_id = uuid.uuid4().hex
    videos.append({
        "entry_id": entry_id,
        "file_id": video.file_id,
        "file_unique_id": getattr(video, "file_unique_id", None),
        "file_name": getattr(video, "file_name", None),
        "duration": video.duration or 0,
        "file_size": video.file_size,
        "category": DEFAULT_CATEGORY,
        "categories": [DEFAULT_CATEGORY],
        "preview": None,
        "file_status": "valid",
        "added_at": datetime.now(timezone.utc).isoformat(),
    })
    save_json(VIDEOS_FILE, videos)

    await update.message.reply_text(
        f"✅ הסרטון נוסף למאגר ({len(videos)} בסך הכול).\n"
        f"⏱ אורך: {format_duration(video.duration or 0)}\n"
        "📁 קטגוריה: רנדומלי\n\n"
        "אפשר לשייך קטגוריה אחר כך דרך גלריית סרטונים ← קטגוריות ← מיון לקטגוריות."
    )
    return ConversationHandler.END

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
    if not is_admin(update.effective_user.id):
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
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_admin, pattern="^back_admin$")],
        per_message=False, per_chat=True,
    )
    send_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_send_start, pattern="^admin_send$")],
        states={
            ADMIN_SEND_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_msg)],
            ADMIN_SEND_ID:  [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_admin, pattern="^back_admin$")],
        per_message=False, per_chat=True,
    )
    approve_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_approve_start, pattern="^admin_approve$")],
        states={
            ADMIN_APPROVE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_approve_count)],
            ADMIN_APPROVE_ID:    [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_approve_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_admin, pattern="^back_admin$")],
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
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_admin, pattern="^back_admin$")],
        per_message=False, per_chat=True,
    )
    coins_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_coins_start, pattern="^admin_coins$")],
        states={
            ADMIN_COINS_ID:     [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coins_id)],
            ADMIN_COINS_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coins_amount)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(admin_coins_cancel, pattern="^admin_coins_menu$"),
            CallbackQueryHandler(back_admin, pattern="^back_admin$"),
        ],
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
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_admin, pattern="^back_admin$")],
        per_message=False, per_chat=True,
    )
    coin_control_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_coin_control_start, pattern="^admin_coin_set_(daily|referral|both)$")],
        states={ADMIN_MULTIPLIER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coin_control_apply)]},
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(back_admin, pattern="^back_admin$"),
        ],
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
            CallbackQueryHandler(admin_gallery, pattern="^admin_gallery$"),
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
    repair_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_repair_scan, pattern="^admin_repair_scan$"),
            CallbackQueryHandler(admin_repair_cached, pattern="^admin_repair_cached$"),
        ],
        states={
            ADMIN_REPAIR_UPLOAD: [
                MessageHandler(filters.VIDEO, admin_repair_handle_file),
                CallbackQueryHandler(admin_repair_skip, pattern="^admin_repair_skip$"),
                CallbackQueryHandler(admin_repair_cancel, pattern="^admin_repair_cancel$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(admin_repair_cancel, pattern="^admin_repair_cancel$"),
            CallbackQueryHandler(admin_repair_cancel, pattern="^admin_gallery$"),
        ],
        per_message=False,
        per_chat=True,
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
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_admin, pattern="^back_admin$")],
        per_message=False, per_chat=True,
    )
    # ── Register handlers ─────────────────────────────────────────────────────────────────────────────
    cat_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_cat_add_start, pattern="^admin_cat_add$")],
        states={ADMIN_VIDEO_CAT_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_cat_add_input)]},
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(admin_cat_edit_menu, pattern="^admin_cat_edit$"),
            CallbackQueryHandler(admin_categories_menu, pattern="^admin_categories$"),
            CallbackQueryHandler(back_admin, pattern="^back_admin$"),
        ],
        per_message=False, per_chat=True,
    )
    manager_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_manager_add_start, pattern="^admin_mgr_add$")],
        states={ADMIN_MANAGER_ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_manager_add_input)]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(admin_managers_menu, pattern="^admin_managers$"), CallbackQueryHandler(back_admin, pattern="^back_admin$")],
        per_message=False, per_chat=True,
    )
    assistant_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_assistant_start, pattern="^admin_assistant$")],
        states={ADMIN_ASSISTANT_COMMAND: [MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🛠 פאנל אדמין$"),
            admin_assistant_command,
        )]},
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(admin_assistant_back, pattern="^admin_assistant_back$"),
            CallbackQueryHandler(admin_assistant_back, pattern="^back_admin$"),
            MessageHandler(filters.Regex("^🛠 פאנל אדמין$"), admin_assistant_exit_to_panel),
        ],
        per_message=False,
        per_chat=True,
    )
    cat_rename_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_cat_rename_pick, pattern=r"^cat_rename_pick_\d+$")],
        states={ADMIN_CATEGORY_RENAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_cat_rename_input)]},
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(admin_cat_edit_menu, pattern="^admin_cat_edit$"),
            CallbackQueryHandler(admin_categories_menu, pattern="^admin_categories$"),
            CallbackQueryHandler(back_admin, pattern="^back_admin$"),
        ],
        per_message=False,
        per_chat=True,
    )
    video_upload_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.VIDEO, handle_video)],
        states={},
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
        per_chat=True,
    )
    
    for conv in [
        check_conv, send_conv, approve_conv, broadcast_conv, coins_conv, vip_conv,
        coupon_new_conv, coin_control_conv, restore_conv, global_reset_conv,
        video_search_conv, video_search_sec_conv, repair_conv, support_conv, coupon_redeem_conv, support_reply_conv,
        cat_add_conv, cat_rename_conv, manager_add_conv, assistant_conv, video_upload_conv
    ]:
        app.add_handler(conv)

    app.add_error_handler(telegram_error_handler)
    # The access gate runs before all private callbacks and blocks unauthorized actions centrally.
    app.add_handler(CallbackQueryHandler(admin_callback_gate), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🛠 פאנל אדמין$"), admin_panel))

    # Callback handlers (standalone)
    cbs = [
        ("^noop$",                      noop_callback),
        ("^payment_method$",            payment_method_menu),
        ("^purchase_help$",             purchase_help),
        ("^paypal_menu$",               paypal_menu),
        (r"^pp_\d+$",                   paypal_package_selected),
        ("^coins_menu$",                coins_menu),
        (r"^coin_\d+$",                 coin_package_buy),
        ("^referrals$",                 referrals_menu),
        ("^wallet$",                    wallet_menu),
        ("^daily_bonus$",               daily_bonus),
        ("^vip_info$",                  vip_info),
        ("^back_main$",                 back_main),
        ("^admin_menu_users$",          admin_menu_users),
        ("^admin_menu_rewards$",        admin_menu_rewards),
        ("^admin_coins_menu$",          admin_coins_menu),
        ("^admin_coin_control$",         admin_coin_control_menu),
        ("^admin_menu_communications$", admin_menu_communications),
        ("^admin_menu_system$",         admin_menu_system),
        ("^admin_managers$",            admin_managers_menu),
        ("^admin_owner_assistant_settings$", admin_owner_assistant_settings),
        (r"^admin_mgr_assistant_pick_\d+$", admin_manager_assistant_pick),
        ("^admin_mgr_assistant_list$",    admin_manager_assistant_list),
        (r"^admin_mgr_pick_\d+$",        admin_manager_pick),
        ("^admin_mgr_assistant$",         admin_manager_assistant_menu),
        (r"^admin_mgr_assist_toggle_.+$", admin_manager_assistant_toggle),
        (r"^admin_mgr_toggle_.+$",       admin_manager_toggle),
        ("^admin_assistant_back$",        admin_assistant_back),
        ("^admin_mgr_remove$",           admin_manager_remove),
        ("^admin_stats$",               admin_stats),
        (r"^admin_actions_page_\d+$",    admin_actions_page),
        (r"^admin_orders_page_\d+$",    admin_orders_page),
        (r"^users_page_\d+$",           users_page),
        ("^admin_gallery$",             admin_gallery),
        ("^admin_repair_start$",        admin_repair_start),
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
        ("^admin_cat_order$",           admin_cat_order_menu),
        (r"^cat_order_(up|down)_\d+$", admin_cat_order_move),
        ("^cat_order_alpha$",           admin_cat_order_alphabetical),
        ("^admin_cat_browse$",          admin_cat_browse_menu),
        (r"^cat_browse_pick_\d+$",      admin_cat_browse_category),
        (r"^cat_browse_page_\d+$",      admin_cat_browse_page),
        ("^cat_browse_current$",        admin_cat_browse_current),
        ("^cat_browse_send_all$",       admin_cat_browse_send_all),
        ("^admin_cat_edit$",            admin_cat_edit_menu),
        ("^admin_cat_clone$",           admin_cat_clone_start),
        (r"^cat_clone_pick_\d+$",       admin_cat_clone_pick),
        ("^admin_cat_merge$",           admin_cat_merge_start),
        (r"^cat_merge_source_\d+$",     admin_cat_merge_source),
        (r"^cat_merge_target_\d+$",     admin_cat_merge_target),
        ("^cat_merge_keep$",            admin_cat_merge_confirm),
        ("^cat_merge_remove$",          admin_cat_merge_confirm),
        ("^admin_cat_rename$",          admin_cat_rename_start),
        ("^admin_cat_delete$",          admin_cat_delete_start),
        (r"^cat_delete_pick_\d+$",    admin_cat_delete_pick),
        (r"^cat_delete_confirm_\d+$", admin_cat_delete_confirm),
        (r"^cat_sort_page_\d+$",       admin_cat_sort_navigation),
        (r"^cat_sort_done_\d+$",       admin_cat_sort_done),
        ("^admin_cat_sort_start$",      admin_cat_sort_continue),
        ("^admin_cat_sort_continue$",   admin_cat_sort_continue),
        ("^admin_cat_sort_rescan$",     admin_cat_sort_rescan),
        (r"^cat_assign_",               admin_cat_assign),
        (r"^cat_quick_menu_[0-9a-f]+$", admin_quick_category_menu),
        (r"^cat_quick_toggle_[0-9a-f]+_\d+$", admin_quick_category_toggle),
        (r"^cat_quick_done_[0-9a-f]+$", admin_quick_category_done),
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
