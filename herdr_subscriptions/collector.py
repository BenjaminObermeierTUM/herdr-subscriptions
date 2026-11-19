"""Independent provider refreshes with a private last-good cache."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
import fcntl
import os
import time

from . import storage
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
        old = {s["provider"]: s for s in storage.cached()["subscriptions"]}
        providers = config["providers"]
        def fetch(provider):
            previous = old.get(provider, {})
            if not force and now - previous.get("attempted_at", 0) < config["refresh_seconds"]:
                return previous
            try:
                value = FETCHERS[provider]()
                value.update({"fetched_at": time.time(), "attempted_at": time.time()})
                return value
            except ProviderError as exc:
                return failed(previous, provider, str(exc), time.time())
            except Exception:
                # Third-party errors may contain account IDs, paths or tokens.
                return failed(previous, provider, "usage request failed; check CLI/sign-in", time.time())
        with ThreadPoolExecutor(max_workers=2) as executor:
            subscriptions = list(executor.map(fetch, providers))
        snapshot = {"schema": 1, "subscriptions": subscriptions}
        for sub in subscriptions:
            if sub["provider"] == "claude" and not sub.get("error"):
                before = exhausted_windows(old.get("claude", {}), now)
                if exhausted_windows(sub, now) - before:
                    request_reset_inventory(root, time.time())
        storage.write_json(root / "usage.json", snapshot)
        return storage.cached()
