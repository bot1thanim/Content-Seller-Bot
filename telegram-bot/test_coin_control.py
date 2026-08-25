"""Offline tests for direct coin reward controls."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("coin_control_bot", ROOT / "bot.py")
bot = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bot
SPEC.loader.exec_module(bot)


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeUpdate:
    def __init__(self, text, user_id=7706183809):
        self.effective_user = SimpleNamespace(id=user_id)
        self.message = FakeMessage(text)


async def run():
    saved = {}
    settings = {"daily_gift_amount": 1, "referral_reward_amount": 1}
    bot.has_admin_permission = lambda user_id, permission: permission == "coins"
    bot.load_settings = lambda: dict(settings)
    def save_settings(value):
        settings.clear()
        settings.update(value)
        saved.update(value)
    bot.save_settings = save_settings
    bot.log_admin_action = lambda *args, **kwargs: None

    context = SimpleNamespace(user_data={"coin_control_target": "both"})
    update = FakeUpdate("2 3")
    result = await bot.admin_coin_control_apply(update, context)
    assert result == bot.ConversationHandler.END
    assert settings["daily_gift_amount"] == 2
    assert settings["referral_reward_amount"] == 3
    assert "יתרות הקיימות לא השתנו" in update.message.replies[-1][0]

    context = SimpleNamespace(user_data={"coin_control_target": "daily"})
    update = FakeUpdate("0")
    result = await bot.admin_coin_control_apply(update, context)
    assert result == bot.ConversationHandler.END
    assert settings["daily_gift_amount"] == 0
    assert settings["referral_reward_amount"] == 3

    context = SimpleNamespace(user_data={"coin_control_target": "referral"})
    update = FakeUpdate("bad")
    result = await bot.admin_coin_control_apply(update, context)
    assert result == bot.ADMIN_MULTIPLIER
    assert "קלט לא תקין" in update.message.replies[-1][0]

    # Changing a balance is allowed only for users already registered through /start.
    users = {"42": {"first_name": "משתמש בדיקה"}}
    coins = {"42": 5}
    def fake_load_json(filepath):
        if filepath == bot.USERS_FILE:
            return dict(users)
        if filepath == bot.COINS_FILE:
            return dict(coins)
        return {}
    bot.load_json = fake_load_json
    context = SimpleNamespace(user_data={})
    invalid = FakeUpdate("123")
    result = await bot.admin_coins_id(invalid, context)
    assert result == bot.ADMIN_COINS_ID
    assert "ID לא תקין" in invalid.message.replies[-1][0]
    assert "coins_target_id" not in context.user_data

    context = SimpleNamespace(user_data={})
    valid = FakeUpdate("42")
    result = await bot.admin_coins_id(valid, context)
    assert result == bot.ADMIN_COINS_AMOUNT
    assert context.user_data["coins_target_id"] == "42"


if __name__ == "__main__":
    asyncio.run(run())
    print("Coin control tests passed.")
