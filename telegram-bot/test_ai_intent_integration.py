"""Offline tests for bot.py's optional AI intent rewrite layer."""
from __future__ import annotations

import asyncio
import base64
from email.message import Message
from urllib.error import HTTPError
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


class FakeGeminiResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


async def run() -> None:
    old_key = os.environ.get("OPENAI_API_KEY")
    old_gemini_key = os.environ.get("GEMINI_API_KEY")
    old_urlopen = bot.urllib.request.urlopen
    os.environ["OPENAI_API_KEY"] = "test-only"
    os.environ.pop("GEMINI_API_KEY", None)
    try:
        # The production assistant is Gemini-only: an OpenAI key alone must never trigger a fallback request.
        rewritten, reply = await bot._assistant_ai_rewrite("תביא לי וידאו מעשר עד עשרים", 1)
        assert rewritten is None and reply is None
        assert not bot._assistant_ai_enabled()

        os.environ["GEMINI_API_KEY"] = "gemini-test-only"
        captured = {}

        def fake_urlopen(request, timeout):
            if request.full_url.endswith("/v1beta/models?pageSize=100"):
                return FakeGeminiResponse({"models": [{"name": "models/gemini-3.7-flash", "supportedGenerationMethods": ["generateContent"]}]})
            captured["url"] = request.full_url
            captured["headers"] = {key.lower(): value for key, value in request.header_items()}
            captured["body"] = json.loads(request.data.decode("utf-8"))
            assert timeout == 25
            return FakeGeminiResponse({
                "candidates": [{
                    "content": {
                        "parts": [{
                            "text": json.dumps({
                                "kind": "answer",
                                "canonical_text": None,
                                "reply": "🤖 תשובת Gemini חופשית בעברית.",
                            }, ensure_ascii=False)
                        }]
                    }
                }]
            })

        bot.urllib.request.urlopen = fake_urlopen
        bot._ASSISTANT_MODEL_CACHE.clear()
        payload = bot._assistant_gemini_payload("ספר לי משהו כללי")
        assert payload["kind"] == "answer"
        assert "Gemini" in payload["reply"]
        assert captured["url"].endswith("/models/gemini-3.7-flash:generateContent")
        assert "gemini-test-only" not in captured["url"]
        assert captured["headers"]["x-goog-api-key"] == "gemini-test-only"
        declarations = captured["body"]["tools"][0]["functionDeclarations"]
        assert any(item["name"] == "set_rewards" for item in declarations)
        assert any(item["name"] == "send_user_message" for item in declarations)
        assert captured["body"]["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"

        rewritten, reply = await bot._assistant_ai_rewrite(
            "ספר לי משהו כללי", 1, runtime_context="מצב חי מורשה:\n- סרטונים במאגר: 4"
        )
        assert rewritten is None
        assert "Gemini" in reply
        assert "מצב חי מורשה" in captured["body"]["contents"][0]["parts"][0]["text"]

        def fake_reward_urlopen(request, timeout):
            return FakeGeminiResponse({
                "candidates": [{
                    "content": {"parts": [{"text": json.dumps({
                        "kind": "rewrite",
                        "canonical_text": "SET_REWARDS:3,2",
                        "reply": None,
                    }, ensure_ascii=False)}]}
                }]
            })

        bot.urllib.request.urlopen = fake_reward_urlopen
        rewritten, reply = await bot._assistant_ai_rewrite("תעשה מתנות 3 והפניות 2", 1)
        assert rewritten == "SET_REWARDS:3,2"
        assert reply is None
        def fake_fenced_reward_urlopen(request, timeout):
            return FakeGeminiResponse({
                "candidates": [{
                    "content": {"parts": [{"text": "```json\n{\"kind\": \"rewrite\", \"canonical_text\": \"SET_DAILY_GIFT:4\", \"reply\": null}\n```"}]}
                }]
            })

        bot.urllib.request.urlopen = fake_fenced_reward_urlopen
        rewritten, reply = await bot._assistant_ai_rewrite("שנה את המתנה ל-4", 1)
        assert rewritten == "SET_DAILY_GIFT:4" and reply is None
        malformed = bot._assistant_parse_structured_response("```json\n{not valid}\n```")
        assert malformed is None

        assert bot._assistant_action_steps("SET_REWARDS:3,2;;ADJUST_COINS:5:+1") == ["SET_REWARDS:3,2", "ADJUST_COINS:5:+1"]
        function_payload = bot._assistant_function_call_payload({"name": "set_rewards", "args": {"daily_gift": 3, "referral_reward": 2}})
        assert function_payload == {"kind": "rewrite", "canonical_text": "SET_REWARDS:3,2"}
        assert bot._assistant_function_call_payload({"name": "unknown", "args": {}}) is None

        def fake_function_call_urlopen(request, timeout):
            return FakeGeminiResponse({"candidates": [{"content": {"parts": [{"functionCall": {"name": "get_user_balance", "args": {"user_id": "55"}}}]}}]})

        bot.urllib.request.urlopen = fake_function_call_urlopen
        assert bot._assistant_gemini_payload("מה היתרה של משתמש 55") == {"kind": "rewrite", "canonical_text": "GET_USER_BALANCE:55"}

        def fake_multi_function_call_urlopen(request, timeout):
            return FakeGeminiResponse({"candidates": [{"content": {"parts": [
                {"functionCall": {"name": "get_user", "args": {"user_id": "55"}}},
                {"functionCall": {"name": "get_user_coin_history", "args": {"user_id": "55"}}},
            ]}}]})

        bot.urllib.request.urlopen = fake_multi_function_call_urlopen
        assert bot._assistant_gemini_payload("בדוק משתמש 55 והיסטוריה") == {"kind": "rewrite", "canonical_text": "GET_USER:55;;GET_USER_COIN_HISTORY:55"}
        fake_context = SimpleNamespace(user_data={})
        bot._assistant_append_history(fake_context, "user", "שנה את המתנה ל-5")
        bot._assistant_append_history(fake_context, "assistant", "המתנה עודכנה")
        assert "שנה את המתנה ל-5" in bot._assistant_history_context(fake_context)
        assert bot._assistant_action_steps("SET_DAILY_GIFT:5;;SET_REFERRAL_REWARD:3") == ["SET_DAILY_GIFT:5", "SET_REFERRAL_REWARD:3"]
        assert bot._assistant_risk_level("GET_USER_BALANCE:55") == "info"
        assert bot._assistant_risk_level("SET_DAILY_GIFT:5") == "normal"
        assert bot._assistant_risk_level("SEND_USER_MESSAGE:55:hello") == "dangerous"

        image_bytes = base64.b64encode(b"image-test").decode("ascii")
        def fake_image_urlopen(request, timeout):
            if request.full_url.endswith("/v1beta/models?pageSize=100"):
                return FakeGeminiResponse({"models": [{"name": "models/gemini-2.5-flash-image", "supportedGenerationMethods": ["generateContent"]}]})
            assert request.full_url.endswith("/v1beta/interactions")
            assert timeout == 60
            body = json.loads(request.data.decode("utf-8"))
            assert body["model"] == "gemini-2.5-flash-image"
            return FakeGeminiResponse({"outputs": [{"type": "image", "mime_type": "image/png", "data": image_bytes}]})

        bot.urllib.request.urlopen = fake_image_urlopen
        image, mime = bot._assistant_gemini_image("create a blue icon")
        assert image == b"image-test" and mime == "image/png"

        retry_count = {"value": 0}
        def fake_429_then_image(request, timeout):
            if request.full_url.endswith("/v1beta/models?pageSize=100"):
                return FakeGeminiResponse({"models": [{"name": "models/gemini-2.5-flash-image"}]})
            retry_count["value"] += 1
            if retry_count["value"] == 1:
                headers = Message()
                headers["Retry-After"] = "0"
                raise HTTPError(request.full_url, 429, "rate limited", headers, None)
            return FakeGeminiResponse({"outputs": [{"type": "image", "mime_type": "image/png", "data": image_bytes}]})

        original_sleep = bot.time.sleep
        bot.time.sleep = lambda _: None
        bot.urllib.request.urlopen = fake_429_then_image
        image, mime = bot._assistant_gemini_image("retry image")
        assert retry_count["value"] == 2 and image == b"image-test" and mime == "image/png"
        bot.time.sleep = original_sleep
        assert bot._assistant_explicit_coin_command("תוסיף 5 מטבעות למשתמש 123") == "ADJUST_COINS:123:+5"
        assert bot._assistant_explicit_coin_command("תוריד 4 coins למשתמש 42") == "ADJUST_COINS:42:-4"
        assert bot._assistant_explicit_image_command("צור תמונה של רובוט כחול") == "GENERATE_IMAGE:רובוט כחול"
        assert bot._assistant_explicit_image_command("generate image of a small blue robot") == "GENERATE_IMAGE:a small blue robot"
    finally:
        bot.urllib.request.urlopen = old_urlopen
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
