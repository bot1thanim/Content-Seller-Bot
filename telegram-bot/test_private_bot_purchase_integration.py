"""Integration tests for private-bot purchase and manager approval flow.

Run with: python3 test_private_bot_purchase_integration.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from private_bots import TokenCipher

ROOT = Path(__file__).resolve().parent
BOT_PATH = ROOT / "bot.py"


class FakeMessage:
    def __init__(self, text: str = ""):
        self.text = text
        self.deleted = False
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))

    async def delete(self):
        self.deleted = True


class FakeQuery:
    def __init__(self, data, user_id, first_name="לקוח"):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id, first_name=first_name, username=f"u{user_id}")
        self.message = FakeMessage()
        self.answers = []
        self.edits = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class FakeCandidateBot:
    def __init__(self, token):
        self.token = token

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get_me(self):
        return SimpleNamespace(id=123456789, username="private_test_bot", is_bot=True)


def load_bot_module():
    spec = importlib.util.spec_from_file_location("bot_private_integration", BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def test_flow(bot, temporary_root: Path):
    data_dir = temporary_root / "data"
    bot.DATA_DIR = data_dir
    bot.USERS_FILE = data_dir / "users.json"
    bot.COINS_FILE = data_dir / "coins.json"
    bot.REFERRALS_FILE = data_dir / "referrals.json"
    bot.VIDEOS_FILE = data_dir / "videos.json"
    bot.ORDERS_FILE = data_dir / "orders.json"
    bot.COUPONS_FILE = data_dir / "coupons.json"
    bot.SETTINGS_FILE = data_dir / "settings.json"
    bot.TRASH_FILE = data_dir / "trash.json"
    bot.ADMIN_ACTIONS_FILE = data_dir / "admin_actions.json"
    bot.DUPLICATE_REVIEWS_FILE = data_dir / "duplicate_reviews.json"
    bot.PRIVATE_BOTS_FILE = data_dir / "private_bots.json"
    bot.AUTO_BACKUPS_DIR = data_dir / "auto_backups"
    bot.ensure_data_files()

    customer_id = 222222
    admin_id = bot.ADMIN_ID
    bot.save_json(bot.USERS_FILE, {str(customer_id): {"id": customer_id, "first_name": "לקוח", "purchases": 0, "total_spent": 0, "seen_videos": []}})
    bot.save_json(bot.COINS_FILE, {str(customer_id): 400})

    fake_bot = FakeBot()
    customer_query = FakeQuery("privatebot_pay_coins", customer_id)
    customer_update = SimpleNamespace(callback_query=customer_query, effective_user=customer_query.from_user)
    customer_context = SimpleNamespace(bot=fake_bot, user_data={})
    await bot.private_bot_pay_coins(customer_update, customer_context)

    assert bot.load_json(bot.COINS_FILE)[str(customer_id)] == 0
    pending = bot.private_bot_store().list_bots()
    assert len(pending) == 1 and pending[0]["state"] == "payment_pending"
    request_id = pending[0]["id"]

    assert bot.callback_permission("admin_private_bots") == "user_messages"
    assert bot.callback_permission(f"admin_privatebot_approve_{request_id}") == "user_messages"

    admin_query = FakeQuery(f"admin_privatebot_approve_{request_id}", admin_id, "מנהל")
    admin_update = SimpleNamespace(callback_query=admin_query, effective_user=admin_query.from_user)
    admin_context = SimpleNamespace(bot=fake_bot, user_data={})
    await bot.admin_private_bot_approve(admin_update, admin_context)
    approved = bot.private_bot_store().get(request_id)
    assert approved["state"] == "approved_waiting_token"
    assert any(item["chat_id"] == customer_id for item in fake_bot.sent)

    token_query = FakeQuery(f"privatebot_token_{request_id}", customer_id)
    token_update = SimpleNamespace(callback_query=token_query, effective_user=token_query.from_user)
    token_context = SimpleNamespace(bot=fake_bot, user_data={})
    state = await bot.private_bot_token_start(token_update, token_context)
    assert state == bot.PRIVATE_BOT_TOKEN

    bot.Bot = FakeCandidateBot
    raw_token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWX"
    token_message = FakeMessage(raw_token)
    token_message_update = SimpleNamespace(
        message=token_message,
        effective_user=SimpleNamespace(id=customer_id),
        effective_chat=SimpleNamespace(id=customer_id),
    )
    state = await bot.private_bot_token_receive(token_message_update, token_context)
    assert state == bot.ConversationHandler.END
    assert token_message.deleted is True
    configured = bot.private_bot_store().get(request_id)
    assert configured["state"] == "configured_waiting_media_sync"
    assert configured["token_encrypted"] != raw_token
    assert raw_token not in bot.PRIVATE_BOTS_FILE.read_text(encoding="utf-8")
    assert bot.private_bot_store(TokenCipher()).decrypted_token(request_id) == raw_token


def main():
    os.environ["PRIVATE_BOTS_MASTER_KEY"] = TokenCipher.generate_key()
    bot = load_bot_module()
    with tempfile.TemporaryDirectory(prefix="private_bot_purchase_") as directory:
        asyncio.run(test_flow(bot, Path(directory)))
    print("Private bot purchase integration test passed.")


if __name__ == "__main__":
    main()
