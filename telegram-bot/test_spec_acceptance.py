from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("spec_acceptance_bot", ROOT / "bot.py")
bot = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bot
SPEC.loader.exec_module(bot)


class FakeQuery:
    def __init__(self, data="", user_id=1):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id, first_name="tester")
        self.edits = []
        self.message = SimpleNamespace(chat_id=123)

    async def answer(self, *args, **kwargs):
        return None

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=1)


async def run():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        bot.DATA_DIR = root
        bot.VIDEOS_FILE = root / "videos.json"
        bot.TRASH_FILE = root / "trash.json"
        bot.SETTINGS_FILE = root / "settings.json"
        bot.ADMIN_ACTIONS_FILE = root / "admin_actions.json"
        bot.BROADCASTS_FILE = root / "broadcasts.json"
        bot.save_json(bot.VIDEOS_FILE, [])
        bot.save_json(bot.TRASH_FILE, [])
        bot.save_json(bot.SETTINGS_FILE, {"categories": [bot.DEFAULT_CATEGORY, "דרמה"]})
        bot.save_json(bot.ADMIN_ACTIONS_FILE, [])
        bot.save_json(bot.BROADCASTS_FILE, [])

        # Legacy multi-membership is normalized to one default/unassigned category,
        # without deleting the video record.
        legacy = {"entry_id": "legacy", "file_id": "f", "categories": ["דרמה", "קומדיה"]}
        changed = bot.normalize_video_categories(legacy)
        assert changed is True
        assert legacy["categories"] == [bot.DEFAULT_CATEGORY]
        assert legacy["category"] == bot.DEFAULT_CATEGORY

        # 1,000 existing videos plus 10 newly added videos are sourced dynamically.
        old = [{"entry_id": f"v{i}", "file_id": f"f{i}", "duration": i} for i in range(1000)]
        bot.save_json(bot.VIDEOS_FILE, old)
        settings = bot.load_settings()
        settings[bot.CATEGORY_SORT_REVIEWED_KEY] = [f"v{i}" for i in range(1000)]
        settings[bot.CATEGORY_SORT_SHARED_PROGRESS_KEY] = {
            "mode": "continue", "entry_ids": [f"v{i}" for i in range(1000)],
            "page": 999, "actor_id": 1,
        }
        bot.save_settings(settings)
        new = old + [{"entry_id": f"new{i}", "file_id": f"nf{i}", "duration": 2000 + i} for i in range(10)]
        bot.save_json(bot.VIDEOS_FILE, new)
        assert len(bot._category_sort_pending_videos()) == 10
        context = SimpleNamespace(user_data={})
        session = bot._start_category_sort_session(context, "continue")
        assert len(session) == 1010
        assert [v["entry_id"] for v in session[-10:]] == [f"new{i}" for i in range(10)]
        assert context.user_data["cat_sort_shared_resume_page"] == 999

        # Delete one item by stable ID; next/previous source is re-read from active data.
        moved, remaining = bot._move_video_to_trash("v500")
        assert moved["entry_id"] == "v500"
        assert len(remaining) == 1009
        assert "v500" not in {v["entry_id"] for v in bot.load_videos_with_entry_ids()}
        assert bot.load_json(bot.TRASH_FILE)[-1]["entry_id"] == "v500"

        # Server-side permission mapping exists independently of button visibility.
        assert bot.callback_permission("admin_cat_visibility") == "owner"
        assert bot.callback_permission("cat_visibility_toggle_0") == "owner"
        assert bot.callback_permission("admin_search_size_start") == "gallery_browse"

        # Actual admin category UI contains the visibility and future-random controls.
        bot.has_admin_permission = lambda user_id, permission: True
        edit_query = FakeQuery("admin_cat_edit")
        await bot.admin_cat_edit_menu(SimpleNamespace(callback_query=edit_query), SimpleNamespace(user_data={}))
        markup = edit_query.edits[-1][1]["reply_markup"]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        assert "admin_cat_visibility" in callbacks
        assert "admin_cat_random" in callbacks

        # Coupon choices are buttons, not command-only prompts.
        message = FakeMessage("10")
        await bot.admin_coupon_get_coins(SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1)), SimpleNamespace(user_data={}))
        coupon_callbacks = [
            button.callback_data
            for row in message.replies[-1][1]["reply_markup"].inline_keyboard
            for button in row
        ]
        assert "coupon_new_expiry_none" in coupon_callbacks

        # Scheduling after confirmation persists pending status and does not send now.
        fake_bot = FakeBot()
        original_load_json = bot.load_json
        bot.load_json = lambda path: {"u1": {}, "u2": {}} if path == bot.USERS_FILE else original_load_json(path)
        bot.is_admin = lambda user_id: True
        bot.get_admin_inline_keyboard = lambda user_id=None: None
        bot.log_admin_action = lambda *args, **kwargs: None
        query = FakeQuery("broadcast_confirm_send")
        query.message = SimpleNamespace(chat_id=123)
        context = SimpleNamespace(
            user_data={"broadcast_msg": "בדיקת תזמון", "broadcast_delay": 30, "broadcast_media": None, "broadcast_markup": None},
            bot=fake_bot,
        )
        # The handler stores the schedule and returns without calling send payload.
        await bot.admin_broadcast_preview_action(SimpleNamespace(callback_query=query), context)
        records = bot.load_json(bot.BROADCASTS_FILE)
        assert len(records) == 1
        assert records[0]["status"] == "pending"
        assert fake_bot.sent == []

    print("Specification acceptance tests passed.")


if __name__ == "__main__":
    asyncio.run(run())
