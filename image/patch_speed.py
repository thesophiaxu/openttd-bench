#!/usr/bin/env python3
"""Patch an OpenTTD 15.x binary so its unpaused game speed is a fixed percentage.

OpenTTD has no dedicated-server speed setting. Since 14.0 the dedicated main loop
no longer resets `_game_speed` every frame (the 13.x two-site patch is dead); the
one thing that decides the tick interval is `VideoDriver::GetGameInterval()`:

    microseconds(MILLISECONDS_PER_TICK * 1000 * 100 / _game_speed)

which the 15.3 official amd64 build inlines TWICE into `VideoDriver::GameLoop`
(`next_game_tick += interval` and the drift check), each as

    movzwl %dx,%ecx ; mov $0x2932e0,%eax ; xor %edx,%edx ; div %ecx

where 0x2932e0 = 2,700,000. Replacing that immediate with 2,700,000 * 100 / percent
scales the unpaused tick interval (27 ms at 100 %) without touching `_game_speed`,
so the paused branch (a fixed 27 ms tick, where the GameScript still runs) and the
fast-forward toggle keep their meaning.

Discipline (fail closed): the full instruction signature must match EXACTLY the
expected number of times, or the patcher refuses. A version bump then breaks the
image build visibly, never silently.
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

NOMINAL_INTERVAL_US = 27 * 1000 * 100  # MILLISECONDS_PER_TICK * 1000 * 100

# movzwl %dx,%ecx ; mov $imm32,%eax ; xor %edx,%edx ; div %ecx ; mov %eax,%eax
SIGNATURE_PREFIX = bytes.fromhex("0f b7 ca b8")
SIGNATURE_SUFFIX = bytes.fromhex("31 d2 f7 f1 89 c0")
EXPECTED_SITES = 2


def scaled_interval(percent: int) -> int:
    if not 1 <= percent <= 400:
        raise ValueError("percent must be between 1 and 400")
    return NOMINAL_INTERVAL_US * 100 // percent


def patch(image: bytes, percent: int) -> bytes:
    """Return the patched image, or raise if the binary is not the one we expect."""
    nominal = NOMINAL_INTERVAL_US.to_bytes(4, "little")
    signature = SIGNATURE_PREFIX + nominal + SIGNATURE_SUFFIX
    count = image.count(signature)
    if count != EXPECTED_SITES:
        raise SystemExit(
            "Refusing to patch OpenTTD: expected the game-interval signature exactly "
            f"{EXPECTED_SITES} times, found {count}. The binary is not the pinned 15.x "
            "build this patcher was derived for; re-derive the signature."
        )
    replacement = SIGNATURE_PREFIX + scaled_interval(percent).to_bytes(4, "little") + SIGNATURE_SUFFIX
    return image.replace(signature, replacement)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--percent", type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.percent <= 400:
        parser.error("--percent must be between 1 and 400")
    patched = patch(args.source.read_bytes(), args.percent)
    args.output.write_bytes(patched)
    shutil.copymode(args.source, args.output)
    os.chmod(args.output, args.output.stat().st_mode | 0o111)
    print(f"patched {args.source} -> {args.output} at {args.percent}%")


if __name__ == "__main__":
    main()
