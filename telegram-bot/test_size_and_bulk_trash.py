from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("size_bulk_bot", ROOT / "bot.py")
bot = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bot
SPEC.loader.exec_module(bot)


class FakeQuery:
    def __init__(self, data: str, user_id: int = 1):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id, first_name="tester")
        self.edits = []
        self.message = SimpleNamespace(chat_id=123)

    async def answer(self, *args, **kwargs):
        return None

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


def update_for(data: str) -> SimpleNamespace:
    return SimpleNamespace(callback_query=FakeQuery(data))


class FakeBot:
    async def send_message(self, **kwargs):
        return SimpleNamespace(message_id=900)

    async def delete_message(self, **kwargs):
        return None


async def fake_send_duplicate_group_media(context, group, chat_id):
    context.user_data["dup_sent_media_message_ids"] = [101, 102]
    context.user_data["admin_preview_chat_id"] = chat_id
    return len(group), 0


async def run():
    bot.has_admin_permission = lambda user_id, permission: True
    bot.get_admin_inline_keyboard = lambda: None

    bot.load_videos_with_entry_ids = lambda: [
        {"entry_id": "a", "file_size": 200 * 1024, "duration": 1},
        {"entry_id": "b", "file_size": 500 * 1024, "duration": 2},
        {"entry_id": "c", "file_size": 2 * 1024 * 1024, "duration": 3},
        {"entry_id": "d", "file_size": 2 * 1024 * 1024, "duration": 4},
        {"entry_id": "e"},
    ]
    sizes = bot.find_duplicate_size_groups()
    assert len(sizes) == 1
    assert [item["entry_id"] for item in sizes[0]] == ["c", "d"]
    assert bot.format_file_size(200 * 1024) == "200KB"
    assert bot.format_file_size(2 * 1024 * 1024) == "2MB"
    assert bot.format_file_size(None) == "גודל לא ידוע"
    assert bot.callback_permission("admin_search_size_start") == "gallery_browse"
    assert bot.callback_permission("admin_size_group_2048") == "gallery_browse"
    assert bot.callback_permission("admin_trash_bulk_apply_empty") == "recycle_bin"

    # Build the actual admin gallery keyboard and verify the visible UI layout.
    root_query = FakeQuery("admin_gallery")
    await bot.admin_gallery(SimpleNamespace(callback_query=root_query), SimpleNamespace(user_data={}))
    root_markup = root_query.edits[-1][1]["reply_markup"]
    root_rows = [[(button.text, button.callback_data) for button in row] for row in root_markup.inline_keyboard]
    callbacks = [callback for row in root_rows for _, callback in row]
    labels = [text for row in root_rows for text, _ in row]
    assert "admin_search_size_start" in callbacks
    assert "🛠 תיקון מזהים שבורים" not in labels
    assert "admin_repair_start" not in callbacks
    assert "admin_dup_rescan" in callbacks
    size_row = next(row for row in root_rows if any(callback == "admin_search_size_start" for _, callback in row))
    assert size_row == [("🔎 מצא כפילויות", "admin_dup_scan"), ("💾 לפי גודל", "admin_search_size_start")]

    original_sender = bot.send_duplicate_group_media
    bot.send_duplicate_group_media = fake_send_duplicate_group_media
    size_query = FakeQuery("admin_search_size_start")
    size_context = SimpleNamespace(user_data={}, bot=FakeBot())
    await bot.admin_search_size_start(SimpleNamespace(callback_query=size_query), size_context)
    assert "קבוצת גודל (1/1)" in size_query.edits[-1][0]
    assert "מספר סרטונים בקבוצה: 2" in size_query.edits[-1][0]
    assert size_context.user_data["size_review_control_message_id"] == 900
    bot.send_duplicate_group_media = original_sender

    with TemporaryDirectory() as temp:
        root = Path(temp)
        bot.DATA_DIR = root
        bot.VIDEOS_FILE = root / "videos.json"
        bot.TRASH_FILE = root / "trash.json"
        bot.ADMIN_ACTIONS_FILE = root / "admin_actions.json"
        videos = [
            {"entry_id": "v1", "file_id": "f1", "file_size": 100, "duration": 5},
            {"entry_id": "v2", "file_id": "f2", "file_size": 200, "duration": 6},
        ]
        trash = [{"entry_id": "t1", "file_id": "t1", "deleted_at": "yesterday"}]
        bot.save_json(bot.VIDEOS_FILE, videos)
        bot.save_json(bot.TRASH_FILE, trash)
        bot.create_auto_backup = lambda reason, actor_id: root / "safety.zip"

        # Confirmation only renders a second-step button and does not write data.
        before_videos = bot.VIDEOS_FILE.read_bytes()
        confirm_query = FakeQuery("admin_trash_bulk_recycle")
        await bot.admin_trash_bulk_confirm(SimpleNamespace(callback_query=confirm_query), SimpleNamespace(user_data={}))
        assert bot.VIDEOS_FILE.read_bytes() == before_videos
        confirm_markup = confirm_query.edits[-1][1]["reply_markup"]
        assert any(button.callback_data == "admin_trash_bulk_apply_recycle" for row in confirm_markup.inline_keyboard for button in row)

        actions = []
        bot.log_admin_action = lambda actor, action, details=None, **kwargs: actions.append((action, details, kwargs))
        await bot.admin_trash_bulk_apply(update_for("admin_trash_bulk_apply_recycle"), SimpleNamespace(user_data={}))
        assert bot.load_json(bot.VIDEOS_FILE) == []
        recycled = bot.load_json(bot.TRASH_FILE)
        assert {item["entry_id"] for item in recycled} == {"v1", "v2", "t1"}
        assert actions[-1][0] == "all_videos_recycled"

        await bot.admin_trash_bulk_apply(update_for("admin_trash_bulk_apply_restore"), SimpleNamespace(user_data={}))
        restored = bot.load_json(bot.VIDEOS_FILE)
        assert {item["entry_id"] for item in restored} == {"v1", "v2", "t1"}
        assert bot.load_json(bot.TRASH_FILE) == []
        assert all("deleted_at" not in item for item in restored)
        assert actions[-1][0] == "all_trash_restored"

        bot.save_json(bot.TRASH_FILE, [{"entry_id": "t2"}, {"entry_id": "t3"}])
        await bot.admin_trash_bulk_apply(update_for("admin_trash_bulk_apply_empty"), SimpleNamespace(user_data={}))
        assert bot.load_json(bot.TRASH_FILE) == []
        assert actions[-1][0] == "trash_emptied"

    print("Size filtering and bulk recycle-bin tests passed.")


if __name__ == "__main__":
    asyncio.run(run())
