import asyncio
import importlib.util
import io
import json
import os
import zipfile
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("bot_under_test", ROOT / "bot.py")
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)


class Msg:
    counter = 100
    def __init__(self):
        Msg.counter += 1
        self.message_id = Msg.counter


class FakeBot:
    def __init__(self):
        self.sent_videos = []
        self.sent_texts = []
        self.sent_photos = []
        self.sent_documents = []
        self.deleted = []
        self.chat_actions = []
    async def send_video(self, chat_id, video, reply_markup=None):
        self.sent_videos.append((chat_id, video, reply_markup))
        return Msg()
    async def send_message(self, chat_id, text, **kwargs):
        self.sent_texts.append((chat_id, text, kwargs))
        return Msg()
    async def send_photo(self, chat_id, photo, caption=None, **kwargs):
        self.sent_photos.append((chat_id, photo, caption, kwargs))
        return Msg()
    async def send_document(self, chat_id, document, filename=None, caption=None, **kwargs):
        self.sent_documents.append((chat_id, document.getvalue(), filename, caption, kwargs))
        return Msg()
    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
    async def send_chat_action(self, chat_id, action):
        self.chat_actions.append((chat_id, action))


class FailingDocumentBot(FakeBot):
    async def send_document(self, chat_id, document, filename=None, caption=None, **kwargs):
        raise RuntimeError("document delivery failed")


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.caption = None
        self.photo = None
        self.video = None
        self.replies = []
    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return Msg()


class FakeQuery:
    def __init__(self, data, user_id):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(chat_id=user_id)
        self.edits = []
        self.answers = []
    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))
        return None
    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))
    async def edit_message_reply_markup(self, **kwargs):
        self.edits.append(("markup", kwargs))


class FakeUpdate:
    def __init__(self, data, user_id):
        self.callback_query = FakeQuery(data, user_id)
        self.effective_user = SimpleNamespace(id=user_id)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def button_callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def button_rows(markup):
    return [[button.callback_data for button in row] for row in markup.inline_keyboard]


async def gate_allows(callback_data, user_id):
    update = FakeUpdate(callback_data, user_id)
    try:
        await bot.admin_callback_gate(update, SimpleNamespace())
        return True
    except bot.ApplicationHandlerStop:
        return False


async def run():
    with tempfile.TemporaryDirectory() as td:
        data = Path(td) / "data"
        data.mkdir()
        bot.DATA_DIR = data
        bot.USERS_FILE = data / "users.json"
        bot.COINS_FILE = data / "coins.json"
        bot.REFERRALS_FILE = data / "referrals.json"
        bot.VIDEOS_FILE = data / "videos.json"
        bot.ORDERS_FILE = data / "orders.json"
        bot.COUPONS_FILE = data / "coupons.json"
        bot.SETTINGS_FILE = data / "settings.json"
        bot.TRASH_FILE = data / "trash.json"
        bot.ADMIN_ACTIONS_FILE = data / "admin_actions.json"
        bot.COIN_TRANSACTIONS_FILE = data / "coin_transactions.json"
        bot.AI_AUDIT_FILE = data / "ai_audit.json"
        bot.USER_ACTIVITY_FILE = data / "user_activity.json"
        bot.ALERTS_FILE = data / "alerts.json"
        bot.DUPLICATE_REVIEWS_FILE = data / "duplicate_reviews.json"
        bot.AUTO_BACKUPS_DIR = data / "auto_backups"
        owner = bot.ADMIN_ID
        bot.ensure_data_files()
        write(bot.SETTINGS_FILE, {"categories": ["כללי", "ישראלי", "קצר"], "admin_managers": {}})
        bot.log_user_activity(owner, "daily_gift", {"amount": 2, "amount_before": 14, "amount_after": 16, "reason": "daily_gift"})
        write(bot.VIDEOS_FILE, [
            {"file_id": "f1", "duration": 10, "entry_id": "a", "category": "ישראלי"},
            {"file_id": "f2", "duration": 10, "entry_id": "b", "category": "ישראלי"},
            {"file_id": "f3", "duration": 20, "entry_id": "c", "category": "קצר"},
            {"file_id": "f4", "duration": 20, "entry_id": "d", "category": "קצר"},
        ])
        assert bot._assistant_live_answer("כמה סרטונים יש במאגר?", owner) == "🎬 כרגע יש *4* סרטונים במאגר."
        assert bot._assistant_live_answer("כמה סרטונים יש במאגר?", 12345) is None

        # The unified activity center exposes every required log family and human-readable user events.
        owner_markup = bot.get_admin_inline_keyboard(owner)
        assert "admin_activity_center" in button_callbacks(owner_markup), "Owner panel must expose unified activity logs"
        center_query = FakeQuery("admin_activity_center", owner)
        await bot.admin_activity_center(FakeUpdate("admin_activity_center", owner), SimpleNamespace())
        center_buttons = button_callbacks(center_query) if hasattr(center_query, "inline_keyboard") else []
        activity_update = FakeUpdate("admin_activity_center", owner)
        await bot.admin_activity_center(activity_update, SimpleNamespace())
        center_markup = activity_update.callback_query.edits[-1][1]["reply_markup"]
        center_callbacks = button_callbacks(center_markup)
        for kind in ("users", "admins", "ai", "coins", "audit", "blocked", "system"):
            assert f"admin_activity_{kind}_0" in center_callbacks, f"Missing unified activity log: {kind}"
        users_activity_update = FakeUpdate("admin_activity_users_0", owner)
        await bot.admin_activity_page(users_activity_update, SimpleNamespace())
        users_activity_text = users_activity_update.callback_query.edits[-1][0]
        assert "פעילות משתמשים" in users_activity_text and "משתמש:" in users_activity_text
        event_update = FakeUpdate("admin_activity_event_users_0_0", owner)
        await bot.admin_activity_event(event_update, SimpleNamespace())
        assert "מתי:" in event_update.callback_query.edits[-1][0] and "קבלת מתנה יומית" in event_update.callback_query.edits[-1][0]
        write(bot.TRASH_FILE, [{"entry_id": "trash-entry", "file_id": "old-file"}])
        write(bot.COUPONS_FILE, {"OLD": {"coins": 1, "expires": "2020-01-01", "used_by": []}})
        bot.save_json(bot.ALERTS_FILE, [{"kind": "test_alert"}])
        report = bot._system_problem_report()
        assert len(report["duplicates"]) == 2 and len(report["coupons"]) == 1 and len(report["trash"]) == 1
        dashboard_metrics = bot._dashboard_metrics()
        assert dashboard_metrics["alerts"] == 1 and dashboard_metrics["problem_counts"]["coupons"] == 1
        problem_center = FakeUpdate("admin_problem_center", owner)
        await bot.admin_problem_center(problem_center, SimpleNamespace())
        problem_buttons = button_callbacks(problem_center.callback_query.edits[-1][1]["reply_markup"])
        assert "admin_problem_show_coupons_0" in problem_buttons
        problems_page = FakeUpdate("admin_problem_show_coupons_0", owner)
        await bot.admin_problem_show(problems_page, SimpleNamespace())
        assert "OLD" in problems_page.callback_query.edits[-1][0]
        parsed_filters, parse_error = bot._parse_combined_video_search("קטגוריה=ישראלי;משך=10-10;מועדף=לא")
        assert parse_error is None and parsed_filters["category"] == "ישראלי" and parsed_filters["duration"] == (10, 10)
        combined_message = FakeMessage("קטגוריה=ישראלי;משך=10-10")
        combined_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=combined_message)
        assert await bot.admin_combined_search_input(combined_update, SimpleNamespace(user_data={})) == bot.ConversationHandler.END
        assert "נמצאו 2 סרטונים" in combined_message.replies[-1][0]
        reward_message = FakeMessage("תעשה את המתנה היומית 3 ואת ההפניות 2")
        reward_context = SimpleNamespace(user_data={})
        previous_reward_key = os.environ.get("GEMINI_API_KEY")
        previous_reward_payload = bot._assistant_gemini_payload
        try:
            os.environ["GEMINI_API_KEY"] = "test-only"
            bot._assistant_gemini_payload = lambda _: {
                "kind": "rewrite", "canonical_text": "SET_REWARDS:3,2", "reply": None
            }
            reward_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=reward_message)
            assert await bot.admin_assistant_command(reward_update, reward_context) == bot.ADMIN_ASSISTANT_COMMAND
            reward_settings = bot.load_settings()
            assert reward_settings["daily_gift_amount"] == 3
            assert reward_settings["referral_reward_amount"] == 2
            assert "הבוט הבין" in reward_message.replies[-1][0]
            ai_events = bot.load_json(bot.AI_AUDIT_FILE)
            assert ai_events[0]["event"] == "request_received"
            assert any(row["event"] == "ai_action_plan" and row["canonical_text"] == "SET_REWARDS:3,2" for row in ai_events)
            assert any(row["event"] == "tool_execution" and row["status"] == "success" for row in ai_events)
        finally:
            bot._assistant_gemini_payload = previous_reward_payload
            if previous_reward_key is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = previous_reward_key
        broadcast_context = SimpleNamespace(user_data={
            "broadcast_msg": "מבצע *חדש* [בדיקה]",
            "broadcast_media": None,
            "broadcast_markup": None,
        })
        immediate_message = FakeMessage("0")
        immediate_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=immediate_message)
        assert await bot.admin_broadcast_get_delay(immediate_update, broadcast_context) == bot.ADMIN_BROADCAST_PREVIEW
        assert broadcast_context.user_data["broadcast_delay"] == 0
        assert "מיידית ללא המתנה" in immediate_message.replies[-1][0]
        assert "parse_mode" not in immediate_message.replies[-1][1]
        delayed_message = FakeMessage("1")
        delayed_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=delayed_message)
        assert await bot.admin_broadcast_get_delay(delayed_update, broadcast_context) == bot.ADMIN_BROADCAST_PREVIEW
        assert broadcast_context.user_data["broadcast_delay"] == 1
        assert "בעוד 1 דקות" in delayed_message.replies[-1][0]
        invalid_delay_message = FakeMessage("-1")
        invalid_delay_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=invalid_delay_message)
        assert await bot.admin_broadcast_get_delay(invalid_delay_update, broadcast_context) == bot.ADMIN_BROADCAST_DELAY
        assert "מספר לא תקין" in invalid_delay_message.replies[-1][0]
        assert bot.callback_permission("broadcast_confirm_send") == "broadcast"
        assert bot._assistant_parse_delivery_delay("עכשיו") == 0
        assert bot._assistant_parse_delivery_delay("עוד דקה") == 60
        assert bot._assistant_parse_delivery_delay("עוד שעה") == 3600
        assert bot._assistant_parse_delivery_delay("2 שעות") == 7200
        assert bot._assistant_parse_delivery_delay("לא זמן") is None
        write(bot.USERS_FILE, {"77": {"first_name": "Dana", "username": "dana_admin", "joined": "today"}})
        write(bot.COINS_FILE, {"77": 42})
        write(bot.REFERRALS_FILE, {"77": {"count": 3}})
        write(bot.ORDERS_FILE, [{"user_id": "77", "type": "coins"}])
        bot.save_json(bot.COIN_TRANSACTIONS_FILE, [{
            "at": "2026-08-26T12:00:00+00:00", "user_id": "77", "amount_before": 40,
            "change": 2, "amount_after": 42, "reason": "test", "source": "test", "actor_id": owner,
        }])
        coin_rows = [{
            "at": f"2026-08-26T12:{index:02d}:00+00:00", "user_id": "77", "amount_before": index, "change": 1, "amount_after": index + 1, "reason": "daily_gift", "source": "daily_gift", "actor_id": owner, "id": f"coin-event-{index}"
        } for index in range(6)] + [{
            "at": "2026-08-26T12:00:00+00:00", "user_id": "77", "amount_before": 40,
            "change": 2, "amount_after": 42, "reason": "test", "source": "test", "actor_id": owner, "id": "coin-event-original",
        }]
        bot.save_json(bot.COIN_TRANSACTIONS_FILE, coin_rows)
        coins_page = FakeUpdate("admin_activity_coins_0", owner)
        await bot.admin_activity_page(coins_page, SimpleNamespace())
        coins_page_markup = coins_page.callback_query.edits[-1][1]["reply_markup"]
        coins_page_callbacks = button_callbacks(coins_page_markup)
        assert "admin_activity_coins_1" in coins_page_callbacks
        coin_event_callback = "admin_activity_event_coins_0_1"
        coin_event = FakeUpdate(coin_event_callback, owner)
        await bot.admin_activity_event(coin_event, SimpleNamespace())
        coin_event_text, coin_event_kwargs = coin_event.callback_query.edits[-1]
        assert "משתמש שהושפע" in coin_event_text
        assert "יתרה לפני" in coin_event_text and "שינוי" in coin_event_text and "יתרה אחרי" in coin_event_text
        assert "סיבת השינוי: מתנה יומית" in coin_event_text
        assert "מקור הפעולה" in coin_event_text and "מנהל מבצע" in coin_event_text
        assert "מזהה אירוע" in coin_event_text and "הצליחה" in coin_event_text
        assert coin_event_kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "admin_activity_coins_0"
        coins_second_page = FakeUpdate("admin_activity_coins_1", owner)
        await bot.admin_activity_page(coins_second_page, SimpleNamespace())
        second_callbacks = button_callbacks(coins_second_page.callback_query.edits[-1][1]["reply_markup"])
        assert "admin_activity_coins_0" in second_callbacks

        assistant_user_message = FakeMessage("בדוק משתמש 77")
        assistant_user_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=assistant_user_message)
        assert await bot._assistant_apply_runtime_command(assistant_user_update, SimpleNamespace(user_data={}), "GET_USER:77", owner)
        assert "Dana" in assistant_user_message.replies[-1][0] and "42" in assistant_user_message.replies[-1][0]
        assistant_history_message = FakeMessage("תנועות משתמש 77")
        assistant_history_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=assistant_history_message)
        assert await bot._assistant_apply_runtime_command(assistant_history_update, SimpleNamespace(user_data={}), "GET_USER_COIN_HISTORY:77", owner)
        assert "נוספו 2 מטבעות" in assistant_history_message.replies[-1][0]
        tool_context = SimpleNamespace(user_data={"assistant_audit_request": {"request_id": "audit-1", "request": "בדיקות Tools"}}, bot=FakeBot())
        balance_message = FakeMessage("יתרת משתמש 77")
        assert await bot._assistant_apply_runtime_command(SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=balance_message), tool_context, "GET_USER_BALANCE:77", owner)
        assert "42" in balance_message.replies[-1][0]
        orders_message = FakeMessage("הזמנות משתמש 77")
        assert await bot._assistant_apply_runtime_command(SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=orders_message), tool_context, "GET_USER_ORDERS:77", owner)
        assert "הזמנות" in orders_message.replies[-1][0]
        system_message = FakeMessage("מצב מערכת")
        assert await bot._assistant_apply_runtime_command(SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=system_message), tool_context, "GET_SYSTEM_STATUS", owner)
        assert "מצב מערכת" in system_message.replies[-1][0]
        problems_message = FakeMessage("בעיות")
        assert await bot._assistant_apply_runtime_command(SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=problems_message), tool_context, "GET_PROBLEMS", owner)
        assert "מרכז בעיות" in problems_message.replies[-1][0]
        send_message = FakeMessage("שלח הודעה")
        assert await bot._assistant_apply_runtime_command(SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=send_message), tool_context, "SEND_USER_MESSAGE:77:hello", owner)
        assert tool_context.user_data["assistant_pending_action"]["name"] == "send_user_message"
        send_confirmation = FakeQuery("assistant_confirm_action", owner)
        await bot.assistant_confirm_action(SimpleNamespace(callback_query=send_confirmation), tool_context)
        assert tool_context.bot.sent_texts[-1][:2] == (77, "hello")
        bot.AUTO_BACKUPS_DIR.mkdir(exist_ok=True)
        snapshot_path = bot.AUTO_BACKUPS_DIR / "auto_20260827T120000Z_global_reset.zip"
        with zipfile.ZipFile(snapshot_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("users.json", json.dumps({"77": {"first_name": "Dana"}}, ensure_ascii=False))
            archive.writestr("coins.json", json.dumps({"77": 42}, ensure_ascii=False))
            archive.writestr("orders.json", json.dumps([{ "user_id": "77", "type": "coins"}], ensure_ascii=False))
            archive.writestr("coin_transactions.json", json.dumps([{
                "at": "2026-08-26T12:00:00+00:00", "user_id": "77", "amount_before": 40,
                "change": 2, "amount_after": 42, "reason": "daily_gift", "source": "system_daily_gift",
            }], ensure_ascii=False))
        write(bot.USERS_FILE, {})
        write(bot.COINS_FILE, {})
        write(bot.ORDERS_FILE, [])
        bot.save_json(bot.COIN_TRANSACTIONS_FILE, [])
        historical_message = FakeMessage("האם היו למשתמש 77 מטבעות לפני האיפוס?")
        historical_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=historical_message)
        assert await bot._assistant_apply_runtime_command(historical_update, tool_context, "GET_USER_HISTORY:77", owner)
        historical_text = historical_message.replies[-1][0]
        assert "אינו רשום כרגע" in historical_text and "יתרה שנשמרה: 42" in historical_text
        assert "מתנה יומית" in historical_text
        missing_history_message = FakeMessage("מה היה למשתמש 99?")
        assert await bot._assistant_apply_runtime_command(
            SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=missing_history_message),
            tool_context, "GET_USER_HISTORY:99", owner,
        )
        assert "לא נמצא" in missing_history_message.replies[-1][0]
        write(bot.USERS_FILE, {
            "77": {"first_name": "Dana", "username": "dana_admin", "joined": "today", "language": "he"},
            "7706183809": {"first_name": "Owner", "username": "owner", "joined": "today", "language": "en"},
        })
        assistant_broadcast_message = FakeMessage("שלח הודעה לכולם")
        assistant_broadcast_context = SimpleNamespace(user_data={})
        assistant_broadcast_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=assistant_broadcast_message)
        assert await bot.admin_assistant_command(assistant_broadcast_update, assistant_broadcast_context) == bot.ADMIN_ASSISTANT_DELIVERY_CONTENT
        assert "מה ההודעה" in assistant_broadcast_message.replies[-1][0]
        broadcast_content_message = FakeMessage("מבצע חדש")
        broadcast_content_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=broadcast_content_message)
        assert await bot.admin_assistant_delivery_content(broadcast_content_update, assistant_broadcast_context) == bot.ADMIN_ASSISTANT_DELIVERY_DELAY
        broadcast_delay_message = FakeMessage("עוד דקה")
        broadcast_delay_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=broadcast_delay_message)
        assert await bot.admin_assistant_delivery_delay(broadcast_delay_update, assistant_broadcast_context) == bot.ADMIN_ASSISTANT_DELIVERY_CONFIRM
        assert assistant_broadcast_context.user_data["assistant_pending_action"]["delay_seconds"] == 60
        direct_content_message = FakeMessage("שלום אישי")
        direct_context = SimpleNamespace(user_data={})
        assert await bot._assistant_start_delivery_draft(
            SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=direct_content_message),
            direct_context, owner, "user", "77",
        ) == bot.ADMIN_ASSISTANT_DELIVERY_CONTENT
        assert "מה ההודעה" in direct_content_message.replies[-1][0]
        direct_media_message = SimpleNamespace(text=None, caption="תמונה אישית", photo=[SimpleNamespace(file_id="photo-1")], video=None, replies=[])
        async def direct_media_reply(text, **kwargs):
            direct_media_message.replies.append((text, kwargs))
            return Msg()
        direct_media_message.reply_text = direct_media_reply
        assert await bot.admin_assistant_delivery_content(
            SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=direct_media_message), direct_context
        ) == bot.ADMIN_ASSISTANT_DELIVERY_DELAY
        direct_delay_message = FakeMessage("עכשיו")
        assert await bot.admin_assistant_delivery_delay(
            SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=direct_delay_message), direct_context
        ) == bot.ADMIN_ASSISTANT_DELIVERY_CONFIRM
        direct_context.bot = FakeBot()
        direct_confirmation = FakeQuery("assistant_confirm_action", owner)
        await bot.assistant_confirm_action(SimpleNamespace(callback_query=direct_confirmation), direct_context)
        assert direct_context.bot.sent_photos[-1][:3] == (77, "photo-1", "תמונה אישית")
        localization_context = SimpleNamespace(bot=FakeBot())
        sent, failed = await bot._send_broadcast_payload(
            localization_context, bot.load_json(bot.USERS_FILE), "מבצע חדש"
        )
        assert (sent, failed) == (2, 0)
        assert {(row[0], row[1]) for row in localization_context.bot.sent_texts} == {(77, "מבצע חדש"), (7706183809, "מבצע חדש")}
        write(bot.COUPONS_FILE, {
            "WELCOME": {
                "coins": 10, "expires": "2030-01-01", "max_uses": 10,
                "used_by": ["77", "88"], "referral_mode": "none", "referral_minimum": 0,
            }
        })
        coupons_query = FakeQuery("admin_coupons", owner)
        await bot.admin_coupons_menu(SimpleNamespace(callback_query=coupons_query), SimpleNamespace())
        coupon_menu_buttons = button_callbacks(coupons_query.edits[-1][1]["reply_markup"])
        assert "coupon_edit_pick_0_0" in coupon_menu_buttons
        assert bot.callback_permission("coupon_edit_pick_0_0") == "coupon_manage"
        coupon_edit_context = SimpleNamespace(user_data={})
        coupon_pick = FakeUpdate("coupon_edit_pick_0_0", owner)
        assert await bot.admin_coupon_edit_start(coupon_pick, coupon_edit_context) == bot.ADMIN_COUPON_EDIT_MENU
        assert "עריכת קופון" in coupon_pick.callback_query.edits[-1][0]
        coupon_coins_field = FakeUpdate("coupon_edit_field_coins", owner)
        assert await bot.admin_coupon_edit_field(coupon_coins_field, coupon_edit_context) == bot.ADMIN_COUPON_EDIT_VALUE
        coupon_coins_message = FakeMessage("30")
        assert await bot.admin_coupon_edit_value(SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=coupon_coins_message), coupon_edit_context) == bot.ADMIN_COUPON_EDIT_MENU
        updated_coupon = bot.load_json(bot.COUPONS_FILE)["WELCOME"]
        assert updated_coupon["coins"] == 30 and updated_coupon["used_by"] == ["77", "88"]
        coupon_limit_field = FakeUpdate("coupon_edit_field_max_uses", owner)
        assert await bot.admin_coupon_edit_field(coupon_limit_field, coupon_edit_context) == bot.ADMIN_COUPON_EDIT_VALUE
        too_low_limit_message = FakeMessage("1")
        assert await bot.admin_coupon_edit_value(SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=too_low_limit_message), coupon_edit_context) == bot.ADMIN_COUPON_EDIT_VALUE
        assert bot.load_json(bot.COUPONS_FILE)["WELCOME"]["max_uses"] == 10
        coupon_expiry_field = FakeUpdate("coupon_edit_field_expires", owner)
        assert await bot.admin_coupon_edit_field(coupon_expiry_field, coupon_edit_context) == bot.ADMIN_COUPON_EDIT_VALUE
        assert await bot.admin_coupon_edit_value(SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=FakeMessage("skip")), coupon_edit_context) == bot.ADMIN_COUPON_EDIT_MENU
        assert bot.load_json(bot.COUPONS_FILE)["WELCOME"]["expires"] is None
        coupon_referral_field = FakeUpdate("coupon_edit_field_referrals", owner)
        assert await bot.admin_coupon_edit_field(coupon_referral_field, coupon_edit_context) == bot.ADMIN_COUPON_EDIT_REFERRAL_MODE
        assert await bot.admin_coupon_edit_referral_mode(SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=FakeMessage("total")), coupon_edit_context) == bot.ADMIN_COUPON_EDIT_REFERRAL_MINIMUM
        assert await bot.admin_coupon_edit_referral_minimum(SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=FakeMessage("10")), coupon_edit_context) == bot.ADMIN_COUPON_EDIT_MENU
        updated_coupon = bot.load_json(bot.COUPONS_FILE)["WELCOME"]
        assert (updated_coupon["referral_mode"], updated_coupon["referral_minimum"]) == ("total", 10)
        coupon_referral_field = FakeUpdate("coupon_edit_field_referrals", owner)
        assert await bot.admin_coupon_edit_field(coupon_referral_field, coupon_edit_context) == bot.ADMIN_COUPON_EDIT_REFERRAL_MODE
        assert await bot.admin_coupon_edit_referral_mode(SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=FakeMessage("skip")), coupon_edit_context) == bot.ADMIN_COUPON_EDIT_MENU
        updated_coupon = bot.load_json(bot.COUPONS_FILE)["WELCOME"]
        assert (updated_coupon["referral_mode"], updated_coupon["referral_minimum"]) == ("none", 0)
        assert any(row["action"] == "coupon_updated" for row in bot.load_json(bot.ADMIN_ACTIONS_FILE))
        settings = bot.load_settings()
        settings["admin_managers"] = {"88": {"permissions": ["assistant", "users"], "assistant_capabilities": ["users"]}}
        bot.save_settings(settings)
        assert await gate_allows("broadcast_confirm_send", owner)
        assert not await gate_allows("broadcast_confirm_send", 88)
        assert not await gate_allows("coupon_edit_pick_0_0", 88)
        denied_message = FakeMessage("הוסף מטבעות")
        denied_context = SimpleNamespace(user_data={"assistant_audit_request": {"request_id": "audit-denied", "request": "הוסף מטבעות"}})
        assert await bot._assistant_apply_runtime_command(SimpleNamespace(effective_user=SimpleNamespace(id=88), message=denied_message), denied_context, "ADJUST_COINS:77:+2", 88)
        assert "אין לך הרשאה" in denied_message.replies[-1][0]
        bot.log_ai_audit(owner, "api_key=AIzaABCDEF012345678901234567890", "redaction_check", response_text="Bearer secret-token")
        redacted = bot.load_json(bot.AI_AUDIT_FILE)[-1]
        assert "AIza" not in redacted["request"] and "secret-token" not in redacted["response"]
        name_lookup_message = FakeMessage("@dana_admin")
        name_lookup_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=name_lookup_message)
        await bot.admin_check_user(name_lookup_update, SimpleNamespace())
        assert "`77`" in name_lookup_message.replies[-1][0]
        # The currency-multiplier button opens for the owner and explains its exact effect.
        multiplier_query = FakeQuery("admin_multiplier", owner)
        multiplier_state = await bot.admin_multiplier_start(
            SimpleNamespace(callback_query=multiplier_query), SimpleNamespace()
        )
        multiplier_text = multiplier_query.edits[-1][0]
        assert multiplier_state == bot.ADMIN_MULTIPLIER
        assert "מכפיל הפניות" in multiplier_text
        assert "מחיר PayPal" in multiplier_text and "המתנה היומית" in multiplier_text

        # Legacy category values are migrated safely and can hold multiple memberships.
        videos = bot.load_videos_with_entry_ids()
        assert bot.video_categories(videos[0]) == ["ישראלי"]
        videos[0]["categories"] = ["ישראלי", "קצר"]
        bot.normalize_video_categories(videos[0])
        bot.save_json(bot.VIDEOS_FILE, videos)
        assert set(bot.video_categories(bot.load_json(bot.VIDEOS_FILE)[0])) == {"ישראלי", "קצר"}
        legacy_random_video = {"file_id": "legacy-random", "duration": 9, "category": "כללי"}
        bot.normalize_video_categories(legacy_random_video)
        assert bot.video_categories(legacy_random_video) == ["רנדומלי"]
        assert "כללי" not in bot._admin_categories()
        assert bot._admin_categories() == sorted(bot._admin_categories(), key=lambda category: category.casefold())
        # Automatic duplicate delivery sends the first group immediately and cleans it on navigation.
        fake_bot = FakeBot()
        context = SimpleNamespace(bot=fake_bot, user_data={})
        update = FakeUpdate("admin_dup_scan", owner)
        await bot.show_duplicate_scan(update, context, include_reviewed=False)
        assert len(fake_bot.sent_videos) == 2, fake_bot.sent_videos
        first_control_callbacks = button_callbacks(fake_bot.sent_texts[-1][2]["reply_markup"])
        assert {"dup_send_0", "dup_mark_0", "dup_back_gallery", "dup_page_1"}.issubset(first_control_callbacks)
        update.callback_query.data = "dup_page_1"
        await bot.admin_dup_page(update, context)
        assert len(fake_bot.sent_videos) == 4
        # Navigation removes the two prior preview videos and their lower action panel.
        assert len(fake_bot.deleted) == 3
        assert len(fake_bot.sent_texts) == 2
        second_control_callbacks = button_callbacks(fake_bot.sent_texts[-1][2]["reply_markup"])
        assert {"dup_send_1", "dup_mark_1", "dup_back_gallery", "dup_page_0"}.issubset(second_control_callbacks)

        # A library preview is removed when the administrator returns to the gallery.
        preview_context = SimpleNamespace(bot=fake_bot, user_data={})
        gallery_preview = FakeUpdate("vid_page_0", owner)
        deleted_before_gallery_back = len(fake_bot.deleted)
        await bot.admin_gallery_page(gallery_preview, preview_context, 0)
        await bot.admin_gallery(FakeUpdate("admin_gallery", owner), preview_context)
        assert len(fake_bot.deleted) == deleted_before_gallery_back + 1

        # A second manager's gallery preview must stay in that manager's own chat.
        manager_chat_id = 88
        manager_preview_context = SimpleNamespace(bot=fake_bot, user_data={})
        manager_gallery_preview = FakeUpdate("vid_page_0", manager_chat_id)
        await bot.admin_gallery_page(manager_gallery_preview, manager_preview_context, 0)
        assert fake_bot.sent_videos[-1][0] == manager_chat_id
        await bot.admin_gallery(FakeUpdate("admin_gallery", manager_chat_id), manager_preview_context)
        assert fake_bot.deleted[-1][0] == manager_chat_id

        # A trash preview follows the same clean-up rule on the Back button.
        write(bot.TRASH_FILE, [{"file_id": "t1", "duration": 12, "entry_id": "trash-a", "deleted_at": "today"}])
        trash_preview = FakeUpdate("admin_trash_page_0", owner)
        deleted_before_trash_back = len(fake_bot.deleted)
        await bot.admin_trash_page(trash_preview, preview_context, 0)
        await bot.admin_gallery(FakeUpdate("admin_gallery", owner), preview_context)
        assert len(fake_bot.deleted) == deleted_before_trash_back + 1
        # A manual backup containing the admin audit file must reach preview safely.
        restore_archive = io.BytesIO()
        with zipfile.ZipFile(restore_archive, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("videos.json", '[{"file_id": "legacy-restore", "duration": 15, "category": "כללי"}]')
            archive.writestr("settings.json", '{"categories": ["כללי", "ישראלי"]}')
            archive.writestr("admin_actions.json", "[]")
        restore_payloads = bot.parse_restore_archive(restore_archive.getvalue())
        restore_preview = bot.restore_summary(restore_payloads)
        assert "יומן פעולות מנהל: 0" in restore_preview
        assert restore_payloads["videos.json"][0]["category"] == "רנדומלי"
        assert "רנדומלי" in restore_payloads["settings.json"]["categories"]
        assert "כללי" not in restore_payloads["settings.json"]["categories"]

        # Duplicate-review marks are preserved both in old settings and in the dedicated backup file.
        reviewed_signature = "reviewed-group-signature"
        bot.save_reviewed_non_duplicate_signatures([reviewed_signature])
        review_archive = io.BytesIO()
        with zipfile.ZipFile(review_archive, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("settings.json", json.dumps({bot.DUPLICATE_REVIEWED_KEY: [reviewed_signature]}))
            # Newer backups may contain an empty dedicated file while legacy marks still live in settings.
            archive.writestr("duplicate_reviews.json", json.dumps([]))
        review_payloads = bot.parse_restore_archive(review_archive.getvalue())
        bot.apply_restore_payloads(review_payloads)
        assert review_payloads["duplicate_reviews.json"] == [reviewed_signature]
        assert reviewed_signature in bot.reviewed_non_duplicate_signatures()
        assert "סימוני לא־כפול: 1" in bot.restore_summary(review_payloads)
        
        # Smart time and number parsing accept inclusive single values and ranges.
        assert bot.format_duration(70) == "1:10"
        assert bot.format_duration(26) == "26 שניות"
        assert bot.parse_smart_time_range("10-13") == (10, 13)
        assert bot.parse_smart_time_range("1:30-22:30") == (90, 1350)
        assert bot.parse_smart_time_range("1:61") is None
        assert bot.parse_number_range("10-28", 100) == (10, 28)
        assert bot.parse_number_range("28-10", 100) is None

        # Time-range results include both endpoints and are delivered from shortest to longest.
        original_videos = bot.load_json(bot.VIDEOS_FILE)
        range_videos = [
            {"file_id": "r13", "duration": 13, "entry_id": "r13", "category": "כללי"},
            {"file_id": "r10b", "duration": 10, "entry_id": "r10b", "category": "כללי"},
            {"file_id": "r12", "duration": 12, "entry_id": "r12", "category": "כללי"},
            {"file_id": "r10a", "duration": 10, "entry_id": "r10a", "category": "כללי"},
            {"file_id": "r11", "duration": 11, "entry_id": "r11", "category": "כללי"},
        ]
        write(bot.VIDEOS_FILE, range_videos)
        time_range_bot = FakeBot()
        time_range_message = FakeMessage("10-13")
        time_range_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), effective_chat=SimpleNamespace(id=owner), message=time_range_message)
        await bot.admin_search_sec_input(time_range_update, SimpleNamespace(bot=time_range_bot, user_data={}))
        assert [video for _, video, _ in time_range_bot.sent_videos] == ["r10a", "r10b", "r11", "r12", "r13"]
        assert {chat_id for chat_id, _, _ in time_range_bot.sent_videos} == {owner}
        assert "5 סרטונים" in time_range_message.replies[0][0]

        # Number-range results preserve the exact library order between the chosen endpoints.
        number_range_bot = FakeBot()
        number_range_message = FakeMessage("2-4")
        number_range_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), effective_chat=SimpleNamespace(id=owner), message=number_range_message)
        await bot.admin_video_search_input(number_range_update, SimpleNamespace(bot=number_range_bot, user_data={}))
        assert [video for _, video, _ in number_range_bot.sent_videos] == ["r10b", "r12", "r10a"]
        assert {chat_id for chat_id, _, _ in number_range_bot.sent_videos} == {owner}
        assert "3 סרטונים" in number_range_message.replies[0][0]
        write(bot.VIDEOS_FILE, original_videos)

        # Safety snapshot and audit persistence are created before dangerous work.
        snapshot = bot.create_auto_backup("test_dangerous_action", owner)
        assert snapshot and snapshot.exists()
        bot.log_admin_action(owner, "test_action", {"ok": True})
        actions = bot.load_json(bot.ADMIN_ACTIONS_FILE)
        assert actions[-1]["action"] == "test_action"
        daily_backup_bot = FakeBot()
        await bot.send_owner_daily_report_with_backup(daily_backup_bot)
        assert len(daily_backup_bot.sent_documents) == 1
        daily_document = daily_backup_bot.sent_documents[0]
        assert daily_document[0] == owner and daily_document[2].startswith("daily_backup_")
        assert "דוח יומי לבעלים" in daily_document[3] and "גיבוי יומי מלא" in daily_document[3]
        # An empty assistant-manager list offers a direct manager-creation action.
        empty_assistant_list = FakeUpdate("admin_mgr_assistant_list", owner)
        await bot.admin_manager_assistant_list(empty_assistant_list, SimpleNamespace(user_data={}))
        empty_assistant_buttons = button_callbacks(empty_assistant_list.callback_query.edits[-1][1]["reply_markup"])
        assert "admin_mgr_add" in empty_assistant_buttons
        assert "admin_owner_assistant_settings" in empty_assistant_buttons
        assert "עדיין לא הוספת מנהל" in empty_assistant_list.callback_query.edits[-1][0]
        owner_assistant_update = FakeUpdate("admin_owner_assistant_settings", owner)
        await bot.admin_owner_assistant_settings(owner_assistant_update, SimpleNamespace(user_data={}))
        assert "כל יכולות העוזר פעילות" in owner_assistant_update.callback_query.edits[-1][0]
        assert "admin_assistant" in button_callbacks(owner_assistant_update.callback_query.edits[-1][1]["reply_markup"])
        assert await gate_allows("admin_owner_assistant_settings", owner)
        assert not await gate_allows("admin_owner_assistant_settings", 12345)

        # Owner can define a manager and toggle only explicit permissions.
        settings = bot.load_settings()
        settings["admin_managers"] = {"12345": {"name": "tester", "permissions": ["assistant", "gallery"], "assistant_capabilities": ["gallery"]}}
        bot.save_settings(settings)
        assert bot.is_admin(12345)
        assert bot.has_admin_permission(12345, "gallery")
        assert not bot.has_admin_permission(12345, "backup")
        keyboard = bot.get_admin_inline_keyboard(12345)
        callback_data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        assert callback_data == ["admin_assistant", "admin_gallery", "admin_ops_dashboard"], callback_data
        # The owner retains the familiar detailed panel with direct day-to-day actions.
        owner_keyboard = bot.get_admin_inline_keyboard(owner)
        owner_callbacks = button_callbacks(owner_keyboard)
        assert {"admin_stats", "admin_gallery", "admin_maintenance", "admin_menu_system"}.issubset(owner_callbacks)
        assert owner_callbacks.count("admin_gallery") == 1
        assert "admin_backup" not in owner_callbacks and "admin_actions_page_0" not in owner_callbacks
        assert "admin_menu_users" not in owner_callbacks
        assert button_rows(owner_keyboard)[2:5] == [
            ["admin_stats", "admin_orders_page_0"],
            ["admin_check", "users_page_0"],
            ["admin_send", "admin_approve"],
        ]
        # A manager with only user messaging permission sees only that section's allowed controls.
        settings = bot.load_settings()
        settings["admin_managers"]["12345"]["permissions"] = ["user_messages"]
        bot.save_settings(settings)
        user_menu_update = FakeUpdate("admin_menu_users", 12345)
        await bot.admin_menu_users(user_menu_update, SimpleNamespace())
        user_menu_buttons = [button.callback_data for row in user_menu_update.callback_query.edits[-1][1]["reply_markup"].inline_keyboard for button in row]
        assert "admin_send" in user_menu_buttons and "admin_check" not in user_menu_buttons
        # Every partial-permission manager sees only the direct controls for their own permission.
        permission_controls = {
            "gallery": (["admin_gallery", "admin_ops_dashboard"], "admin_backup"),
            "duplicates": (["admin_gallery", "admin_ops_dashboard"], "admin_backup"),
            "users": (["admin_stats", "admin_orders_page_0", "admin_check", "users_page_0"], "admin_backup"),
            "user_messages": (["admin_send", "admin_approve"], "admin_backup"),
            "broadcast": (["admin_broadcast"], "admin_backup"),
            "coins": (["admin_coins_menu"], "admin_backup"),
            "maintenance": (["admin_maintenance"], "admin_backup"),
            "audit_log": (["admin_activity_center", "admin_menu_system"], "admin_backup"),
            "backup": (["admin_menu_system"], "admin_delete"),
            "dangerous_delete": (["admin_menu_system"], "admin_backup"),
            "assistant": (["admin_assistant"], "admin_backup"),
        }
        for permission, (expected_controls, blocked_callback) in permission_controls.items():
            settings = bot.load_settings()
            settings["admin_managers"]["12345"]["permissions"] = [permission]
            bot.save_settings(settings)
            assert set(button_callbacks(bot.get_admin_inline_keyboard(12345))) == set(expected_controls)
            for allowed_callback in expected_controls:
                assert await gate_allows(allowed_callback, 12345), (permission, allowed_callback)
        assert not await gate_allows("admin_managers", 12345), permission
        assert not await gate_allows(blocked_callback, 12345), permission

        # Fine-grained permission groups expose only the explicitly enabled button.
        settings = bot.load_settings()
        settings["admin_managers"]["12345"]["permissions"] = ["assistant", "coin_balances"]
        settings["admin_managers"]["12345"]["assistant_capabilities"] = ["coins"]
        bot.save_settings(settings)
        assert bot.has_admin_permission(12345, "coin_balances")
        assert bot.has_admin_permission(12345, "coins")
        assert not bot.has_admin_permission(12345, "reward_settings")
        assert not bot.has_admin_permission(12345, "coupon_manage")
        assert bot.has_assistant_capability(12345, "coins", "coin_balances")
        assert not bot.has_assistant_capability(12345, "coins", "reward_settings")
        assert button_callbacks(bot.get_admin_inline_keyboard(12345)) == ["admin_assistant", "admin_coins_menu"]
        fine_coins_menu = FakeUpdate("admin_coins_menu", 12345)
        await bot.admin_coins_menu(fine_coins_menu, SimpleNamespace())
        fine_coins_buttons = button_callbacks(fine_coins_menu.callback_query.edits[-1][1]["reply_markup"])
        assert "admin_coins" in fine_coins_buttons
        assert "admin_coupons" not in fine_coins_buttons and "admin_coin_control" not in fine_coins_buttons
        assert await gate_allows("admin_coins", 12345)
        assert not await gate_allows("admin_coupons", 12345)
        assert not await gate_allows("admin_coin_control", 12345)

        # The owner can open one group and select exact buttons, without changing a nickname.
        permission_context = SimpleNamespace(user_data={"selected_manager_id": "12345"})
        permission_group_update = FakeUpdate("admin_mgr_group_coins", owner)
        await bot.admin_manager_permission_group(permission_group_update, permission_context)
        permission_group_callbacks = button_callbacks(permission_group_update.callback_query.edits[-1][1]["reply_markup"])
        assert "admin_mgr_group_all_coins" in permission_group_callbacks
        assert "admin_mgr_detail_coupon_manage" in permission_group_callbacks
        detail_toggle_update = FakeUpdate("admin_mgr_detail_coupon_manage", owner)
        await bot.admin_manager_permission_detail_toggle(detail_toggle_update, permission_context)
        assert bot.has_admin_permission(12345, "coupon_manage")

        nickname_start_update = FakeUpdate("admin_mgr_nickname", owner)
        assert await bot.admin_nickname_start(nickname_start_update, permission_context) == bot.ADMIN_NICKNAME
        nickname_message = FakeMessage("מנהל שירות")
        nickname_input_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=nickname_message)
        assert await bot.admin_nickname_input(nickname_input_update, permission_context) == bot.ConversationHandler.END
        assert bot.admin_display_name(12345) == "מנהל שירות"
        assert bot.load_settings()["admin_managers"]["12345"]["nickname"] == "מנהל שירות"
        owner_nickname_context = SimpleNamespace(user_data={})
        owner_nickname_start = FakeUpdate("admin_owner_nickname", owner)
        assert await bot.admin_nickname_start(owner_nickname_start, owner_nickname_context) == bot.ADMIN_NICKNAME
        owner_nickname_message = FakeMessage("מנהל ראשי")
        owner_nickname_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=owner_nickname_message)
        assert await bot.admin_nickname_input(owner_nickname_update, owner_nickname_context) == bot.ConversationHandler.END
        assert bot.admin_display_name(owner) == "מנהל ראשי"

        # A manager with both gallery permissions still receives one gallery entry point only.
        settings = bot.load_settings()
        settings["admin_managers"]["12345"]["permissions"] = ["gallery", "duplicates"]
        bot.save_settings(settings)
        assert button_callbacks(bot.get_admin_inline_keyboard(12345)).count("admin_gallery") == 1

        # The free command assistant parses only permitted commands and sends results in a safe order.
        settings = bot.load_settings()
        settings["admin_managers"]["12345"]["permissions"] = ["assistant", "gallery"]
        settings["admin_managers"]["12345"]["assistant_capabilities"] = ["gallery"]
        bot.save_settings(settings)
        assistant_bot = FakeBot()
        assistant_message = FakeMessage("שלח סרטונים מ-10 עד 20 שניות")
        assistant_update = SimpleNamespace(effective_user=SimpleNamespace(id=12345), message=assistant_message)
        assistant_context = SimpleNamespace(bot=assistant_bot, user_data={})
        assert await bot.admin_assistant_command(assistant_update, assistant_context) == bot.ADMIN_ASSISTANT_COMMAND
        assert [video for _, video, _ in assistant_bot.sent_videos] == ["f1", "f2", "f3", "f4"]
        assert all(markup is None for _, _, markup in assistant_bot.sent_videos)

        blocked_assistant_message = FakeMessage("מצא כפילויות")
        blocked_assistant_update = SimpleNamespace(effective_user=SimpleNamespace(id=12345), message=blocked_assistant_message)
        assert await bot.admin_assistant_command(blocked_assistant_update, assistant_context) == bot.ADMIN_ASSISTANT_COMMAND
        assert "לא הייתי בטוח" in blocked_assistant_message.replies[-1][0]

        settings = bot.load_settings()
        settings["admin_managers"]["12345"]["permissions"] = ["assistant", "duplicates"]
        settings["admin_managers"]["12345"]["assistant_capabilities"] = ["duplicates"]
        bot.save_settings(settings)
        duplicate_assistant_message = FakeMessage("תמצא סרטונים שחשודים בכפילויות")
        duplicate_assistant_update = SimpleNamespace(effective_user=SimpleNamespace(id=12345), message=duplicate_assistant_message)
        assert await bot.admin_assistant_command(duplicate_assistant_update, SimpleNamespace(bot=FakeBot(), user_data={})) == bot.ConversationHandler.END
        assert "admin_dup_scan" in button_callbacks(duplicate_assistant_message.replies[-1][1]["reply_markup"])

        # Assistant settings are visible immediately from the main manager-management screen.
        managers_home_update = FakeUpdate("admin_managers", owner)
        await bot.admin_managers_menu(managers_home_update, SimpleNamespace(user_data={}))
        managers_home_buttons = button_callbacks(managers_home_update.callback_query.edits[-1][1]["reply_markup"])
        assert "admin_mgr_assistant_list" in managers_home_buttons
        assistant_list_update = FakeUpdate("admin_mgr_assistant_list", owner)
        await bot.admin_manager_assistant_list(assistant_list_update, SimpleNamespace(user_data={}))
        assistant_list_buttons = button_callbacks(assistant_list_update.callback_query.edits[-1][1]["reply_markup"])
        assert "admin_mgr_assistant_pick_12345" in assistant_list_buttons

        # The owner can set every assistant capability inside the selected manager's settings.
        manager_settings_update = FakeUpdate("admin_mgr_assistant", owner)
        manager_settings_context = SimpleNamespace(user_data={"selected_manager_id": "12345"})
        await bot.admin_manager_assistant_menu(manager_settings_update, manager_settings_context)
        manager_settings_buttons = button_callbacks(manager_settings_update.callback_query.edits[-1][1]["reply_markup"])
        assert "admin_mgr_assist_toggle_gallery" in manager_settings_buttons
        assert "admin_mgr_assist_toggle_duplicates" in manager_settings_buttons
        toggle_update = FakeUpdate("admin_mgr_assist_toggle_gallery", owner)
        await bot.admin_manager_assistant_toggle(toggle_update, manager_settings_context)
        assert "gallery" in bot.assistant_capabilities(12345)
        await bot.admin_manager_assistant_toggle(toggle_update, manager_settings_context)
        assert "gallery" not in bot.assistant_capabilities(12345)

        # Greeting, clarification, and back control are safe within the assistant conversation.
        greeting_message = FakeMessage("היי מה קורה")
        greeting_update = SimpleNamespace(effective_user=SimpleNamespace(id=12345), message=greeting_message)
        manager_settings = bot.load_settings()
        manager_settings["admin_managers"]["12345"]["permissions"] = ["assistant", "gallery"]
        manager_settings["admin_managers"]["12345"]["assistant_capabilities"] = ["gallery"]
        bot.save_settings(manager_settings)
        assert await bot.admin_assistant_command(greeting_update, SimpleNamespace(bot=FakeBot(), user_data={})) == bot.ADMIN_ASSISTANT_COMMAND
        assert "היי" in greeting_message.replies[-1][0]

        # Gemini requests display Telegram's typing indicator before a free-form answer.
        previous_gemini_key = os.environ.get("GEMINI_API_KEY")
        previous_gemini_payload = bot._assistant_gemini_payload
        try:
            os.environ["GEMINI_API_KEY"] = "test-only"
            bot._assistant_gemini_payload = lambda _: {
                "kind": "answer",
                "canonical_text": None,
                "reply": "תשובת Gemini מדומה.",
            }
            typing_bot = FakeBot()
            typing_message = FakeMessage("מה ההבדל בין גיבוי לשחזור?")
            typing_update = SimpleNamespace(
                effective_user=SimpleNamespace(id=12345),
                effective_chat=SimpleNamespace(id=98765),
                message=typing_message,
            )
            assert await bot.admin_assistant_command(
                typing_update, SimpleNamespace(bot=typing_bot, user_data={})
            ) == bot.ADMIN_ASSISTANT_COMMAND
            assert typing_bot.chat_actions == [(98765, bot.ChatAction.TYPING)]
            assert typing_message.replies[-1][0] == "תשובת Gemini מדומה."
        finally:
            bot._assistant_gemini_payload = previous_gemini_payload
            if previous_gemini_key is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = previous_gemini_key

        clarification_message = FakeMessage("שלח סרטונים")
        clarification_update = SimpleNamespace(effective_user=SimpleNamespace(id=12345), message=clarification_message)
        assert await bot.admin_assistant_command(clarification_update, SimpleNamespace(bot=FakeBot(), user_data={})) == bot.ADMIN_ASSISTANT_COMMAND
        assert "איזה סרטונים" in clarification_message.replies[-1][0]
        journal_explanation_message = FakeMessage("מה זה יומן פעולות?")
        journal_explanation_update = SimpleNamespace(effective_user=SimpleNamespace(id=12345), message=journal_explanation_message)
        assert await bot.admin_assistant_command(journal_explanation_update, SimpleNamespace(bot=FakeBot(), user_data={})) == bot.ADMIN_ASSISTANT_COMMAND
        assert "תיעוד של פעולות ניהול" in journal_explanation_message.replies[-1][0]
        value_explanation_message = FakeMessage("הסבר ערך מטבע")
        value_explanation_update = SimpleNamespace(effective_user=SimpleNamespace(id=12345), message=value_explanation_message)
        assert await bot.admin_assistant_command(value_explanation_update, SimpleNamespace(bot=FakeBot(), user_data={})) == bot.ADMIN_ASSISTANT_COMMAND
        assert "כמה מטבעות משתמש מקבל" in value_explanation_message.replies[-1][0]
        panel_explanation_message = FakeMessage("מה כל כפתור עושה?")
        panel_explanation_update = SimpleNamespace(effective_user=SimpleNamespace(id=12345), message=panel_explanation_message)
        assert await bot.admin_assistant_command(panel_explanation_update, SimpleNamespace(bot=FakeBot(), user_data={})) == bot.ADMIN_ASSISTANT_COMMAND
        assert "מדריך קצר לפאנל הניהול" in panel_explanation_message.replies[-1][0]
        assert bot.callback_permission("admin_assistant_back") == "assistant"

        # Gallery-only and duplicates-only managers enter the same category but receive different controls.
        settings = bot.load_settings()
        settings["admin_managers"]["12345"]["permissions"] = ["gallery"]
        bot.save_settings(settings)
        gallery_update = FakeUpdate("admin_gallery", 12345)
        await bot.admin_gallery(gallery_update, SimpleNamespace(user_data={}))
        gallery_buttons = button_callbacks(gallery_update.callback_query.edits[-1][1]["reply_markup"])
        assert "vid_page_0" in gallery_buttons and "admin_dup_scan" not in gallery_buttons
        assert await gate_allows("vid_page_0", 12345)
        assert await gate_allows("admin_video_search", 12345)
        assert await gate_allows("admin_search_sec_start", 12345)
        assert not await gate_allows("admin_dup_scan", 12345)
        assert bot.callback_permission("admin_video_search") == "gallery_browse"
        assert bot.callback_permission("admin_search_sec_start") == "gallery_browse"
        number_search_update = FakeUpdate("admin_video_search", 12345)
        assert await bot.admin_video_search_start(number_search_update, SimpleNamespace()) == bot.ADMIN_VIDEO_SEARCH
        time_search_update = FakeUpdate("admin_search_sec_start", 12345)
        assert await bot.admin_search_sec_start(time_search_update, SimpleNamespace()) == bot.ADMIN_VIDEO_SEARCH_SECONDS

        settings = bot.load_settings()
        settings["admin_managers"]["12345"]["permissions"] = ["duplicates"]
        bot.save_settings(settings)
        duplicates_update = FakeUpdate("admin_gallery", 12345)
        await bot.admin_gallery(duplicates_update, SimpleNamespace(user_data={}))
        duplicate_buttons = button_callbacks(duplicates_update.callback_query.edits[-1][1]["reply_markup"])
        assert "admin_dup_scan" in duplicate_buttons and "vid_page_0" not in duplicate_buttons
        assert await gate_allows("admin_gallery", 12345)
        assert await gate_allows("admin_dup_scan", 12345)
        assert not await gate_allows("vid_page_0", 12345)

        # Each submenu exposes only the controls covered by its matching permission.
        settings = bot.load_settings()
        settings["admin_managers"]["12345"]["permissions"] = ["users"]
        bot.save_settings(settings)
        users_only = FakeUpdate("admin_menu_users", 12345)
        await bot.admin_menu_users(users_only, SimpleNamespace())
        users_only_buttons = button_callbacks(users_only.callback_query.edits[-1][1]["reply_markup"])
        assert "admin_stats" in users_only_buttons and "admin_send" not in users_only_buttons
        assert await gate_allows("admin_stats", 12345)
        assert not await gate_allows("admin_send", 12345)

        settings = bot.load_settings()
        settings["admin_managers"]["12345"]["permissions"] = ["backup"]
        bot.save_settings(settings)
        backup_only = FakeUpdate("admin_menu_system", 12345)
        await bot.admin_menu_system(backup_only, SimpleNamespace())
        backup_only_buttons = button_callbacks(backup_only.callback_query.edits[-1][1]["reply_markup"])
        assert "admin_backup" in backup_only_buttons and "admin_delete" not in backup_only_buttons
        assert await gate_allows("admin_backup", 12345)
        assert not await gate_allows("admin_delete", 12345)

        owner_system = FakeUpdate("admin_menu_system", owner)
        await bot.admin_menu_system(owner_system, SimpleNamespace())
        owner_system_buttons = button_callbacks(owner_system.callback_query.edits[-1][1]["reply_markup"])
        assert {"admin_actions_page_0", "admin_backup", "admin_global_reset", "admin_audit_center", "admin_managers"}.issubset(owner_system_buttons)
        assert await gate_allows("admin_managers", owner)

        # Coupon referral conditions distinguish all-time referrals from referrals after creation.
        created_at = "2026-08-26T10:00:00+00:00"
        write(bot.USERS_FILE, {"55": {"language": "he"}})
        write(bot.REFERRALS_FILE, {
            "55": {
                "count": 2,
                "referred_ids": ["a", "b"],
                "referred_at": {
                    "a": "2026-08-26T09:00:00+00:00",
                    "b": "2026-08-26T11:00:00+00:00",
                },
            },
        })
        all_time_coupon = {"referral_mode": "total", "referral_minimum": 2, "created_at": created_at}
        since_coupon = {"referral_mode": "since_created", "referral_minimum": 1, "created_at": created_at}
        assert bot._coupon_eligible_referral_count(all_time_coupon, "55") == 2
        assert bot._coupon_eligible_referral_count(since_coupon, "55") == 1

        write(bot.COINS_FILE, {"55": 0})
        write(bot.COUPONS_FILE, {
            "NEED3": {"coins": 8, "expires": None, "max_uses": None, "used_by": [], "referral_mode": "total", "referral_minimum": 3, "created_at": created_at},
            "SINCE1": {"coins": 6, "expires": None, "max_uses": None, "used_by": [], "referral_mode": "since_created", "referral_minimum": 1, "created_at": created_at},
        })
        blocked_message = FakeMessage("NEED3")
        blocked_update = SimpleNamespace(effective_user=SimpleNamespace(id=55), message=blocked_message)
        assert await bot.coupon_redeem_input(blocked_update, SimpleNamespace(bot=FakeBot(), user_data={})) == bot.ConversationHandler.END
        assert bot.load_json(bot.COINS_FILE)["55"] == 0
        assert "דורש 3" in blocked_message.replies[-1][0]

        allowed_message = FakeMessage("SINCE1")
        allowed_update = SimpleNamespace(effective_user=SimpleNamespace(id=55), message=allowed_message)
        assert await bot.coupon_redeem_input(allowed_update, SimpleNamespace(bot=FakeBot(), user_data={})) == bot.ConversationHandler.END
        assert bot.load_json(bot.COINS_FILE)["55"] == 6

        # Creating a coupon keeps the chosen referral policy in its persisted record.
        coupon_context = SimpleNamespace(user_data={
            "new_coupon_code": "TOTAL5", "new_coupon_coins": 12,
            "new_coupon_expiry": None, "new_coupon_max_uses": None,
        })
        mode_message = FakeMessage("total")
        mode_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=mode_message)
        assert await bot.admin_coupon_get_referral_mode(mode_update, coupon_context) == bot.ADMIN_COUPON_REFERRAL_MINIMUM
        minimum_message = FakeMessage("5")
        minimum_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=minimum_message)
        assert await bot.admin_coupon_get_referral_minimum(minimum_update, coupon_context) == bot.ConversationHandler.END
        saved_coupon = bot.load_json(bot.COUPONS_FILE)["TOTAL5"]
        assert saved_coupon["referral_mode"] == "total" and saved_coupon["referral_minimum"] == 5

        # Audit records persist all normal history and coin entries include their full balance delta.
        bot.save_json(bot.ADMIN_ACTIONS_FILE, [])
        bot.log_admin_action(owner, "audit_test", {"safe": True}, source="assistant", status="blocked", target_user_id="55")
        audit_record = bot.load_json(bot.ADMIN_ACTIONS_FILE)[-1]
        assert audit_record["action"] == "audit_test"
        assert audit_record["source"] == "assistant" and audit_record["status"] == "blocked"
        assert audit_record["target_user_id"] == "55"

        bot.log_admin_action(owner, "coin_reward_settings_updated", {}, source="manual")
        human_callback = bot._format_admin_action_record({
            "at": "2026-08-26T17:35:00+00:00", "admin_id": owner,
            "action": "admin_callback_accessed", "details": {"callback": "admin_problem_center", "permission": "dashboard"},
            "source": "telegram_callback", "status": "success",
        })
        assert "מרכז הבעיות" in human_callback and "admin_problem_center" not in human_callback
        human_assistant_action = bot._format_admin_action_record({
            "at": "2026-08-26T17:35:00+00:00", "admin_id": owner,
            "action": "assistant_reward_update", "details": {}, "source": "assistant", "status": "success",
        })
        assert "בוצע דרך העוזר של הבוט" in human_assistant_action
        settings = bot.load_settings()
        settings["admin_managers"] = {"12345": {"nickname": "מנהל משני", "permissions": []}}
        bot.save_settings(settings)
        human_coin_action = bot._format_admin_action_record({
            "at": "2026-08-26T17:35:00+00:00", "admin_id": 12345,
            "action": "coins_balance_changed", "details": {"target_user_id": "77", "change": 10}, "status": "success",
        })
        assert "━━━━━━━━" in human_coin_action and "מנהל משני" in human_coin_action
        assert "מזהה מנהל: `12345`" in human_coin_action
        assert "הוסיף 10 מטבעות למשתמש 77" in human_coin_action
        human_ai = bot._format_ai_audit_record({
            "at": "2026-08-26T18:02:00+00:00", "admin_id": owner, "request": "תן למשתמש 77 עוד 2 מטבעות",
            "event": "tool_execution", "canonical_text": "ADJUST_COINS:77:+2", "status": "success",
            "details": {"tool": "ADJUST_COINS", "required_permission": "coins", "risk": "normal", "arguments": {"target_user_id": "77", "change": 2}, "amount_before": 40, "change": 2, "amount_after": 42, "result": "יתרת המטבעות עודכנה"},
        })
        assert "שינוי יתרת מטבעות" in human_ai and "40 → 42" in human_ai and "שינוי רגיל" in human_ai and "ADJUST_COINS" not in human_ai
        audit_center = FakeUpdate("admin_audit_center", owner)
        await bot.admin_audit_center(audit_center, SimpleNamespace(user_data={}))
        audit_buttons = button_callbacks(audit_center.callback_query.edits[-1][1]["reply_markup"])
        assert "admin_audit_all_0" in audit_buttons and "admin_audit_blocked_0" in audit_buttons
        blocked_audit = FakeUpdate("admin_audit_blocked_0", owner)
        await bot.admin_audit_filtered_page(blocked_audit, SimpleNamespace(user_data={}))
        assert "נחסמה" in blocked_audit.callback_query.edits[-1][0]
        assert "audit_test" not in blocked_audit.callback_query.edits[-1][0]
        search_records, search_error = bot._audit_search_records(f"מנהל={owner};מצב=נחסמה")
        assert search_error is None and search_records
        audit_search_message = FakeMessage(f"מנהל={owner};פעולה=מטבעות")
        audit_search_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=audit_search_message)
        assert await bot.admin_audit_search_input(audit_search_update, SimpleNamespace()) == bot.ConversationHandler.END
        assert "נמצאו" in audit_search_message.replies[-1][0]
        write(bot.USERS_FILE, {"77": {"first_name": "Dana"}})
        write(bot.COINS_FILE, {"77": 42})
        bot.log_ai_audit(owner, "בדוק יתרה", "tool_execution", canonical_text="GET_USER_BALANCE:77", status="success", details={"tool": "GET_USER_BALANCE", "required_permission": "users", "risk": "info"})
        replay_id = bot.load_json(bot.AI_AUDIT_FILE)[-1]["id"]
        replay_update = FakeUpdate(f"admin_audit_replay_{replay_id}", owner)
        await bot.admin_audit_replay(replay_update, SimpleNamespace())
        assert "Replay" in replay_update.callback_query.edits[-1][0] and "42" in replay_update.callback_query.edits[-1][0]
        assert bot._safe_audit_replay_command("ADJUST_COINS:77:+2") is None
        assert not await gate_allows("admin_audit_center", 12345)

        bot.save_json(bot.COIN_TRANSACTIONS_FILE, [])
        bot.log_coin_transaction("55", 6, -2, 4, reason="test_purchase", source="test", actor_id=owner)
        transaction = bot.load_json(bot.COIN_TRANSACTIONS_FILE)[-1]
        assert transaction["user_id"] == "55"
        assert transaction["amount_before"] == 6 and transaction["change"] == -2 and transaction["amount_after"] == 4
        assert transaction["reason"] == "test_purchase" and transaction["actor_id"] == owner

        # Daily-bonus data continues to maintain an accurate total balance.
        write(bot.USERS_FILE, {"55": {"last_bonus_ts": 0}})
        write(bot.COINS_FILE, {"55": 7})
        assert bot.count_unseen_videos(55) == 4

        # Destructive actions must deliver a restorable emergency ZIP before changing data.
        write(bot.VIDEOS_FILE, [{"entry_id": "emergency-video", "file_id": "v", "duration": 10}])
        reset_bot = FakeBot()
        reset_message = FakeMessage("מאשר")
        reset_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=reset_message)
        assert await bot.admin_global_reset_execute(reset_update, SimpleNamespace(bot=reset_bot, user_data={})) == bot.ConversationHandler.END
        assert len(reset_bot.sent_documents) == 1
        assert "גיבוי חירום" in reset_bot.sent_documents[0][3]
        assert bot.load_json(bot.USERS_FILE) == {} and bot.load_json(bot.VIDEOS_FILE) == []
        write(bot.VIDEOS_FILE, [{"entry_id": "emergency-video", "file_id": "v", "duration": 10}])
        delete_bot = FakeBot()
        delete_update = FakeUpdate("admin_delete_confirm", owner)
        await bot.admin_delete_confirm(delete_update, SimpleNamespace(bot=delete_bot, user_data={}))
        assert len(delete_bot.sent_documents) == 1
        assert "מחיקת כל הסרטונים" in delete_bot.sent_documents[0][3]
        assert bot.load_json(bot.VIDEOS_FILE) == []
        write(bot.USERS_FILE, {"55": {"last_bonus_ts": 0}})
        failed_reset_message = FakeMessage("מאשר")
        failed_reset_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=failed_reset_message)
        assert await bot.admin_global_reset_execute(
            failed_reset_update, SimpleNamespace(bot=FailingDocumentBot(), user_data={})
        ) == bot.ConversationHandler.END
        assert "האיפוס בוטל" in failed_reset_message.replies[-1][0]
        assert "55" in bot.load_json(bot.USERS_FILE)
        write(bot.VIDEOS_FILE, [{"entry_id": "keep-video", "file_id": "v", "duration": 10}])
        failed_delete_update = FakeUpdate("admin_delete_confirm", owner)
        await bot.admin_delete_confirm(failed_delete_update, SimpleNamespace(bot=FailingDocumentBot(), user_data={}))
        assert "מחיקת הסרטונים בוטלה" in failed_delete_update.callback_query.edits[-1][0]
        assert len(bot.load_json(bot.VIDEOS_FILE)) == 1
    print("PASS: gallery, user, messaging, rewards, communications, system, and owner permission boundaries are enforced.")


if __name__ == "__main__":
    asyncio.run(run())
