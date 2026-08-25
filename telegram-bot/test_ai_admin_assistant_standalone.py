"""Safe, offline tests for the standalone Hebrew admin assistant contract."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("standalone_ai", ROOT / "ai_admin_assistant_standalone.py")
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def main() -> None:
    adapter = module.DryRunAdapter()
    assistant = module.HebrewAdminAssistant(adapter)
    manager = module.ManagerContext(
        user_id="manager",
        permissions={"assistant", "coins", "users", "user_messages", "maintenance"},
        assistant_capabilities={"coins", "users", "user_messages", "maintenance"},
    )

    assistant.plan = lambda text: {
        "kind": "actions",
        "clarification": None,
        "unsupported_reason": None,
        "actions": [
            {"action": "set_daily_gift", "parameters": {"amount": 2}},
            {"action": "set_referral_reward", "parameters": {"amount": 3}},
        ],
    }
    result = assistant.handle(manager, "תן 2 במתנה ו-3 על הזמנה")
    assert result.status == "executed"
    assert len(adapter.calls) == 2

    assistant.plan = lambda text: {
        "kind": "actions",
        "clarification": None,
        "unsupported_reason": None,
        "actions": [{"action": "send_user_message", "parameters": {"user_id": "123", "message": "שלום"}}],
    }
    result = assistant.handle(manager, "שלח למשתמש הודעה")
    assert result.status == "executed"
    assert adapter.calls[-1]["action"] == "send_user_message"

    restricted = module.ManagerContext(
        user_id="restricted",
        permissions={"assistant", "coins"},
        assistant_capabilities={"coins"},
    )
    result = assistant.handle(restricted, "שלח הודעה")
    assert result.status == "forbidden"
    assert len(adapter.calls) == 3

    assistant.plan = lambda text: {
        "kind": "actions",
        "clarification": None,
        "unsupported_reason": None,
        "actions": [{"action": "delete_videos", "parameters": {}}],
    }
    owner = module.build_demo_manager()
    result = assistant.handle(owner, "מחק את כל הסרטונים")
    assert result.status == "confirmation_required"
    assert len(adapter.calls) == 3
    result = assistant.confirm_pending(owner)
    assert result.status == "executed"
    assert len(adapter.calls) == 4

    assistant.plan = lambda text: {
        "kind": "unsupported",
        "clarification": None,
        "unsupported_reason": "אין פונקציה לשינוי צבע כפתור",
        "actions": [],
    }
    result = assistant.handle(owner, "שנה צבע כפתור")
    assert result.status == "unsupported"

    print("Standalone AI assistant tests passed.")


if __name__ == "__main__":
    main()
