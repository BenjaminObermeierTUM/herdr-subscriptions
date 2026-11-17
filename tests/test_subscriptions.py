import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from unittest.mock import patch

from herdr_subscriptions import collector, daemon, herdr, native, providers, render, setup, storage
from herdr_subscriptions.cli import demo


class ProviderTests(unittest.TestCase):
    def test_codex_weekly_can_be_primary_and_credit_ids_are_discarded(self):
        raw = {"ordinaryUsageAllowed": False, "accountId": "PRIVATE_ACCOUNT",
               "rateLimitsByLimitId": {"codex": {"planType": "pro", "primary": {
                   "usedPercent": 100, "windowDurationMins": 10080, "resetsAt": 2000000000},
                   "secondary": None, "credits": {"hasCredits": True, "balance": "1234.50"}},
                   "review": {"limitName": "Reviews", "primary": {
                       "usedPercent": 12, "windowDurationMins": 10080, "resetsAt": 2000000001}}},
               "rateLimitResetCredits": {"availableCount": 3, "credits": [
                   {"id": "PRIVATE_RESET", "expiresAt": 2100000000}]}}
        result = providers.normalize_codex(raw)
        self.assertEqual(result["limits"][0]["label"], "Weekly")
        self.assertEqual(len(result["limits"]), 2)
        self.assertFalse(any(m.get("window_minutes") == 300 for m in result["limits"]))
        self.assertEqual(result["credits"]["balance"], "1234.50")
        self.assertEqual(result["resets"]["available"], 3)
        self.assertNotIn("PRIVATE_", json.dumps(result))

    def test_claude_structured_rows_include_scoped_model_and_minor_currency(self):
        raw = {"subscription_type": "team", "email": "PRIVATE_EMAIL", "rate_limits": {
            "five_hour": {"utilization": 1},  # New canonical rows must win.
            "limits": [{"kind": "session", "percent": 100, "resets_at": "2030-01-01T12:00:00Z"},
                       {"kind": "weekly_all", "percent": 32},
                       {"kind": "weekly_scoped", "percent": 53, "scope": {"model": {"display_name": "Fable"}}}],
            "spend": {"percent": 100, "enabled": True,
                      "used": {"amount_minor": 1265, "currency": "EUR", "exponent": 2},
                      "limit": {"amount_minor": 1000, "currency": "EUR", "exponent": 2}},
            "extra_usage": {"spend_limit_reached": False}}}
        result = providers.normalize_claude(raw)
        self.assertEqual([m["label"] for m in result["limits"]], ["5h", "Weekly", "Fable", "Credits"])
        self.assertEqual(result["limits"][0]["used_percent"], 100)
        self.assertEqual(result["limits"][-1]["money_used"]["amount"], "12.65")
        self.assertIsNone(result["limits"][-1]["resets_at"])
        self.assertFalse(result["limits"][-1]["spend_limit_reached"])
        self.assertNotIn("PRIVATE_", json.dumps(result))

    def test_unknown_windows_and_nonfinite_values_are_not_false_zeroes(self):
        result = providers.normalize_codex({"rateLimits": {"primary": {"usedPercent": "NaN", "windowDurationMins": 15}}})
        self.assertEqual(result["limits"][0]["label"], "15m")
        self.assertIsNone(result["limits"][0]["used_percent"])
        self.assertIsNone(providers.decimal_text("Infinity"))
        self.assertNotIn("\x1b", providers.text("\x1b[31mtest\n"))

    def test_legacy_claude_model_limits_and_disabled_extra_are_visible(self):
        result = providers.normalize_claude({"rate_limits": {
            "five_hour": {"utilization": 20}, "seven_day": {"utilization": 10},
            "seven_day_sonnet": {"utilization": 5},
            "extra_usage": {"is_enabled": False, "utilization": None}}})
        self.assertEqual(len(result["limits"]), 4)
        self.assertFalse(result["limits"][-1]["enabled"])

    def test_metadata_protocol_never_submits_a_prompt_or_reset(self):
        calls = []
        replies = [{"id": 1, "result": {}}, {"id": 2, "result": {"rateLimits": {
            "primary": {"usedPercent": 10, "windowDurationMins": 10080}}}}]
        class FakeProcess:
            def __init__(self, argv): calls.append(argv)
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def send(self, obj): calls.append(obj)
            def receive(self, predicate):
                reply = replies.pop(0)
                assert predicate(reply)
                return reply
        with patch.object(providers, "JsonProcess", FakeProcess), patch.object(providers.shutil, "which", return_value="codex"):
            providers.fetch_codex()
        methods = [c["method"] for c in calls if isinstance(c, dict)]
        self.assertEqual(methods, ["initialize", "initialized", "account/rateLimits/read"])

        calls.clear()
        replies[:] = [{"type": "control_response", "response": {"request_id": "subscriptions-init", "subtype": "success", "response": {}}},
                      {"type": "control_response", "response": {"request_id": "subscriptions-usage", "subtype": "success", "response": {
                          "rate_limits": {"five_hour": {"utilization": 10}}}}}]
        with patch.object(providers, "JsonProcess", FakeProcess), patch.object(providers.shutil, "which", return_value="claude"), patch.object(storage, "claude_resets", return_value=None):
            providers.fetch_claude()
        requests = [c["request"]["subtype"] for c in calls if isinstance(c, dict)]
        self.assertEqual(requests, ["initialize", "get_usage"])
        self.assertIn("--safe-mode", calls[0])
        self.assertIn("--no-session-persistence", calls[0])

    def test_transport_handles_fragmentation_notifications_and_timeout(self):
        script = "import sys,time; sys.stdin.readline(); sys.stdout.write('{\"event\":1}\\n{\"id\":'); sys.stdout.flush(); time.sleep(.05); print('7}'); sys.stdout.flush(); time.sleep(10)"
        with providers.JsonProcess([sys.executable, "-c", script], timeout=1) as client:
            process = client.proc
            client.send({"hello": True})
            self.assertEqual(client.receive(lambda o: o.get("id") == 7), {"id": 7})
            with self.assertRaises(providers.ProviderError):
                client.receive(lambda o: False)
        self.assertIsNotNone(process.poll())


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": self.temp.name + "/state", "HERDR_PLUGIN_CONFIG_DIR": self.temp.name + "/config"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_provider_failure_keeps_only_its_last_good_data(self):
        now = time.time()
        storage.write_json(storage.state_dir() / "usage.json", {"schema": 1, "subscriptions": [
            {"provider": "codex", "label": "Codex", "fetched_at": now - 600, "limits": [{"used_percent": 45}]}]})
        def broken(): raise providers.ProviderError("client unavailable")
        with patch.dict(providers.FETCHERS, {"codex": broken, "claude": lambda: {"provider": "claude", "label": "Claude", "limits": []}}):
            result = collector.refresh(force=True)
        self.assertEqual(result["subscriptions"][0]["limits"][0]["used_percent"], 45)
        self.assertEqual(result["subscriptions"][0]["fetched_at"], now - 600)
        self.assertIn("error", result["subscriptions"][0])
        self.assertNotIn("error", result["subscriptions"][1])
        self.assertEqual((storage.state_dir() / "usage.json").stat().st_mode & 0o777, 0o600)

    def test_exception_text_cannot_leak_to_cache(self):
        def broken(): raise RuntimeError("PRIVATE_TOKEN")
        with patch.dict(providers.FETCHERS, {"codex": broken, "claude": broken}):
            collector.refresh(force=True)
        self.assertNotIn("PRIVATE_TOKEN", (storage.state_dir() / "usage.json").read_text())

    def test_claude_reset_snapshot_retains_check_time_and_removes_expired_offers(self):
        storage.write_json(storage.config_dir() / "claude-resets.json", {
            "available": 2, "checked_at": 100,
            "items": [{"label": "Full reset", "expires_at": 110}, {"label": "Model reset", "expires_at": 210}]})
        value = storage.claude_resets(now=150)
        self.assertEqual(value["available"], 1)
        self.assertEqual(value["checked_at"], 100)
        self.assertEqual(value["source"], "usage-page")
        self.assertEqual(value["expires_at"], [210])
        storage.write_json(storage.config_dir() / "claude-resets.json", {"available": True, "checked_at": 100})
        self.assertIsNone(storage.claude_resets(now=150))

    def test_fresh_cache_skips_network(self):
        with patch.dict(providers.FETCHERS, {"codex": lambda: {"provider": "codex", "limits": []}, "claude": lambda: {"provider": "claude", "limits": []}}):
            collector.refresh(force=True)
        with patch.dict(providers.FETCHERS, {"codex": lambda: self.fail("polled fresh cache"), "claude": lambda: self.fail("polled fresh cache")}):
            collector.refresh()


class RenderTests(unittest.TestCase):
    def test_current_five_limit_layout_fits_stock_herdr(self):
        rows = render.sidebar_rows(demo(), time.time())
        self.assertEqual(len(rows), 14)
        self.assertEqual(rows[0], render.GAP)
        self.assertEqual(rows[1], "Subscriptions (2)")
        self.assertEqual(rows.count(render.GAP), 2)
        self.assertEqual(sum(row.startswith("└ ") for row in rows), 1)
        self.assertIn("Codex Pro", rows)
        self.assertTrue(any(row.startswith("Claude Team") for row in rows))
        self.assertTrue(all(render.width(row) <= 23 for row in rows))
        for expected in ("Codex", "Claude", "Weekly", "Fable", "€", "left", "resets"):
            self.assertTrue(any(expected in r for r in rows), expected)
        self.assertFalse(any("more" in r or "dashboard" in r for r in rows))

    def test_past_reset_does_not_claim_a_refill_or_old_percentage(self):
        sub = {"provider": "codex", "label": "Codex", "fetched_at": 100,
               "limits": [{"label": "Weekly", "used_percent": 100, "resets_at": 200}]}
        rows = render.subscription_rows(sub, 201)
        self.assertNotIn("100%", " ".join(rows))
        self.assertIn("refresh", " ".join(rows))
        self.assertNotIn("0%", " ".join(rows))

    def test_error_uses_stale_marker_and_overflow_is_explicit(self):
        data = demo()
        data["subscriptions"][0]["error"] = "offline"
        rows = render.sidebar_rows(data, time.time(), row_budget=5)
        self.assertEqual(len(rows), 5)
        self.assertTrue(rows[2].startswith("? "))
        self.assertIn("dashboard", rows[-1])

    def test_bar_clamps_overages_but_percentage_is_preserved(self):
        self.assertEqual(render.bar(105), "██████")
        self.assertEqual(render.bar(-1), "░░░░░░")
        self.assertEqual(render.bar(None), "░░░░░░")
        self.assertEqual(render.amount({"amount": "12.65", "currency": "EUR"}), "€12.65")

    def test_fractional_reset_boundary_does_not_display_the_previous_minute(self):
        boundary = 1893499200
        expected = datetime.fromtimestamp(boundary).astimezone().strftime("%d %b %H:%M")
        self.assertIn(expected, render.reset_line(boundary - 0.00001, boundary - 100))


    def test_extra_budget_is_remaining_spend_headroom_not_purchased_balance(self):
        limit = {"key": "extra", "label": "Extra", "used_percent": 100,
                 "money_used": {"amount": "12.65", "currency": "EUR"},
                 "money_limit": {"amount": "10", "currency": "EUR"}, "enabled": True}
        self.assertEqual(render.extra_budget_left(limit), {"amount": "0", "currency": "EUR"})
        sub = {"provider": "claude", "label": "Claude", "fetched_at": 100, "limits": [limit]}
        rows = render.subscription_rows(sub, 101, compact=True)
        self.assertIn("Credits €0 left", rows[1])
        self.assertIn("Monthly reset: ?", rows[2])
        limit["money_used"]["amount"] = "4"
        self.assertEqual(render.extra_budget_left(limit)["amount"], "6")
        limit["money_used"]["currency"] = "USD"
        self.assertIsNone(render.extra_budget_left(limit))


class NativeSectionTests(unittest.TestCase):
    def test_section_is_independent_of_any_workspace_or_agent(self):
        with patch.object(storage, "settings", return_value={"stale_seconds": 900}):
            document = native.section_document(demo(), time.time())
        self.assertEqual(document["schema"], 1)
        self.assertEqual(document["title"], "Subscriptions (2)")
        self.assertTrue(any(r["kind"] == "codex" for r in document["rows"]))
        self.assertTrue(any(r["kind"] == "claude" for r in document["rows"]))
        self.assertTrue(any(r["text"] == "" for r in document["rows"]))
        self.assertTrue(any("Credits" in r["text"] and "left" in r["text"] for r in document["rows"]))
        self.assertNotIn("pane_id", json.dumps(document))
        self.assertNotIn("workspace_id", json.dumps(document))

    def test_native_setup_contains_shortcut_but_no_agent_row_override(self):
        value = setup.proposed('onboarding = false\n[ui]\n', section=True)
        self.assertNotIn("ui.sidebar.agents", value)
        self.assertNotIn("$subscriptions_", value)
        self.assertIn("subscriptions.open", value)
        custom = '[ui.sidebar.agents]\nrows = [["agent"]]\n'
        self.assertTrue(setup.proposed(custom, section=True).startswith(custom.rstrip()))


class IntegrationTests(unittest.TestCase):
    def test_metadata_moves_with_last_agent_and_clears_previous(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": temporary}):
            storage.write_json(storage.session_dir() / "published.json", {"kind": "pane", "target": "w1:p-old"})
            with patch.object(herdr, "agents", return_value=[{"pane_id": "w1:p-new", "agent_status": "idle"}]), patch.object(herdr, "agent_sort", return_value="priority"), patch.object(herdr, "patch") as write:
                herdr.publish(demo())
                self.assertEqual(write.call_args_list[0].args, ("w1:p-old",))
                self.assertEqual(write.call_args_list[1].args[0], "w1:p-new")
                self.assertEqual(len(write.call_args_list[1].args[1]), 14)
                self.assertEqual(write.call_args_list[1].kwargs, {"kind": "pane"})

    def test_last_agent_uses_herdr_priority_then_recency(self):
        entries = [{"pane_id": "new-idle", "agent_status": "idle", "state_change_seq": 100},
                   {"pane_id": "old-idle", "agent_status": "idle", "state_change_seq": 10},
                   {"pane_id": "busy", "agent_status": "working", "state_change_seq": 1}]
        self.assertEqual(herdr.last_agent(entries, "priority"), "old-idle")
        self.assertEqual(herdr.last_agent(entries, "spaces"), "busy")
        self.assertIsNone(herdr.last_agent([], "priority"))

    def test_metadata_never_exceeds_the_sixteen_token_request_limit(self):
        with patch.object(herdr, "call", return_value={}) as call:
            herdr.patch("w-known", ["one"])
            args = call.call_args.args[0]
            self.assertEqual(args.count("--token") + args.count("--clear-token"), 14)
            self.assertIn("--ttl-ms", args)

    def test_setup_is_idempotent_preserves_config_and_refuses_collisions(self):
        original = 'onboarding = false\n[ui]\nagent_panel_sort = "priority"\n'
        candidate = setup.proposed(original)
        self.assertTrue(candidate.startswith(original.rstrip()))
        self.assertEqual(setup.proposed(candidate), candidate)
        self.assertEqual(candidate.count('"state_icon", "workspace", "tab"'), 1)
        self.assertEqual(candidate.count('token = "$subscriptions_'), 14)
        with self.assertRaises(ValueError):
            setup.proposed(original + '\n[ui.sidebar.agents]\nrows = [["workspace"]]\n')
        with self.assertRaises(ValueError):
            setup.proposed(original + '\n[[keys.command]]\nkey = "prefix+u"\n')

    def test_invalid_config_is_never_written_and_valid_config_is_backed_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "herdr.toml"
            config.write_text('[ui]\nagent_panel_sort = "priority"\n')
            original = config.read_bytes()
            with patch.dict(os.environ, {"HERDR_CONFIG_PATH": str(config), "HERDR_PLUGIN_CONFIG_DIR": temporary + "/plugin"}):
                with patch.object(setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
                    with self.assertRaises(ValueError): setup.apply()
                self.assertEqual(config.read_bytes(), original)
                with patch.object(setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
                    backup = setup.apply()
                    self.assertEqual(backup.read_bytes(), original)
                    self.assertIsNone(setup.apply())
                    setup.apply(remove=True)
                    self.assertEqual(config.read_text().strip(), original.decode().strip())


if __name__ == "__main__":
    unittest.main()
