"""Publish the local JSON feed consumed by the optional Herdr client section."""
from __future__ import annotations

import time

from . import storage
from .render import subscription_rows


def section_document(snapshot, now):
    rows = []
    subscriptions = snapshot.get("subscriptions", [])
    for index, subscription in enumerate(subscriptions):
        if index:
            rows.append({"text": "", "kind": "text"})
        for value in subscription_rows(subscription, now, columns=32, cells=6,
                                       stale_seconds=storage.settings()["stale_seconds"], native=True):
            kind = ("critical" if value.startswith("! ") else "warning" if value.startswith("~ ")
                    else "dim" if value.startswith("? ") else "codex" if value.startswith("Codex")
                    else "claude" if value.startswith("Claude") else "text")
            rows.append({"text": value, "kind": kind})
    if not subscriptions:
        rows.append({"text": "Loading subscription usage…", "kind": "dim"})
    return {"schema": 1, "title": f"Subscriptions ({len(subscriptions)})",
            "valid_until": now + 90, "rows": rows[:64]}


def publish(snapshot):
    storage.write_json(storage.state_dir() / "section.json", section_document(snapshot, time.time()))


def clear():
    storage.write_json(storage.state_dir() / "section.json", {"schema": 1, "title": "Subscriptions", "valid_until": 0, "rows": []})
