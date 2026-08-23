"""Regression tests for duplicate and category-sort review persistence.

Run with: python3 test_review_progress_persistence.py
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOT_PATH = ROOT / "bot.py"


def load_module():
    spec = importlib.util.spec_from_file_location("bot_review_progress_test", BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def configure_paths(bot, root: Path):
    bot.DATA_DIR = root
    bot.SETTINGS_FILE = root / "settings.json"
    bot.DUPLICATE_REVIEWS_FILE = root / "duplicate_reviews.json"
    bot.VIDEOS_FILE = root / "videos.json"
    bot.TRASH_FILE = root / "trash.json"
    bot.USERS_FILE = root / "users.json"
    bot.COINS_FILE = root / "coins.json"
    bot.REFERRALS_FILE = root / "referrals.json"
    bot.ORDERS_FILE = root / "orders.json"
    bot.COUPONS_FILE = root / "coupons.json"
    bot.ADMIN_ACTIONS_FILE = root / "admin_actions.json"
    root.mkdir(parents=True, exist_ok=True)
    bot.save_json(bot.SETTINGS_FILE, {"categories": ["רנדומלי", "ישראלי"]})
    bot.save_json(bot.DUPLICATE_REVIEWS_FILE, [])


def archive_payloads(payloads: dict) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename, content in payloads.items():
            archive.writestr(filename, json.dumps(content, ensure_ascii=False))
    return output.getvalue()


def main():
    bot = load_module()
    with tempfile.TemporaryDirectory(prefix="review_progress_") as directory:
        root = Path(directory)
        configure_paths(bot, root)
        videos = [
            {"entry_id": "a" * 32, "file_id": "one", "duration": 20, "category": "רנדומלי", "categories": ["רנדומלי"]},
            {"entry_id": "b" * 32, "file_id": "two", "duration": 20, "category": "רנדומלי", "categories": ["רנדומלי"]},
            {"entry_id": "c" * 32, "file_id": "three", "duration": 30, "category": "ישראלי", "categories": ["ישראלי"]},
        ]
        bot.save_json(bot.VIDEOS_FILE, videos)

        groups = bot.find_duplicate_groups(include_reviewed=False)
        assert len(groups) == 1
        original_signature = bot.duplicate_group_signature(groups[0])
        assert bot.mark_group_as_not_duplicate(groups[0]) is True
        assert bot.find_duplicate_groups(include_reviewed=False) == []
        assert original_signature in bot.reviewed_non_duplicate_signatures()
        assert original_signature in bot.load_json(bot.DUPLICATE_REVIEWS_FILE)

        # A new video at the same duration changes exactly that group and requires a new review.
        videos.append({"entry_id": "d" * 32, "file_id": "four", "duration": 20, "category": "רנדומלי", "categories": ["רנדומלי"]})
        bot.save_json(bot.VIDEOS_FILE, videos)
        changed_groups = bot.find_duplicate_groups(include_reviewed=False)
        assert len(changed_groups) == 1
        assert len(changed_groups[0]) == 3
        assert bot.duplicate_group_signature(changed_groups[0]) != original_signature

        # Category sort progress shows only unhandled entries and survives a restore.
        bot.mark_category_sort_reviewed("a" * 32)
        assert [video["entry_id"] for video in bot._category_sort_pending_videos()] == ["b" * 32, "d" * 32, "c" * 32]

        settings_before_restore = bot.load_settings()
        review_file_before_restore = bot.load_json(bot.DUPLICATE_REVIEWS_FILE)
        payloads = bot.parse_restore_archive(archive_payloads({
            "settings.json": settings_before_restore,
            "duplicate_reviews.json": review_file_before_restore,
            "videos.json": videos,
        }))
        assert original_signature in payloads["duplicate_reviews.json"]
        assert "a" * 32 in payloads["settings.json"][bot.CATEGORY_SORT_REVIEWED_KEY]

        # Simulate a clean destination then apply the restored JSON payloads.
        bot.save_json(bot.SETTINGS_FILE, {})
        bot.save_json(bot.DUPLICATE_REVIEWS_FILE, [])
        bot.save_json(bot.VIDEOS_FILE, [])
        for filename, content in payloads.items():
            bot.save_json(root / filename, content)
        assert original_signature in bot.reviewed_non_duplicate_signatures()
        assert "a" * 32 in bot.category_sort_reviewed_entry_ids()

        # Search/browse media controls must expose direct category assignment callbacks.
        markup = bot._quick_category_markup("b" * 32)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        assert f"cat_quick_menu_{'b' * 32}" in callbacks

    print("Review-progress persistence tests passed.")


if __name__ == "__main__":
    main()
