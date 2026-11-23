"""Isolated Python entrypoint for a numbered shell command."""
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from herdr_subscriptions import selection, storage

if __name__ == "__main__":
    try:
        config_dir, sid, binding, *args = sys.argv[1:]
        os.environ["HERDR_PLUGIN_CONFIG_DIR"] = config_dir
        entry = storage.subscription(sid)
        if entry["binding"] != binding:
            raise ValueError("the login for this command is no longer configured")
        selection.launch_agent(entry, args)
    except (ValueError, OSError) as exc:
        sys.exit(f"Subscriptions: {exc}")
