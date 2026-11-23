import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from herdr_subscriptions import browser, collector, native, providers, render, selection, storage


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": temp.name, "HERDR_PLUGIN_CONFIG_DIR": temp.name,
                                     "XDG_BIN_HOME": temp.name + "/bin", "PATH": temp.name + "/bin:" + os.defpath,
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
        self.assertEqual(kinds["select_claude:work"], "Claude Team [claude]")
        self.assertEqual(kinds["select_claude:personal"], "Personal [claude2]")
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

    def test_added_claude_and_codex_commands_stay_bound_to_their_login(self):
        folder = Path(os.environ["XDG_BIN_HOME"])
        folder.mkdir()
        for provider, variable in (("claude", "CLAUDE_CONFIG_DIR"), ("codex", "CODEX_HOME")):
            stub = folder / provider
            stub.write_text('#!/bin/sh\nprintf "%s\\n" "${' + variable + ':-default}" "$@"\n')
            stub.chmod(0o700)

        def refresh():
            with patch.dict(providers.FETCHERS, {"codex": lambda *args, **kwargs: {"provider": "codex"},
                                                 "claude": lambda *args: {"provider": "claude"}}):
                return collector.refresh(force=True)["subscriptions"]

        def run(command, *args, check=True):
            return subprocess.run([command, *args], check=check, capture_output=True, text=True)

        self.rows.append({"id": "main", "provider": "codex", "label": "Codex Pro"})
        self.save()
        self.assertEqual([s["launch_command"] for s in refresh()], ["claude", "claude2", "codex"])
        third_home = self.home + " third"
        codex_home = self.home + " codex"
        self.rows.reverse()
        self.rows[:0] = [{"id": "third", "provider": "claude", "label": "Third", "config_dir": third_home},
                         {"id": "other", "provider": "codex", "label": "Other", "config_dir": codex_home}]
        self.save()
        snapshot = refresh()
        commands = {s["id"]: s["launch_command"] for s in snapshot}
        self.assertEqual(commands, {"main": "codex", "other": "codex2", "work": "claude",
                                    "personal": "claude2", "third": "claude3"})
        for row in snapshot:
            self.assertTrue(render.subscription_heading(row).endswith("[" + commands[row["id"]] + "]"))
        for name, directory in (("claude2", self.home), ("claude3", third_home), ("codex2", codex_home)):
            self.assertEqual(run(name, "--resume", "one argument").stdout.splitlines(),
                             [directory, "--resume", "one argument"])
        removed = self.rows.pop(0)
        self.save()
        self.assertNotEqual(run("claude3", check=False).returncode, 0)
        self.rows.insert(0, {**removed, "config_dir": third_home + "-changed"})
        self.save()
        self.assertEqual(refresh()[0]["launch_command"], "claude4")
        self.assertNotEqual(run("claude3", check=False).returncode, 0)
        self.rows[0] = removed
        self.save()
        self.assertEqual(refresh()[0]["launch_command"], "claude3")

    def test_launch_command_skips_existing_names_and_survives_a_long_label(self):
        folder = Path(os.environ["XDG_BIN_HOME"])
        folder.mkdir()
        occupied = folder / "claude2"
        occupied.write_text("user command")
        self.rows[1]["label"] = "My long secondary account label"
        self.save()
        with patch.dict(providers.FETCHERS, claude=Mock(side_effect=providers.ProviderError("offline"))):
            snapshot = collector.refresh(force=True)
        secondary = snapshot["subscriptions"][1]
        self.assertEqual(secondary["launch_command"], "claude3")
        self.assertEqual(occupied.read_text(), "user command")
        occupied.unlink()
        with patch.dict(providers.FETCHERS, claude=Mock(side_effect=providers.ProviderError("offline"))):
            self.assertEqual(collector.refresh(force=True)["subscriptions"][1]["launch_command"], "claude3")
        title = render.subscription_rows(secondary, 0, columns=23)[0]
        self.assertTrue(title.startswith("? "))
        self.assertTrue(title.endswith("… [claude3]"))
        self.assertLessEqual(render.width(title), 23)

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
