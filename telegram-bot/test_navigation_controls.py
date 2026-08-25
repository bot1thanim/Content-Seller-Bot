"""Offline verification for visible back controls in management input flows."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("navigation_bot", ROOT / "bot.py")
bot = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bot
SPEC.loader.exec_module(bot)


class FakeQuery:
    def __init__(self, user_id=7706183809, data=""):
        self.from_user = SimpleNamespace(id=user_id)
        self.data = data
        self.edits = []
        self.message = SimpleNamespace(reply_text=self.reply_text)

    async def answer(self, *args, **kwargs):
        return None

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def reply_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


def has_back(markup) -> bool:
    return any(
        button.callback_data in {"back_admin", "admin_coins_menu"}
        for row in markup.inline_keyboard
        for button in row
    )


async def assert_back(handler, data=""):
    query = FakeQuery(data=data)
    result = await handler(SimpleNamespace(callback_query=query), SimpleNamespace(user_data={}))
    assert query.edits, handler.__name__
    assert has_back(query.edits[-1][1]["reply_markup"]), handler.__name__
    return result


async def run():
    bot.is_admin = lambda user_id: True
    bot.has_admin_permission = lambda user_id, permission: True
    await assert_back(bot.admin_check_start)
    await assert_back(bot.admin_send_start)
    await assert_back(bot.admin_approve_start)
    await assert_back(bot.admin_broadcast_start)
    await assert_back(bot.admin_vip_start)
    await assert_back(bot.admin_coins_start)
    await assert_back(bot.admin_coupon_new_start)
    await assert_back(bot.admin_coin_control_menu, "admin_coin_control")

    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    for name in ("check_conv", "send_conv", "approve_conv", "broadcast_conv", "coins_conv", "vip_conv", "coupon_new_conv", "support_reply_conv"):
        start = source.index(f"    {name} = ConversationHandler(")
        end = source.index("    )\n", start) + len("    )\n")
        assert "back_admin" in source[start:end], name


if __name__ == "__main__":
    asyncio.run(run())
    print("Navigation control tests passed.")
