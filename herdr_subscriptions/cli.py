from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import select
import shutil
import sys
import termios
import time
import tty

from . import collector, daemon, herdr, storage
from .render import dashboard, sidebar_rows


def demo():
    now = time.time()
    return {"schema": 1, "subscriptions": [
        {"provider": "codex", "label": "Codex", "plan": "pro", "fetched_at": now,
         "limits": [{"key": "codex.primary", "label": "Weekly", "used_percent": 72, "resets_at": now + 172800}],
         "credits": {"balance": "1250", "available": True}, "resets": {"available": 2}},
        {"provider": "claude", "label": "Claude", "plan": "team", "fetched_at": now,
         "limits": [{"key": "session", "label": "5h", "used_percent": 85, "resets_at": now + 5400},
                    {"key": "weekly", "label": "Weekly", "used_percent": 32, "resets_at": now + 86400},
                    {"key": "fable", "label": "Fable", "used_percent": 53, "resets_at": now + 86400},
                    {"key": "extra", "label": "Extra", "used_percent": 25, "period": "monthly", "enabled": True,
                     "money_used": {"amount": "5", "currency": "EUR"}, "money_limit": {"amount": "20", "currency": "EUR"}}]}
    ]}


def panel(use_demo=False):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("\n".join(dashboard(demo() if use_demo else storage.cached(), time.time())))
        return
    if not use_demo:
        daemon.start()
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    offset = 0
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\x1b[?25l")
        while True:
            size = shutil.get_terminal_size((70, 25))
            rows = dashboard(demo() if use_demo else storage.cached(), time.time(), columns=max(1, size.columns - 2))
            height = max(1, size.lines - 1)
            offset = min(offset, max(0, len(rows) - height))
            lines = rows[offset:offset + height]
            sys.stdout.write("\x1b[H\x1b[2J" + "\n".join(lines))
            if len(rows) > height:
                sys.stdout.write(f"\n[{offset + 1}–{min(len(rows), offset + height)}/{len(rows)}] ↑/↓ scroll · q close")
            sys.stdout.flush()
            if select.select([fd], [], [], 1)[0]:
                key = os.read(fd, 32)
                if key in (b"q", b"\x03", b"\x1b"):
                    break
                if key in (b"r", b"R") and not use_demo:
                    daemon.request_refresh()
                if key in (b"j", b"\x1b[B"):
                    offset += 1
                if key in (b"k", b"\x1b[A"):
                    offset = max(0, offset - 1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
        sys.stdout.write("\x1b[?25h\n")
        sys.stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only subscription quotas for Herdr")
    parser.add_argument("command", choices=["status", "refresh", "start", "stop", "worker", "publish", "request-refresh", "panel", "open", "setup", "unsetup", "diagnostics"], nargs="?", default="status")
    parser.add_argument("--json", action="store_true", help="print normalized metadata only")
    parser.add_argument("--demo", action="store_true", help="use synthetic data; never reads accounts")
    parser.add_argument("--sidebar", action="store_true", help="preview compact sidebar rows")
    parser.add_argument("--apply", action="store_true", help="apply the previewed sidebar config, backing it up first")
    args = parser.parse_args(argv)
    try:
        if args.command in ("status", "refresh"):
            snapshot = demo() if args.demo else collector.refresh(force=True) if args.command == "refresh" else storage.cached()
            if args.json:
                print(json.dumps(snapshot, indent=2, ensure_ascii=False))
            else:
                renderer = sidebar_rows if args.sidebar else dashboard
                print("\n".join(renderer(snapshot, time.time())))
        elif args.command == "panel":
            panel(args.demo)
        elif args.command in ("setup", "unsetup"):
            from . import setup
            if args.apply:
                backup = setup.apply(remove=args.command == "unsetup")
                print(f"Updated {setup.config_path()}; backup: {backup}" if backup else "Config already up to date")
                if args.command == "setup":
                    herdr.call(["server", "reload-config"])
                    daemon.request_refresh()
                else:
                    daemon.stop()
                    herdr.call(["server", "reload-config"])
            else:
                print(setup.snippet())
                print("Preview only. Run setup --apply to back up, validate and apply it.")
        elif args.command == "diagnostics":
            config = storage.settings()
            snapshot = storage.cached()
            print(json.dumps({"state_directory": str(storage.state_dir()), "config_directory": str(storage.config_dir()),
                              "updater_running": daemon.running(), "settings": config,
                              "providers": [{k: s.get(k) for k in ("provider", "plan", "fetched_at", "attempted_at", "error")} for s in snapshot["subscriptions"]]}, indent=2))
        elif args.command == "open":
            daemon.start()
            herdr.open_dashboard()
        elif args.command == "publish":
            herdr.publish()
        elif args.command == "request-refresh":
            daemon.request_refresh()
        else:
            getattr(daemon, args.command)()
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        # Only our own controlled errors reach this path, never provider replies.
        print(f"Subscriptions: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
