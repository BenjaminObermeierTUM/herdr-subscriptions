# Independent sidebar section

The companion client is based on upstream Herdr **v0.9.5**, commit
`2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01`:
https://github.com/herdrdev/herdr/tree/2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01

`herdr-0.9.5-subscriptions.patch` adds a client-local section below Spaces and
Agents. The client polls the file named by `HERDR_SUBSCRIPTIONS_FILE` once per
second. Rendering uses an already-decoded, bounded cache, never filesystem IO.
The plugin shows the Codex saved-reset count as text. Clicking an eligible Claude reset offer opens the Claude Usage page. The section scrolls independently of the agent list.
A missing setting leaves the original layout unchanged. Small windows preserve
room for native lists and allow independent wheel scrolling in Subscriptions.

The feed is versioned JSON, at most 64 KiB and 64 rows. Text controls are removed.
Malformed or expired feeds preserve last-good data visibly marked stale. An empty
row set hides the section. The plugin supplies only normalized subscription
metadata and writes the feed with private permissions.

No server APIs, messages, frozen wire codecs, or agent/workspace structures are
changed. The stock binary is not overwritten. The patch is distributed here as
a custom client extension; no upstream contribution or endorsement is implied.

Validation on macOS arm64:

- Cargo check against the pinned source.
- Nine section-specific tests, including independent rendering with no agents,
  short-window geometry, and malformed/oversized feed handling.
- Herdr client UI suite: **388 passed, 2 ignored**.
- Python provider, privacy, cache, setup and rendering tests in this repository.

Build with `python3 scripts/build-client.py`, or validate the pinned source/patch
without building with `--check-only`. The builder uses Herdr's pinned Rust
configuration and requires Zig 0.16.0. Validation in the development environment
used Rust 1.96.0 and Zig 0.16.0. This is a macOS/Linux extension; Windows is not
currently packaged or verified.

The upstream Apache-2.0 license is included as `LICENSE.herdr`. The remainder of
this repository is MIT licensed unless otherwise noted.
