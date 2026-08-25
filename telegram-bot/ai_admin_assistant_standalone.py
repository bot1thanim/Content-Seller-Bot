"""Standalone Hebrew admin AI assistant prototype.

This module is intentionally decoupled from bot.py. It parses natural-language
Hebrew requests into a strict action plan, checks permissions, asks for
confirmation on dangerous actions, and executes only through an injected
adapter. It never executes Python/JSON supplied by the model.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

try:
    from openai import OpenAI
except ImportError:  # Optional until explicitly connected to a deployment.
    OpenAI = None  # type: ignore


MODEL = os.getenv("ADMIN_ASSISTANT_MODEL", "gpt-5-mini")

ACTION_PERMISSIONS = {
    "set_daily_gift": "coins",
    "set_referral_reward": "coins",
    "get_user": "users",
    "send_user_message": "user_messages",
    "approve_purchase": "user_messages",
    "add_coins": "coins",
    "remove_coins": "coins",
    "set_vip": "coins",
    "get_statistics": "users",
    "set_maintenance": "maintenance",
    "find_duplicates": "duplicates",
    "send_videos": "gallery",
    "delete_videos": "dangerous_delete",
    "restore_backup": "backup",
}

DANGEROUS_ACTIONS = {
    "delete_videos",
    "restore_backup",
    "set_maintenance",
}

ACTION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "admin_action_plan",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "kind": {"type": "string", "enum": ["actions", "clarification", "unsupported"]},
                "clarification": {"type": ["string", "null"]},
                "unsupported_reason": {"type": ["string", "null"]},
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "action": {"type": "string", "enum": sorted(ACTION_PERMISSIONS)},
                            "parameters": {"type": "object", "additionalProperties": True},
                        },
                        "required": ["action", "parameters"],
                    },
                },
            },
            "required": ["kind", "clarification", "unsupported_reason", "actions"],
        },
    },
}

SYSTEM_PROMPT = """אתה שכבת הבנת כוונה לעוזר ניהול של בוט Telegram בעברית.
החזר JSON בלבד לפי הסכמה שסופקה. בחר רק פעולות מהרשימה. אל תמציא פעולות.
זהה מספר משתמש בכל ניסוח עברי. אפשר להחזיר כמה פעולות בהודעה אחת.
אל תבצע פעולה ואל תשנה נתונים; רק בנה תכנית מובנית.
אם חסר מידע מהותי, החזר clarification. אם הפעולה אינה קיימת, החזר unsupported.
"""


@dataclass
class ManagerContext:
    user_id: str
    permissions: set[str]
    assistant_capabilities: set[str] = field(default_factory=set)
    is_owner: bool = False

    def can(self, permission: str) -> bool:
        return self.is_owner or (
            permission in self.permissions and
            permission in self.assistant_capabilities
        )


@dataclass
class AssistantResult:
    status: str
    message: str
    plan: dict[str, Any] | None = None
    results: list[dict[str, Any]] = field(default_factory=list)


class ActionAdapter:
    """Interface expected from the real bot integration later."""

    def execute(self, action: str, parameters: dict[str, Any], actor_id: str) -> dict[str, Any]:
        raise NotImplementedError


class DryRunAdapter(ActionAdapter):
    """Safe adapter for testing: records intent but never changes bot data."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute(self, action: str, parameters: dict[str, Any], actor_id: str) -> dict[str, Any]:
        call = {"action": action, "parameters": parameters, "actor_id": actor_id}
        self.calls.append(call)
        return {"ok": True, "dry_run": True, "action": action, "parameters": parameters}


class HebrewAdminAssistant:
    """Natural-language planner with strict permission and confirmation gates."""

    def __init__(self, adapter: ActionAdapter, client: Any = None, model: str = MODEL):
        self.adapter = adapter
        self.client = client
        self.model = model
        self.pending_plans: dict[str, dict[str, Any]] = {}

    def _get_client(self):
        if self.client is not None:
            return self.client
        if OpenAI is None:
            raise RuntimeError("openai package is not installed")
        self.client = OpenAI()
        return self.client

    def plan(self, text: str) -> dict[str, Any]:
        clean = text.strip()
        if not clean:
            return {"kind": "clarification", "clarification": "מה תרצה שאבצע?", "unsupported_reason": None, "actions": []}
        response = self._get_client().chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": clean},
            ],
            response_format=ACTION_SCHEMA,
            max_completion_tokens=1800,
        )
        content = response.choices[0].message.content
        result = json.loads(content)
        self._validate_plan(result)
        return result

    @staticmethod
    def _validate_plan(plan: dict[str, Any]) -> None:
        if plan.get("kind") not in {"actions", "clarification", "unsupported"}:
            raise ValueError("invalid intent kind")
        for item in plan.get("actions", []):
            if item.get("action") not in ACTION_PERMISSIONS:
                raise ValueError("model returned an unsupported action")
            if not isinstance(item.get("parameters"), dict):
                raise ValueError("action parameters must be an object")

    def handle(self, manager: ManagerContext, text: str, confirm: bool = False) -> AssistantResult:
        if not manager.is_owner and "assistant" not in manager.permissions:
            return AssistantResult("forbidden", "❌ אין לך הרשאה להשתמש בעוזר.")
        try:
            plan = self.plan(text)
        except Exception as exc:
            return AssistantResult("error", f"❌ העוזר לא הצליח לנתח את הבקשה: {exc}")
        return self._apply_plan(manager, plan, confirm=confirm)

    def confirm_pending(self, manager: ManagerContext) -> AssistantResult:
        plan = self.pending_plans.pop(manager.user_id, None)
        if not plan:
            return AssistantResult("none", "אין פעולה שממתינה לאישור.")
        return self._apply_plan(manager, plan, confirm=True)

    def cancel_pending(self, manager: ManagerContext) -> AssistantResult:
        self.pending_plans.pop(manager.user_id, None)
        return AssistantResult("cancelled", "❌ הפעולה בוטלה ולא בוצע שינוי.")

    def _apply_plan(self, manager: ManagerContext, plan: dict[str, Any], confirm: bool) -> AssistantResult:
        if plan["kind"] == "clarification":
            return AssistantResult("clarification", f"❓ {plan.get('clarification') or 'נא לדייק את הבקשה.'}", plan)
        if plan["kind"] == "unsupported":
            return AssistantResult("unsupported", f"❌ הפעולה אינה זמינה: {plan.get('unsupported_reason') or 'אין פונקציה כזו בבוט.'}", plan)
        actions = plan.get("actions", [])
        if not actions:
            return AssistantResult("clarification", "❓ לא זוהתה פעולה לביצוע.", plan)

        denied = []
        for item in actions:
            action = item["action"]
            required = ACTION_PERMISSIONS[action]
            if not manager.can(required):
                denied.append((action, required))
        if denied:
            names = ", ".join(action for action, _ in denied)
            return AssistantResult("forbidden", f"❌ אין לך הרשאה לבצע: {names}.", plan)

        dangerous = [item["action"] for item in actions if item["action"] in DANGEROUS_ACTIONS]
        if dangerous and not confirm:
            self.pending_plans[manager.user_id] = plan
            return AssistantResult("confirmation_required", "⚠️ הבקשה כוללת פעולה מסוכנת. אשר או בטל לפני ביצוע.", plan)

        results = []
        for item in actions:
            try:
                results.append(self.adapter.execute(item["action"], item["parameters"], manager.user_id))
            except Exception as exc:
                results.append({"ok": False, "action": item["action"], "error": str(exc)})
        succeeded = sum(1 for result in results if result.get("ok"))
        if succeeded == len(results):
            status = "executed"
            message = f"✅ בוצעו בהצלחה {succeeded} פעולות."
        elif succeeded:
            status = "partial"
            message = f"⚠️ בוצעו {succeeded} מתוך {len(results)} פעולות; השאר נכשלו."
        else:
            status = "failed"
            message = "❌ אף פעולה לא בוצעה בהצלחה."
        return AssistantResult(status, message, plan, results)


def build_demo_manager() -> ManagerContext:
    permissions = set(ACTION_PERMISSIONS.values()) | {"assistant"}
    return ManagerContext("demo-manager", permissions, set(ACTION_PERMISSIONS.values()))


if __name__ == "__main__":
    print("Standalone assistant module created. Use test_ai_admin_assistant_standalone.py for safe dry-run tests.")
