"""Eligibility and user-initiated use of a saved subscription reset."""

import fcntl
import hashlib
import sys
import time
import json
import uuid

from . import providers, storage


def current_count(sub, now):
    resets = sub.get("resets") or {}
    count = resets.get("available")
    if type(count) is not int or count < 0:
        return None
    if sub.get("provider") == "claude":
        if resets.get("source") != "chrome-usage":
            return None
        checked = resets.get("checked_at", 0)
    else:
        if sub.get("error"):
            return None
        checked = sub.get("fetched_at", 0)
    max_age = resets.get("interval", 86400) + 90 if sub.get("provider") == "claude" else 90
    return count if 0 <= now - checked <= max_age else None


def eligible_limit(sub, now):
    if sub.get("error") or not 0 <= now - sub.get("fetched_at", 0) <= 90 or not current_count(sub, now):
        return None
    for row in sub.get("limits", []):
        if (row.get("used_percent") or 0) < 100 or (row.get("resets_at") is not None and row["resets_at"] <= now):
            continue
        if sub["provider"] == "codex":
            if row["key"] in ("codex.primary", "codex.secondary") and row.get("reached") in (None, "", "rate_limit_reached"):
                return row
        elif row["key"] != "extra":
            scope = {"5h": "session", "Weekly": "weekly", "Fable": "fable"}.get(row["label"])
            kinds = {"session": {"five_hour", "full", "fable"}, "weekly": {"weekly", "full"},
                     "fable": {"full", "fable"}}
            inventory = sub["resets"]
            if scope in inventory.get("exhausted", []) and kinds.get(scope, set()).intersection(inventory["usable"]):
                return row
    return None


def attempt_key(root, account, window):
    # Retrying a timeout or double-click for the same period must reuse the key.
    signature = json.dumps([account, window["key"], window.get("resets_at")]).encode()
    fingerprint = hashlib.sha256(signature).hexdigest()
    path = root / "reset-attempt.json"
    previous = storage.read_json(path, {})
    if previous.get("window") == fingerprint:
        return previous["idempotency_key"]
    key = str(uuid.uuid4())
    storage.write_json(path, {"window": fingerprint, "idempotency_key": key})
    return key


def confirm_codex():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("Open the reset confirmation in an interactive terminal")
    root = storage.private_dir(storage.state_dir())
    with (root / "redeem.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("A reset confirmation is already open.")
            return
        try:
            with providers.codex_session() as client:
                sub = providers.codex_usage(client)
                sub["fetched_at"] = time.time()
                account = client.usage_account
                window = eligible_limit(sub, time.time())
                if not window:
                    print("No exhausted main Codex limit with an available saved reset.")
                else:
                    print(f"Codex {sub['plan'].title()} · {window['label']} at 100% · {sub['resets']['available']} saved resets")
                    if input("Use one saved reset now? [y/N] ").strip().lower() != "y":
                        return
                    # Recheck in this same signed-in client immediately before redeeming.
                    live = providers.codex_usage(client, request_id=3)
                    live["fetched_at"] = time.time()
                    latest = eligible_limit(live, time.time())
                    if (not latest or client.usage_account != account or live["plan"] != sub["plan"]
                            or (latest["key"], latest["resets_at"]) != (window["key"], window["resets_at"])):
                        print("That limit changed. Reopen the confirmation to review current usage.")
                    else:
                        client.send({"id": 4, "method": "account/rateLimitResetCredit/consume", "params": {
                            "idempotencyKey": attempt_key(root, account, latest)}})
                        result = client.receive(lambda message: message.get("id") == 4)
                        outcome = (result.get("result") or {}).get("outcome")
                        print({"reset": "Reset applied.", "alreadyRedeemed": "This reset was already applied.",
                               "noCredit": "No saved resets remain.", "nothingToReset": "No eligible limit to reset."}
                              .get(outcome, "Reset unconfirmed. Check Codex Usage before trying again."))
        except (EOFError, KeyboardInterrupt):
            return
        except providers.ProviderError as exc:
            print(f"Could not confirm the reset: {exc}. Check Codex Usage.")
        from . import daemon
        daemon.request_refresh()
        input("Press Enter to close. ")
