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

from . import browser, collector, daemon, herdr, selection, storage
from .render import dashboard, sidebar_rows
from .resets import confirm_codex, eligible_limit


def demo():
    now = time.time()
    return {"schema": 1, "subscriptions": [
        {"provider": "codex", "label": "Codex", "plan": "pro", "fetched_at": now,
         "limits": [{"key": "codex.primary", "label": "Weekly", "used_percent": 72, "resets_at": now + 172800}],
         "credits": {"balance": "1250", "available": True}, "resets": {"available": 2}},
        {"provider": "claude", "label": "Claude", "plan": "team", "fetched_at": now,
         "resets": {"available": 1, "source": "chrome-usage", "checked_at": now, "usable": ["full"], "offers": ["full"],
                    "items": [{"label": "Full reset", "expires_at": now + 604800}], "expires_at": [now + 604800]},
         "limits": [{"key": "session", "label": "5h", "used_percent": 85, "resets_at": now + 5400},
                    {"key": "weekly", "label": "Weekly", "window_minutes": 10080, "used_percent": 32, "resets_at": now + 86400},
                    {"key": "fable", "label": "Fable", "window_minutes": 10080, "used_percent": 53, "resets_at": now + 86400},
                    {"key": "extra", "label": "Extra", "used_percent": 25, "period": "monthly", "enabled": True,
                     "money_used": {"amount": "5", "currency": "EUR"}, "money_limit": {"amount": "20", "currency": "EUR"}}]}
    ]}


def choose_subscription(rows):
    for i, row in enumerate(rows, 1):
        print(f"{i}. {row.get('subscription_label') or row['id']}")
    choice = input("Choose subscription (Enter to cancel): ").strip()
    if choice.isdecimal() and 1 <= int(choice) <= len(rows):
        return rows[int(choice) - 1]
    return None


def panel(use_demo=False):
    stale_seconds = storage.MAX_USAGE_AGE if use_demo else storage.settings()["stale_seconds"]
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("\n".join(dashboard(demo() if use_demo else storage.cached(), time.time(), stale_seconds=stale_seconds)))
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
            rows = dashboard(demo() if use_demo else storage.cached(), time.time(),
                             columns=max(1, size.columns - 2), stale_seconds=stale_seconds)
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
                if not use_demo and key == b"s":
                    termios.tcsetattr(fd, termios.TCSADRAIN, previous)
                    sys.stdout.write("\x1b[?25h\n")
                    try:
                        row = choose_subscription(storage.cached()["subscriptions"])
                        if row:
                            selection.select_pane(storage.subscription(row["id"]))
                            break
                    finally:
                        tty.setcbreak(fd)
                        sys.stdout.write("\x1b[?25l")
                if not use_demo and key in (b"c", b"a"):
                    provider = "codex" if key == b"c" else "claude"
                    choices = [s for s in storage.cached()["subscriptions"] if s["provider"] == provider
                               and eligible_limit(s, time.time())]
                    if choices:
                        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
                        sys.stdout.write("\x1b[?25h\n")
                        try:
                            sub = choices[0] if len(choices) == 1 else choose_subscription(choices)
                            if not sub:
                                continue
                            entry = storage.subscription(sub["id"], provider=provider)
                            if provider == "claude":
                                browser.open_offers(entry)
                            else:
                                confirm_codex(entry)
                        except ValueError as exc:
                            print(exc)
                            input("Press Enter to return. ")
                        finally:
                            tty.setcbreak(fd)
                            sys.stdout.write("\x1b[?25l")
                if key in (b"j", b"\x1b[B"):
                    offset += 1
                if key in (b"k", b"\x1b[A"):
                    offset = max(0, offset - 1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
        sys.stdout.write("\x1b[?25h\n")
        sys.stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Subscription quotas and saved resets for Herdr")
    parser.add_argument("command", choices=["status", "refresh", "start", "stop", "worker", "publish", "request-refresh", "panel", "open", "setup", "unsetup", "diagnostics", "section-path", "browser-setup", "reset-codex", "select", "open-offers"], nargs="?", default="status")
    parser.add_argument("--json", action="store_true", help="print normalized metadata only")
    parser.add_argument("--demo", action="store_true", help="use synthetic data; never reads accounts")
    parser.add_argument("--sidebar", action="store_true", help="preview compact sidebar rows")
    parser.add_argument("--section", action="store_true", help="use the independent section in the custom Herdr client")
    parser.add_argument("--apply", action="store_true", help="apply the previewed sidebar config, backing it up first")
    parser.add_argument("--subscription", help="configured subscription ID")
    args = parser.parse_args(argv)
    try:
        if args.command == "browser-setup":
            from .browser import install
            install()
        elif args.command == "select":
            sid = selection.selected_id(args.subscription)
            if sid:
                selection.select_pane(storage.subscription(sid))
            else:
                herdr.open_dashboard()
        elif args.command == "open-offers":
            sid = selection.selected_id(args.subscription)
            if sid:
                browser.open_offers(storage.subscription(sid, provider="claude"))
            else:
                herdr.open_dashboard()
        elif args.command == "reset-codex":
            confirm_codex(storage.subscription(args.subscription, provider="codex"))
        elif args.command == "section-path":
            print(storage.state_dir() / "section.json")
        elif args.command in ("status", "refresh"):
            snapshot = demo() if args.demo else collector.refresh(force=True) if args.command == "refresh" else storage.cached()
            if args.json:
                print(json.dumps(snapshot, indent=2, ensure_ascii=False))
            else:
                renderer = sidebar_rows if args.sidebar else dashboard
                stale_seconds = storage.MAX_USAGE_AGE if args.demo else storage.settings()["stale_seconds"]
                print("\n".join(renderer(snapshot, time.time(), stale_seconds=stale_seconds)))
        elif args.command == "panel":
            panel(args.demo)
        elif args.command in ("setup", "unsetup"):
            from . import setup
            if args.apply:
                mode = "section" if args.section else "agents"
                if args.command == "setup" and storage.settings()["sidebar_mode"] != mode:
                    root = daemon.controller_dir()
                    daemon.stop()
                    daemon.wait_stopped(root)
                backup = setup.apply(remove=args.command == "unsetup", section=args.section)
                print(f"Updated {setup.config_path()}; backup: {backup}" if backup else "Config already up to date")
                if args.command == "setup":
                    config_file = storage.config_dir() / "config.json"
                    existing = storage.read_json(config_file, {})
                    if existing.get("sidebar_mode") != mode:
                        existing["sidebar_mode"] = mode
                        storage.write_json(config_file, existing)
                    if backup:
                        try:
                            herdr.call(["server", "reload-config"])
                        except herdr.ServerNotRunning:
                            pass  # No running server yet; it reads the config when it starts.
                    daemon.start()
                else:
                    daemon.stop()
                    herdr.call(["server", "reload-config"])
            else:
                print(setup.snippet(section=args.section))
                print("Preview only. Run setup --apply to back up, validate and apply it.")
        elif args.command == "diagnostics":
            config = storage.settings()
            snapshot = storage.cached()
            print(json.dumps({"state_directory": str(storage.state_dir()), "config_directory": str(storage.config_dir()),
                              "updater_running": daemon.running(), "settings": config,
                              "subscriptions": [{k: s.get(k) for k in ("id", "provider", "subscription_label", "plan", "fetched_at", "attempted_at", "error")} for s in snapshot["subscriptions"]]}, indent=2))
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
