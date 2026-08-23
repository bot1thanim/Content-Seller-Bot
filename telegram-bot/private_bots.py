"""Private-bot domain model for Content-Seller-Bot.

This module deliberately contains no Telegram handlers.  It is the safe, testable
core used by the main bot to sell, approve, activate, and account for private
child bots.  All customer and reward data remains global; a child bot is only a
separate Telegram identity and entry point.

IMPORTANT: raw bot tokens must never be stored in JSON, source code, logs,
orders, backups, or audit details.  They are encrypted with a deployment-only
Fernet key before persistence.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

PRIVATE_BOT_CREATION_COINS = 400
PRIVATE_BOT_CREATION_PAYPAL_NIS = 40.0
PRIVATE_BOTS_SCHEMA_VERSION = 1
MAIN_BOT_ID = "main"
TOKEN_ENV_NAME = "PRIVATE_BOTS_MASTER_KEY"

# Intentionally strict. Telegram bot tokens have a numeric bot id, a colon and
# a secret. Validation is only a first safety check; get_me() is required later.
BOT_TOKEN_PATTERN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{20,}$")


class PrivateBotError(ValueError):
    """Raised for invalid private-bot workflow transitions."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def validate_bot_token(token: str) -> str:
    """Validate the token shape without retaining it in an error message."""
    normalized = (token or "").strip()
    if not BOT_TOKEN_PATTERN.fullmatch(normalized):
        raise PrivateBotError("טוקן הבוט אינו בפורמט תקין.")
    return normalized


def token_fingerprint(token: str) -> str:
    """Return a non-reversible diagnostic fingerprint; never return the token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


class TokenCipher:
    """Encryption boundary for child-bot tokens persisted by the service."""

    def __init__(self, key: str | bytes | None = None):
        raw_key = key or os.environ.get(TOKEN_ENV_NAME)
        if not raw_key:
            raise PrivateBotError(
                f"חסר מפתח הצפנה ב־{TOKEN_ENV_NAME}; אי אפשר לשמור טוקן בוט פרטי בבטחה."
            )
        try:
            self._fernet = Fernet(raw_key.encode("utf-8") if isinstance(raw_key, str) else raw_key)
        except (ValueError, TypeError) as exc:
            raise PrivateBotError("מפתח ההצפנה של הבוטים הפרטיים אינו תקין.") from exc

    @staticmethod
    def generate_key() -> str:
        """Generate a deploy-time Fernet key. The caller must place it in secrets."""
        return Fernet.generate_key().decode("ascii")

    def encrypt(self, token: str) -> str:
        return self._fernet.encrypt(validate_bot_token(token).encode("utf-8")).decode("ascii")

    def decrypt(self, encrypted_token: str) -> str:
        try:
            token = self._fernet.decrypt(encrypted_token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, AttributeError) as exc:
            raise PrivateBotError("לא ניתן לפענח את טוקן הבוט הפרטי השמור.") from exc
        return validate_bot_token(token)


class PrivateBotStore:
    """Persistent workflow state for private bots, stored separately from settings."""

    def __init__(self, path: Path, cipher: TokenCipher | None = None):
        self.path = Path(path)
        self.cipher = cipher

    @staticmethod
    def empty() -> dict[str, Any]:
        return {"schema_version": PRIVATE_BOTS_SCHEMA_VERSION, "bots": {}}

    def load(self) -> dict[str, Any]:
        data = _read_json(self.path, self.empty())
        if not isinstance(data, dict):
            raise PrivateBotError("קובץ נתוני הבוטים הפרטיים אינו תקין.")
        data.setdefault("schema_version", PRIVATE_BOTS_SCHEMA_VERSION)
        data.setdefault("bots", {})
        if not isinstance(data["bots"], dict):
            raise PrivateBotError("רשימת הבוטים הפרטיים אינה תקינה.")
        return data

    def save(self, data: dict[str, Any]) -> None:
        _write_json_atomic(self.path, data)

    def list_bots(self) -> list[dict[str, Any]]:
        return sorted(self.load()["bots"].values(), key=lambda item: item["created_at"])

    def get(self, private_bot_id: str) -> dict[str, Any] | None:
        return self.load()["bots"].get(private_bot_id)

    def create_paid_request(self, creator_id: int, payment_method: str) -> dict[str, Any]:
        if payment_method not in {"coins", "paypal"}:
            raise PrivateBotError("אמצעי התשלום אינו תקין.")
        data = self.load()
        private_bot_id = uuid.uuid4().hex
        record = {
            "id": private_bot_id,
            "creator_id": int(creator_id),
            # Both PayPal and coin purchases require a human approval before a token is accepted.
            "state": "payment_pending",
            "payment_method": payment_method,
            "coins_price": PRIVATE_BOT_CREATION_COINS,
            "paypal_price_nis": PRIVATE_BOT_CREATION_PAYPAL_NIS,
            "created_at": utc_now(),
            "approved_at": None,
            "approved_by": None,
            "token_encrypted": None,
            "token_fingerprint": None,
            "telegram_bot_id": None,
            "bot_username": None,
            "activated_at": None,
            "activation_error": None,
            "new_global_visitors": 0,
            "owner_visit_rewards": 0,
            "visited_user_ids": [],
        }
        data["bots"][private_bot_id] = record
        self.save(data)
        return record

    def approve_paypal_request(self, private_bot_id: str, admin_id: int) -> dict[str, Any]:
        data = self.load()
        record = data["bots"].get(private_bot_id)
        if not record:
            raise PrivateBotError("בקשת יצירת הבוט לא נמצאה.")
        if record["state"] != "payment_pending":
            raise PrivateBotError("בקשה זו אינה ממתינה לאישור תשלום.")
        record["state"] = "approved_waiting_token"
        record["approved_at"] = utc_now()
        record["approved_by"] = int(admin_id)
        data["bots"][private_bot_id] = record
        self.save(data)
        return record

    def reject_or_cancel(self, private_bot_id: str, admin_id: int, reason: str) -> dict[str, Any]:
        data = self.load()
        record = data["bots"].get(private_bot_id)
        if not record:
            raise PrivateBotError("בקשת יצירת הבוט לא נמצאה.")
        if record["state"] not in {"payment_pending", "approved_waiting_token"}:
            raise PrivateBotError("אי אפשר לבטל בוט שכבר הופעל או הושבת.")
        record["state"] = "cancelled"
        record["cancelled_at"] = utc_now()
        record["cancelled_by"] = int(admin_id)
        record["cancellation_reason"] = (reason or "בוטל על ידי מנהל")[:300]
        data["bots"][private_bot_id] = record
        self.save(data)
        return record

    def save_token_after_validation(
        self,
        private_bot_id: str,
        token: str,
        telegram_bot_id: int,
        bot_username: str | None,
    ) -> dict[str, Any]:
        if self.cipher is None:
            raise PrivateBotError("אין הצפנה מוגדרת לשמירת טוקני בוטים פרטיים.")
        safe_token = validate_bot_token(token)
        data = self.load()
        record = data["bots"].get(private_bot_id)
        if not record:
            raise PrivateBotError("בקשת יצירת הבוט לא נמצאה.")
        if record["state"] != "approved_waiting_token":
            raise PrivateBotError("הבוט אינו ממתין לטוקן מאושר.")
        if any(
            item.get("telegram_bot_id") == int(telegram_bot_id) and item["id"] != private_bot_id
            for item in data["bots"].values()
        ):
            raise PrivateBotError("הבוט הזה כבר מחובר למערכת.")
        record.update(
            {
                "state": "configured_waiting_media_sync",
                "token_encrypted": self.cipher.encrypt(safe_token),
                "token_fingerprint": token_fingerprint(safe_token),
                "telegram_bot_id": int(telegram_bot_id),
                "bot_username": (bot_username or "").lstrip("@") or None,
                "token_saved_at": utc_now(),
                "activation_error": None,
            }
        )
        data["bots"][private_bot_id] = record
        self.save(data)
        return record

    def activate_after_media_sync(self, private_bot_id: str) -> dict[str, Any]:
        data = self.load()
        record = data["bots"].get(private_bot_id)
        if not record:
            raise PrivateBotError("הבוט הפרטי לא נמצא.")
        if record["state"] != "configured_waiting_media_sync":
            raise PrivateBotError("אי אפשר להפעיל בוט לפני סנכרון המדיה.")
        record["state"] = "active"
        record["activated_at"] = utc_now()
        record["activation_error"] = None
        data["bots"][private_bot_id] = record
        self.save(data)
        return record

    def set_activation_error(self, private_bot_id: str, message: str) -> dict[str, Any]:
        data = self.load()
        record = data["bots"].get(private_bot_id)
        if not record:
            raise PrivateBotError("הבוט הפרטי לא נמצא.")
        record["state"] = "activation_failed"
        record["activation_error"] = (message or "שגיאה לא ידועה")[:500]
        data["bots"][private_bot_id] = record
        self.save(data)
        return record

    def decrypted_token(self, private_bot_id: str) -> str:
        record = self.get(private_bot_id)
        if not record or not record.get("token_encrypted"):
            raise PrivateBotError("אין טוקן שמור לבוט זה.")
        if self.cipher is None:
            raise PrivateBotError("אין הצפנה מוגדרת לקריאת טוקני בוטים פרטיים.")
        return self.cipher.decrypt(record["token_encrypted"])

    def register_global_visit(self, private_bot_id: str, user_id: int) -> bool:
        """Record a unique global-new visit reward for an active private bot.

        Returns True only when the creator should receive the one-coin owner
        reward. The caller is responsible for adding that coin to the global
        wallet, preserving a single transactional accounting point.
        """
        data = self.load()
        record = data["bots"].get(private_bot_id)
        if not record or record.get("state") != "active":
            return False
        visitor_id = str(int(user_id))
        if int(user_id) == int(record["creator_id"]):
            return False
        visitors = set(record.get("visited_user_ids", []))
        if visitor_id in visitors:
            return False
        visitors.add(visitor_id)
        record["visited_user_ids"] = sorted(visitors, key=int)
        record["new_global_visitors"] = int(record.get("new_global_visitors", 0)) + 1
        record["owner_visit_rewards"] = int(record.get("owner_visit_rewards", 0)) + 1
        data["bots"][private_bot_id] = record
        self.save(data)
        return True


def create_global_user_record(user: Any) -> dict[str, Any]:
    """Create the core, bot-independent user record used by all bot identities."""
    return {
        "id": int(user.id),
        "first_name": getattr(user, "first_name", None),
        "username": getattr(user, "username", None),
        "joined": utc_now()[:10],
        "purchases": 0,
        "total_spent": 0,
        "seen_videos": [],
        "last_bonus": None,
        "visited_bot_ids": [],
        "first_seen_bot_id": None,
    }


def register_global_user(
    users: dict[str, Any],
    referrals: dict[str, Any],
    coins: dict[str, Any],
    user: Any,
    referrer_id: int | None,
    source_bot_id: str,
    private_store: PrivateBotStore | None = None,
) -> dict[str, Any]:
    """Register a start across the entire bot family and apply only valid rewards.

    A person is globally new only once, irrespective of which bot they start
    first. When that first start is inside an active private bot, its creator
    receives one coin. A valid referrer receives one additional coin. If the
    creator is also the referrer, both deliberate rewards are paid.
    """
    uid = str(int(user.id))
    globally_new = uid not in users
    if globally_new:
        users[uid] = create_global_user_record(user)

    user_data = users[uid]
    visits = set(user_data.get("visited_bot_ids", []))
    visits.add(source_bot_id or MAIN_BOT_ID)
    user_data["visited_bot_ids"] = sorted(visits)
    user_data.setdefault("first_seen_bot_id", source_bot_id or MAIN_BOT_ID)

    result = {
        "globally_new": globally_new,
        "referral_rewarded": False,
        "owner_visit_rewarded": False,
        "referrer_id": None,
        "private_bot_creator_id": None,
    }
    if not globally_new:
        return result

    if referrer_id is not None and int(referrer_id) != int(user.id):
        ref_key = str(int(referrer_id))
        if ref_key in users:
            ref_data = referrals.setdefault(ref_key, {"count": 0, "referred_ids": []})
            ref_data.setdefault("referred_ids", [])
            if uid not in ref_data["referred_ids"]:
                ref_data["referred_ids"].append(uid)
                ref_data["count"] = int(ref_data.get("count", 0)) + 1
                coins[ref_key] = int(coins.get(ref_key, 0)) + 1
                result["referral_rewarded"] = True
                result["referrer_id"] = int(referrer_id)

    if source_bot_id != MAIN_BOT_ID and private_store is not None:
        private_record = private_store.get(source_bot_id)
        if private_record and private_store.register_global_visit(source_bot_id, int(user.id)):
            creator_key = str(int(private_record["creator_id"]))
            coins[creator_key] = int(coins.get(creator_key, 0)) + 1
            result["owner_visit_rewarded"] = True
            result["private_bot_creator_id"] = int(private_record["creator_id"])

    return result


def private_bot_public_view(record: dict[str, Any]) -> dict[str, Any]:
    """A copy safe for buttons, lists, logs and manager-facing status screens."""
    excluded = {"token_encrypted"}
    return {key: value for key, value in record.items() if key not in excluded}
