import os
import json
import random
import asyncio
import logging
import threading
import warnings
import zipfile
import io
import shutil
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

# --- Configuration & Setup ---
warnings.filterwarnings("ignore", message=".*per_message=False.*CallbackQueryHandler.*")
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7706183809"))
PAYPAL_LINK = "https://paypal.me/Eliyas2005"

DATA_DIR = Path("data")
USERS_FILE = DATA_DIR / "users.json"
COINS_FILE = DATA_DIR / "coins.json"
REFERRALS_FILE = DATA_DIR / "referrals.json"
VIDEOS_FILE = DATA_DIR / "videos.json"
ORDERS_FILE = DATA_DIR / "orders.json"
COUPONS_FILE = DATA_DIR / "coupons.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

COINS_PER_SHEKEL = 10
ORDERS_PER_PAGE = 10

PACKAGES = [
    {"price": 2,   "videos": 1,   "coins": 20,   "label": "1 סרטון"},
    {"price": 9,   "videos": 5,   "coins": 90,   "label": "5 סרטונים"},
    {"price": 16,  "videos": 10,  "coins": 160,  "label": "10 סרטונים"},
    {"price": 30,  "videos": 20,  "coins": 300,  "label": "20 סרטונים"},
    {"price": 65,  "videos": 50,  "coins": 650,  "label": "50 סרטונים"},
    {"price": 85,  "videos": 70,  "coins": 850,  "label": "70 סרטונים"},
    {"price": 110, "videos": 100, "coins": 1100, "label": "100 סרטונים"},
    {"price": 180, "videos": 200, "coins": 1800, "label": "200 סרטונים"},
]

VIP_LEVELS = [
    {"name": "יהלום", "min": 31, "discount": 0.40, "icon": "💎"},
    {"name": "זהב",   "min": 16, "discount": 0.25, "icon": "🥇"},
    {"name": "כסף",   "min": 6,  "discount": 0.10, "icon": "🥈"},
    {"name": "ברונזה", "min": 0,  "discount": 0.00, "icon": "🥉"},
]

# --- Conversation States ---
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
    ADMIN_SET_WELCOME_TEXT,
    ADMIN_SET_WELCOME_MEDIA,
) = range(23)

# --- Data Helpers ---

def ensure_data_files():
    DATA_DIR.mkdir(exist_ok=True)
    defaults = [
        (USERS_FILE, {}),
        (COINS_FILE, {}),
        (REFERRALS_FILE, {}),
        (VIDEOS_FILE, []),
        (ORDERS_FILE, []),
        (COUPONS_FILE, {}),
        (SETTINGS_FILE, {"referral_multiplier": 1.0, "maintenance": False, "welcome_text": "שלום {name} 👋\nברוכים הבאים לבוט התכנים האסורים 🤫", "welcome_media": None}),
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

# --- Business Logic ---

def get_user_vip(user_id):
    users = load_json(USERS_FILE)
    u = users.get(str(user_id), {})
    purchases = u.get("purchases", 0)
    vip = VIP_LEVELS[-1]
    for level in VIP_LEVELS:
        if purchases >= level["min"]:
            vip = level
            break
    return vip

def register_user(user, ref_id=None):
    users = load_json(USERS_FILE)
    uid = str(user.id)
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
                coins = load_json(COINS_FILE)
                multiplier = load_settings().get("referral_multiplier", 1.0)
                coins[ref_key] = coins.get(ref_key, 0) + int(1 * multiplier)
                save_json(COINS_FILE, coins)
    return users.get(uid, {})

async def send_videos_to_user(context, user_id: int, count: int, is_admin=False) -> int:
    videos = load_json(VIDEOS_FILE)
    videos.sort(key=lambda x: x.get("duration", 0))
    users = load_json(USERS_FILE)
    uid_str = str(user_id)
    u_data = users.get(uid_str, {})
    seen = u_data.get("seen_videos", [])
    
    unseen = [v for v in videos if v["file_id"] not in seen]
    if len(unseen) >= count:
        selected = unseen[:count]
    else:
        # If not enough new videos, take all unseen and fill with random seen ones
        others = [v for v in videos if v["file_id"] in seen]
        selected = unseen + random.sample(others, min(count - len(unseen), len(others)))
        
    sent = 0
    for i, v in enumerate(selected):
        try:
            file_id = v['file_id']
            # Find overall index in sorted list
            overall_idx = next((idx+1 for idx, vid in enumerate(videos) if vid["file_id"] == file_id), 0)
            caption = f"🎬 סרטון #{overall_idx}"
            reply_markup = None
            if is_admin:
                v_idx = next((idx for idx, vid in enumerate(videos) if vid["file_id"] == file_id), 0)
                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(f"🗑 מחק {overall_idx}", callback_data=f"vid_del_{v_idx}")]])
            
            await context.bot.send_video(chat_id=user_id, video=file_id, caption=caption, reply_markup=reply_markup)
            if not is_admin:
                if file_id not in seen: seen.append(file_id)
            sent += 1
            await asyncio.sleep(0.1)
        except Exception: pass
    
    if not is_admin:
        u_data["seen_videos"] = seen
        users[uid_str] = u_data
        save_json(USERS_FILE, users)
    return sent

def record_order(user_id: int, amount: float, videos_count: int, order_type: str):
    orders = load_json(ORDERS_FILE)
    orders.append({
        "user_id": user_id,
        "amount": amount,
        "videos_count": videos_count,
        "date": str(date.today()),
        "type": order_type,
    })
    save_json(ORDERS_FILE, orders)
    users = load_json(USERS_FILE)
    uid = str(user_id)
    if uid in users:
        users[uid]["purchases"] = users[uid].get("purchases", 0) + 1
        users[uid]["total_spent"] = users[uid].get("total_spent", 0) + amount
        save_json(USERS_FILE, users)

async def alert_admin(context, text: str):
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="Markdown")
    except Exception: pass

# --- Keyboard Builders ---

def get_main_keyboard(user_id):
    vip = get_user_vip(user_id)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{vip['icon']} רמת {vip['name']}", callback_data="vip_info")],
        [
            InlineKeyboardButton("💳 תשלום", callback_data="payment_method"),
            InlineKeyboardButton("👥 הפניות שלי", callback_data="referrals"),
        ],
        [
            InlineKeyboardButton("💰 ארנק מטבעות", callback_data="wallet"),
            InlineKeyboardButton("🎟 מימוש קופון", callback_data="coupon_redeem"),
        ],
        [
            InlineKeyboardButton("🎁 מתנה יומית", callback_data="daily_bonus"),
            InlineKeyboardButton("💬 תמיכה", callback_data="support")
        ],
    ])

def get_admin_inline_keyboard():
    s = load_settings()
    ml = "🔧 כבה תחזוקה" if s.get("maintenance") else "🔧 מצב תחזוקה"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 סטטיסטיקה", callback_data="admin_stats"), InlineKeyboardButton("🧾 הזמנות", callback_data="admin_orders_page_0")],
        [InlineKeyboardButton("🔍 בדוק משתמש", callback_data="admin_check"), InlineKeyboardButton("👥 רשימת משתמשים", callback_data="users_page_0")],
        [InlineKeyboardButton("📩 שלח למשתמש", callback_data="admin_send"), InlineKeyboardButton("✅ אישור תשלום", callback_data="admin_approve")],
        [InlineKeyboardButton("🎬 גלריית סרטונים", callback_data="admin_gallery"), InlineKeyboardButton("🔢 חיפוש סרטון", callback_data="admin_video_search")],
        [InlineKeyboardButton("📢 הודעה לכולם", callback_data="admin_broadcast"), InlineKeyboardButton("🪙 ניהול מטבעות", callback_data="admin_coins")],
        [InlineKeyboardButton("🎟 ניהול קופונים", callback_data="admin_coupons"), InlineKeyboardButton("👋 הודעת פתיחה", callback_data="admin_welcome_msg")],
        [InlineKeyboardButton("💱 ערך מטבע", callback_data="admin_multiplier"), InlineKeyboardButton("💾 ניהול גיבויים", callback_data="admin_backup_menu")],
        [InlineKeyboardButton("🔄 איפוס נתונים", callback_data="admin_global_reset"), InlineKeyboardButton("🧹 מחק סרטונים", callback_data="admin_delete_all")],
        [InlineKeyboardButton(ml, callback_data="admin_maintenance")]
    ])

# --- Handlers ---

async def maintenance_gate(update: Update) -> bool:
    if update.effective_user and update.effective_user.id == ADMIN_ID: return False
    if not is_maintenance(): return False
    msg = "🔧 *הבוט בשיפוצים*\nנחזור בקרוב! 🙏"
    if update.callback_query: await update.callback_query.answer("הבוט בשיפוצים!", show_alert=True)
    elif update.message: await update.message.reply_text(msg, parse_mode="Markdown")
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.date:
        if (datetime.now(timezone.utc) - update.message.date).total_seconds() > 30: return
    if await maintenance_gate(update): return
    user = update.effective_user
    ref_id = None
    if context.args and context.args[0].startswith("ref_"):
        try: ref_id = int(context.args[0].split("ref_")[1])
        except: pass
    register_user(user, ref_id)
    if user.id == ADMIN_ID: await update.message.reply_text("👋 אדמין!", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🛠 פאנל אדמין")]], resize_keyboard=True))
    s = load_settings(); vip = get_user_vip(user.id)
    txt = s["welcome_text"].format(name=user.first_name) + f"\n\nדרגה: {vip['icon']} *{vip['name']}* ({int(vip['discount']*100)}% הנחה)"
    m = s.get("welcome_media")
    if m:
        if m[0] == "photo": await context.bot.send_photo(user.id, m[1], caption=txt, parse_mode="Markdown", reply_markup=get_main_keyboard(user.id))
        else: await context.bot.send_video(user.id, m[1], caption=txt, parse_mode="Markdown", reply_markup=get_main_keyboard(user.id))
    else: await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=get_main_keyboard(user.id))

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); user = q.from_user; s = load_settings(); vip = get_user_vip(user.id)
    txt = s["welcome_text"].format(name=user.first_name) + f"\n\nדרגה: {vip['icon']} *{vip['name']}* ({int(vip['discount']*100)}% הנחה)"
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=get_main_keyboard(user.id))

async def vip_info(update, context):
    q = update.callback_query; await q.answer(); uid = q.from_user.id; vip = get_user_vip(uid); ps = load_json(USERS_FILE).get(str(uid), {}).get("purchases", 0)
    txt = f"👑 *דרגות VIP*\nשלך: {vip['icon']} *{vip['name']}*\nרכישות: *{ps}*\n\n"
    for l in VIP_LEVELS: txt += f"{l['icon']} *{l['name']}*: {l['min']}+ | {int(l['discount']*100)}% הנחה\n"
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]))

async def daily_bonus(update, context):
    q = update.callback_query; await q.answer(); uid = str(q.from_user.id); users = load_json(USERS_FILE); u = users.get(uid, {})
    if u.get("last_bonus") == str(date.today()): await q.answer("❌ כבר קיבלת!", show_alert=True); return
    u["last_bonus"] = str(date.today()); save_json(USERS_FILE, users); c = load_json(COINS_FILE); c[uid] = c.get(uid, 0) + 1; save_json(COINS_FILE, c)
    await q.answer("🎁 קיבלת 1 מטבע!", show_alert=True); await back_main(update, context)

async def payment_method_menu(update, context):
    q = update.callback_query; await q.answer()
    if await maintenance_gate(update): return
    bal = load_json(COINS_FILE).get(str(q.from_user.id), 0)
    await q.edit_message_text("💰 *אמצעי תשלום:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 פייפאל", callback_data="paypal_menu")], [InlineKeyboardButton(f"🪙 מטבעות ({bal})", callback_data="coins_menu")], [InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]))

async def paypal_menu(update, context):
    q = update.callback_query; await q.answer(); vip = get_user_vip(q.from_user.id); btns = []
    for i, p in enumerate(PACKAGES):
        pr = round(p["price"] * (1 - vip["discount"]), 2)
        btns.append([InlineKeyboardButton(f"₪{pr} – {p['label']}", callback_data=f"pp_{i}")])
    btns.append([InlineKeyboardButton("🔙 חזרה", callback_data="payment_method")])
    await q.edit_message_text("💳 *פייפאל*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def paypal_package_selected(update, context):
    q = update.callback_query; await q.answer(); idx = int(q.data.split("_")[1]); pkg = PACKAGES[idx]; vip = get_user_vip(q.from_user.id); pr = round(pkg["price"] * (1 - vip["discount"]), 2)
    await q.edit_message_text(f"✅ *{pkg['videos']} סרטונים*\n💰 מחיר: *₪{pr}*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 שלם", url=f"{PAYPAL_LINK}/{pr}")], [InlineKeyboardButton("🔙 חזרה", callback_data="paypal_menu")]]))

async def coins_menu(update, context):
    q = update.callback_query; await q.answer(); uid = str(q.from_user.id); vip = get_user_vip(uid); bal = load_json(COINS_FILE).get(uid, 0); btns = []
    for i, p in enumerate(PACKAGES):
        cost = int(p["coins"] * (1 - vip["discount"]))
        btns.append([InlineKeyboardButton(f"🪙{cost} – {p['label']}", callback_data=f"coin_{i}")])
    btns.append([InlineKeyboardButton("🔙 חזרה", callback_data="payment_method")])
    await q.edit_message_text(f"🪙 *מטבעות* (יתרה: {bal}):", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def coin_package_buy(update, context):
    q = update.callback_query; await q.answer(); uid = str(q.from_user.id); idx = int(q.data.split("_")[1]); pkg = PACKAGES[idx]; cost = int(pkg["coins"] * (1 - get_user_vip(uid)["discount"])); coins = load_json(COINS_FILE)
    if coins.get(uid, 0) < cost: await q.answer("❌ אין מספיק!", show_alert=True); return
    coins[uid] -= cost; save_json(COINS_FILE, coins); sent = await send_videos_to_user(context, q.from_user.id, pkg["videos"])
    if sent > 0: record_order(q.from_user.id, 0, sent, "coins"); await q.message.reply_text(f"✅ נשלחו {sent}!")
    else: coins[uid] += cost; save_json(COINS_FILE, coins); await q.message.reply_text("❌ ריק.")
    await back_main(update, context)

# --- Admin Handlers ---

async def admin_stats(update, context):
    q = update.callback_query; await q.answer()
    if q.from_user.id != ADMIN_ID: return
    u, o, v, c, cp = load_json(USERS_FILE), load_json(ORDERS_FILE), load_json(VIDEOS_FILE), load_json(COINS_FILE), load_json(COUPONS_FILE)
    today = str(date.today()); week = str(date.today() - timedelta(days=7))
    new_t = sum(1 for x in u.values() if x.get("joined") == today)
    new_w = sum(1 for x in u.values() if x.get("joined", "") >= week)
    rev = sum(x.get("amount", 0) for x in o if x.get("type") in ("manual", "paypal"))
    pp_o = sum(1 for x in o if x.get("type") in ("manual", "paypal"))
    cn_o = sum(1 for x in o if x.get("type") == "coins")
    maint = "✅ פעיל" if load_settings().get("maintenance") else "❌ כבוי"
    txt = f"📊 *סטטיסטיקה מפורטת*\n\n👤 משתמשים: *{len(u)}*\n🆕 היום: *{new_t}* | שבוע: *{new_w}*\n\n💰 פייפאל: *₪{rev:.1f}*\n🧾 הזמנות פייפאל: *{pp_o}*\n🪙 הזמנות מטבעות: *{cn_o}*\n\n🪙 שוק: *{int(sum(c.values()))}*\n🎟 קופונים: *{sum(len(x.get('used_by', [])) for x in cp.values())}*\n🎬 סרטונים: *{len(v)}*\n🔧 תחזוקה: {maint}"
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

async def admin_gallery(update, context):
    q = update.callback_query; await q.answer()
    vids = load_json(VIDEOS_FILE)
    if not vids: await q.edit_message_text("ריק.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="back_admin")]])); return
    await admin_gallery_page(update, context, 0)

async def admin_gallery_page(update, context, page: int):
    vids = load_json(VIDEOS_FILE); total = len(vids); pages = (total + 5 - 1) // 5; page = max(0, min(page, pages - 1))
    start = page * 5; chunk = vids[start:start + 5]; btns = []
    for i, v in enumerate(chunk, start + 1): btns.append([InlineKeyboardButton(f"🎬 #{i} (מחק)", callback_data=f"vid_del_{start+i-1}")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"vid_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="noop"))
    if page < pages - 1: nav.append(InlineKeyboardButton("➡️", callback_data=f"vid_page_{page+1}"))
    btns.append(nav); btns.append([InlineKeyboardButton("📤 שלח הכל", callback_data="vid_send_all")]); btns.append([InlineKeyboardButton("🔙", callback_data="back_admin")])
    await update.callback_query.edit_message_text(f"🎬 גלריה ({page+1}/{pages})", reply_markup=InlineKeyboardMarkup(btns))

async def admin_gallery_send_all(update, context):
    q = update.callback_query; await q.answer(); vids = load_json(VIDEOS_FILE); await q.edit_message_text(f"📤 שולח {len(vids)}...")
    sent = await send_videos_to_user(context, ADMIN_ID, len(vids), is_admin=True)
    await context.bot.send_message(ADMIN_ID, f"✅ נשלחו {sent}.", reply_markup=get_admin_inline_keyboard())

async def admin_gallery_delete(update, context):
    q = update.callback_query; idx = int(q.data.split("_")[2]); vids = load_json(VIDEOS_FILE)
    if 0 <= idx < len(vids): del vids[idx]; save_json(VIDEOS_FILE, vids); await q.answer("נמחק!")
    await admin_gallery(update, context)

# --- Other Handlers (Consolidated) ---

async def handle_video(update, context):
    if update.effective_user.id != ADMIN_ID: return
    v = update.message.video; vids = load_json(VIDEOS_FILE)
    if not any(x['file_id'] == v.file_id for x in vids):
        vids.append({"file_id": v.file_id, "duration": v.duration or 0})
        vids.sort(key=lambda x: x['duration']); save_json(VIDEOS_FILE, vids)
        await update.message.reply_text(f"✅ נשמר! סה\"כ: {len(vids)}")


async def admin_coupons_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID: return
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
    query = update.callback_query; await query.answer()
    if query.from_user.id != ADMIN_ID: return
    code = query.data.replace("coupon_del_", "")
    coupons = load_json(COUPONS_FILE)
    if code in coupons: del coupons[code]; save_json(COUPONS_FILE, coupons)
    await admin_coupons_menu(update, context)

async def admin_coupon_new_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID: return ConversationHandler.END
    await query.edit_message_text("🎟 *קופון חדש*\n\nשלח את *קוד הקופון* (אותיות/מספרים):", parse_mode="Markdown")
    return ADMIN_COUPON_CODE

async def admin_coupon_get_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    code = update.message.text.strip().upper()
    context.user_data["new_coupon_code"] = code
    await update.message.reply_text(f"✅ קוד: `{code}`\n\nכמה 🪙 מטבעות?", parse_mode="Markdown")
    return ADMIN_COUPON_COINS

async def admin_coupon_get_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    try:
        val = int(update.message.text.strip())
        context.user_data["new_coupon_coins"] = val
        await update.message.reply_text("📅 תאריך תפוגה? (`YYYY-MM-DD` או `skip`):")
        return ADMIN_COUPON_EXPIRY
    except:
        await update.message.reply_text("❌ מספר לא תקין.")
        return ADMIN_COUPON_COINS

async def admin_coupon_get_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    raw = update.message.text.strip()
    context.user_data["new_coupon_expiry"] = None if raw.lower() == "skip" else raw
    await update.message.reply_text("👥 מגבלת שימושים? (מספר או `skip`):")
    return ADMIN_COUPON_LIMIT

async def admin_coupon_get_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    raw = update.message.text.strip()
    max_u = None if raw.lower() == "skip" else int(raw)
    code = context.user_data["new_coupon_code"]
    coins = context.user_data["new_coupon_coins"]
    exp = context.user_data["new_coupon_expiry"]
    coupons = load_json(COUPONS_FILE)
    coupons[code] = {"coins": coins, "expires": exp, "max_uses": max_u, "used_by": []}
    save_json(COUPONS_FILE, coupons)
    await update.message.reply_text(f"✅ קופון `{code}` נוצר!")
    return ConversationHandler.END

def main():
    ensure_data_files()
    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), type("H", (BaseHTTPRequestHandler,), {"do_GET": lambda s: (s.send_response(200), s.end_headers(), s.wfile.write(b"OK"))})).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    
    # Converstations (Broadcast, Welcome, Restore) - Minimal implementation
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🛠 פאנל אדמין$"), lambda u, c: u.message.reply_text("🛠 פאנל אדמין", reply_markup=get_admin_inline_keyboard())))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_coupon_new_start, pattern="^admin_coupon_new$")],
        states={
            ADMIN_COUPON_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coupon_get_code)],
            ADMIN_COUPON_COINS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coupon_get_coins)],
            ADMIN_COUPON_EXPIRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coupon_get_expiry)],
            ADMIN_COUPON_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coupon_get_limit)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
    ))
    
    cbs = [
        ("^back_main$", back_main), ("^payment_method$", payment_method_menu), ("^paypal_menu$", paypal_menu), ("^pp_", paypal_package_selected),
        ("^coins_menu$", coins_menu), ("^coin_", coin_package_buy), ("^vip_info$", vip_info), ("^daily_bonus$", daily_bonus),
        ("^admin_stats$", admin_stats), ("^admin_gallery$", admin_gallery), ("^vid_send_all$", admin_gallery_send_all), ("^vid_del_", admin_gallery_delete),
        ("^admin_coupons$", admin_coupons_menu), ("^coupon_del_", admin_coupon_delete),
        ("^back_admin$", lambda u, c: u.callback_query.edit_message_text("🛠 פאנל אדמין", reply_markup=get_admin_inline_keyboard())),
        ("^admin_backup_menu$", lambda u, c: u.callback_query.edit_message_text("💾 ניהול גיבויים", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 הורד ZIP", callback_data="admin_backup_download")], [InlineKeyboardButton("🔙", callback_data="back_admin")]]))),
        ("^admin_backup_download$", lambda u, c: (u.callback_query.answer(), context.bot.send_document(ADMIN_ID, io.BytesIO(zipfile.ZipFile(io.BytesIO(), "w").write(DATA_DIR).fp.getvalue()), filename="backup.zip")))
    ]
    for p, h in cbs: app.add_handler(CallbackQueryHandler(h, pattern=p))
    
    app.run_polling()

if __name__ == "__main__": main()
