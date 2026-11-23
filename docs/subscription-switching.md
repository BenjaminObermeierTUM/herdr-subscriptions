# Switch coding subscriptions in Herdr

Click **Claude Team**, **Claude Ultra**, or **Codex Pro** in the Subscriptions sidebar to use that login. The first click opens a named agent tab in the current worktree. Click the same heading again in that workspace and worktree to return to its open conversation. Each subscription keeps its own tab.

The brackets beside each heading show its shell command, for example **Claude Ultra [claude2]**. The default logins use `claude` and `codex`. On the next usage refresh, each additional login gets the next available number for its provider, such as `claude3` or `codex2`. You can pass the usual CLI arguments to these commands.

Herdr installs numbered launchers in `~/.local/bin` (or `$XDG_BIN_HOME`). Put this directory on your shell’s `PATH` to use the short names. Existing executable names are skipped. Renaming or reordering subscriptions preserves their numbers. Removing a subscription disables its command and keeps its number reserved for that login.

For keyboard selection, press **Ctrl+B**, then **U** to open the dashboard. Press **s**, type the number beside the subscription, and press **Enter**.

## Add a second Claude login

Keep the first subscription on the default `claude` login. From a shell, sign in to the second subscription using a separate configuration directory. Choose the second account in the browser before authorizing.

```sh
CLAUDE_CONFIG_DIR="$HOME/.claude-secondary" claude auth login --claudeai
```

Check that the two commands below show the intended accounts. If either login expires, repeat the login command for that account using the same directory.

```sh
claude auth status --text
CLAUDE_CONFIG_DIR="$HOME/.claude-secondary" claude auth status --text
```

## Configure the sidebar

Run `herdr plugin config-dir subscriptions` to find the directory containing `config.json`. Add the `subscriptions` array shown here to that file, preserving any other settings. Use a unique, stable ID for each login. You can change its display label later.

```json
{
  "subscriptions": [
    {"id": "codex-pro", "provider": "codex", "label": "Codex Pro"},
    {"id": "claude-team", "provider": "claude", "label": "Claude Team"},
    {
      "id": "claude-ultra",
      "provider": "claude",
      "label": "Claude Ultra",
      "config_dir": "~/.claude-secondary",
      "show_idle_five_hour": false
    }
  ]
}
```

An omitted `config_dir` selects the provider’s default login. For additional logins, use the same directory you used to sign in. A Claude entry uses it as `CLAUDE_CONFIG_DIR`, and a Codex entry uses it as `CODEX_HOME`.

## Activate clickable headings

For a fresh installation, follow the [companion client setup](../README.md#install-the-independent-section). When upgrading an existing checkout, run the build from the plugin directory.

```sh
python3 scripts/build-client.py
```

Detach the old client with **Ctrl+B**, then **Q**. From the outer shell, run `./bin/herdr-with-subscriptions` from the plugin directory. Your running agents stay attached to the Herdr server.

## Read usage and resets

- **Team five-hour limit.** An idle window shows `Starts on first use`. Once Claude reports a reset timestamp, the sidebar shows the local time and countdown.
- **Ultra weekly limits.** For a verified Ultra account with weekly limits only, `show_idle_five_hour: false` hides the empty five-hour row the CLI sometimes returns. A reported five-hour reset time or positive session usage still appears.
- **Saved resets.** To see the `Resets` count, run `python3 -m herdr_subscriptions browser-setup` from the plugin directory. In each account’s signed-in Chrome profile, open `chrome://extensions`, enable **Developer mode**, and use **Load unpacked** to load the directory printed for that account. Reload an existing extension after upgrading. Quota readings and account switching work independently of the Chrome monitor.
