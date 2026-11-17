"""Ask the vendor CLIs for metadata. Never read credentials or send prompts."""

from __future__ import annotations

import json
import math
import os
import selectors
import shutil
import subprocess
import time
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


class ProviderError(Exception):
    """An intentionally short, credential-free provider error."""


def text(value, limit=80):
    value = str(value or "")
    return "".join(c for c in value if not unicodedata.category(c).startswith("C"))[:limit]


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def timestamp(value):
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
        except (ValueError, OverflowError):
            pass
    return None


def decimal_text(value):
    try:
        d = Decimal(str(value))
        return str(d) if d.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


class JsonProcess:
    """Bounded newline-JSON transport; one deadline covers the entire exchange."""

    def __init__(self, argv, timeout=25):
        self.deadline = time.monotonic() + timeout
        self.buffer = b""
        self.env = dict(os.environ)
        # Metadata subprocesses must not accidentally attach to an agent session.
        self.env.pop("CLAUDECODE", None)
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=Path.home(), env=self.env,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.proc.stdout, selectors.EVENT_READ)

    def send(self, message):
        try:
            self.proc.stdin.write((json.dumps(message) + "\n").encode())
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ProviderError("client exited") from exc

    def receive(self, matches):
        while time.monotonic() < self.deadline:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(message, dict) and matches(message):
                    return message
            if not self.selector.select(max(0, self.deadline - time.monotonic())):
                break
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                raise ProviderError("client exited; check sign-in")
            self.buffer += chunk
            if len(self.buffer) > 4 * 1024 * 1024:
                raise ProviderError("client response too large")
        raise ProviderError("usage request timed out")

    def close(self):
        self.selector.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=2)
        for stream in (self.proc.stdin, self.proc.stdout):
            stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def metric(key, label, used, resets_at=None, **kwargs):
    return {"key": text(key), "label": text(label), "used_percent": number(used),
            "resets_at": timestamp(resets_at), **kwargs}


def normalize_codex(response, account=None):
    buckets = response.get("rateLimitsByLimitId") or {}
    if not buckets and response.get("rateLimits"):
        buckets = {"codex": response["rateLimits"]}
    result = {"provider": "codex", "label": "Codex", "plan": "", "limits": [],
              "credits": None, "resets": None,
              "ordinary_usage_allowed": response.get("ordinaryUsageAllowed")}
    if account:
        result["plan"] = text(account.get("planType"))
    for bucket_id, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        result["plan"] = text(bucket.get("planType")) or result["plan"]
        name = text(bucket.get("limitName") or bucket_id)
        for slot in ("primary", "secondary"):
            window = bucket.get(slot)
            if not isinstance(window, dict):
                continue
            minutes = window.get("windowDurationMins")
            label = {300: "5h", 10080: "Weekly"}.get(minutes)
            if not label:
                label = f"{minutes}m" if minutes else slot.title()
            if bucket_id != "codex":
                label = f"{name} {label}"
            result["limits"].append(metric(
                f"{bucket_id}.{slot}", label, window.get("usedPercent"),
                window.get("resetsAt"), window_minutes=minutes,
                reached=text(bucket.get("rateLimitReachedType")),
            ))
        credits = bucket.get("credits")
        if bucket_id == "codex" and isinstance(credits, dict):
            result["credits"] = {
                "balance": decimal_text(credits.get("balance")),
                "available": credits.get("hasCredits"),
                "unlimited": credits.get("unlimited"),
                "spend_control_reached": bucket.get("spendControlReached"),
            }
        individual = bucket.get("individualLimit")
        if isinstance(individual, dict):
            # Surface changing/unknown schemas rather than inventing a quota.
            result["limits"].append(metric(f"{bucket_id}.individual", f"{name} individual", None))
    resets = response.get("rateLimitResetCredits")
    if isinstance(resets, dict):
        count = resets.get("availableCount")
        expirations = [timestamp(c.get("expiresAt")) for c in (resets.get("credits") or [])
                       if isinstance(c, dict)]
        result["resets"] = {"available": count if isinstance(count, int) else None,
                            "expires_at": sorted(x for x in expirations if x is not None)}
    return result


def money(value):
    if not isinstance(value, dict):
        return None
    amount = decimal_text(value.get("amount_minor"))
    exponent = value.get("exponent")
    if amount is None or not isinstance(exponent, int) or not 0 <= exponent <= 6:
        return None
    return {"amount": str(Decimal(amount).scaleb(-exponent)),
            "currency": text(value.get("currency"), 8)}


def normalize_claude(response):
    rate = response.get("rate_limits") or {}
    result = {"provider": "claude", "label": "Claude", "plan": text(response.get("subscription_type")),
              "limits": [], "credits": None, "resets": None}
    rows = rate.get("limits")
    if isinstance(rows, list) and rows:
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            kind = row.get("kind", "unknown")
            model = ((row.get("scope") or {}).get("model") or {}).get("display_name")
            surface = (row.get("scope") or {}).get("surface")
            label = {"session": "5h", "weekly_all": "Weekly"}.get(kind)
            if not label:
                label = text(model or surface or kind).replace("weekly_", "")
            result["limits"].append(metric(
                f"claude.{kind}.{index}", label, row.get("percent"), row.get("resets_at"),
                window_minutes=300 if kind == "session" else 10080 if kind.startswith("weekly") else None,
            ))
    else:
        for key, value in rate.items():
            if not isinstance(value, dict) or "utilization" not in value or key == "extra_usage":
                continue
            label = {"five_hour": "5h", "seven_day": "Weekly"}.get(key, key.replace("seven_day_", "").replace("_", " ").title())
            result["limits"].append(metric(key, label, value.get("utilization"), value.get("resets_at")))
        for index, row in enumerate(rate.get("model_scoped") or []):
            label = text(row.get("display_name"))
            if label not in {m["label"] for m in result["limits"]}:
                result["limits"].append(metric(f"scoped.{index}", label, row.get("utilization"), row.get("resets_at")))
    spend = rate.get("spend")
    extra = rate.get("extra_usage")
    if isinstance(spend, dict):
        result["limits"].append(metric(
            "extra", "Credits", spend.get("percent"),
            spend.get("resets_at") or (extra.get("resets_at") if isinstance(extra, dict) else None), period="monthly",
            money_used=money(spend.get("used")), money_limit=money(spend.get("limit")),
            enabled=spend.get("enabled"),
            spend_limit_reached=extra.get("spend_limit_reached") if isinstance(extra, dict) else None,
        ))
    elif isinstance(extra, dict):
        exponent = extra.get("decimal_places", 2)
        currency = extra.get("currency", "USD")
        result["limits"].append(metric(
            "extra", "Credits", extra.get("utilization"), extra.get("resets_at"), period="monthly",
            money_used=money({"amount_minor": extra.get("used_credits"), "exponent": exponent, "currency": currency}),
            money_limit=money({"amount_minor": extra.get("monthly_limit"), "exponent": exponent, "currency": currency}),
            enabled=extra.get("is_enabled"), spend_limit_reached=extra.get("spend_limit_reached"),
        ))
    if not result["limits"]:
        raise ProviderError("usage unavailable; check subscription sign-in")
    return result


def fetch_codex():
    binary = shutil.which("codex")
    if not binary:
        raise ProviderError("Codex CLI not installed")
    with JsonProcess([binary, "app-server", "--listen", "stdio://"]) as client:
        client.send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "herdr_subscriptions", "version": "0.2.0"}}})
        if client.receive(lambda o: o.get("id") == 1).get("error"):
            raise ProviderError("Codex initialization failed")
        client.send({"method": "initialized", "params": {}})
        client.send({"id": 2, "method": "account/rateLimits/read", "params": {}})
        reply = client.receive(lambda o: o.get("id") == 2)
        if "error" in reply:
            raise ProviderError("usage unavailable; check Codex sign-in")
        # The quota response normally already includes planType. Avoid another request.
        result = normalize_codex(reply.get("result") or {})
        if not result["limits"] and result["credits"] is None:
            raise ProviderError("Codex returned no subscription usage")
        return result


def fetch_claude():
    binary = shutil.which("claude")
    if not binary:
        raise ProviderError("Claude CLI not installed")
    argv = [binary, "--safe-mode", "--setting-sources", "", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--tools", "", "--no-session-persistence",
            "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    with JsonProcess(argv) as client:
        def request(request_id, body):
            client.send({"type": "control_request", "request_id": request_id, "request": body})
            reply = client.receive(lambda o: o.get("type") == "control_response"
                                   and o.get("response", {}).get("request_id") == request_id)["response"]
            if reply.get("subtype") != "success":
                raise ProviderError("Claude usage protocol unavailable; check CLI/sign-in")
            return reply.get("response") or {}
        request("subscriptions-init", {"subtype": "initialize"})
        result = normalize_claude(request("subscriptions-usage", {"subtype": "get_usage", "skip_behaviors": True}))
        from .storage import claude_resets
        result["resets"] = claude_resets()
        return result


FETCHERS = {"codex": fetch_codex, "claude": fetch_claude}
