"""Open or focus the selected subscription in the caller's worktree."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from . import herdr, storage


def selected_id(sid=None):
    if sid is None:
        source = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON", "{}")).get("invocation_source", "")
        if source and source.startswith("subscriptions/"):
            sid = source.removeprefix("subscriptions/")
    return sid


def worktree(cwd):
    result = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True, timeout=5)
    return Path(result.stdout.rstrip("\n")).resolve() if result.returncode == 0 else cwd


def select_pane(entry):
    context = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON", "{}"))
    workspace = context.get("workspace_id") or os.environ.get("HERDR_WORKSPACE_ID")
    if not workspace:
        raise ValueError("select a subscription inside a Herdr workspace")
    cwd = Path(context.get("focused_pane_cwd") or context.get("workspace_cwd") or os.getcwd()).resolve()
    identity = json.dumps([workspace, str(worktree(cwd)), entry["id"], entry["binding"]])
    key = hashlib.sha256(identity.encode()).hexdigest()
    root = storage.private_dir(storage.session_dir() / "subscription-panes")
    with (root / (key + ".lock")).open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / (key + ".json")
        saved = storage.read_json(path, {})
        if saved:
            panes = herdr.call(["pane", "list", "--workspace", workspace])["result"]["panes"]
            pane = next((p for p in panes if p["pane_id"] == saved["pane_id"]
                         and p["terminal_id"] == saved["terminal_id"]), None)
            if pane:
                herdr.call(["plugin", "pane", "focus", pane["pane_id"]])
                return
        opened = herdr.call(["plugin", "pane", "open", "--plugin", "subscriptions", "--entrypoint", "agent",
                             "--placement", "tab", "--workspace", workspace, "--cwd", str(cwd), "--focus",
                             "--env", "HERDR_SUBSCRIPTION_ID=" + entry["id"]])
        pane = opened["result"]["plugin_pane"]["pane"]
        storage.write_json(path, {k: pane[k] for k in ("pane_id", "terminal_id")})
        herdr.call(["tab", "rename", pane["tab_id"], entry["label"] or entry["id"]])


def launch_agent():
    entry = storage.subscription(os.environ["HERDR_SUBSCRIPTION_ID"])
    program = shutil.which(entry["provider"])
    if not program:
        raise ValueError("agent CLI is not installed")
    os.execve(program, [program], storage.login_env(entry["provider"], entry["config_dir"]))
