"""One small updater per Herdr session; providers share a locked local cache."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from . import collector, herdr, storage


def generation():
    return storage.read_json(storage.session_dir() / "generation.json", {}).get("generation")


def running():
    root = storage.private_dir(storage.session_dir())
    with (root / "daemon.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def start():
    root = storage.private_dir(storage.session_dir())
    stop = root / "stop"
    stop.unlink(missing_ok=True)
    if running():
        return
    storage.write_json(root / "generation.json", {"generation": str(uuid.uuid4())})
    env = dict(os.environ)
    project = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = project + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.Popen([sys.executable, "-m", "herdr_subscriptions", "worker"],
                     cwd=project, env=env, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    # Wait briefly for the daemon lock, not for the provider network requests.
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if running():
            return
        time.sleep(0.05)
    raise RuntimeError("updater did not start")


def stop():
    root = storage.private_dir(storage.session_dir())
    (root / "stop").touch(mode=0o600)
    # The worker notices within two seconds. Token TTL also handles crashes.
    herdr.clear()


def request_refresh():
    storage.private_dir(storage.session_dir())
    (storage.session_dir() / "refresh").touch(mode=0o600)
    start()


def worker():
    root = storage.private_dir(storage.session_dir())
    with (root / "daemon.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        ours = generation()
        next_poll = 0
        next_fetch = 0
        errors = 0
        future = None
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            while generation() == ours and not (root / "stop").exists():
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
                    next_fetch = now + storage.settings()["refresh_seconds"]
                    for sub in snapshot["subscriptions"]:
                        for limit in sub.get("limits", []):
                            reset = limit.get("resets_at")
                            if reset and reset > now:
                                next_fetch = min(next_fetch, reset + 2)
                if future is None and (force or now >= next_fetch):
                    (root / "refresh").unlink(missing_ok=True)
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
                time.sleep(1)
        finally:
            executor.shutdown(wait=True)
            try:
                herdr.clear()
            except Exception:
                pass
