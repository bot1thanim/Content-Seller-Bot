import os
import json
import random
import logging
from datetime import datetime, date
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

PACKAGES = [
    {"price": 2, "videos": 1, "label": "₪2 – 1 סרטון"},
    {"price": 9, "videos": 5, "label": "₪9 – 5 סרטונים"},
    {"price": 16, "videos": 10, "label": "₪16 – 10 סרטונים"},
    {"price": 30, "videos": 20, "label": "₪30 – 20 סרטונים"},
    {"price": 65, "videos": 50, "label": "₪65 – 50 סרטונים"},
    {"price": 85, "videos": 70, "label": "₪85 – 70 סרטונים"},
    {"price": 110, "videos": 100, "label": "₪110 – 100 סרטונים"},
    {"price": 180, "videos": 200, "label": "₪180 – 200 סרטונים"},
]

(
    ADMIN_SEND_MSG,
    ADMIN_SEND_ID,
    ADMIN_APPROVE_COUNT,
    ADMIN_APPROVE_ID,
    ADMIN_CHECK_USER,
    ADMIN_COINS_ID,
    ADMIN_COINS_AMOUNT,
    ADMIN_BROADCAST,
    ADMIN_DELETE_CONFIRM,
) = range(9)


def ensure_data_files():
    DATA_DIR.mkdir(exist_ok=True)
    for filepath, default in [
        (USERS_FILE, {}),
        (COINS_FILE, {}),
        (REFERRALS_FILE, {}),
        (VIDEOS_FILE, []),
        (ORDERS_FILE, []),
    ]:
        if not filepath.exists():
            save_json(filepath, default)


def load_json(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {} if "videos" not in str(filepath) and "orders" not in str(filepath) else []


def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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


def get_main_keyboard(user_id):
    buttons = [
        [
            InlineKeyboardButton("💳 תשלום בפייפאל", callback_data="payment"),
            InlineKeyboardButton("👥 הפניות שלי", callback_data="referrals"),
        ],
        [
            InlineKeyboardButton("💰 ארנק מטבעות", callback_data="wallet"),
            InlineKeyboardButton("💬 תמיכה", callback_data="support"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def get_admin_reply_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🛠 פאנל אדמין")]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def get_admin_inline_keyboard():
    buttons = [
        [
            InlineKeyboardButton("📊 סטטיסטיקה", callback_data="admin_stats"),
            InlineKeyboardButton("🧾 הזמנות", callback_data="admin_orders"),
        ],
        [
            InlineKeyboardButton("🔍 בדוק משתמש", callback_data="admin_check"),
            InlineKeyboardButton("📩 שלח למשתמש", callback_data="admin_send"),
        ],
        [
            InlineKeyboardButton("✅ אישור תשלום", callback_data="admin_approve"),
            InlineKeyboardButton("📜 סרטונים", callback_data="admin_videos"),
        ],
        [
            InlineKeyboardButton("📢 הודעה לכולם", callback_data="admin_broadcast"),
            InlineKeyboardButton("🪙 ניהול מטבעות", callback_data="admin_coins"),
        ],
        [
            InlineKeyboardButton("💾 גיבוי", callback_data="admin_backup"),
            InlineKeyboardButton("🧹 מחק הכל", callback_data="admin_delete"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    ref_id = None

    if args and args[0].startswith("ref_"):
        try:
            ref_id = int(args[0].split("ref_")[1])
        except ValueError:
            pass

    register_user(user, ref_id)

    text = f"שלום {user.first_name} 👋\nברוכים הבאים לבוט התכנים האסורים 🤫\nבחר אפשרות:"

    if user.id == ADMIN_ID:
        await update.message.reply_text(
            "👋 ברוך הבא אדמין!",
            reply_markup=get_admin_reply_keyboard(),
        )

    await update.message.reply_text(text, reply_markup=get_main_keyboard(user.id))


async def payment_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    buttons = []
    for i, pkg in enumerate(PACKAGES):
        buttons.append([InlineKeyboardButton(pkg["label"], callback_data=f"pkg_{i}")])
    buttons.append([InlineKeyboardButton("🔙 חזרה", callback_data="back_main")])

    await query.edit_message_text(
        "💳 בחר חבילה:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def package_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    idx = int(query.data.split("pkg_")[1])
    pkg = PACKAGES[idx]
    user_id = query.from_user.id

    paypal_url = f"{PAYPAL_LINK}/{pkg['price']}ILS"
    text = (
        f"📦 *חבילה נבחרת:* {pkg['label']}\n\n"
        f"🔗 [לחץ כאן לתשלום בפייפאל]({paypal_url})\n\n"
        f"לאחר השלמת התשלום:\n"
        f"שלח את התשלום וציין את ה-ID שלך: `{user_id}`\n"
        f"לאחר מכן שלח צילום מסך לתמיכה ✅"
    )

    buttons = [[InlineKeyboardButton("🔙 חזרה לחבילות", callback_data="payment")]]
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
        disable_web_page_preview=False,
    )


async def referrals_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    bot = context.bot
    bot_info = await bot.get_me()
    bot_username = bot_info.username

    referrals = load_json(REFERRALS_FILE)
    coins = load_json(COINS_FILE)

    ref_data = referrals.get(str(user.id), {"count": 0})
    ref_count = ref_data.get("count", 0)
    ref_coins = coins.get(str(user.id), 0)

    ref_link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    text = (
        f"👥 *הפניות שלי*\n\n"
        f"🔗 הקישור שלך:\n`{ref_link}`\n\n"
        f"👤 אנשים שנרשמו: *{ref_count}*\n"
        f"🪙 מטבעות שצברת: *{ref_coins}*\n\n"
        f"שתף את הקישור - כל הצטרפות מזכה אותך במטבע! 🎉"
    )

    buttons = [[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    coins = load_json(COINS_FILE)
    balance = coins.get(str(user.id), 0)
    shekel_value = balance / 10

    text = (
        f"💰 *הארנק שלי*\n\n"
        f"🪙 יתרת מטבעות: *{balance}*\n"
        f"💵 שווי בשקלים: *₪{shekel_value:.1f}*\n\n"
        f"_10 מטבעות = ₪1_"
    )

    buttons = [[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = (
        "💬 *תמיכה*\n\n"
        "לכל שאלה או שליחת צילום מסך של תשלום,\n"
        "פנה למנהל ויחזרו אליך בהקדם האפשרי. 🙏"
    )

    buttons = [[InlineKeyboardButton("🔙 חזרה", callback_data="back_main")]]
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    text = f"שלום {user.first_name} 👋\nברוכים הבאים לבוט התכנים האסורים 🤫\nבחר אפשרות:"
    await query.edit_message_text(text, reply_markup=get_main_keyboard(user.id))


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return

    await update.message.reply_text(
        "🛠 *פאנל אדמין*\nבחר פעולה:",
        parse_mode="Markdown",
        reply_markup=get_admin_inline_keyboard(),
    )


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    users = load_json(USERS_FILE)
    orders = load_json(ORDERS_FILE)
    videos = load_json(VIDEOS_FILE)

    today = str(date.today())
    new_today = sum(1 for u in users.values() if u.get("joined") == today)
    total_revenue = sum(o.get("amount", 0) for o in orders)

    text = (
        f"📊 *סטטיסטיקה*\n\n"
        f"👤 סה\"כ משתמשים: *{len(users)}*\n"
        f"🆕 משתמשים חדשים היום: *{new_today}*\n"
        f"💰 הכנסות כוללות: *₪{total_revenue}*\n"
        f"🎬 סרטונים במאגר: *{len(videos)}*"
    )

    buttons = [[InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")]]
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    orders = load_json(ORDERS_FILE)
    last_10 = orders[-10:] if len(orders) > 10 else orders

    if not last_10:
        text = "🧾 *הזמנות*\n\nאין הזמנות עדיין."
    else:
        lines = ["🧾 *10 הזמנות אחרונות:*\n"]
        for o in reversed(last_10):
            lines.append(
                f"👤 ID: `{o.get('user_id')}` | 💰 ₪{o.get('amount')} | 📅 {o.get('date')} | 🎬 {o.get('videos_count')} סרטונים"
            )
        text = "\n".join(lines)

    buttons = [[InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")]]
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_check_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    await query.edit_message_text(
        "🔍 *בדיקת משתמש*\n\nשלח את ה-ID של המשתמש:",
        parse_mode="Markdown",
    )
    return ADMIN_CHECK_USER


async def admin_check_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    try:
        target_id = str(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ ID לא תקין.", reply_markup=get_admin_inline_keyboard())
        return ConversationHandler.END

    users = load_json(USERS_FILE)
    coins = load_json(COINS_FILE)
    referrals = load_json(REFERRALS_FILE)
    orders = load_json(ORDERS_FILE)

    user_data = users.get(target_id)
    if not user_data:
        await update.message.reply_text(
            "❌ משתמש לא נמצא.",
            reply_markup=get_admin_inline_keyboard(),
        )
        return ConversationHandler.END

    balance = coins.get(target_id, 0)
    ref_data = referrals.get(target_id, {"count": 0})
    user_orders = [o for o in orders if str(o.get("user_id")) == target_id]

    text = (
        f"🔍 *דוח משתמש*\n\n"
        f"👤 שם: {user_data.get('first_name')}\n"
        f"🆔 ID: `{target_id}`\n"
        f"📅 הצטרף: {user_data.get('joined')}\n"
        f"🪙 מטבעות: {balance}\n"
        f"👥 הפניות: {ref_data.get('count', 0)}\n"
        f"🛒 רכישות: {len(user_orders)}\n"
        f"💰 סה\"כ הוציא: ₪{sum(o.get('amount', 0) for o in user_orders)}"
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_admin_inline_keyboard(),
    )
    return ConversationHandler.END


async def admin_send_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    context.user_data["admin_action"] = "send"
    await query.edit_message_text("📩 *שליחה למשתמש*\n\nשלח את ההודעה שברצונך לשלוח:", parse_mode="Markdown")
    return ADMIN_SEND_MSG


async def admin_send_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    context.user_data["msg_to_send"] = update.message.text
    await update.message.reply_text("📲 עכשיו שלח את ה-ID של המשתמש:")
    return ADMIN_SEND_ID


async def admin_send_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    try:
        target_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ ID לא תקין.", reply_markup=get_admin_inline_keyboard())
        return ConversationHandler.END

    msg = context.user_data.get("msg_to_send", "")
    try:
        await context.bot.send_message(chat_id=target_id, text=msg)
        await update.message.reply_text("✅ ההודעה נשלחה בהצלחה!", reply_markup=get_admin_inline_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה בשליחה: {e}", reply_markup=get_admin_inline_keyboard())

    return ConversationHandler.END


async def admin_approve_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    await query.edit_message_text("✅ *אישור תשלום*\n\nכמה סרטונים לשלוח?", parse_mode="Markdown")
    return ADMIN_APPROVE_COUNT


async def admin_approve_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    try:
        count = int(update.message.text.strip())
        context.user_data["approve_count"] = count
    except ValueError:
        await update.message.reply_text("❌ מספר לא תקין.")
        return ADMIN_APPROVE_COUNT

    await update.message.reply_text(f"📲 מה ה-ID של המשתמש שיקבל {count} סרטונים?")
    return ADMIN_APPROVE_ID


async def admin_approve_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    try:
        target_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ ID לא תקין.")
        return ConversationHandler.END

    count = context.user_data.get("approve_count", 0)
    videos = load_json(VIDEOS_FILE)

    if len(videos) < count:
        await update.message.reply_text(
            f"❌ אין מספיק סרטונים במאגר! יש {len(videos)} סרטונים.",
            reply_markup=get_admin_inline_keyboard(),
        )
        return ConversationHandler.END

    selected = random.sample(videos, count)

    await update.message.reply_text(
        f"📤 שולח {count} סרטונים למשתמש `{target_id}`...",
        parse_mode="Markdown",
    )

    sent = 0
    for video_file_id in selected:
        try:
            await context.bot.send_video(chat_id=target_id, video=video_file_id)
            sent += 1
        except Exception:
            pass

    orders = load_json(ORDERS_FILE)
    orders.append({
        "user_id": target_id,
        "amount": 0,
        "videos_count": sent,
        "date": str(date.today()),
        "type": "manual",
    })
    save_json(ORDERS_FILE, orders)

    users = load_json(USERS_FILE)
    uid = str(target_id)
    if uid in users:
        users[uid]["purchases"] = users[uid].get("purchases", 0) + 1
        save_json(USERS_FILE, users)

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"🎉 תשלומך אושר! קיבלת {sent} סרטונים. תהנה! 🔥",
        )
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ נשלחו {sent} סרטונים בהצלחה!",
        reply_markup=get_admin_inline_keyboard(),
    )
    return ConversationHandler.END


async def admin_videos_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    videos = load_json(VIDEOS_FILE)

    if not videos:
        text = "📜 *סרטונים במאגר*\n\nאין סרטונים עדיין.\nשלח סרטון לבוט כדי להוסיף."
    else:
        lines = [f"📜 *סרטונים במאגר ({len(videos)} סה\"כ):*\n"]
        for i, v in enumerate(videos, 1):
            short_id = v[:20] + "..." if len(v) > 20 else v
            lines.append(f"{i}. `{short_id}`")
        text = "\n".join(lines[:51])

    buttons = [[InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")]]
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    await query.edit_message_text(
        "📢 *הודעה לכולם*\n\nשלח את ההודעה שברצונך לשלוח לכל המשתמשים:",
        parse_mode="Markdown",
    )
    return ADMIN_BROADCAST


async def admin_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    msg = update.message.text
    users = load_json(USERS_FILE)
    sent = 0
    failed = 0

    await update.message.reply_text(f"📤 שולח הודעה ל-{len(users)} משתמשים...")

    for uid in users:
        try:
            await context.bot.send_message(chat_id=int(uid), text=msg)
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"✅ ההודעה נשלחה!\n✔️ הצליח: {sent}\n❌ נכשל: {failed}",
        reply_markup=get_admin_inline_keyboard(),
    )
    return ConversationHandler.END


async def admin_coins_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    await query.edit_message_text(
        "🪙 *ניהול מטבעות*\n\nשלח את ה-ID של המשתמש:",
        parse_mode="Markdown",
    )
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

    users = load_json(USERS_FILE)
    coins = load_json(COINS_FILE)
    current = coins.get(uid, 0)
    user_name = users.get(uid, {}).get("first_name", "לא ידוע")

    await update.message.reply_text(
        f"👤 משתמש: {user_name}\n🪙 יתרה נוכחית: {current}\n\nשלח כמות (חיובי להוספה, שלילי להורדה):"
    )
    return ADMIN_COINS_AMOUNT


async def admin_coins_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    try:
        amount = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ כמות לא תקינה.")
        return ConversationHandler.END

    uid = context.user_data.get("coins_target_id")
    coins = load_json(COINS_FILE)
    current = coins.get(uid, 0)
    new_balance = max(0, current + amount)
    coins[uid] = new_balance
    save_json(COINS_FILE, coins)

    action = "נוספו ➕" if amount >= 0 else "הוסרו ➖"
    await update.message.reply_text(
        f"✅ עודכן!\n🪙 {abs(amount)} מטבעות {action}\n💰 יתרה חדשה: {new_balance}",
        reply_markup=get_admin_inline_keyboard(),
    )
    return ConversationHandler.END


async def admin_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    await query.edit_message_text("💾 שולח גיבוי...")

    for filepath in [USERS_FILE, COINS_FILE, REFERRALS_FILE, VIDEOS_FILE, ORDERS_FILE]:
        if filepath.exists():
            with open(filepath, "rb") as f:
                await context.bot.send_document(
                    chat_id=ADMIN_ID,
                    document=f,
                    filename=filepath.name,
                    caption=f"📁 {filepath.name}",
                )

    await context.bot.send_message(chat_id=ADMIN_ID, text="✅ הגיבוי הושלם!", reply_markup=get_admin_inline_keyboard())


async def admin_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    videos = load_json(VIDEOS_FILE)
    buttons = [
        [
            InlineKeyboardButton("✅ כן, מחק הכל", callback_data="admin_delete_confirm"),
            InlineKeyboardButton("❌ ביטול", callback_data="back_admin"),
        ]
    ]
    await query.edit_message_text(
        f"🧹 *מחיקת כל הסרטונים*\n\nיש {len(videos)} סרטונים במאגר.\nהאם אתה בטוח שברצונך למחוק הכל?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    save_json(VIDEOS_FILE, [])
    await query.edit_message_text(
        "✅ כל הסרטונים נמחקו בהצלחה!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לפאנל", callback_data="back_admin")]]),
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


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return

    video = update.message.video
    if not video:
        return

    file_id = video.file_id
    videos = load_json(VIDEOS_FILE)

    if file_id not in videos:
        videos.append(file_id)
        save_json(VIDEOS_FILE, videos)
        await update.message.reply_text(
            f"✅ הסרטון נשמר במאגר!\n📦 סה\"כ סרטונים: {len(videos)}"
        )
    else:
        await update.message.reply_text("ℹ️ סרטון זה כבר קיים במאגר.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ הפעולה בוטלה.", reply_markup=get_admin_inline_keyboard())
    return ConversationHandler.END


def main():
    ensure_data_files()

    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN לא הוגדר!")
        return

    app = Application.builder().token(TOKEN).build()

    check_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_check_start, pattern="^admin_check$")],
        states={ADMIN_CHECK_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_check_user)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
        per_chat=True,
    )

    send_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_send_start, pattern="^admin_send$")],
        states={
            ADMIN_SEND_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_msg)],
            ADMIN_SEND_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
        per_chat=True,
    )

    approve_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_approve_start, pattern="^admin_approve$")],
        states={
            ADMIN_APPROVE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_approve_count)],
            ADMIN_APPROVE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_approve_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
        per_chat=True,
    )

    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast$")],
        states={ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_send)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
        per_chat=True,
    )

    coins_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_coins_start, pattern="^admin_coins$")],
        states={
            ADMIN_COINS_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coins_id)],
            ADMIN_COINS_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_coins_amount)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
        per_chat=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🛠 פאנל אדמין$"), admin_panel))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))

    app.add_handler(check_conv)
    app.add_handler(send_conv)
    app.add_handler(approve_conv)
    app.add_handler(broadcast_conv)
    app.add_handler(coins_conv)

    app.add_handler(CallbackQueryHandler(payment_menu, pattern="^payment$"))
    app.add_handler(CallbackQueryHandler(package_selected, pattern=r"^pkg_\d+$"))
    app.add_handler(CallbackQueryHandler(referrals_menu, pattern="^referrals$"))
    app.add_handler(CallbackQueryHandler(wallet_menu, pattern="^wallet$"))
    app.add_handler(CallbackQueryHandler(support_menu, pattern="^support$"))
    app.add_handler(CallbackQueryHandler(back_main, pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
    app.add_handler(CallbackQueryHandler(admin_orders, pattern="^admin_orders$"))
    app.add_handler(CallbackQueryHandler(admin_videos_list, pattern="^admin_videos$"))
    app.add_handler(CallbackQueryHandler(admin_backup, pattern="^admin_backup$"))
    app.add_handler(CallbackQueryHandler(admin_delete_start, pattern="^admin_delete$"))
    app.add_handler(CallbackQueryHandler(admin_delete_confirm, pattern="^admin_delete_confirm$"))
    app.add_handler(CallbackQueryHandler(back_admin, pattern="^back_admin$"))

    logger.info("הבוט מופעל... 🚀")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
