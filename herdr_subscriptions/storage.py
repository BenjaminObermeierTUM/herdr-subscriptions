"""Private, normalized cache outside the plugin checkout."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def state_dir():
    if os.environ.get("HERDR_PLUGIN_STATE_DIR"):
        return Path(os.environ["HERDR_PLUGIN_STATE_DIR"])
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "herdr/plugins/subscriptions"


def config_dir():
    if os.environ.get("HERDR_PLUGIN_CONFIG_DIR"):
        return Path(os.environ["HERDR_PLUGIN_CONFIG_DIR"])
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "herdr/plugins/config/subscriptions"


def session_dir():
    identity = os.environ.get("HERDR_SOCKET_PATH", "default") + os.environ.get("HERDR_SESSION_NAME", "")
    return state_dir() / "sessions" / hashlib.sha256(identity.encode()).hexdigest()[:16]


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def write_json(path, value):
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".subscriptions-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, default):
    try:
        if path.stat().st_size > 1024 * 1024:
            return default
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def settings():
    value = read_json(config_dir() / "config.json", {})
    if not isinstance(value, dict):
        raise ValueError("config.json must contain an object")
    providers = value.get("providers", ["codex", "claude"])
    if not isinstance(providers, list) or any(p not in ("codex", "claude") for p in providers):
        raise ValueError("providers must be a list containing codex and/or claude")
    providers = list(dict.fromkeys(providers))
    refresh = value.get("refresh_seconds", 300)
    stale = value.get("stale_seconds", 900)
    if not isinstance(refresh, int) or not 30 <= refresh <= 3600:
        raise ValueError("refresh_seconds must be between 30 and 3600")
    if not isinstance(stale, int) or stale < refresh:
        raise ValueError("stale_seconds must be at least refresh_seconds")
    mode = value.get("sidebar_mode", "agents")
    if mode not in ("agents", "section"):
        raise ValueError("sidebar_mode must be agents or section")
    return {"providers": providers, "refresh_seconds": refresh, "stale_seconds": stale, "sidebar_mode": mode}


def cached():
    value = read_json(state_dir() / "usage.json", {"schema": 1, "subscriptions": []})
    return value if isinstance(value, dict) and isinstance(value.get("subscriptions"), list) else {"schema": 1, "subscriptions": []}


def claude_resets(now=None):
    """Optional, explicitly last-checked Usage-page snapshot; never infer zero."""
    import time
    from .providers import timestamp, text
    now = time.time() if now is None else now
    record = read_json(config_dir() / "claude-resets.json", {})
    if not isinstance(record, dict):
        return None
    count = record.get("available")
    checked = timestamp(record.get("checked_at"))
    if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= 100 or checked is None:
        return None
    items = []
    expired = 0
    raw_items = record.get("items", [])
    if not isinstance(raw_items, list):
        return None
    for item in raw_items[:count]:
        if not isinstance(item, dict):
            continue
        expiration = timestamp(item.get("expires_at"))
        if expiration is None:
            continue
        if expiration <= now:
            expired += 1
        else:
            items.append({"label": text(item.get("label") or "Saved reset", 32), "expires_at": expiration})
    return {"available": max(0, count - expired), "source": "usage-page", "checked_at": checked,
            "items": items, "expires_at": sorted(i["expires_at"] for i in items)}
