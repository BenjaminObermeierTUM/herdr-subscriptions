#!/usr/bin/env python3
"""Build the optional client from a pinned upstream source and the included patch."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

PROJECT = Path(__file__).resolve().parent.parent
BASE = "2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01"
UPSTREAM = "https://github.com/herdrdev/herdr.git"
PATCH = PROJECT / "host-patches/herdr-0.9.5-subscriptions.patch"


def run(args, cwd, **kwargs):
    return subprocess.run(args, cwd=cwd, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="use an existing dedicated Herdr 0.9.5 source checkout")
    parser.add_argument("--check-only", action="store_true", help="verify the pinned source and patch without building")
    args = parser.parse_args()
    source = (args.source or PROJECT / ".local/herdr-v0.9.5").resolve()
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--depth", "1", "--branch", "v0.9.5", UPSTREAM, str(source)], PROJECT)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if head != BASE:
        raise SystemExit(f"Expected the pinned Herdr 0.9.5 source ({BASE}); got {head}")
    reverse = subprocess.run(["git", "apply", "--reverse", "--check", str(PATCH)], cwd=source, capture_output=True)
    if reverse.returncode:
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=source):
            raise SystemExit("Source has other changes; use a fresh dedicated checkout")
        run(["git", "apply", "--check", str(PATCH)], source)
        if not args.check_only:
            run(["git", "apply", str(PATCH)], source)
    if args.check_only:
        print("Pinned source and client-only patch verified")
        return
    if not shutil.which(os.environ.get("ZIG", "zig")):
        raise SystemExit("Building requires Zig 0.16.0. Install it from https://ziglang.org/download/ or set ZIG to its executable.")
    run(["cargo", "build", "--locked", "--release"], source)
    destination = PROJECT / "native/bin/herdr"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "target/release/herdr", destination)
    destination.chmod(0o755)
    manifest = {"base_commit": BASE, "patch_sha256": hashlib.sha256(PATCH.read_bytes()).hexdigest(),
                "client_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}
    (destination.parent / "build.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Client ready: {destination}\nStart with: {PROJECT / 'bin/herdr-with-subscriptions'}")


if __name__ == "__main__":
    main()
