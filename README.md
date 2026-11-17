# Herdr Subscriptions

An independent **Subscriptions** section below Spaces and Agents in Herdr.
Bold provider headings and vertical spacing separate Claude and Codex. Each
quota shows usage and its next reset; Credits shows the amount left in a neutral colour.

![Synthetic preview](docs/sidebar.svg)

The independent section uses the companion Herdr 0.9.5 client included as a small
source patch. It has its own geometry and scrolling, stays visible when there
are no agents, and never attaches itself to an agent or workspace. The existing
Herdr server, protocol, agent processes and stock binary remain unchanged.

## Install the independent section

Requirements: macOS or Linux, Python 3.10+, Herdr 0.9.5, and signed-in `codex` and
`claude` CLIs. Python has no external dependencies. Building the companion client
also requires Rust/Cargo and **Zig 0.16.0**.

```sh
 git clone https://github.com/BenjaminObermeierTUM/herdr-subscriptions.git
 cd herdr-subscriptions
 herdr plugin link "$PWD" --enabled
 python3 scripts/build-client.py
```

The builder downloads the pinned upstream source, applies the included patch,
and puts the client in `native/bin/herdr`. It does not replace your installed
Herdr executable. Set `ZIG=/path/to/zig` if Zig is not on PATH.

Detach the old client with **Ctrl+B, then Q**, and run this at the outer shell:

```sh
./bin/herdr-with-subscriptions
```

The launcher backs up and validates the config, removes this plugin's agent-row
workaround, preserves the dashboard shortcut, starts the quota updater and
attaches the companion client. Detaching keeps the server and agents running.
Use this launcher whenever you want the independent section.

The native section is client-only. It reads a private local JSON feed; there are
no new server messages, API methods, or wire-format fields. See
[the client patch notes](host-patches/README.md) for the exact base and validation.

## What it shows

- Codex quota windows, including additional model or feature windows when reported.
- Claude's five-hour, overall weekly and model-specific weekly limits.
- Next local reset times and countdowns; weekly limits sharing a reset are bracketed together.
- Codex's remaining usage-credit balance and saved-reset count.
- Claude's `Credits €X left` and renewal line. This currently represents remaining
  monthly extra-usage spending headroom; exact spending/cap details are in the dashboard.
- Claude saved resets as an explicitly **last checked** Usage-page snapshot when supplied.

Higher quota percentages mean more **used**. A passed reset time is marked
pending until a fresh vendor report arrives. Missing renewal dates say
`Monthly reset: ?`; the plugin never invents a billing date or treats missing
data as zero. Claude's credit renewal date is not currently returned by its CLI.

`↻2*` beside Claude means two saved resets were last checked on its official
Usage page; `↻?` means that count is unavailable. These offers are separate from
ordinary five-hour/weekly quota refreshes and do not replenish on a guaranteed
weekly or monthly schedule.

## Dashboard and refresh

Press **Ctrl+B, then U** (`prefix+u`) for the full dashboard. It opens as a temporary
overlay and restores the underlying pane. Use `r` to refresh, arrows or `j`/`k` to
scroll and `q` to close. The plugin action menu also offers refresh and stop.

Vendor metadata refreshes every five minutes and just after the next reported
quota reset, with a 25-second deadline per provider. Placement/feed/countdowns
update every five seconds. Provider failures preserve last-good values marked
stale, without discarding the other provider. The native section dims stale feeds.

## Stock Herdr fallback

Stock Herdr has no standalone plugin-section slot. If you want to keep its
unmodified client, use the fallback that places rows below the final agent entry:

```sh
herdr plugin install BenjaminObermeierTUM/herdr-subscriptions
# From its plugin directory (shown by herdr plugin list --json):
python3 -m herdr_subscriptions setup --apply
```

This shares the agent list's scrolling and its 16-row limit. Excess rows are
explicitly flagged and remain available in the dashboard. Custom agent filters
or client-local unseen-completion ordering may differ from server ordering.

## Configuration

Find the directory with `herdr plugin config-dir subscriptions`. Optional `config.json`:

```json
{
  "providers": ["codex", "claude"],
  "refresh_seconds": 300,
  "stale_seconds": 900,
  "sidebar_mode": "section"
}
```

The launcher sets `section` automatically. The fallback setup sets `agents`.
This version reads each CLI's default signed-in account; multiple account
profiles within the same provider are not yet supported.

### Claude saved-reset snapshot

Claude's CLI does not currently expose saved-reset availability. You may record
an independently checked count in `claude-resets.json` in the plugin config directory:

```json
{
  "available": 1,
  "checked_at": "2030-01-01T12:00:00+00:00",
  "items": [{"label": "Full reset", "kind": "full", "expires_at": "2030-01-15T12:00:00+00:00"}]
}
```

The count always carries `*` and its last-check time in the dashboard. Classifying
every offer with `kind` (`full`, `fable`, `five_hour`, or `weekly`) also shows the
remaining full and Fable counts in the dashboard. Unclassified offers leave that
breakdown unavailable rather than implying zero. Expired
offers are removed automatically. A reset redeemed elsewhere requires updating
this snapshot. The plugin never redeems saved resets.

## Useful commands

Run from the plugin checkout:

```sh
python3 -m herdr_subscriptions status
python3 -m herdr_subscriptions status --json
python3 -m herdr_subscriptions refresh
python3 -m herdr_subscriptions diagnostics
python3 -m herdr_subscriptions status --demo --sidebar
python3 -m herdr_subscriptions stop
python3 -m herdr_subscriptions unsetup --apply
```

To uninstall, detach the companion client, run `unsetup --apply`, uninstall the
plugin with `herdr plugin uninstall subscriptions`, and reattach with stock Herdr.
Setup/unsetup edits only its marked block and keeps a private backup first.

## Privacy and read-only behavior

The plugin sends no model prompts, creates no agent tasks, redeems no resets,
buys no credits and changes no subscription. It queries the installed clients:

- Codex: app-server initialization and `account/rateLimits/read` over stdio.
- Claude: safe-mode initialization and experimental `get_usage` with `skip_behaviors`.
  Hooks, MCP servers, tools, project settings and session persistence are disabled.

It does not directly read credential files, Keychain entries, browser cookies,
prompts, replies or conversation logs. The vendor clients handle their own
normal authentication. Only allowlisted quota, plan, spend and reset metadata is
cached privately outside the repository. Raw responses, account identifiers,
redemption IDs and exception details are not retained. There is no telemetry.

The Claude control protocol is experimental. Failures stay explicit and do not
trigger credential scraping. Vendor clients can initialize their own normal
state while answering metadata queries. Official billing dashboards remain the
authority for account balances and charges.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q herdr_subscriptions scripts
```

Tests use synthetic metadata and fake provider clients. The companion patch was
also validated against Herdr's client UI suite. The repository preview contains
synthetic data only.

Independent community plugin. Plugin code is MIT licensed. The companion patch
modifies Apache-2.0-licensed Herdr; its upstream license is included. Herdr,
Codex and Claude belong to their respective owners.
