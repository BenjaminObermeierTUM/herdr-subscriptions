"""Private, normalized cache outside the plugin checkout."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

MAX_USAGE_AGE = 30 * 60


def usage_is_current(sub, now, max_age=MAX_USAGE_AGE):
    fetched = sub.get("fetched_at")
    return fetched is not None and 0 <= now - fetched <= min(max_age, MAX_USAGE_AGE)


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


def write_json(path, value, *, private_parent=True):
    if private_parent:
        private_dir(path.parent)
    else:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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


def subscriptions(value):
    entries = value.get("subscriptions")
    if "subscriptions" not in value:
        providers = value.get("providers", ["codex", "claude"])
        if not isinstance(providers, list) or any(p not in ("claude", "codex") for p in providers):
            raise ValueError("invalid providers list")
        entries = [{"id": p, "provider": p} for p in dict.fromkeys(providers)]
    if not isinstance(entries, list):
        raise ValueError("subscriptions must be a list")
    result = []
    ids, homes = set(), set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each subscription must be an object")
        sid, provider = entry.get("id"), entry.get("provider")
        if not isinstance(sid, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", sid) or sid in ids:
            raise ValueError("each subscription needs a unique lowercase id")
        if provider not in ("claude", "codex"):
            raise ValueError("unsupported subscription provider")
        label, home = entry.get("label", ""), entry.get("config_dir")
        if not isinstance(label, str) or len(label) > 32 or (label and not label.isprintable()):
            raise ValueError("invalid subscription label")
        if home is not None:
            if not isinstance(home, str) or not Path(home).expanduser().is_absolute():
                raise ValueError("config_dir must be an absolute path")
            home = str(Path(home).expanduser())
        show_idle = entry.get("show_idle_five_hour", True)
        if type(show_idle) is not bool:
            raise ValueError("show_idle_five_hour must be true or false")
        identity = (provider, str(Path(home or Path.home() / ("." + provider)).resolve()))
        if identity in homes:
            raise ValueError("two subscriptions use the same provider login directory")
        ids.add(sid)
        homes.add(identity)
        binding = hashlib.sha256(json.dumps([provider, home]).encode()).hexdigest()[:16]
        result.append({"id": sid, "provider": provider, "label": label, "config_dir": home, "binding": binding,
                       "show_idle_five_hour": show_idle})
    return result


def subscription(sid=None, *, provider=None):
    entries = settings()["subscriptions"]
    match = next((s for s in entries if (not provider or s["provider"] == provider)
                  and (s["id"] == sid if sid else s["config_dir"] is None)), None)
    if not match:
        raise ValueError("subscription is no longer configured")
    return match


def subscription_dir(entry):
    return state_dir() / "subscriptions" / entry["id"] / entry["binding"]


def login_env(provider, directory):
    env = dict(os.environ)
    variable = "CLAUDE_CONFIG_DIR" if provider == "claude" else "CODEX_HOME"
    env.pop(variable, None)
    if directory is not None:
        env[variable] = directory
    if provider == "claude":
        for key in ("CLAUDE_SECURESTORAGE_CONFIG_DIR", "CLAUDECODE", "CLAUDE_CODE_OAUTH_TOKEN",
                    "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                    "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                    "CLAUDE_CODE_USE_FOUNDRY"):
            env.pop(key, None)
    return env


def settings():
    value = read_json(config_dir() / "config.json", {})
    if not isinstance(value, dict):
        raise ValueError("config.json must contain an object")
    refresh = value.get("refresh_seconds", 60)
    reset_refresh = value.get("reset_refresh_seconds", 86400)
    if type(reset_refresh) is not int or not 60 <= reset_refresh <= 86400:
        raise ValueError("reset_refresh_seconds must be between 60 and 86400")
    stale = value.get("stale_seconds", MAX_USAGE_AGE)
    if not isinstance(refresh, int) or not 30 <= refresh <= 3600:
        raise ValueError("refresh_seconds must be between 30 and 3600")
    if not isinstance(stale, int) or stale < refresh:
        raise ValueError("stale_seconds must be at least refresh_seconds")
    mode = value.get("sidebar_mode", "agents")
    if mode not in ("agents", "section"):
        raise ValueError("sidebar_mode must be agents or section")
    return {"subscriptions": subscriptions(value), "refresh_seconds": min(refresh, 60), "reset_refresh_seconds": reset_refresh,
            "stale_seconds": min(stale, MAX_USAGE_AGE), "sidebar_mode": mode}


def cached():
    value = read_json(state_dir() / "usage.json", {"schema": 1, "subscriptions": []})
    if not isinstance(value, dict) or not isinstance(value.get("subscriptions"), list):
        return {"schema": 1, "subscriptions": []}
    # Pre-0.4 snapshots are usable only for that provider's default login.
    rows = []
    for entry in settings()["subscriptions"]:
        row = next((s for s in value["subscriptions"] if s.get("id") == entry["id"]
                    and s.get("binding") == entry["binding"]), None)
        if row is None and entry["config_dir"] is None:
            row = next((s for s in value["subscriptions"] if "id" not in s and s["provider"] == entry["provider"]), None)
        if row is not None:
            row.update(id=entry["id"], binding=entry["binding"], subscription_label=entry["label"])
            row.setdefault("launch_command", entry["provider"] if entry["config_dir"] is None else None)
            if row["provider"] == "claude":
                row["show_idle_five_hour"] = entry["show_idle_five_hour"]
                row["resets"] = latest_claude_resets(entry)
            rows.append(row)
    return {**value, "subscriptions": rows}


def latest_claude_resets(entry=None):
    root = subscription_dir(entry) if entry else state_dir()
    record = read_json(root / "browser-resets.json", None)
    if entry and entry["config_dir"] is None and record is None:
        record = read_json(state_dir() / "browser-resets.json", None)
    if isinstance(record, dict) and record.get("source") == "chrome-usage":
        checked = record.get("checked_at", 0)
        request = read_json(root / "reset-request.json", {}).get("requested_at", 0)
        now = time.time()
        if request <= checked and 0 <= now - checked <= record.get("interval", 86400) + 90:
            # The daily sample knows its own expirations even while Chrome is closed.
            for grant in record.get("expiring", []):
                if grant["at"] <= now and record.get("available") is not None:
                    record["available"] = max(0, record["available"] - grant["count"])
                    for _ in range(grant["count"]):
                        if grant["kind"] in record["offers"]:
                            record["offers"].remove(grant["kind"])
                    if grant["usable"] and grant["kind"] in record["usable"]:
                        record["usable"].remove(grant["kind"])
            return record
    else:
        record = claude_resets() if not entry or entry["config_dir"] is None else None
    if record:
        return {**record, "last_known_available": record.get("available"), "available": None}
    return None


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
            saved = {"label": text(item.get("label") or "Saved reset", 32), "expires_at": expiration}
            if item.get("kind") in ("full", "fable", "five_hour", "weekly"):
                saved["kind"] = item["kind"]
            items.append(saved)
    available = max(0, count - expired)
    result = {"available": available, "source": "usage-page", "checked_at": checked,
              "items": items, "expires_at": sorted(i["expires_at"] for i in items)}
    # Only report absent types as zero when every available offer was classified.
    if len(items) == available and all("kind" in item for item in items):
        result["by_type"] = {kind: sum(item["kind"] == kind for item in items)
                             for kind in ("full", "fable", "five_hour", "weekly")}
    return result
