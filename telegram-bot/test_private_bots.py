"""Regression tests for the private-bot domain model.

Run with: python3 test_private_bots.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from private_bots import (
    MAIN_BOT_ID,
    PRIVATE_BOT_CREATION_COINS,
    PRIVATE_BOT_CREATION_PAYPAL_NIS,
    PrivateBotError,
    PrivateBotStore,
    TokenCipher,
    register_global_user,
)


def make_user(user_id: int, name: str = "בדיקה"):
    return SimpleNamespace(id=user_id, first_name=name, username=f"user_{user_id}")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_token_encryption_and_workflow(tmp: Path):
    cipher = TokenCipher(TokenCipher.generate_key())
    store = PrivateBotStore(tmp / "private_bots.json", cipher)
    request = store.create_paid_request(100, "paypal")
    assert request["state"] == "payment_pending"
    assert request["coins_price"] == PRIVATE_BOT_CREATION_COINS
    assert request["paypal_price_nis"] == PRIVATE_BOT_CREATION_PAYPAL_NIS

    approved = store.approve_paypal_request(request["id"], admin_id=999)
    assert approved["state"] == "approved_waiting_token"
    configured = store.save_token_after_validation(
        approved["id"], "123456789:ABCDEFGHIJKLMNOPQRSTUVWX", 123456789, "my_private_bot"
    )
    assert configured["state"] == "configured_waiting_media_sync"
    assert configured["token_encrypted"] != "123456789:ABCDEFGHIJKLMNOPQRSTUVWX"
    assert store.decrypted_token(approved["id"]) == "123456789:ABCDEFGHIJKLMNOPQRSTUVWX"
    persisted = read_json(tmp / "private_bots.json")
    assert "ABCDEFGHIJKLMNOPQRSTUVWX" not in json.dumps(persisted)

    active = store.activate_after_media_sync(approved["id"])
    assert active["state"] == "active"


def test_global_new_user_rewards(tmp: Path):
    cipher = TokenCipher(TokenCipher.generate_key())
    store = PrivateBotStore(tmp / "private_bots.json", cipher)
    paid = store.create_paid_request(10, "coins")
    assert paid["state"] == "payment_pending", "Coin purchase must wait for manager approval"
    paid = store.approve_paypal_request(paid["id"], admin_id=999)
    store.save_token_after_validation(
        paid["id"], "123456789:ABCDEFGHIJKLMNOPQRSTUVWX", 123456789, "creator_bot"
    )
    store.activate_after_media_sync(paid["id"])

    users = {
        "10": {"id": 10, "first_name": "יוצר", "seen_videos": [], "purchases": 0, "total_spent": 0},
        "20": {"id": 20, "first_name": "מפנה", "seen_videos": [], "purchases": 0, "total_spent": 0},
    }
    refs, coins = {}, {"10": 0, "20": 0}

    # A globally new visitor starts the creator's bot via the creator's referral link.
    result = register_global_user(users, refs, coins, make_user(30), 10, paid["id"], store)
    assert result["globally_new"] is True
    assert result["referral_rewarded"] is True
    assert result["owner_visit_rewarded"] is True
    assert coins["10"] == 2, "Creator must receive referral + private-bot visit reward"

    # The same person must never earn rewards again in the main bot or another start.
    result = register_global_user(users, refs, coins, make_user(30), 20, MAIN_BOT_ID, store)
    assert result["globally_new"] is False
    assert coins["10"] == 2 and coins["20"] == 0

    # A new visitor using another user's referral rewards both distinct recipients once.
    result = register_global_user(users, refs, coins, make_user(40), 20, paid["id"], store)
    assert result["globally_new"] is True
    assert result["referral_rewarded"] is True
    assert result["owner_visit_rewarded"] is True
    assert coins["20"] == 1
    assert coins["10"] == 3

    # The creator starting their own bot is not a monetized visitor.
    result = register_global_user(users, refs, coins, make_user(10), None, paid["id"], store)
    assert result["globally_new"] is False
    assert coins["10"] == 3


def test_invalid_transitions_and_token(tmp: Path):
    cipher = TokenCipher(TokenCipher.generate_key())
    store = PrivateBotStore(tmp / "private_bots.json", cipher)
    request = store.create_paid_request(100, "paypal")
    try:
        store.save_token_after_validation(request["id"], "not-a-token", 1, None)
    except PrivateBotError:
        pass
    else:
        raise AssertionError("An unapproved/invalid token must never be persisted")

    try:
        store.activate_after_media_sync(request["id"])
    except PrivateBotError:
        pass
    else:
        raise AssertionError("Activation must require approved token and media synchronization")


def main():
    with tempfile.TemporaryDirectory(prefix="private_bot_tests_") as directory:
        tmp = Path(directory)
        test_token_encryption_and_workflow(tmp / "a")
        test_global_new_user_rewards(tmp / "b")
        test_invalid_transitions_and_token(tmp / "c")
    print("All private bot domain tests passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"TEST FAILURE: {exc}", file=sys.stderr)
        raise
