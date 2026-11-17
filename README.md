# Herdr Subscriptions

Claude and Codex subscription limits in Herdr's sidebar, grouped by subscription.
Each quota has a progress bar, percentage **used**, next local reset time and
countdown. Credits and saved resets are shown separately from the included quota.

![Synthetic sidebar preview](docs/sidebar.svg)

## What it shows

- Every limit window Codex reports, including additional model/feature buckets.
- Claude's five-hour, overall weekly and model-specific weekly limits.
- Claude extra-usage money budget (`€15/€20 left`) and consumption bar, with exact spending and enabled status in the dashboard.
- Codex usage-credit balance, saved-reset count and their first expiration in the dashboard.
- Explicit unavailable, failed, stale and reset-pending states. A passed reset time
  never becomes an invented 0% usage reading.

Stock Herdr 0.9.5 has no fixed sidebar-footer plugin slot. This plugin adds its
rows **below the final agent entry** in Agents, separated by a blank line. It
follows Herdr's priority/recency or workspace ordering as agents change, and
clears the block from the previous agent. Spaces keep their original layout.
The block shares the agent list's scrolling; it is not a pinned native footer.
With no agents open, use the dashboard. Custom agent-view filters and per-client
unseen-completion ordering may differ from the public server ordering.

## Install

Requirements: **Herdr 0.9.5+**, **Python 3.10+**, macOS or Linux, and the vendor
CLI for each subscription you want to monitor. There are no Python dependencies.
Sign in normally using `codex` and `claude` first.

```sh
herdr plugin install BenjaminObermeierTUM/herdr-subscriptions
herdr plugin action invoke subscriptions.setup
```

Setup prints the proposed config. To apply it, open the plugin directory shown by
`herdr plugin list --json` and run:

```sh
python3 -m herdr_subscriptions setup --apply
```

This backs up and validates Herdr's config, preserves its existing contents, adds
the sidebar rows and a `prefix+u` dashboard shortcut, reloads config without
restarting Herdr, and starts the updater. Existing custom Agent rows or a
`prefix+u` binding are left for you to merge manually using the printed snippet.

`prefix` is normally **Ctrl+B**: press Ctrl+B, then U to open the dashboard.
Inside the dashboard, use `r` to refresh, arrows or `j`/`k` to scroll, and `q` to close.
The dashboard opens as a temporary overlay and restores the underlying pane.

The sidebar is limited to 16 rows per workspace by Herdr. Two native agent rows leave
14 subscription rows, including a gap before the heading, enough for Codex weekly/credits/resets plus Claude
five-hour/weekly/Fable/extra spending. A compact `↻?` means the reset date was not reported. If future limits exceed this, an explicit
overflow row directs you to the complete dashboard. Nothing is silently dropped.

## Refresh and data freshness

Provider metadata is fetched every five minutes and just after the next reported
reset, with a 25-second deadline per provider. The providers are independent, so
one outage does not discard the other's reading. Sidebar placement and countdowns
update every five seconds. **Subscriptions: refresh now** forces a refresh.

On a failed fetch, last-good numbers remain marked stale together with their age.
Successful data becomes stale after 15 minutes. If a quota reset has passed but
the provider has not returned a fresh window yet, the display says to refresh
instead of assuming a refill. Claude's money row shows remaining monthly spending headroom, while Codex's
credit row shows its reported credit balance. Those are different quantities.
Monthly renewal dates are shown as unknown when
the provider does not return them. Credit amounts are credits, not currency.

## Configuration

Find the config directory with `herdr plugin config-dir subscriptions`. An optional
`config.json` there accepts:

```json
{
  "providers": ["codex", "claude"],
  "refresh_seconds": 300,
  "stale_seconds": 900
}
```

This first version reads the default signed-in account from each installed CLI.
Separate accounts/configuration profiles within one provider are not yet supported.

Useful commands from the plugin checkout:

```sh
python3 -m herdr_subscriptions status                 # cached dashboard
python3 -m herdr_subscriptions status --json          # normalized cache only
python3 -m herdr_subscriptions refresh                # one live metadata refresh
python3 -m herdr_subscriptions diagnostics
python3 -m herdr_subscriptions status --demo --sidebar # synthetic preview, no account access
python3 -m herdr_subscriptions stop
python3 -m herdr_subscriptions unsetup --apply         # remove only the marked config block
```

Stop the updater and remove the config block before uninstalling:

```sh
python3 -m herdr_subscriptions unsetup --apply
herdr plugin uninstall subscriptions
```

## Privacy and read-only behavior

The plugin sends **no model prompts**, creates no agent tasks, and never redeems a
reset, purchases credits or changes a subscription. It asks the installed vendor
clients for metadata:

- Codex: `app-server` initialization and `account/rateLimits/read` over stdio.
- Claude: safe-mode stream initialization and the experimental `get_usage`
  control request with `skip_behaviors`. Hooks, MCP servers, tools, project
  settings and conversation persistence are disabled for this subprocess.

It never reads credential files, the macOS Keychain, browser cookies, prompts,
replies or conversation logs directly. Vendor clients handle their own normal
sign-in. Only allowlisted quota, plan, spend and reset metadata is cached, with
private file permissions, outside the repository. Raw provider responses,
account identifiers, reset redemption IDs and exception details are not saved.
There is no telemetry and no external service operated by this project.

Claude's control protocol is experimental and may change. A protocol failure is
shown explicitly; the plugin does not fall back to scraping credentials. Codex
and Claude may initialize their own normal client state while answering metadata
queries. The plugin does not replace their official billing dashboards.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q herdr_subscriptions
```

Tests use synthetic data and fake clients; they do not contact real accounts.

Independent community plugin. MIT licensed. Herdr, Codex and Claude belong to their
respective owners.
