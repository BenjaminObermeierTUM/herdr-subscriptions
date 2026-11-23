import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from herdr_subscriptions import browser, collector, native, providers, selection, storage


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": temp.name, "HERDR_PLUGIN_CONFIG_DIR": temp.name,
                                     "HERDR_WORKSPACE_ID": "w1", "HERDR_PLUGIN_CONTEXT_JSON": "{}"})
        env.start()
        self.addCleanup(env.stop)
        self.home = str(Path(temp.name) / "secondary")
        self.rows = [{"id": "work", "provider": "claude", "label": "Claude Team"},
                     {"id": "personal", "provider": "claude", "label": "Claude Ultra", "config_dir": self.home}]
        self.save()

    def save(self):
        storage.write_json(storage.config_dir() / "config.json", {"subscriptions": self.rows})

    def test_two_subscriptions_and_independent_failures_and_resets(self):
        def fetch(directory):
            subscription = providers.normalize_claude({"rate_limits": {"five_hour": {"utilization": 73 if directory else 5}}})
            return subscription
        with patch.dict(providers.FETCHERS, claude=fetch):
            first = collector.refresh(force=True)["subscriptions"]
        self.assertEqual([s["limits"][0]["used_percent"] for s in first], [5, 73])
        browser.save_inventory({"available": 3}, first[0]["fetched_at"], root=storage.subscription_dir(storage.subscription("work")))
        self.rows[1]["label"] = "Personal"
        self.save()
        with patch.dict(providers.FETCHERS, claude=Mock(side_effect=providers.ProviderError("offline"))):
            result = collector.refresh(force=True)
        work, personal = result["subscriptions"]
        self.assertEqual(work["resets"]["available"], 3)
        self.assertIsNone(personal["resets"])
        self.assertEqual(personal["limits"][0]["used_percent"], 73)
        kinds = {r["kind"]: r["text"] for r in native.section_document(result, max(work["fetched_at"], personal["fetched_at"]))["rows"]}
        self.assertEqual(kinds["select_claude:work"], "Claude Team")
        self.assertEqual(kinds["select_claude:personal"], "Personal")
        self.rows[1]["config_dir"] = self.home + "-changed"
        self.save()
        self.assertEqual([s["id"] for s in storage.cached()["subscriptions"]], ["work"])

    def test_idle_session_is_subscription_specific_and_real_windows_stay_visible(self):
        self.rows[1]["show_idle_five_hour"] = False
        self.save()
        for shape in ("legacy", "limits"):
            for usage, reset, team_hint, ultra_visible in [
                    (0, None, "Starts on first use", False),
                    (0, "invalid", "reset time unavailable", True),
                    (2, None, "reset time unavailable", True),
                    (0, "2099-01-01T00:00:00Z", None, True)]:
                with self.subTest(shape=shape, usage=usage, reset=reset):
                    rate = {"five_hour": {"utilization": usage, "resets_at": reset}}
                    if shape == "limits":
                        rate = {"limits": [{"kind": "session", "percent": usage, "resets_at": reset}]}
                    def fetch(directory):
                        return providers.normalize_claude({"rate_limits": rate})
                    with patch.dict(providers.FETCHERS, claude=fetch):
                        snapshot = collector.refresh(force=True)
                    now = max(s["fetched_at"] for s in snapshot["subscriptions"])
                    rows = native.section_document(snapshot, now)["rows"]
                    split = next(i for i, row in enumerate(rows) if row["kind"] == "select_claude:personal")
                    team = [row["text"].strip() for row in rows[:split]]
                    ultra = [row["text"].strip() for row in rows[split:]]
                    self.assertTrue(any("5h " in line for line in team))
                    self.assertEqual(any("5h " in line for line in ultra), ultra_visible)
                    if team_hint:
                        self.assertIn(team_hint, team)
                    else:
                        self.assertTrue(any("Jan" in line or "Dec" in line for line in team))

    def test_distinct_cli_environments_and_stable_ids(self):
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "wrong", "CLAUDE_CODE_OAUTH_TOKEN": "test", "CODEX_HOME": "wrong"}):
            self.assertNotIn("CLAUDE_CONFIG_DIR", storage.login_env("claude", None))
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", storage.login_env("claude", self.home))
            self.assertEqual(storage.login_env("claude", self.home)["CLAUDE_CONFIG_DIR"], self.home)
            self.assertNotIn("CODEX_HOME", storage.login_env("codex", None))
            self.assertEqual(storage.login_env("codex", self.home)["CODEX_HOME"], self.home)
        for change in ({"id": "work"}, {"id": "../unsafe"}, {"config_dir": None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                storage.subscriptions({"subscriptions": [self.rows[0], {**self.rows[1], **change}]})

    def test_selection_reuses_only_the_same_live_terminal(self):
        entry = storage.subscription("personal")
        pane = {"pane_id": "w1:p2", "tab_id": "w1:t2", "terminal_id": "t100"}
        opened = {"result": {"plugin_pane": {"pane": pane}}}
        with patch.object(selection.herdr, "call", side_effect=[opened, {}, {"result": {"panes": [pane]}}, {},
                {"result": {"panes": [{**pane, "terminal_id": "replacement"}]}}, opened, {}]) as call:
            for _ in range(3):
                selection.select_pane(entry)
        commands = [c.args[0] for c in call.call_args_list]
        self.assertEqual(commands[3], ["plugin", "pane", "focus", pane["pane_id"]])
        self.assertIn("HERDR_SUBSCRIPTION_ID=personal", commands[0])
        self.assertEqual(commands[0], commands[5])
