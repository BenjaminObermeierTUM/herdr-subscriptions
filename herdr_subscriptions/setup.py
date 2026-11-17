"""Preview and apply a small, backed-up Herdr config addition."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from . import herdr, storage
from .render import SIDEBAR_ROWS

BEGIN = "# BEGIN herdr-subscriptions"
END = "# END herdr-subscriptions"


def snippet():
    lines = [BEGIN, "[ui.sidebar.agents]", "rows = [",
             '  ["state_icon", "workspace", "tab"],', '  ["agent"],']
    for index in range(1, SIDEBAR_ROWS + 1):
        lines.append('  [{ token = "$subscriptions_%02d", rules = [' % index
                     + '{ starts_with = "! ", fg = "#f7768e", bold = true }, '
                     + '{ starts_with = "~ ", fg = "#e0af68" }, '
                     + '{ starts_with = "? ", dim = true }'
                     + '] }],')
    lines.extend(["]", "", "[[keys.command]]", 'key = "prefix+u"', 'type = "plugin_action"',
                  'command = "subscriptions.open"', 'description = "subscription limits"', END])
    return "\n".join(lines) + "\n"


def proposed(original):
    if original.count(BEGIN) != original.count(END) or original.count(BEGIN) > 1:
        raise ValueError("existing plugin config markers are malformed")
    if BEGIN in original:
        return re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", snippet(), original, flags=re.S)
    if re.search(r"(?m)^\s*\[ui\.sidebar\.agents(?:\.rows_by_agent)?\]\s*(?:#.*)?$", original):
        raise ValueError("existing agent sidebar layout found; append the printed tokens to your rows manually")
    if re.search(r'(?m)^\s*key\s*=\s*[\"\']prefix\+u[\"\']', original):
        raise ValueError("prefix+u is already configured; choose another dashboard key in the printed snippet")
    return original.rstrip() + "\n\n" + snippet()


def config_path():
    return Path(os.environ.get("HERDR_CONFIG_PATH", str(Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "herdr/config.toml")))


def apply(*, remove=False):
    path = config_path()
    original = path.read_text() if path.exists() else ""
    if remove:
        candidate = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", original, flags=re.S)
    else:
        candidate = proposed(original)
    if candidate == original:
        return None
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=".subscriptions-config-", suffix=".toml", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(candidate)
        env = dict(os.environ, HERDR_CONFIG_PATH=temporary)
        check = subprocess.run([herdr.binary(), "config", "check"], env=env, capture_output=True, timeout=10)
        if check.returncode:
            raise ValueError("Herdr rejected the proposed config; the original was not changed")
        backup = storage.private_dir(storage.config_dir() / "backups") / (datetime.now(timezone.utc).strftime("herdr-%Y%m%dT%H%M%S.%fZ.toml"))
        backup.write_text(original)
        backup.chmod(0o600)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        return backup
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
