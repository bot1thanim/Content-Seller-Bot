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
    {"price": 2,   "videos": 1,   "coins": 20},
    {"price": 9,   "videos": 5,   "coins": 90},
    {"price": 16,  "videos": 10,  "coins": 160},
    {"price": 30,  "videos": 20,  "coins": 300},
    {"price": 65,  "videos": 50,  "coins": 650},
    {"price": 85,  "videos": 70,  "coins": 850},
    {"price": 110, "videos": 100, "coins": 1100},
    {"price": 180, "videos": 200, "coins": 1800},
]

VIP_LEVELS = [
    {"name": "ברונזה", "min_purchases": 0,  "discount": 0.0,  "icon": "🥉"},
    {"name": "כסף",   "min_purchases": 6,  "discount": 0.10, "icon": "🥈"},
    {"name": "זהב",   "min_purchases": 16, "discount": 0.25, "icon": "🥇"},
    {"name": "יהלום", "min_purchases": 31, "discount": 0.40, "icon": "💎"},
]

(
    ADMIN_SEND_MSG, ADMIN_SEND_ID, ADMIN_APPROVE_COUNT, ADMIN_APPROVE_ID,
    ADMIN_CHECK_USER, ADMIN_COINS_ID, ADMIN_COINS_AMOUNT, ADMIN_BROADCAST,
    ADMIN_BROADCAST_BTN, ADMIN_BROADCAST_DELAY, SUPPORT_WAITING_MSG,
    SUPPORT_REPLY_MSG, ADMIN_COUPON_CODE, ADMIN_COUPON_COINS, COUPON_REDEEM,
    ADMIN_MULTIPLIER, ADMIN_RESTORE, ADMIN_GLOBAL_RESET_CONFIRM,
    ADMIN_VIDEO_SEARCH, ADMIN_VIDEO_PREVIEW, ADMIN_BROADCAST_MEDIA,
    ADMIN_SET_WELCOME_TEXT, ADMIN_SET_WELCOME_MEDIA,
) = range(23)

def ensure_data_files():
    DATA_DIR.mkdir(exist_ok=True)
    defaults = [(USERS_FILE, {}), (COINS_FILE, {}), (REFERRALS_FILE, {}), (VIDEOS_FILE, []), (ORDERS_FILE, []), (COUPONS_FILE, {}), (SETTINGS_FILE, {"referral_multiplier": 1.0, "maintenance": False, "welcome_text": "שלום {name} 👋\nברוכים הבאים לבוט התכנים האסורים 🤫", "welcome_media": None})]
    for f, d in defaults:
        if not f.exists(): save_json(f, d)

def load_json(f):
    try:
        with open(f, "r", encoding="utf-8") as f_in: return json.load(f_in)
    except: return [] if "videos" in str(f) or "orders" in str(f) else {}

def save_json(f, data):
    tmp = Path(str(f) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f_out: json.dump(data, f_out, ensure_ascii=False, indent=2)
    tmp.replace(f)

def load_settings():
    s = load_json(SETTINGS_FILE)
    if not isinstance(s, dict): s = {}
    s.setdefault("referral_multiplier", 1.0)
    s.setdefault("maintenance", False)
    s.setdefault("welcome_text", "שלום {name} 👋\nברוכים הבאים לבוט התכנים האסורים 🤫")
    s.setdefault("welcome_media", None)
    return s

def save_settings(s): save_json(SETTINGS_FILE, s)
def is_maintenance(): return load_settings().get("maintenance", False)

def get_user_vip(uid):
    u = load_json(USERS_FILE).get(str(uid), {})
    purchases = u.get("purchases", 0)
    vip = VIP_LEVELS[0]
    for level in VIP_LEVELS:
        if purchases >= level["min_purchases"]: vip = level
    return vip

def register_user(user, ref_id=None):
    users = load_json(USERS_FILE)
    uid = str(user.id)
    if uid not in users:
        users[uid] = {"id": user.id, "first_name": user.first_name, "username": user.username, "joined": str(date.today()), "purchases": 0, "total_spent": 0, "seen_videos": [], "last_bonus": None}
        save_json(USERS_FILE, users)
        if ref_id and str(ref_id) != uid:
            refs = load_json(REFERRALS_FILE)
            rk = str(ref_id)
            if rk not in refs: refs[rk] = {"count": 0, "referred_ids": []}
            if uid not in refs[rk]["referred_ids"]:
                refs[rk]["count"] += 1; refs[rk]["referred_ids"].append(uid); save_json(REFERRALS_FILE, refs)
                c = load_json(COINS_FILE); c[rk] = c.get(rk, 0) + 1; save_json(COINS_FILE, c)

async def send_videos_to_user(context, user_id, count):
    vids = load_json(VIDEOS_FILE)
    vids.sort(key=lambda x: x.get("duration", 0))
    users = load_json(USERS_FILE)
    uid = str(user_id)
    u_data = users.get(uid, {})
    seen = u_data.get("seen_videos", [])
    unseen = [v for v in vids if v["file_id"] not in seen]
    if len(unseen) >= count: selected = unseen[:count]
    else: selected = unseen + random.sample([v for v in vids if v["file_id"] in seen], min(count - len(unseen), len(vids) - len(unseen)))
    sent = 0
    for v in selected:
        try:
            fid = v["file_id"]
            idx = next((i+1 for i, vid in enumerate(vids) if vid["file_id"] == fid), 0)
            await context.bot.send_video(chat_id=user_id, video=fid, caption=f"🎬 סרטון #{idx}")
            if fid not in seen: seen.append(fid)
            sent += 1; await asyncio.sleep(0.1)
        except: pass
    u_data["seen_videos"] = seen; users[uid] = u_data; save_json(USERS_FILE, users)
    return sent

def record_order(uid, amount, count, otype):
    orders = load_json(ORDERS_FILE)
    orders.append({"user_id": uid, "amount": amount, "videos_count": count, "date": str(date.today()), "type": otype})
    save_json(ORDERS_FILE, orders)
    users = load_json(USERS_FILE)
    u = users.get(str(uid))
    if u: u["purchases"] += 1; u["total_spent"] += amount; save_json(USERS_FILE, users)

def get_main_keyboard(uid):
    vip = get_user_vip(uid)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{vip['icon']} רמת {vip['name']}", callback_data="vip_info"), InlineKeyboardButton("🎁 מתנה יומית", callback_data="daily_bonus")],
        [InlineKeyboardButton("💳 תשלום", callback_data="payment_method"), InlineKeyboardButton("👥 הפניות שלי", callback_data="referrals")],
        [InlineKeyboardButton("💰 ארנק מטבעות", callback_data="wallet"), InlineKeyboardButton("🎟 מימוש קופון", callback_data="coupon_redeem")],
        [InlineKeyboardButton("💬 תמיכה", callback_data="support")]
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
        [InlineKeyboardButton("🎟 ניהול קופונים", callback_data="admin_coupons"), InlineKeyboardButton("👋 הודעת ברוכים הבאים", callback_data="admin_welcome_set")],
        [InlineKeyboardButton("💱 ערך מטבע", callback_data="admin_multiplier"), InlineKeyboardButton("💾 ניהול גיבויים", callback_data="admin_backup_menu")],
        [InlineKeyboardButton("🔄 איפוס נתונים", callback_data="admin_global_reset"), InlineKeyboardButton("🧹 מחק סרטונים", callback_data="admin_delete")],
        [InlineKeyboardButton(ml, callback_data="admin_maintenance")]
    ])

async def start(update, context):
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

async def back_main(update, context):
    q = update.callback_query; await q.answer(); user = q.from_user; s = load_settings(); vip = get_user_vip(user.id)
    txt = s["welcome_text"].format(name=user.first_name) + f"\n\nדרגה: {vip['icon']} *{vip['name']}* ({int(vip['discount']*100)}% הנחה)"
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=get_main_keyboard(user.id))

async def daily_bonus(update, context):
    q = update.callback_query; await q.answer(); uid = str(q.from_user.id); users = load_json(USERS_FILE); u = users.get(uid, {})
    if u.get("last_bonus") == str(date.today()): await q.answer("❌ כבר קיבלת!", show_alert=True); return
    u["last_bonus"] = str(date.today()); save_json(USERS_FILE, users); c = load_json(COINS_FILE); c[uid] = c.get(uid, 0) + 1; save_json(COINS_FILE, c)
    await q.answer("🎁 קיבלת 1 מטבע!", show_alert=True); await back_main(update, context)

async def vip_info(update, context):
    q = update.callback_query; await q.answer(); uid = str(q.from_user.id); vip = get_user_vip(uid); ps = load_json(USERS_FILE).get(uid, {}).get("purchases", 0)
    txt = f"👑 *דרגות VIP*\n\nשלך: {vip['icon']} *{vip['name']}*\nרכישות: *{ps}*\n\n"
    for l in VIP_LEVELS: txt += f"{l['icon']} *{l['name']}*: {l['min_purchases']}+ | {int(l['discount']*100)}% הנחה\n"
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]))

async def payment_method_menu(update, context):
    q = update.callback_query; await q.answer(); if await maintenance_gate(update): return
    bal = load_json(COINS_FILE).get(str(q.from_user.id), 0)
    await q.edit_message_text("💰 *אמצעי תשלום:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 פייפאל", callback_data="paypal_menu")], [InlineKeyboardButton(f"🪙 מטבעות ({bal})", callback_data="coins_menu")], [InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]))

async def paypal_menu(update, context):
    q = update.callback_query; await q.answer(); vip = get_user_vip(q.from_user.id); btns = []
    for i, p in enumerate(PACKAGES):
        pr = round(p["price"] * (1 - vip["discount"]), 2)
        btns.append([InlineKeyboardButton(f"₪{pr} – {p['videos']} סרטונים", callback_data=f"pp_{i}")])
    btns.append([InlineKeyboardButton("🔙 חזרה", callback_data="payment_method")])
    await q.edit_message_text("💳 *פייפאל*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def paypal_package_selected(update, context):
    q = update.callback_query; await q.answer(); idx = int(q.data.split("_")[1]); pkg = PACKAGES[idx]; vip = get_user_vip(q.from_user.id); pr = round(pkg["price"] * (1 - vip["discount"]), 2)
    await q.edit_message_text(f"✅ *{pkg['videos']} סרטונים*\n💰 מחיר: *₪{pr}*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 שלם", url=f"{PAYPAL_LINK}/{pr}")], [InlineKeyboardButton("💬 שלח אישור", callback_data="support")], [InlineKeyboardButton("🔙 חזרה", callback_data="paypal_menu")]]))

async def coins_menu(update, context):
    q = update.callback_query; await q.answer(); uid = str(q.from_user.id); vip = get_user_vip(uid); bal = load_json(COINS_FILE).get(uid, 0); btns = []
    for i, p in enumerate(PACKAGES):
        cost = int(p["coins"] * (1 - vip["discount"]))
        btns.append([InlineKeyboardButton(f"🪙{cost} – {p['videos']} סרטונים", callback_data=f"coin_{i}")])
    btns.append([InlineKeyboardButton("🔙 חזרה", callback_data="payment_method")])
    await q.edit_message_text(f"🪙 *מטבעות* (יתרה: {bal}):", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def coin_package_buy(update, context):
    q = update.callback_query; await q.answer(); uid = str(q.from_user.id); idx = int(q.data.split("_")[1]); pkg = PACKAGES[idx]; cost = int(pkg["coins"] * (1 - get_user_vip(uid)["discount"])); coins = load_json(COINS_FILE)
    if coins.get(uid, 0) < cost: await q.answer("❌ אין מספיק!", show_alert=True); return
    coins[uid] -= cost; save_json(COINS_FILE, coins); sent = await send_videos_to_user(context, q.from_user.id, pkg["videos"])
    if sent > 0: record_order(q.from_user.id, 0, sent, "coins"); await q.message.reply_text(f"✅ נשלחו {sent}!")
    else: coins[uid] += cost; save_json(COINS_FILE, coins); await q.message.reply_text("❌ ריק.")
    await back_main(update, context)

async def referrals_menu(update, context):
    q = update.callback_query; await q.answer(); uid = str(q.from_user.id); cnt = load_json(REFERRALS_FILE).get(uid, {}).get("count", 0); b = (await context.bot.get_me()).username
    await q.edit_message_text(f"👥 *הפניות*: {cnt}\nקישור: `https://t.me/{b}?start=ref_{uid}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]))

async def wallet_menu(update, context):
    q = update.callback_query; await q.answer(); bal = load_json(COINS_FILE).get(str(q.from_user.id), 0)
    await q.edit_message_text(f"💰 *יתרה*: {bal} מטבעות", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]))

async def coupon_redeem_start(update, context):
    q = update.callback_query; await q.answer(); await q.edit_message_text("🎟 הזן קוד:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]])); return COUPON_REDEEM

async def coupon_redeem_input(update, context):
    uid = str(update.effective_user.id); code = update.message.text.strip().upper(); cs = load_json(COUPONS_FILE); c = cs.get(code)
    if not c or uid in c.get("used_by", []): await update.message.reply_text("❌ לא תקין.")
    else: c.setdefault("used_by", []).append(uid); save_json(COUPONS_FILE, cs); coins = load_json(COINS_FILE); coins[uid] = coins.get(uid, 0) + c["coins"]; save_json(COINS_FILE, coins); await update.message.reply_text(f"✅ קיבלת {c['coins']}!")
    return ConversationHandler.END

async def support_menu(update, context):
    q = update.callback_query; await q.answer(); await q.edit_message_text("💬 כתוב הודעה:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]])); return SUPPORT_WAITING_MSG

async def support_receive_msg(update, context):
    u = update.effective_user; await context.bot.send_message(ADMIN_ID, f"📩 *תמיכה*\n👤 {u.first_name} (`{u.id}`)\n💬 {update.message.text}", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ תשובה", callback_data=f"support_reply_{u.id}")]])); await update.message.reply_text("✅ נשלח!"); return ConversationHandler.END

async def admin_support_reply_start(update, context):
    q = update.callback_query; await q.answer(); context.user_data["target"] = q.data.replace("support_reply_", ""); await q.message.reply_text("✏️ תשובה:"); return SUPPORT_REPLY_MSG

async def admin_support_reply_send(update, context):
    t = context.user_data.get("target")
    try: await context.bot.send_message(int(t), f"📬 *תשובה:*\n\n{update.message.text}", parse_mode="Markdown"); await update.message.reply_text("✅ נשלח!")
    except: await update.message.reply_text("❌ נכשל.")
    return ConversationHandler.END

async def admin_panel(update, context):
    if update.effective_user.id == ADMIN_ID: await update.message.reply_text("🛠 *פאנל אדמין*", reply_markup=get_admin_inline_keyboard())

async def back_admin(update, context):
    q = update.callback_query; await q.answer(); await q.edit_message_text("🛠 *פאנל אדמין*", reply_markup=get_admin_inline_keyboard())

async def admin_stats(update, context):
    q = update.callback_query; await q.answer(); us = load_json(USERS_FILE); vs = load_json(VIDEOS_FILE); os = load_json(ORDERS_FILE); rev = sum(o.get("amount", 0) for o in os)
    await q.edit_message_text(f"📊 *סטטיסטיקה*\n👤 משתמשים: {len(us)}\n🎬 סרטונים: {len(vs)}\n💰 הכנסות: ₪{rev}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

async def admin_orders_page(update, context):
    q = update.callback_query; await q.answer(); p = int(q.data.split("admin_orders_page_")[1]); os = list(reversed(load_json(ORDERS_FILE))); chunk = os[p*10:(p+1)*10]
    txt = f"🧾 *הזמנות ({p+1}):*\n" + "\n".join([f"`{o['user_id']}` | ₪{o['amount']} | 🎬 {o['videos_count']}" for o in chunk])
    nav = []
    if p > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"admin_orders_page_{p-1}"))
    if len(os) > (p+1)*10: nav.append(InlineKeyboardButton("➡️", callback_data=f"admin_orders_page_{p+1}"))
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([nav, [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

async def users_page(update, context):
    q = update.callback_query; await q.answer(); idx = int(q.data.split("users_page_")[1]); ids = list(load_json(USERS_FILE).keys())
    if not ids: return
    u = load_json(USERS_FILE)[ids[idx]]; txt = f"👤 *משתמש {idx+1}/{len(ids)}*\n📛 {u['first_name']}\n🆔 `{ids[idx]}`\n🛒 רכישות: {u['purchases']}"
    nav = []; if idx > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"users_page_{idx-1}"))
    if idx < len(ids)-1: nav.append(InlineKeyboardButton("➡️", callback_data=f"users_page_{idx+1}"))
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([nav, [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

async def admin_check_start(update, context):
    q = update.callback_query; await q.answer(); await q.edit_message_text("🔍 שלח ID:"); return ADMIN_CHECK_USER

async def admin_check_user(update, context):
    u = load_json(USERS_FILE).get(update.message.text.strip())
    if not u: await update.message.reply_text("❌ לא נמצא.")
    else: await update.message.reply_text(f"👤 {u['first_name']}\n🛒 רכישות: {u['purchases']}", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_send_start(update, context):
    q = update.callback_query; await q.answer(); await q.edit_message_text("📩 כמה סרטונים?"); return ADMIN_SEND_MSG

async def admin_send_msg(update, context):
    context.user_data["sc"] = int(update.message.text); await update.message.reply_text("שלח ID:"); return ADMIN_SEND_ID

async def admin_send_id(update, context):
    uid = update.message.text.strip(); sent = await send_videos_to_user(context, int(uid), context.user_data["sc"])
    await update.message.reply_text(f"✅ נשלחו {sent}!", reply_markup=get_admin_inline_keyboard()); return ConversationHandler.END

async def admin_approve_start(update, context):
    q = update.callback_query; await q.answer(); await q.edit_message_text("✅ כמה סרטונים?"); return ADMIN_APPROVE_COUNT

async def admin_approve_count(update, context):
    context.user_data["ac"] = int(update.message.text); await update.message.reply_text("שלח ID:"); return ADMIN_APPROVE_ID

async def admin_approve_id(update, context):
    uid = update.message.text.strip(); sent = await send_videos_to_user(context, int(uid), context.user_data["ac"])
    if sent > 0: record_order(int(uid), 0, sent, "manual"); await update.message.reply_text("✅ אושר!", reply_markup=get_admin_inline_keyboard())
    else: await update.message.reply_text("❌ נכשל."); return ConversationHandler.END

async def admin_gallery(update, context):
    q = update.callback_query; await q.answer(); await admin_gallery_page(update, context, 0)

async def admin_gallery_page(update, context, page=None):
    q = update.callback_query; if page is None: page = int(q.data.split("vid_page_")[1])
    vs = load_json(VIDEOS_FILE); vs.sort(key=lambda x: x.get("duration", 0))
    if not vs: await q.edit_message_text("ריק.", reply_markup=get_admin_inline_keyboard()); return
    v = vs[page]; txt = f"🎬 *סרטון {page+1}/{len(vs)}*\n⏱ אורך: {v.get('duration', 0)} שניות"
    nav = []; if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"vid_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}", callback_data="noop"))
    if page < len(vs)-1: nav.append(InlineKeyboardButton("➡️", callback_data=f"vid_page_{page+1}"))
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([nav, [InlineKeyboardButton("🗑 מחק", callback_data=f"vid_del_{page}")], [InlineKeyboardButton("📤 שלח הכל", callback_data="vid_send_all")], [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))
    await context.bot.send_video(ADMIN_ID, v["file_id"])

async def admin_gallery_delete(update, context):
    q = update.callback_query; await q.answer(); idx = int(q.data.split("vid_del_")[1]); vs = load_json(VIDEOS_FILE)
    if 0 <= idx < len(vs): vs.pop(idx); save_json(VIDEOS_FILE, vs); await q.answer("נמחק!"); await admin_gallery(update, context)

async def admin_gallery_send_all(update, context):
    q = update.callback_query; await q.answer(); vs = load_json(VIDEOS_FILE); vs.sort(key=lambda x: x.get("duration", 0))
    for i, v in enumerate(vs):
        await context.bot.send_video(ADMIN_ID, v["file_id"], caption=f"🎬 סרטון #{i+1}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 מחק", callback_data=f"vid_del_{i}")]]))
        await asyncio.sleep(0.1)
    await context.bot.send_message(ADMIN_ID, "✅ סיימתי.", reply_markup=get_admin_inline_keyboard())

async def admin_video_search_start(update, context):
    q = update.callback_query; await q.answer(); await q.edit_message_text("🔢 שלח מספר:"); return ADMIN_VIDEO_SEARCH

async def admin_video_search_input(update, context):
    vs = load_json(VIDEOS_FILE); vs.sort(key=lambda x: x.get("duration", 0))
    try:
        idx = int(update.message.text) - 1; v = vs[idx]
        await context.bot.send_video(ADMIN_ID, v["file_id"], caption=f"🎬 סרטון #{idx+1}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 מחק", callback_data=f"vid_del_{idx}")]]))
    except: await update.message.reply_text("❌ לא נמצא.")
    return ConversationHandler.END

async def admin_broadcast_start(update, context):
    q = update.callback_query; await q.answer(); await q.edit_message_text("📢 שלח הודעה:"); return ADMIN_BROADCAST

async def admin_broadcast_get_msg(update, context):
    context.user_data["bm"] = update.message.text; await update.message.reply_text("🖼 שלח מדיה או `skip`:"); return ADMIN_BROADCAST_MEDIA

async def admin_broadcast_get_media(update, context):
    if update.message.photo: context.user_data["bmed"] = ("photo", update.message.photo[-1].file_id)
    elif update.message.video: context.user_data["bmed"] = ("video", update.message.video.file_id)
    else: context.user_data["bmed"] = None
    await update.message.reply_text("🔗 שלח `טקסט|קישור` או `skip`:"); return ADMIN_BROADCAST_BTN

async def admin_broadcast_get_btn(update, context):
    raw = update.message.text; markup = None
    if raw.lower() != "skip" and "|" in raw:
        t, u = raw.split("|", 1); markup = InlineKeyboardMarkup([[InlineKeyboardButton(t.strip(), url=u.strip())]])
    context.user_data["bmk"] = markup; await update.message.reply_text("⏰ השהייה בדקות:"); return ADMIN_BROADCAST_DELAY

async def admin_broadcast_get_delay(update, context):
    d = int(update.message.text); if d > 0: await asyncio.sleep(d*60)
    msg = context.user_data["bm"]; med = context.user_data["bmed"]; mk = context.user_data["bmk"]; users = load_json(USERS_FILE); sent = 0
    for uid in users:
        try:
            if med:
                if med[0] == "photo": await context.bot.send_photo(int(uid), med[1], caption=msg, reply_markup=mk)
                else: await context.bot.send_video(int(uid), med[1], caption=msg, reply_markup=mk)
            else: await context.bot.send_message(int(uid), msg, reply_markup=mk)
            sent += 1; await asyncio.sleep(0.05)
        except: pass
    await update.message.reply_text(f"✅ נשלח ל-{sent}!", reply_markup=get_admin_inline_keyboard()); return ConversationHandler.END

async def admin_coins_start(update, context):
    q = update.callback_query; await q.answer(); await q.edit_message_text("🪙 שלח ID:"); return ADMIN_COINS_ID

async def admin_coins_id(update, context):
    context.user_data["cuid"] = update.message.text.strip(); await update.message.reply_text("כמה להוסיף/להוריד?"); return ADMIN_COINS_AMOUNT

async def admin_coins_amount(update, context):
    uid = context.user_data["cuid"]; cs = load_json(COINS_FILE); cs[uid] = max(0, cs.get(uid, 0) + int(update.message.text)); save_json(COINS_FILE, cs)
    await update.message.reply_text(f"✅ יתרה: {cs[uid]}", reply_markup=get_admin_inline_keyboard()); return ConversationHandler.END

async def admin_multiplier_start(update, context):
    q = update.callback_query; await q.answer(); s = load_settings(); cur = s.get("referral_multiplier", 1.0)
    await q.edit_message_text(f"💱 מכפיל נוכחי: *{cur}x*\nשלח מכפיל חדש:", parse_mode="Markdown"); return ADMIN_MULTIPLIER

async def admin_multiplier_apply(update, context):
    try:
        nm = float(update.message.text.strip()); s = load_settings(); om = s.get("referral_multiplier", 1.0); r = nm / om; cs = load_json(COINS_FILE)
        for uid in cs: cs[uid] = round(cs[uid] * r)
        save_json(COINS_FILE, cs); s["referral_multiplier"] = nm; save_settings(s); await update.message.reply_text(f"✅ עודכן ל-{nm}x", reply_markup=get_admin_inline_keyboard())
    except: await update.message.reply_text("❌ לא תקין.")
    return ConversationHandler.END

async def admin_welcome_set_start(update, context):
    q = update.callback_query; await q.answer(); await q.edit_message_text("👋 שלח טקסט ברוכים הבאים (השתמש ב-{name}):"); return ADMIN_SET_WELCOME_TEXT

async def admin_welcome_set_text(update, context):
    context.user_data["wt"] = update.message.text; await update.message.reply_text("🖼 שלח מדיה או `skip`:"); return ADMIN_SET_WELCOME_MEDIA

async def admin_welcome_set_media(update, context):
    m = None
    if update.message.photo: m = ("photo", update.message.photo[-1].file_id)
    elif update.message.video: m = ("video", update.message.video.file_id)
    s = load_settings(); s["welcome_text"] = context.user_data["wt"]; s["welcome_media"] = m; save_settings(s)
    await update.message.reply_text("✅ עודכן!", reply_markup=get_admin_inline_keyboard()); return ConversationHandler.END

async def admin_backup_menu(update, context):
    q = update.callback_query; await q.answer()
    await q.edit_message_text("💾 *ניהול גיבויים*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 הורד גיבוי ZIP", callback_data="admin_backup_download")], [InlineKeyboardButton("📤 שחזר מגיבוי", callback_data="admin_restore_start")], [InlineKeyboardButton("🔙 חזרה", callback_data="back_admin")]]))

async def admin_backup_download(update, context):
    q = update.callback_query; await q.answer(); buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in DATA_DIR.glob("*.json"): zf.write(f, f.name)
    buf.seek(0); await context.bot.send_document(ADMIN_ID, buf, filename="backup.zip"); await q.answer("✅ נשלח!")

async def admin_restore_start(update, context):
    q = update.callback_query; await q.answer(); await q.edit_message_text("📤 שלח קובץ ZIP לשחזור:"); return ADMIN_RESTORE

async def admin_restore_receive(update, context):
    doc = update.message.document; f = await context.bot.get_file(doc.file_id); buf = io.BytesIO(); await f.download_to_memory(buf); buf.seek(0)
    with zipfile.ZipFile(buf, "r") as zf:
        for n in zf.namelist():
            if n.endswith(".json"): save_json(DATA_DIR / n, json.loads(zf.read(n)))
    await update.message.reply_text("✅ שוחזר!", reply_markup=get_admin_inline_keyboard()); return ConversationHandler.END

async def admin_global_reset_start(update, context):
    q = update.callback_query; await q.answer(); await q.edit_message_text("🔄 בטוח? הקלד 'מאשר':"); return ADMIN_GLOBAL_RESET_CONFIRM

async def admin_global_reset_execute(update, context):
    if update.message.text == "מאשר":
        for f in DATA_DIR.glob("*.json"):
            if f.name != "settings.json": save_json(f, [] if "videos" in f.name or "orders" in f.name else {})
        await update.message.reply_text("✅ אופס!", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END

async def admin_delete_start(update, context):
    q = update.callback_query; await q.answer(); await q.edit_message_text("🧹 מחק את כל הסרטונים?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("כן", callback_data="admin_delete_confirm"), InlineKeyboardButton("לא", callback_data="back_admin")]]))

async def admin_delete_confirm(update, context):
    save_json(VIDEOS_FILE, []); await update.callback_query.answer("✅ נמחק!"); await back_admin(update, context)

async def admin_maintenance_toggle(update, context):
    s = load_settings(); s["maintenance"] = not s["maintenance"]; save_settings(s)
    await update.callback_query.answer(f"תחזוקה: {'פעיל' if s['maintenance'] else 'כבוי'}"); await back_admin(update, context)

async def handle_video(update, context):
    if update.effective_user.id != ADMIN_ID: return
    v = update.message.video; context.user_data["vf"] = v.file_id; context.user_data["vd"] = v.duration
    await update.message.reply_text("🎬 סרטון התקבל! שלח תמונה/סרטון דוגמה או `skip`:"); return ADMIN_VIDEO_PREVIEW

async def admin_video_preview_receive(update, context):
    p = None
    if update.message.photo: p = ("photo", update.message.photo[-1].file_id)
    elif update.message.video: p = ("video", update.message.video.file_id)
    vs = load_json(VIDEOS_FILE); vs.append({"file_id": context.user_data["vf"], "duration": context.user_data["vd"], "preview": p}); save_json(VIDEOS_FILE, vs)
    await update.message.reply_text("✅ נשמר!"); return ConversationHandler.END

async def maintenance_gate(update):
    if update.effective_user and update.effective_user.id == ADMIN_ID: return False
    if not is_maintenance(): return False
    if update.callback_query: await update.callback_query.answer("תחזוקה!", show_alert=True)
    else: await update.message.reply_text("🔧 הבוט בתחזוקה.")
    return True

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, format, *args): pass

def _start_health_server():
    try: HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), _HealthHandler).serve_forever()
    except: pass

async def _run_app(app):
    await app.initialize(); await app.start(); await app.updater.start_polling()
    while True: await asyncio.sleep(3600)

def main():
    ensure_data_files(); threading.Thread(target=_start_health_server, daemon=True).start(); app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start)); app.add_handler(MessageHandler(filters.Regex("^🛠 פאנל אדמין$"), admin_panel))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_check_start, pattern="^admin_check$")], states={ADMIN_CHECK_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_check_user)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_send_start, pattern="^admin_send$")], states={ADMIN_SEND_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_msg)], ADMIN_SEND_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_id)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_approve_start, pattern="^admin_approve$")], states={ADMIN_APPROVE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_approve_count)], ADMIN_APPROVE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_approve_id)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast$")], states={ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_get_msg)], ADMIN_BROADCAST_MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO | filters.Regex("^skip$"), admin_broadcast_get_media)], ADMIN_BROADCAST_BTN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_get_btn)], ADMIN_BROADCAST_DELAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_get_delay)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_coins_start, pattern="^admin_coins$")], states={ADMIN_COINS_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coins_id)], ADMIN_COINS_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coins_amount)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_multiplier_start, pattern="^admin_multiplier$")], states={ADMIN_MULTIPLIER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_multiplier_apply)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_welcome_set_start, pattern="^admin_welcome_set$")], states={ADMIN_SET_WELCOME_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_welcome_set_text)], ADMIN_SET_WELCOME_MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO | filters.Regex("^skip$"), admin_welcome_set_media)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_restore_start, pattern="^admin_restore_start$")], states={ADMIN_RESTORE: [MessageHandler(filters.Document.ALL, admin_restore_receive)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_global_reset_start, pattern="^admin_global_reset$")], states={ADMIN_GLOBAL_RESET_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_global_reset_execute)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_video_search_start, pattern="^admin_video_search$")], states={ADMIN_VIDEO_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_video_search_input)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(support_menu, pattern="^support$")], states={SUPPORT_WAITING_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive_msg)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(coupon_redeem_start, pattern="^coupon_redeem$")], states={COUPON_REDEEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, coupon_redeem_input)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_support_reply_start, pattern=r"^support_reply_\d+$")], states={SUPPORT_REPLY_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_support_reply_send)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.VIDEO, handle_video)], states={ADMIN_VIDEO_PREVIEW: [MessageHandler(filters.PHOTO | filters.VIDEO | filters.Regex("^skip$"), admin_video_preview_receive)]}, fallbacks=[]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(admin_coupon_new_start, pattern="^admin_coupon_new$")], states={ADMIN_COUPON_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coupon_get_code)], ADMIN_COUPON_COINS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coupon_get_coins)]}, fallbacks=[]))
    cbs = [
        ("^noop$", noop_callback), ("^payment_method$", payment_method_menu), ("^paypal_menu$", paypal_menu), (r"^pp_\d+$", paypal_package_selected), ("^coins_menu$", coins_menu), (r"^coin_\d+$", coin_package_buy), ("^referrals$", referrals_menu), ("^wallet$", wallet_menu), ("^daily_bonus$", daily_bonus), ("^vip_info$", vip_info), ("^back_main$", back_main), ("^admin_stats$", admin_stats), (r"^admin_orders_page_\d+$", admin_orders_page), (r"^users_page_\d+$", users_page), ("^admin_gallery$", admin_gallery), (r"^vid_page_\d+$", admin_gallery_page), (r"^vid_del_\d+$", admin_gallery_delete), ("^vid_send_all$", admin_gallery_send_all), ("^admin_coupons$", admin_coupons_menu), (r"^coupon_del_", admin_coupon_delete), ("^admin_backup_menu$", admin_backup_menu), ("^admin_backup_download$", admin_backup_download), ("^admin_delete$", admin_delete_start), ("^admin_delete_confirm$", admin_delete_confirm), ("^admin_maintenance$", admin_maintenance_toggle), ("^back_admin$", back_admin), ("^admin_multiplier$", admin_multiplier_start)
    ]
    for p, h in cbs: app.add_handler(CallbackQueryHandler(h, pattern=p))
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop); loop.create_task(_run_app(app)); loop.run_forever()

if __name__ == "__main__": main()
