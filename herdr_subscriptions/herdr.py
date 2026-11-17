"""Stock Herdr integration, limited to this plugin's own metadata tokens."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import storage
from .render import SIDEBAR_ROWS, sidebar_rows

TOKENS = [f"subscriptions_{i:02d}" for i in range(1, SIDEBAR_ROWS + 1)]


def binary():
    return os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "herdr"


def call(args):
    completed = subprocess.run([binary(), *args], capture_output=True, text=True, timeout=5)
    if completed.returncode:
        raise RuntimeError("Herdr request failed")
    try:
        return json.loads(completed.stdout)
    except ValueError:
        return {}


def workspaces():
    return call(["workspace", "list"]).get("result", {}).get("workspaces", [])


def agents():
    return call(["agent", "list"]).get("result", {}).get("agents", [])


def agent_sort():
    from .setup import config_path
    try:
        config = config_path().read_text()
    except OSError:
        return "spaces"
    match = re.search(r'''(?m)^\s*agent_panel_sort\s*=\s*["'](priority|spaces|workspaces)["']''', config)
    return match.group(1) if match else "spaces"


def last_agent(entries, sort):
    if not entries:
        return None
    if sort == "priority":
        priorities = {"blocked": 4, "done": 3, "working": 2, "idle": 1, "unknown": 0}
        entries = sorted(entries, key=lambda a: (-priorities.get(a.get("agent_status"), 0), -a.get("state_change_seq", 0)))
    return entries[-1]["pane_id"]


def patch(target, rows=None, *, kind="pane"):
    args = [kind, "report-metadata", target, "--source", "herdr-subscriptions", "--ttl-ms", "90000"]
    for index, token in enumerate(TOKENS):
        if rows is not None and index < len(rows):
            args.extend(["--token", token + "=" + rows[index]])
        else:
            args.extend(["--clear-token", token])
    return call(args)


def publish(snapshot=None):
    snapshot = snapshot if snapshot is not None else storage.cached()
    from . import native
    native.publish(snapshot)
    if storage.settings()["sidebar_mode"] == "section":
        return
    rows = sidebar_rows(snapshot, time.time(), stale_seconds=storage.settings()["stale_seconds"])
    target = last_agent(agents(), agent_sort())
    marker = storage.session_dir() / "published.json"
    previous = storage.read_json(marker, {})
    old_target = previous.get("target") or previous.get("workspace_id")
    old_kind = previous.get("kind", "workspace")
    if old_target and (old_target != target or old_kind != "pane"):
        try:
            patch(old_target, kind=old_kind)
        except RuntimeError:
            pass  # The old pane/workspace may have closed.
    if target:
        patch(target, rows, kind="pane")
    storage.write_json(marker, {"kind": "pane", "target": target})


def clear():
    # Clean up both the initial workspace placement and current agent placement.
    for workspace in workspaces():
        patch(workspace["workspace_id"], kind="workspace")
    targets = {a["pane_id"] for a in agents()}
    marker = storage.read_json(storage.session_dir() / "published.json", {})
    if marker.get("kind") == "pane" and marker.get("target"):
        targets.add(marker["target"])
    for target in targets:
        try:
            patch(target, kind="pane")
        except RuntimeError:
            pass


def open_dashboard():
    return call(["plugin", "pane", "open", "--plugin", "subscriptions", "--entrypoint", "dashboard"])
