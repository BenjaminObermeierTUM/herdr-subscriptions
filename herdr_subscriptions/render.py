"""Pure rendering: stable labels, explicit % used, no invented reset dates."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import math
import unicodedata

from .providers import text

SIDEBAR_ROWS = 14
# Herdr hides whitespace-only metadata. A blank Braille cell keeps a gap row.
GAP = "\u2800"


def width(value):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ("F", "W") else 1 for c in value)


def clip(value, columns):
    value = text(value, 500)
    if width(value) <= columns:
        return value
    out = ""
    for c in value:
        if width(out + c) > max(0, columns - 1):
            break
        out += c
    return out + ("…" if columns else "")


def duration(seconds):
    minutes = max(0, math.ceil(seconds / 60))
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours}h"


def reset_line(reset, now):
    if reset is None:
        return "  reset time unavailable"
    if reset <= now:
        return "? reset · refresh"
    # Vendor timestamps can land a fraction of a second before a minute boundary.
    # Round to seconds before displaying minutes, avoiding e.g. 04:59 for 05:00.
    local = datetime.fromtimestamp(round(reset)).astimezone()
    return f"  {local:%d %b %H:%M} · {duration(reset - now)}"


def bar(percent, cells=6):
    if percent is None:
        return "░" * cells
    filled = min(cells, max(0, int(math.floor(min(100, max(0, percent)) / 100 * cells + 0.5))))
    return "█" * filled + "░" * (cells - filled)


def amount(value):
    if not value:
        return "?"
    try:
        number = Decimal(str(value.get("amount")))
        currency = value.get("currency", "")
        prefix = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, currency + " ")
        return prefix + f"{number:,.2f}".rstrip("0").rstrip(".")
    except (InvalidOperation, ValueError):
        return "?"


def extra_budget_left(limit):
    used, cap = limit.get("money_used"), limit.get("money_limit")
    if not used or not cap or used.get("currency") != cap.get("currency"):
        return None
    try:
        used_amount, cap_amount = Decimal(used["amount"]), Decimal(cap["amount"])
        if not used_amount.is_finite() or not cap_amount.is_finite() or cap_amount < 0:
            return None
        return {"amount": str(max(Decimal(0), cap_amount - used_amount)), "currency": cap["currency"]}
    except (KeyError, InvalidOperation, ValueError):
        return None


def credit_line(subscription):
    parts = []
    credits = subscription.get("credits")
    if credits:
        if credits.get("unlimited"):
            parts.append("credits unlimited")
        elif credits.get("balance") is not None:
            try:
                parts.append(f"{Decimal(credits['balance']):,.0f}cr")
            except InvalidOperation:
                parts.append("credits available" if credits.get("available") else "credits ?")
        elif credits.get("available") is not None:
            parts.append("credits yes" if credits["available"] else "credits none")
    resets = subscription.get("resets") if subscription.get("provider") != "claude" else None
    if resets and resets.get("available") is not None:
        parts.append(f"{resets['available']} resets")
    return " · ".join(parts)


def subscription_rows(subscription, now, *, columns=23, cells=6, stale_seconds=900, compact=False, native=False):
    stale = bool(subscription.get("error")) or now - subscription.get("fetched_at", 0) > stale_seconds
    plan = text(subscription.get("plan")).title()
    title = subscription.get("label", subscription["provider"].title()) + (f" {plan}" if plan else "")
    if subscription.get("provider") == "claude":
        saved = subscription.get("resets") or {}
        count = saved.get("available")
        suffix = "*" if count is not None and saved.get("source") == "usage-page" else ""
        title += f" · ↻{count if count is not None else '?'}{suffix}"
    rows = [("? " if stale else "") + title]
    if subscription.get("error"):
        rows.append("? " + text(subscription["error"]))
    if not subscription.get("fetched_at"):
        return [clip(r, columns) for r in rows]
    if stale:
        rows.append(f"? last good {duration(now - subscription['fetched_at'])} ago")
    limits = subscription.get("limits", [])
    shared = set()
    for index, limit in enumerate(limits):
        reset = limit.get("resets_at")
        if (compact or native) and limit.get("window_minutes") == 10080 and reset is not None:
            matching = [i for i, other in enumerate(limits)
                        if other.get("window_minutes") == 10080 and other.get("resets_at") is not None
                        and round(other["resets_at"]) == round(reset)]
            if len(matching) > 1:
                shared.update(matching)
    for index, limit in enumerate(limits):
        used = limit.get("used_percent")
        reset = limit.get("resets_at")
        pending = reset is not None and reset <= now
        prefix = "? " if stale or pending or used is None else "! " if used >= 95 else "~ " if used >= 80 else ""
        label = clip(limit.get("label", "Limit"), 7)
        value = "?" if used is None or pending else f"{used:g}%"
        branch = "├ " if index in shared else ""
        rows.append(f"{prefix}{branch}{label} {bar(None if pending else used, cells)} {value}")
        if limit.get("key") == "extra" and (compact or native):
            left = extra_budget_left(limit)
            rows[-1] = f"{prefix}Credits {amount(left) if left else '?'} left"
            rows.append(reset_line(reset, now) if reset else "  Monthly reset: ?")
            if limit.get("enabled") is False:
                rows[-2] = "Credits disabled"
            continue
        if limit.get("key") == "extra":
            detail = f"{amount(limit.get('money_used'))}/{amount(limit.get('money_limit'))}"
            detail += " · off" if limit.get("enabled") is False else " · reset ?"
            rows.append("  " + detail)
        else:
            if compact and subscription.get("provider") == "codex" and len(limits) == 1 and reset and not pending:
                # Keep all subscription rows inside Herdr's 16-row ceiling.
                # The dashboard retains the wide meter and reset countdown.
                local = datetime.fromtimestamp(round(reset)).astimezone()
                rows[-1] = f"{prefix}Wk {bar(used, 2)} {value} {local:%d%b%H:%M}"
            elif index not in shared:
                rows.append(reset_line(reset, now))
            elif not any(i > index and round(limits[i]["resets_at"]) == round(reset) for i in shared):
                rows.append("└ " + reset_line(reset, now).strip())
    credits = credit_line(subscription)
    if credits:
        rows.append(credits)
    return [clip(r, columns) for r in rows]


def sidebar_rows(snapshot, now, *, columns=23, row_budget=SIDEBAR_ROWS, stale_seconds=900):
    subscriptions = snapshot.get("subscriptions", [])
    rows = [GAP, f"Subscriptions ({len(subscriptions)})"]
    if not subscriptions:
        rows.append("? Loading usage…")
    for index, subscription in enumerate(subscriptions):
        if index:
            rows.append(GAP)
        rows.extend(subscription_rows(subscription, now, columns=columns, stale_seconds=stale_seconds, compact=True))
    if len(rows) > row_budget:
        hidden = len(rows) - row_budget + 1
        rows = rows[:row_budget - 1] + [f"+{hidden} rows · dashboard"]
    return [clip(row, columns) for row in rows]


def dashboard(snapshot, now, *, columns=70, stale_seconds=900):
    rows = ["SUBSCRIPTIONS", "% used · bars show consumption · reset times are local", ""]
    for subscription in snapshot.get("subscriptions", []):
        rows.extend(subscription_rows(subscription, now, columns=columns, cells=min(20, max(6, columns - 40)), stale_seconds=stale_seconds))
        credits = subscription.get("credits")
        if credits and credits.get("balance") is not None:
            rows.append(f"  Credit balance: {credits['balance']} credits (not currency)")
        resets = subscription.get("resets")
        if subscription.get("provider") == "claude" and (not resets or resets.get("available") is None):
            rows.append("  Saved resets: unavailable from Claude CLI; check Settings > Usage")
        if resets and resets.get("source") == "usage-page":
            checked = datetime.fromtimestamp(resets["checked_at"]).astimezone()
            rows.append(f"  * Saved resets last checked on Usage page: {checked:%d %b %H:%M %Z}")
            for item in resets.get("items", []):
                expiration = datetime.fromtimestamp(item["expires_at"]).astimezone()
                rows.append(f"    {item['label']}: expires {expiration:%d %b %Y %H:%M %Z}")
        if resets and resets.get("expires_at"):
            expiration = datetime.fromtimestamp(resets["expires_at"][0]).astimezone()
            rows.append(f"  First saved reset expires: {expiration:%d %b %Y %H:%M %Z}")
        for limit in subscription.get("limits", []):
            if limit.get("key") == "extra":
                left = extra_budget_left(limit)
                if left is not None:
                    rows.append(f"  Extra budget remaining: {amount(left)} of {amount(limit['money_limit'])}")
                rows.append("  Extra usage: monthly spending cap; renewal date not reported")
                if limit.get("spend_limit_reached") is False and (limit.get("used_percent") or 0) >= 100:
                    rows.append("  Cap is used; provider's hard-block flag is false")
        fetched = subscription.get("fetched_at")
        if fetched:
            rows.append("  Updated " + datetime.fromtimestamp(fetched).astimezone().strftime("%d %b %H:%M:%S %Z"))
        rows.append("")
    if not snapshot.get("subscriptions"):
        rows.append("Loading usage…")
    rows.extend(["r refresh · ↑/↓ scroll · q close", "Read-only: never redeems resets, buys credits, or sends prompts."])
    return [clip(row, columns) for row in rows]
