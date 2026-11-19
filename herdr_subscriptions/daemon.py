"""One shared section updater; stock fallback workers remain session-local."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from . import __version__, collector, herdr, storage


def controller_dir():
    if storage.settings()["sidebar_mode"] == "section":
        return storage.state_dir() / "section-controller"
    return storage.session_dir()


def generation(root=None):
    return storage.read_json((root or controller_dir()) / "generation.json", {}).get("generation")


def running(root=None):
    root = storage.private_dir(root or controller_dir())
    with (root / "daemon.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def signal_stop(root):
    (root / "stop").touch(mode=0o600)
    # Older workers already observe this generation, even if another start clears stop.
    storage.write_json(root / "generation.json", {"generation": str(uuid.uuid4())})


def wait_stopped(root):
    deadline = time.monotonic() + 35
    while running(root) and time.monotonic() < deadline:
        time.sleep(0.1)
    if running(root):
        raise RuntimeError("previous updater is still stopping")


def retire_legacy_sections():
    """Drain old session-local writers before starting the single section writer."""
    if storage.settings()["sidebar_mode"] != "section":
        return
    legacy = storage.state_dir() / "sessions"
    if not legacy.is_dir():
        return
    active = [path for path in legacy.iterdir()
              if path.is_dir() and (path / "daemon.lock").is_file() and running(path)]
    for root in active:
        signal_stop(root)
    for root in active:
        wait_stopped(root)


def start():
    root = storage.private_dir(controller_dir())
    # Native clients opened from an outer shell or an agent share this lock.
    with (root / "control.lock").open("a+") as control:
        fcntl.flock(control, fcntl.LOCK_EX)
        retire_legacy_sections()
        if running(root):
            version = storage.read_json(root / "runtime.json", {}).get("version")
            if version == __version__:
                return
            signal_stop(root)
            wait_stopped(root)
        (root / "stop").unlink(missing_ok=True)
        storage.write_json(root / "generation.json", {"generation": str(uuid.uuid4())})
        env = dict(os.environ)
        project = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = project + os.pathsep + env.get("PYTHONPATH", "")
        subprocess.Popen([sys.executable, "-m", "herdr_subscriptions", "worker"],
                         cwd=project, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        # Wait for ownership/version, never for provider network requests.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if running(root) and storage.read_json(root / "runtime.json", {}).get("version") == __version__:
                return
            time.sleep(0.05)
        raise RuntimeError("updater did not start")


def stop():
    root = storage.private_dir(controller_dir())
    with (root / "control.lock").open("a+") as control:
        fcntl.flock(control, fcntl.LOCK_EX)
        signal_stop(root)
        retire_legacy_sections()
        if storage.settings()["sidebar_mode"] == "agents":
            herdr.clear()
        from . import native
        native.clear()


def request_refresh():
    root = storage.private_dir(controller_dir())
    (root / "refresh").touch(mode=0o600)
    start()


def worker():
    root = storage.private_dir(controller_dir())
    with (root / "daemon.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        ours = generation(root)
        storage.write_json(root / "runtime.json", {"pid": os.getpid(), "version": __version__})
        next_poll = 0
        next_fetch = 0
        errors = 0
        future = None
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            while generation(root) == ours and not (root / "stop").exists():
                now = time.time()
                force = (root / "refresh").exists()
                if future is not None and future.done():
                    try:
                        future.result()
                    except Exception:
                        pass  # Cache remains valid; never log raw provider output.
                    future = None
                    next_poll = 0
                    snapshot = storage.cached()
                    for sub in snapshot["subscriptions"]:
                        for limit in sub.get("limits", []):
                            reset = limit.get("resets_at")
                            if reset and reset > now:
                                next_fetch = min(next_fetch, reset + 2)
                if future is None and (force or now >= next_fetch):
                    (root / "refresh").unlink(missing_ok=True)
                    next_fetch = now + storage.settings()["refresh_seconds"]
                    future = executor.submit(collector.refresh, force=True)
                if now >= next_poll:
                    try:
                        herdr.publish()
                        errors = 0
                    except Exception:
                        errors += 1
                        if errors >= 10:
                            break  # Session stopped; do not leave an orphan updater.
                    next_poll = now + 5
                time.sleep(min(1, max(0.01, next_fetch - time.time())) if future is None else 1)
        finally:
            executor.shutdown(wait=True)
            try:
                if storage.settings()["sidebar_mode"] == "agents":
                    herdr.clear()
                from . import native
                native.clear()
            except Exception:
                pass
            (root / "runtime.json").unlink(missing_ok=True)
