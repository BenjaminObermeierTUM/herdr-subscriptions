"""Independent provider refreshes with a private last-good cache."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
import fcntl
import os
import time

from . import launchers, storage
from .providers import FETCHERS, ProviderError


def failed(previous, provider, error, now):
    value = dict(previous or {"provider": provider, "label": provider.title(), "plan": "", "limits": []})
    value.update({"error": error, "attempted_at": now})
    return value


def exhausted_windows(sub, now):
    return {(m.get("key"), round(m["resets_at"]) if m.get("resets_at") else None)
            for m in sub.get("limits", []) if m.get("key") != "extra"
            and (m.get("used_percent") or 0) >= 100
            and (not m.get("resets_at") or m["resets_at"] > now)}


def request_reset_inventory(root, now):
    storage.write_json(root / "reset-request.json", {"requested_at": now})
    try:
        fd = os.open(root / "reset-events.fifo", os.O_WRONLY | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENXIO):
            return  # Chrome will see the durable request when it reconnects.
        raise
    try:
        os.write(fd, b"1")
    except BlockingIOError:
        pass  # A wake-up is already waiting.
    finally:
        os.close(fd)


def refresh(*, force=False):
    config = storage.settings()
    root = storage.private_dir(storage.state_dir())
    with (root / "refresh.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return storage.cached()
        now = time.time()
        entries = config["subscriptions"]
        old = {s["id"]: s for s in storage.cached()["subscriptions"]}

        def fetch(entry):
            provider = entry["provider"]
            previous = old.get(entry["id"], {})
            if previous and not force and now - previous.get("attempted_at", 0) < config["refresh_seconds"]:
                return previous
            try:
                if provider == "codex":
                    value = FETCHERS[provider](entry["config_dir"], previous=previous)
                else:
                    value = FETCHERS[provider](entry["config_dir"])
                value.update({"fetched_at": time.time(), "attempted_at": time.time()})
            except ProviderError as exc:
                value = failed(previous, provider, str(exc), time.time())
            except Exception:
                value = failed(previous, provider, "usage request failed; check CLI/sign-in", time.time())
            value.update(id=entry["id"], binding=entry["binding"], subscription_label=entry["label"])
            return value

        with ThreadPoolExecutor(max_workers=3) as executor:
            subscriptions = list(executor.map(fetch, entries))
        snapshot = {"schema": 1, "subscriptions": subscriptions}
        for entry, sub in zip(entries, subscriptions):
            sub["launch_command"] = launchers.command_for(entry)
            if sub["provider"] == "claude" and not sub.get("error"):
                key = sub["id"]
                if exhausted_windows(sub, now) - exhausted_windows(old.get(key, {}), now):
                    request_reset_inventory(storage.subscription_dir(entry), time.time())
        storage.write_json(root / "usage.json", snapshot)
        return storage.cached()
