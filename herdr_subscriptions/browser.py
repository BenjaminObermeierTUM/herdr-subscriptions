"""Receive only normalized reset metadata from the optional Chrome monitor."""

import base64
import hashlib
import json
import math
import os
import re
import select
import stat
from pathlib import Path
import shlex
import shutil
import subprocess
import struct
import sys
import time
import uuid

from . import storage

KINDS = ("full", "fable", "five_hour", "weekly")
PROJECT = Path(__file__).resolve().parent.parent


def install():
    manifest = json.loads((PROJECT / "browser/manifest.json").read_text())
    digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
    extension_id = "".join(chr(ord("a") + int(c, 16)) for c in digest)
    chrome_root = (Path.home() / "Library/Application Support/Google/Chrome" if sys.platform == "darwin"
                   else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "google-chrome")
    binary = shutil.which("claude")
    if not binary:
        raise ValueError("Claude CLI must be installed to identify the signed-in workspace")
    wrapper = storage.private_dir(storage.config_dir()) / "chrome-host"
    wrapper.write_text("#!/bin/sh\ncd " + shlex.quote(str(PROJECT)) + " || exit 1\n" +
                       "export HERDR_PLUGIN_CONFIG_DIR=" + shlex.quote(str(storage.config_dir().resolve())) + "\n" +
                       "export HERDR_CLAUDE_CLI=" + shlex.quote(binary) + "\n" +
                       "export HERDR_PLUGIN_STATE_DIR=" + shlex.quote(str(storage.state_dir().resolve())) +
                       "\nexec " + shlex.quote(sys.executable) + " -B -m herdr_subscriptions.browser\n")
    wrapper.chmod(0o700)
    host = chrome_root / "NativeMessagingHosts/dev.herdr.subscriptions.json"
    storage.write_json(host, {"name": "dev.herdr.subscriptions", "description": "Herdr reset inventory",
                              "path": str(wrapper), "type": "stdio",
                              "allowed_origins": [f"chrome-extension://{extension_id}/"]}, private_parent=False)
    print(f"In chrome://extensions, enable Developer mode and Load unpacked: {PROJECT / 'browser'}")


def send(output, value):
    payload = json.dumps(value).encode()
    output.write(struct.pack("=I", len(payload)) + payload)
    output.flush()


def read_message(stream):
    prefix = stream.read(4)
    if len(prefix) != 4:
        raise EOFError
    length, = struct.unpack("=I", prefix)
    if length > 4096:
        raise ValueError("invalid message length")
    return json.loads(stream.read(length))


def workspace_id():
    binary = os.environ.get("HERDR_CLAUDE_CLI", "claude")
    try:
        status = subprocess.run([binary, "auth", "status", "--json"], cwd=Path.home(),
                                capture_output=True, timeout=5, check=True)
        data = json.loads(status.stdout)
        return str(uuid.UUID(data["orgId"])) if data.get("loggedIn") else None
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        return None


def save_inventory(msg, checked_at, interval=86400):
    count = msg["available"]
    if count is not None and (type(count) is not int or not 0 <= count <= 100):
        raise ValueError("invalid reset count")
    record = {"available": count, "source": "chrome-usage", "checked_at": checked_at, "interval": interval}
    if count is None and msg.get("reason") in ("workspace unavailable", "rate limited",
            "check Chrome sign-in and site access", "provider request failed", "inventory unavailable from provider"):
        record["reason"] = msg["reason"]
    expiring = msg.get("expiring", [])
    if not isinstance(expiring, list) or len(expiring) > (count or 0):
        raise ValueError("invalid expiring grants")
    record["expiring"] = []
    for grant in expiring:
        if not (isinstance(grant, dict) and type(grant.get("at")) in (int, float) and math.isfinite(grant["at"])
                and type(grant.get("count")) is int and 0 < grant["count"] <= count
                and grant.get("kind") in (*KINDS, None) and type(grant.get("usable")) is bool):
            raise ValueError("invalid grant expiry")
        record["expiring"].append({key: grant[key] for key in ("at", "count", "kind", "usable")})
    for key in ("usable", "offers"):
        values = msg.get(key, [])
        if not isinstance(values, list) or len(values) > (count or 0) or any(v not in KINDS for v in values):
            raise ValueError("invalid reset types")
        record[key] = values
    scopes = msg.get("exhausted", [])
    if not isinstance(scopes, list) or len(scopes) > 3 or any(s not in ("session", "weekly", "fable") for s in scopes):
        raise ValueError("invalid exhausted limits")
    record["exhausted"] = scopes
    expirations = msg.get("expirations", [])
    if not isinstance(expirations, list):
        raise ValueError("invalid expirations")
    record["expirations"] = [v for v in expirations[:count] if isinstance(v, str)
                             and re.fullmatch(r"[A-Za-z0-9 +,:./()\-]{1,65}", v)]
    retry = msg.get("retry_after", 0)
    if isinstance(retry, (int, float)) and checked_at < retry <= checked_at + 86400:
        record["retry_after"] = retry
    storage.write_json(storage.state_dir() / "browser-resets.json", record)



def next_check(record, requested_at, interval=86400):
    checked = record.get("checked_at", 0)
    return 0 if requested_at > checked else checked + interval


def watch(stream, output):
    root = storage.private_dir(storage.state_dir())
    fifo = root / "reset-events.fifo"
    try:
        os.mkfifo(fifo, 0o600)
    except FileExistsError:
        if not stat.S_ISFIFO(fifo.lstat().st_mode):
            raise ValueError("reset event path is not a FIFO")
    events = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    try:
        while True:
            record = storage.read_json(root / "browser-resets.json", {})
            request = storage.read_json(root / "reset-request.json", {}).get("requested_at", 0)
            interval = storage.settings()["reset_refresh_seconds"]
            due = max(next_check(record, request, interval), record.get("retry_after", 0))
            now = time.time()
            if due <= now:
                send(output, {"workspace": workspace_id()})
                if not select.select([stream], [], [], 25)[0]:
                    return
                sample = read_message(stream)
                save_inventory(sample, now, interval)
                continue
            ready, _, _ = select.select([stream, events], [], [], min(due - now, 86400))
            if stream in ready:  # Chrome closed the native port.
                return
            if events in ready:
                os.read(events, 4096)
    finally:
        os.close(events)


def receive(stream, output):
    try:
        if read_message(stream) == {"request": "watch"}:
            watch(stream, output)
    except (EOFError, BrokenPipeError):
        return


if __name__ == "__main__":
    receive(sys.stdin.buffer, sys.stdout.buffer)
