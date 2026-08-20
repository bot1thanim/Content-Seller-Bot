import asyncio
import importlib.util
import io
import json
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
        self.deleted = []
    async def send_video(self, chat_id, video, reply_markup=None):
        self.sent_videos.append((chat_id, video, reply_markup))
        return Msg()
    async def send_message(self, chat_id, text, **kwargs):
        self.sent_texts.append((chat_id, text, kwargs))
        return Msg()
    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []
    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return Msg()


class FakeQuery:
    def __init__(self, data, user_id):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
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
        bot.AUTO_BACKUPS_DIR = data / "auto_backups"
        owner = bot.ADMIN_ID
        bot.ensure_data_files()
        write(bot.SETTINGS_FILE, {"categories": ["כללי", "ישראלי", "קצר"], "admin_managers": {}})
        write(bot.VIDEOS_FILE, [
            {"file_id": "f1", "duration": 10, "entry_id": "a", "category": "ישראלי"},
            {"file_id": "f2", "duration": 10, "entry_id": "b", "category": "ישראלי"},
            {"file_id": "f3", "duration": 20, "entry_id": "c", "category": "קצר"},
            {"file_id": "f4", "duration": 20, "entry_id": "d", "category": "קצר"},
        ])
        # Legacy category values are migrated safely and can hold multiple memberships.
        videos = bot.load_videos_with_entry_ids()
        assert bot.video_categories(videos[0]) == ["ישראלי"]
        videos[0]["categories"] = ["ישראלי", "קצר"]
        bot.normalize_video_categories(videos[0])
        bot.save_json(bot.VIDEOS_FILE, videos)
        assert set(bot.video_categories(bot.load_json(bot.VIDEOS_FILE)[0])) == {"ישראלי", "קצר"}
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
            archive.writestr("videos.json", "[]")
            archive.writestr("admin_actions.json", "[]")
        restore_payloads = bot.parse_restore_archive(restore_archive.getvalue())
        restore_preview = bot.restore_summary(restore_payloads)
        assert "יומן פעולות מנהל: 0" in restore_preview

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
        time_range_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=time_range_message)
        await bot.admin_search_sec_input(time_range_update, SimpleNamespace(bot=time_range_bot, user_data={}))
        assert [video for _, video, _ in time_range_bot.sent_videos] == ["r10a", "r10b", "r11", "r12", "r13"]
        assert "5 סרטונים" in time_range_message.replies[0][0]

        # Number-range results preserve the exact library order between the chosen endpoints.
        number_range_bot = FakeBot()
        number_range_message = FakeMessage("2-4")
        number_range_update = SimpleNamespace(effective_user=SimpleNamespace(id=owner), message=number_range_message)
        await bot.admin_video_search_input(number_range_update, SimpleNamespace(bot=number_range_bot, user_data={}))
        assert [video for _, video, _ in number_range_bot.sent_videos] == ["r10b", "r12", "r10a"]
        assert "3 סרטונים" in number_range_message.replies[0][0]
        write(bot.VIDEOS_FILE, original_videos)

        # Safety snapshot and audit persistence are created before dangerous work.
        snapshot = bot.create_auto_backup("test_dangerous_action", owner)
        assert snapshot and snapshot.exists()
        bot.log_admin_action(owner, "test_action", {"ok": True})
        actions = bot.load_json(bot.ADMIN_ACTIONS_FILE)
        assert actions[-1]["action"] == "test_action"
        # Owner can define a manager and toggle only explicit permissions.
        settings = bot.load_settings()
        settings["admin_managers"] = {"12345": {"name": "tester", "permissions": ["assistant", "gallery"], "assistant_capabilities": ["gallery"]}}
        bot.save_settings(settings)
        assert bot.is_admin(12345)
        assert bot.has_admin_permission(12345, "gallery")
        assert not bot.has_admin_permission(12345, "backup")
        keyboard = bot.get_admin_inline_keyboard(12345)
        callback_data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        assert callback_data == ["admin_assistant", "admin_gallery"], callback_data
        # The owner retains the familiar detailed panel with direct day-to-day actions.
        owner_callbacks = button_callbacks(bot.get_admin_inline_keyboard(owner))
        assert {"admin_stats", "admin_gallery", "admin_maintenance", "admin_menu_system"}.issubset(owner_callbacks)
        assert owner_callbacks.count("admin_gallery") == 1
        assert "admin_backup" not in owner_callbacks and "admin_actions_page_0" not in owner_callbacks
        assert "admin_menu_users" not in owner_callbacks
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
            "gallery": (["admin_gallery"], "admin_backup"),
            "duplicates": (["admin_gallery"], "admin_backup"),
            "users": (["admin_stats", "admin_orders_page_0", "admin_check", "users_page_0"], "admin_backup"),
            "user_messages": (["admin_send", "admin_approve"], "admin_backup"),
            "broadcast": (["admin_broadcast"], "admin_backup"),
            "coins": (["admin_coins", "admin_vip", "admin_coupons", "admin_multiplier"], "admin_backup"),
            "maintenance": (["admin_maintenance"], "admin_backup"),
            "audit_log": (["admin_menu_system"], "admin_backup"),
            "backup": (["admin_menu_system"], "admin_delete"),
            "dangerous_delete": (["admin_menu_system"], "admin_backup"),
            "assistant": (["admin_assistant"], "admin_backup"),
        }
        for permission, (expected_controls, blocked_callback) in permission_controls.items():
            settings = bot.load_settings()
            settings["admin_managers"]["12345"]["permissions"] = [permission]
            bot.save_settings(settings)
            assert button_callbacks(bot.get_admin_inline_keyboard(12345)) == expected_controls
            for allowed_callback in expected_controls:
                assert await gate_allows(allowed_callback, 12345), (permission, allowed_callback)
            assert not await gate_allows("admin_managers", 12345), permission
            assert not await gate_allows(blocked_callback, 12345), permission

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
        clarification_message = FakeMessage("שלח סרטונים")
        clarification_update = SimpleNamespace(effective_user=SimpleNamespace(id=12345), message=clarification_message)
        assert await bot.admin_assistant_command(clarification_update, SimpleNamespace(bot=FakeBot(), user_data={})) == bot.ADMIN_ASSISTANT_COMMAND
        assert "איזה סרטונים" in clarification_message.replies[-1][0]
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
        assert bot.callback_permission("admin_video_search") == "gallery"
        assert bot.callback_permission("admin_search_sec_start") == "gallery"
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
        assert {"admin_actions_page_0", "admin_backup", "admin_global_reset", "admin_managers"}.issubset(owner_system_buttons)
        assert await gate_allows("admin_managers", owner)

        # Daily-bonus data continues to maintain an accurate total balance.
        write(bot.USERS_FILE, {"55": {"last_bonus_ts": 0}})
        write(bot.COINS_FILE, {"55": 7})
        assert bot.count_unseen_videos(55) == 4
    print("PASS: gallery, user, messaging, rewards, communications, system, and owner permission boundaries are enforced.")


if __name__ == "__main__":
    asyncio.run(run())
