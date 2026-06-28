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
    LabeledPrice,
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

# Ignore warnings
warnings.filterwarnings("ignore", message=".*per_message=False.*CallbackQueryHandler.*")

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Constants
TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7706183809"))
PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "BAAd231NK9V9yCPlOHY57GWJDlLY_6W6G6ZZS0g3jUh8SzaLG8Q2sdfHcuE_Pi-m3kDZTvcMpahHCcEYlk")
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET", "EIxM_M8lRQzoIaaxKAa3ugpK_VnFg3wqCoxgUivq-TnLxxGbtVn6m-7c4OWaeAb_tqnOVyd4khvx2LSI")
PAYPAL_API_BASE = "https://api-m.paypal.com"

DATA_DIR       = Path("data")
USERS_FILE     = DATA_DIR / "users.json"
REFERRALS_FILE = DATA_DIR / "referrals.json"
VIDEOS_FILE    = DATA_DIR / "videos.json"
ORDERS_FILE    = DATA_DIR / "orders.json"
SETTINGS_FILE  = DATA_DIR / "settings.json"

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

VIP_LEVELS = [
    {"name": "ברונזה", "min_purchases": 0,  "discount": 0.0,  "icon": "🥉"},
    {"name": "כסף",   "min_purchases": 6,  "discount": 0.10, "icon": "🥈"},
    {"name": "זהב",   "min_purchases": 16, "discount": 0.25, "icon": "🥇"},
    {"name": "יהלום", "min_purchases": 31, "discount": 0.40, "icon": "💎"},
]

# Conversation states
(
    ADMIN_SEND_MSG,
    ADMIN_SEND_ID,
    ADMIN_APPROVE_COUNT,
    ADMIN_APPROVE_ID,
    ADMIN_CHECK_USER,
    ADMIN_BROADCAST,
    ADMIN_BROADCAST_MEDIA,
    ADMIN_BROADCAST_BTN,
    ADMIN_BROADCAST_DELAY,
    SUPPORT_WAITING_MSG,
    SUPPORT_REPLY_MSG,
    ADMIN_MULTIPLIER,
    ADMIN_RESTORE,
    ADMIN_GLOBAL_RESET_CONFIRM,
    ADMIN_VIDEO_SEARCH,
    ADMIN_VIP_ID,
    ADMIN_VIP_LEVEL
) = range(17)

# --- Data Helpers ---
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
            return json.load(f)
    except:
        return [] if "videos" in str(filepath) or "orders" in str(filepath) else {}

def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_settings():
    s = load_json(SETTINGS_FILE)
    if not isinstance(s, dict): s = {}
    s.setdefault("referral_multiplier", 1.0)
    s.setdefault("maintenance", False)
    return s

def save_settings(s):
    save_json(SETTINGS_FILE, s)

# --- Business Logic ---
def get_user_vip(user_id):
    users = load_json(USERS_FILE)
    u = users.get(str(user_id), {})
    purchases = u.get("purchases", 0)
    current_vip = VIP_LEVELS[0]
    for level in VIP_LEVELS:
        if purchases >= level["min_purchases"]:
            current_vip = level
    return current_vip

def register_user(user, ref_id=None):
    users = load_json(USERS_FILE)
    uid = str(user.id)
    if uid not in users:
        users[uid] = {
            "id": user.id,
            "first_name": user.first_name,
            "username": user.username,
            "joined": str(date.today()),
            "purchases": 0,
            "total_spent": 0,
            "seen_videos": []
        }
        save_json(USERS_FILE, users)
        if ref_id and str(ref_id) != uid:
            referrals = load_json(REFERRALS_FILE)
            rk = str(ref_id)
            if rk not in referrals: referrals[rk] = {"count": 0, "referred_ids": []}
            if uid not in referrals[rk]["referred_ids"]:
                referrals[rk]["count"] += 1
                referrals[rk]["referred_ids"].append(uid)
                save_json(REFERRALS_FILE, referrals)
    return users.get(uid)

async def send_videos_to_user(context, user_id, count):
    all_v = load_json(VIDEOS_FILE)
    if not all_v: return 0
    users = load_json(USERS_FILE)
    uid = str(user_id)
    u_data = users.get(uid, {})
    seen = u_data.get("seen_videos", [])
    unseen = [v for v in all_v if v["file_id"] not in seen]
    
    if len(unseen) >= count:
        selected = random.sample(unseen, count)
    else:
        selected = unseen + random.sample(all_v, min(count - len(unseen), len(all_v)))
        
    sent = 0
    for v in selected:
        try:
            await context.bot.send_video(chat_id=user_id, video=v["file_id"])
            if v["file_id"] not in seen: seen.append(v["file_id"])
            sent += 1
            await asyncio.sleep(0.1)
        except: pass
    u_data["seen_videos"] = seen
    users[uid] = u_data
    save_json(USERS_FILE, users)
    return sent

def record_order(user_id, amount, videos_count, order_type):
    orders = load_json(ORDERS_FILE)
    orders.append({"user_id": user_id, "amount": amount, "videos_count": videos_count, "date": str(date.today()), "type": order_type})
    save_json(ORDERS_FILE, orders)
    users = load_json(USERS_FILE)
    uid = str(user_id)
    if uid in users:
        users[uid]["purchases"] = users[uid].get("purchases", 0) + 1
        users[uid]["total_spent"] = users[uid].get("total_spent", 0) + amount
        save_json(USERS_FILE, users)

# --- PayPal ---
def get_paypal_token():
    try:
        res = requests.post(f"{PAYPAL_API_BASE}/v1/oauth2/token", auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET), data={"grant_type": "client_credentials"}, timeout=10)
        return res.json().get("access_token") if res.status_code == 200 else None
    except: return None

def create_paypal_order(amount):
    token = get_paypal_token()
    if not token: return None
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    payload = {"intent": "CAPTURE", "purchase_units": [{"amount": {"currency_code": "ILS", "value": str(amount)}}]}
    try:
        res = requests.post(f"{PAYPAL_API_BASE}/v2/checkout/orders", headers=headers, json=payload, timeout=10)
        if res.status_code == 201:
            data = res.json()
            link = next(l["href"] for l in data["links"] if l["rel"] == "approve")
            return data["id"], link
    except: pass
    return None

def capture_paypal_order(order_id):
    token = get_paypal_token()
    if not token: return False
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    try:
        res = requests.post(f"{PAYPAL_API_BASE}/v2/checkout/orders/{order_id}/capture", headers=headers, timeout=10)
        return res.status_code in [200, 201] and res.json().get("status") == "COMPLETED"
    except: return False

# --- Keyboards ---
def get_main_keyboard(user_id):
    vip = get_user_vip(user_id)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{vip['icon']} רמת {vip['name']}", callback_data="vip_info")],
        [InlineKeyboardButton("💳 תשלום", callback_data="payment_method"), InlineKeyboardButton("👥 הפניות", callback_data="referrals")],
        [InlineKeyboardButton("💬 תמיכה", callback_data="support")]
    ])

def get_admin_inline_keyboard():
    s = load_settings()
    m = "🟠 תחזוקה" if s.get("maintenance") else "🟢 פעיל"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📡 סטטוס: {m}", callback_data="admin_maintenance")],
        [InlineKeyboardButton("📊 סטטיסטיקה", callback_data="admin_stats"), InlineKeyboardButton("🧾 הזמנות", callback_data="admin_orders_page_0")],
        [InlineKeyboardButton("🔍 בדוק משתמש", callback_data="admin_check"), InlineKeyboardButton("👥 משתמשים", callback_data="users_page_0")],
        [InlineKeyboardButton("📩 שלח למשתמש", callback_data="admin_send"), InlineKeyboardButton("✅ אישור ידני", callback_data="admin_approve")],
        [InlineKeyboardButton("🎬 גלריה", callback_data="admin_gallery"), InlineKeyboardButton("🔢 חיפוש", callback_data="admin_video_search")],
        [InlineKeyboardButton("📢 הודעה לכולם", callback_data="admin_broadcast"), InlineKeyboardButton("💎 דרגות", callback_data="admin_vip")],
        [InlineKeyboardButton("💱 מכפיל", callback_data="admin_multiplier"), InlineKeyboardButton("💾 גיבוי", callback_data="admin_backup")],
        [InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]
    ])

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ref_id = context.args[0] if context.args else None
    user = update.effective_user
    register_user(user, ref_id)
    txt = f"👋 שלום {user.first_name}!\nברוך הבא לבוט מכירת הסרטונים שלנו. 🎬\nבחר אפשרות מהתפריט:"
    kb = get_main_keyboard(user.id)
    if user.id == ADMIN_ID:
        await update.message.reply_text(txt, reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🛠 פאנל אדמין")]], resize_keyboard=True))
        await update.message.reply_text("ניהול:", reply_markup=kb)
    else:
        await update.message.reply_text(txt, reply_markup=kb)

async def payment_method_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ שלם בכוכבי טלגרם", callback_data="stars_menu")],
        [InlineKeyboardButton("💳 תשלום בפייפאל", callback_data="paypal_menu")],
        [InlineKeyboardButton("💬 תשלום אחר (פנייה לתמיכה)", callback_data="support")],
        [InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]
    ])
    await query.edit_message_text("💳 *בחר שיטת תשלום:*", parse_mode="Markdown", reply_markup=kb)

async def stars_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    btns = [[InlineKeyboardButton(p["label_stars"], callback_data=f"star_{i}")] for i, p in enumerate(PACKAGES)]
    btns.append([InlineKeyboardButton("🔙 חזרה", callback_data="payment_method")])
    await query.edit_message_text("⭐ *בחר חבילת כוכבים:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def star_package_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    pkg = PACKAGES[idx]
    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title=f"חבילת {pkg['videos']} סרטונים",
        description=f"קבלת {pkg['videos']} סרטונים ישירות לבוט",
        payload=f"star_{idx}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("תשלום", pkg["stars"])]
    )

async def pre_checkout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    pay = msg.successful_payment
    if pay.invoice_payload.startswith("star_"):
        idx = int(pay.invoice_payload.split("_")[1])
        pkg = PACKAGES[idx]
        sent = await send_videos_to_user(context, msg.from_user.id, pkg["videos"])
        if sent > 0:
            record_order(msg.from_user.id, pkg["price"], sent, "stars")
            await msg.reply_text(f"✅ תודה! {sent} סרטונים נשלחו אליך.")

async def paypal_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    btns = [[InlineKeyboardButton(p["label_paypal"], callback_data=f"pp_{i}")] for i, p in enumerate(PACKAGES)]
    btns.append([InlineKeyboardButton("🔙 חזרה", callback_data="payment_method")])
    await query.edit_message_text("💳 *בחר חבילה לתשלום בפייפאל:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def paypal_package_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    pkg = PACKAGES[idx]
    res = create_paypal_order(pkg["price"])
    if res:
        oid, link = res
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 לחץ כאן לתשלום", url=link)],
            [InlineKeyboardButton("✅ לחץ כאן לאחר ששילמת", callback_data=f"ppverify_{idx}_{oid}")],
            [InlineKeyboardButton("🔙 ביטול", callback_data="paypal_menu")]
        ])
        await query.edit_message_text(f"🚀 הזמנה נוצרה!\nחבילה: {pkg['videos']} סרטונים\nמחיר: ₪{pkg['price']}\n\nלאחר הסיום לחץ על הכפתור למטה:", reply_markup=kb)
    else:
        await query.edit_message_text("❌ שגיאה ביצירת הזמנה. נסה שוב מאוחר יותר.")

async def paypal_verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, idx, oid = query.data.split("_")
    if capture_paypal_order(oid):
        pkg = PACKAGES[int(idx)]
        sent = await send_videos_to_user(context, query.from_user.id, pkg["videos"])
        record_order(query.from_user.id, pkg["price"], sent, "paypal")
        await query.edit_message_text(f"✅ התשלום אושר! {sent} סרטונים נשלחו.")
    else:
        await query.answer("❌ התשלום עדיין לא הושלם או שבוטל.", show_alert=True)

# --- Admin Handlers ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("🛠 *פאנל אדמין*", parse_mode="Markdown", reply_markup=get_admin_inline_keyboard())

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    u, o, v = load_json(USERS_FILE), load_json(ORDERS_FILE), load_json(VIDEOS_FILE)
    txt = f"📊 *סטטיסטיקה:*\n👥 משתמשים: {len(u)}\n🧾 הזמנות: {len(o)}\n🎬 סרטונים: {len(v)}"
    await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=get_admin_inline_keyboard())

async def admin_maintenance_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    s = load_settings()
    s["maintenance"] = not s["maintenance"]
    save_settings(s)
    await query.edit_message_text(f"🛠 סטטוס תחזוקה שונה ל: {s['maintenance']}", reply_markup=get_admin_inline_keyboard())

async def admin_check_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("🔍 שלח ID לבדיקה:")
    return ADMIN_CHECK_USER

async def admin_check_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.text.strip()
    u = load_json(USERS_FILE).get(uid)
    if u:
        txt = f"👤 `{uid}`\nשם: {u['first_name']}\nרכישות: {u['purchases']}\nשילם: ₪{u['total_spent']}"
        await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=get_admin_inline_keyboard())
    else: await update.message.reply_text("❌ לא נמצא.")
    return ConversationHandler.END

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID and update.message.video:
        v = load_json(VIDEOS_FILE)
        v.append({"file_id": update.message.video.file_id, "duration": update.message.video.duration})
        save_json(VIDEOS_FILE, v)
        await update.message.reply_text(f"✅ נשמר! סה\"כ: {len(v)}")

async def support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("💬 שלח הודעה לתמיכה:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]))
    return SUPPORT_WAITING_MSG

async def support_receive_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await context.bot.send_message(ADMIN_ID, f"📩 תמיכה מ-{u.first_name} (`{u.id}`):\n{update.message.text}")
    await update.message.reply_text("✅ נשלח!")
    return ConversationHandler.END

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("🏠 תפריט ראשי", reply_markup=get_main_keyboard(update.effective_user.id))

async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.callback_query.answer()
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ בוטל.", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

# --- Health Server ---
class _H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, *args): pass
def _srv():
    p = int(os.environ.get("PORT", "10000"))
    try: HTTPServer(("0.0.0.0", p), _H).serve_forever()
    except: pass

# --- Main ---
def main():
    ensure_data_files()
    threading.Thread(target=_srv, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🛠 פאנל אדמין$"), admin_panel))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_check_start, pattern="^admin_check$")],
        states={ADMIN_CHECK_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_check_user)]},
        fallbacks=[CommandHandler("cancel", cancel)], per_message=False, per_chat=True
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(support_menu, pattern="^support$")],
        states={SUPPORT_WAITING_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive_msg)]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")], per_message=False, per_chat=True
    ))

    cbs = [
        ("^payment_method$", payment_method_menu), ("^stars_menu$", stars_menu), (r"^star_\\d+$", star_package_buy),
        ("^paypal_menu$", paypal_menu), (r"^pp_\\d+$", paypal_package_selected), (r"^ppverify_\\d+_", paypal_verify_payment),
        ("^back_main$", back_main), ("^admin_stats$", admin_stats), ("^admin_maintenance$", admin_maintenance_toggle),
        ("^noop$", noop_callback)
    ]
    for p, h in cbs: app.add_handler(CallbackQueryHandler(h, pattern=p))
    
    logger.info("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
