"""Offline tests for bot.py's optional AI intent rewrite layer."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("bot_ai_integration_test", ROOT / "bot.py")
bot = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bot
SPEC.loader.exec_module(bot)


class FakeCompletions:
    def __init__(self, payload):
        self.payload = payload

    def create(self, **kwargs):
        message = SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self, payload):
        self.chat = SimpleNamespace(completions=FakeCompletions(payload))


async def run() -> None:
    old_key = os.environ.get("OPENAI_API_KEY")
    old_gemini_key = os.environ.get("GEMINI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "test-only"
    os.environ.pop("GEMINI_API_KEY", None)
    try:
        bot.OpenAI = lambda: FakeClient({
            "kind": "rewrite",
            "canonical_text": "שלח סרטונים מ-10 עד 20 שניות",
            "reply": None,
        })
        rewritten, reply = await bot._assistant_ai_rewrite("תביא לי וידאו מעשר עד עשרים", 1)
        assert rewritten == "שלח סרטונים מ-10 עד 20 שניות"
        assert reply is None

        bot.OpenAI = lambda: FakeClient({
            "kind": "clarification",
            "canonical_text": None,
            "reply": "איזה משתמש תרצה לבדוק?",
        })
        rewritten, reply = await bot._assistant_ai_rewrite("תבדוק אותו", 1)
        assert rewritten is None
        assert reply == "איזה משתמש תרצה לבדוק?"

        bot.OpenAI = lambda: FakeClient({
            "kind": "answer",
            "canonical_text": None,
            "reply": "🤖 כן. אני יכול לענות גם על שאלות כלליות בעברית, בלי לבצע פעולה בבוט.",
        })
        rewritten, reply = await bot._assistant_ai_rewrite("מה ההבדל בין גיבוי לשחזור?", 1)
        assert rewritten is None
        assert "שאלות כלליות" in reply

        os.environ["GEMINI_API_KEY"] = "gemini-test-only"
        bot._assistant_gemini_payload = lambda message: {
            "kind": "answer",
            "canonical_text": None,
            "reply": "🤖 תשובת Gemini חופשית בעברית.",
        }
        rewritten, reply = await bot._assistant_ai_rewrite("ספר לי משהו כללי", 1)
        assert rewritten is None
        assert "Gemini" in reply
    finally:
        if old_gemini_key is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = old_gemini_key
        if old_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = old_key


if __name__ == "__main__":
    asyncio.run(run())
    print("AI intent integration tests passed.")
