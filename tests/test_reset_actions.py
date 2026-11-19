import io
import json
import os
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from unittest.mock import Mock, patch

from herdr_subscriptions import browser, daemon, native, providers, resets, storage


def codex(percent=100):
    return {"provider": "codex", "plan": "pro", "label": "Codex", "fetched_at": 100,
            "resets": {"available": 1}, "limits": [
                {"key": "codex.primary", "label": "Weekly", "used_percent": percent, "resets_at": 500}]}


class ResetTests(unittest.TestCase):
    def test_only_main_exhausted_fresh_codex_window_qualifies(self):
        self.assertIsNotNone(resets.eligible_limit(codex(), 105))
        for sub, now in ((codex(99.99), 105), (codex(), 500), (codex(), 191),
                         (dict(codex(), error="offline"), 105), (dict(codex(), resets={"available": 0}), 105)):
            self.assertIsNone(resets.eligible_limit(sub, now))
        sub = codex()
        sub["limits"][0]["key"] = "reviews.primary"
        self.assertIsNone(resets.eligible_limit(sub, 105))

    def test_claude_requires_matching_live_offer_in_both_sources(self):
        sub = codex()
        sub["provider"] = "claude"
        sub["limits"][0]["key"] = "claude.weekly_all.0"
        sub["resets"] = {"available": 1, "checked_at": 100, "source": "chrome-usage",
                         "usable": ["fable"], "exhausted": ["weekly"]}
        self.assertIsNone(resets.eligible_limit(sub, 105))
        sub["resets"]["usable"] = ["full"]
        self.assertEqual(resets.eligible_limit(sub, 105)["label"], "Weekly")
        sub["resets"]["exhausted"] = ["session"]
        self.assertIsNone(resets.eligible_limit(sub, 105))
        sub["limits"][0].update(key="extra", label="Credits")
        self.assertIsNone(resets.eligible_limit(sub, 105))
        sub["resets"]["source"] = "usage-page"
        self.assertIsNone(resets.current_count(sub, 105))

    def test_browser_report_replaces_old_snapshot_then_ages_out(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": tmp,
                      "HERDR_PLUGIN_CONFIG_DIR": tmp}), patch.object(browser.time, "time", return_value=100):
            storage.write_json(storage.config_dir() / "claude-resets.json", {"available": 1, "checked_at": 50})
            storage.write_json(storage.state_dir() / "usage.json", {"subscriptions": [
                {"provider": "claude", "label": "Claude", "fetched_at": 100, "limits": []}]})
            self.assertIsNone(resets.current_count(storage.cached()["subscriptions"][0], 100))
            raw = json.dumps({"available": 0, "private": "discard this"}).encode()
            browser.save_inventory(json.loads(raw), 100)
            sub = storage.cached()["subscriptions"][0]
            self.assertNotIn("discard this", json.dumps(sub))
            self.assertEqual(resets.current_count(sub, 180), 0)
            self.assertIsNone(resets.current_count(sub, 100 + 86400 + 91))
            self.assertIn({"text": "Resets ?", "kind": "text"}, native.section_document({"subscriptions": [sub]}, 100 + 86400 + 91)["rows"])

    def test_repeated_reset_attempts_reuse_a_key_without_persisting_account_id(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            window = codex()["limits"][0]
            key = resets.attempt_key(root, "secret-account", window)
            self.assertEqual(key, resets.attempt_key(root, "secret-account", window))
            self.assertNotEqual(key, resets.attempt_key(root, "secret-account", dict(window, resets_at=501)))
            self.assertNotIn("secret-account", (root / "reset-attempt.json").read_text())

    def test_confirm_cancel_recovered_and_confirmed(self):
        for choice, current, sends in (("n", 100, 0), ("y", 86, 0), ("y", 100, 1)):
            transport = Mock(usage_account="private-account")
            transport.receive.return_value = {"id": 4, "result": {"outcome": "reset"}}

            @contextmanager
            def session():
                yield transport

            with self.subTest(choice=choice, current=current), tempfile.TemporaryDirectory() as tmp, \
                    patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": tmp}), \
                    patch.object(providers, "codex_session", session), \
                    patch.object(providers, "codex_usage", side_effect=[codex(), codex(current)]), \
                    patch.object(daemon, "request_refresh"), patch("builtins.input", side_effect=[choice, ""]), \
                    patch.object(resets.time, "time", return_value=100), redirect_stdout(io.StringIO()), \
                    patch("sys.stdout.isatty", return_value=True), patch("sys.stdin.isatty", return_value=True):
                resets.confirm_codex()
            self.assertEqual(transport.send.call_count, sends)
            if sends:
                request = transport.send.call_args.args[0]
                self.assertEqual(request["method"], "account/rateLimitResetCredit/consume")
                self.assertEqual(set(request["params"]), {"idempotencyKey"})


class ScheduleTests(unittest.TestCase):
    def test_known_grant_expires_between_daily_checks(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": tmp}):
            browser.save_inventory({"available": 2, "offers": ["full", "fable"], "usable": ["full", "fable"],
                                    "expiring": [{"at": 140, "count": 1, "kind": "full", "usable": True}]}, 100)
            with patch("time.time", return_value=150):
                record = storage.latest_claude_resets()
            self.assertEqual((record["available"], record["usable"]), (1, ["fable"]))

    def test_codex_detail_lookup_on_crossing_and_not_on_next_poll(self):
        @contextmanager
        def client():
            yield Mock()
        for before, after, expected in ((99, 100, [False, True]), (100, 100, [False]), (40, 40, [False])):
            prior = codex(before)
            prior["resets"]["details_at"] = 99
            calls = []
            def usage(_, request_id=2, details=True):
                calls.append(details)
                return codex(after)
            with patch.object(providers, "codex_session", client), patch.object(providers, "codex_usage", usage), \
                    patch.object(storage, "cached", return_value={"subscriptions": [prior]}), patch("time.time", return_value=100):
                providers.fetch_codex()
            self.assertEqual(calls, expected)

    def test_daily_and_threshold_deadlines(self):
        self.assertLess(browser.next_check({}, 0), 1800000000)
        self.assertEqual(browser.next_check({"checked_at": 100}, 100), 86500)
        self.assertEqual(browser.next_check({"checked_at": 100}, 100, 60), 160)
        self.assertEqual(browser.next_check({"checked_at": 100}, 101), 0)

    def test_crossing_once_recovery_and_different_exhausted_window(self):
        from herdr_subscriptions import collector
        from pathlib import Path
        def subscription(percent, extra=100):
            return {"provider": "claude", "label": "Claude", "limits": [
                {"key": "s", "label": "5h", "used_percent": percent, "resets_at": 400},
                {"key": "extra", "label": "Credits", "used_percent": extra}]}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": tmp,
                "HERDR_PLUGIN_CONFIG_DIR": tmp}), patch("time.time", return_value=100):
            storage.write_json(Path(tmp) / "config.json", {"providers": ["claude"]})
            with patch.dict(providers.FETCHERS, {"claude": Mock(side_effect=[
                    subscription(i) for i in [65, 99, 100, 100, 50, 100]])}), \
                    patch.object(collector, "request_reset_inventory") as trigger:
                for expected in (0, 0, 1, 1, 1, 2):
                    collector.refresh(force=True)
                    self.assertEqual(trigger.call_count, expected)

    def test_native_host_wakes_without_polling_and_does_not_repeat(self):
        import select
        import subprocess
        import sys
        import time
        from pathlib import Path
        from herdr_subscriptions import collector
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HERDR_PLUGIN_STATE_DIR=tmp, HERDR_CLAUDE_CLI="/usr/bin/false")
            proc = subprocess.Popen([sys.executable, "-B", "-m", "herdr_subscriptions.browser"],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
            try:
                browser.send(proc.stdin, {"request": "watch"})
                self.assertTrue(select.select([proc.stdout], [], [], 3)[0])
                self.assertEqual(browser.read_message(proc.stdout), {"workspace": None})
                browser.send(proc.stdin, {"available": 0})
                self.assertFalse(select.select([proc.stdout], [], [], 0.15)[0])
                collector.request_reset_inventory(Path(tmp), time.time())
                self.assertTrue(select.select([proc.stdout], [], [], 3)[0])
                self.assertEqual(browser.read_message(proc.stdout), {"workspace": None})
                browser.send(proc.stdin, {"available": 1, "offers": ["full"]})
                self.assertFalse(select.select([proc.stdout], [], [], 0.15)[0])
                self.assertEqual(storage.read_json(Path(tmp) / "browser-resets.json", {})["available"], 1)
            finally:
                proc.stdin.close()
                proc.wait(timeout=5)
                proc.stdout.close()
