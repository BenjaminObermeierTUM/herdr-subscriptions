"""Install numbered shell commands for additional logins during a usage refresh."""
from itertools import count
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile

from . import storage


def profile_script(entry):
    root = storage.private_dir(storage.subscription_dir(entry))
    path = root.resolve() / "launch"
    agent = Path(__file__).resolve().with_name("agent.py")
    command = [sys.executable, "-I", str(agent), str(storage.config_dir().resolve()), entry["id"], entry["binding"]]
    script = '#!/bin/sh\nexec ' + shlex.join(command) + ' "$@"\n'
    if path.is_file() and path.read_bytes() == script.encode():
        return path
    with tempfile.NamedTemporaryFile(mode="w", dir=root, delete=False) as temporary:
        try:
            temporary.write(script)
            temporary.flush()
            os.fchmod(temporary.fileno(), 0o700)
            os.replace(temporary.name, path)
        finally:
            Path(temporary.name).unlink(missing_ok=True)
    return path


def numbered_command(entry):
    provider = entry["provider"]
    # The immutable link target ties a number to a login even across config reordering.
    target = profile_script(entry)
    folder = Path(os.environ.get("XDG_BIN_HOME", Path.home() / ".local/bin"))
    folder = folder.expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    for link in sorted(folder.glob(provider + "[0-9]*")):
        if link.name[len(provider):].isdecimal() and link.is_symlink() and link.readlink() == target:
            break
    else:
        for number in count(2):
            link = folder / f"{provider}{number}"
            if shutil.which(link.name):
                continue
            try:
                link.symlink_to(target)
                break
            except FileExistsError:
                continue
    found = shutil.which(link.name)
    if found and link.samefile(found):
        return link.name
    if link.is_relative_to(Path.home()):
        return "~/" + shlex.quote(str(link.relative_to(Path.home())))
    return shlex.quote(str(link))


def command_for(entry):
    provider, home = entry["provider"], entry["config_dir"]
    if home is None or Path(home).resolve() == (Path.home() / ("." + provider)).resolve():
        return provider
    try:
        return numbered_command(entry)
    except OSError:
        # The full invocation also works when the shell command directory is read-only.
        variable = "CLAUDE_CONFIG_DIR" if provider == "claude" else "CODEX_HOME"
        return shlex.join(["env", f"{variable}={home}", provider])
