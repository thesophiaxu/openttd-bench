"""Run an evaluation, then convert its completed artifacts even on nonzero exit.

Usage: python -m openttd_bench.run_with_atif outputs/RUN -- eval @ config.toml ...
The eval command must write to the same output directory.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

from openttd_bench.atif import convert_run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("an evaluation command is required after --")
    args.run.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "OPENTTD_RUN_DIR": str(args.run.resolve())}
    child = subprocess.Popen(command, env=env, start_new_session=True)

    def forward(signum: int, _frame: object) -> None:
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, forward)
    status = child.wait()
    status = status if status >= 0 else 128 - status
    try:
        manifest = convert_run(args.run)
        print(f"ATIF: {len(manifest['trajectories'])} trajectories -> {args.run}/trajectories", flush=True)
    except Exception as exc:
        print(f"ATIF conversion failed: {exc}", file=sys.stderr, flush=True)
        return status or 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
