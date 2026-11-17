"""Independent provider refreshes with a private last-good cache."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import fcntl
import time

from . import storage
from .providers import FETCHERS, ProviderError


def failed(previous, provider, error, now):
    value = dict(previous or {"provider": provider, "label": provider.title(), "plan": "", "limits": []})
    value.update({"error": error, "attempted_at": now})
    return value


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
        storage.write_json(root / "usage.json", snapshot)
        return snapshot
