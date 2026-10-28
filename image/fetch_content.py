#!/usr/bin/env python3
"""Pinned, checksummed content acquisition: OpenTTD 15.3, OpenGFX, OpenSFX, RailwAI.

Runs at image build (Dockerfile) and again, idempotently, from `init_game` so a stock
public image bootstraps itself at setup (Prime sandboxes have egress then).
`--cache` keeps the downloads; `--install` is the OpenTTD installation tree
(binary, baseset/, ai/, game/) that the game process runs from.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

OPENTTD_VERSION = "15.3"
USER_AGENT = "openttd-bench/3.0 (pinned content fetch; python-urllib)"
ASSETS = {
    "openttd": {
        "url": f"https://cdn.openttd.org/openttd-releases/{OPENTTD_VERSION}/openttd-{OPENTTD_VERSION}-linux-generic-amd64.tar.xz",
        "sha256": "f49eb25d61b00f8f4d332fee02b530ad75552d1efb8f2bb01e7ca5e6540fe059",
        "file": f"openttd-{OPENTTD_VERSION}-linux-generic-amd64.tar.xz",
    },
    "opengfx": {
        "url": "https://cdn.openttd.org/opengfx-releases/8.0/opengfx-8.0-all.zip",
        "sha256": "43a0c1dabf39cb865394f3a6cc36d4da5c10ecfaaf55652043104806810903be",
        "file": "opengfx-8.0-all.zip",
    },
    "opensfx": {
        "url": "https://cdn.openttd.org/opensfx-releases/1.0.3/opensfx-1.0.3-all.zip",
        "sha256": "e0a218b7dd9438e701503b0f84c25a97c1c11b7c2f025323fb19d6db16ef3759",
        "file": "opensfx-1.0.3-all.zip",
    },
    # RailwAI v30 = BaNaNaS content id 419267 (unique id 52776169), pinned.
    "railwai": {
        "url": "https://bananas-cdn.openttd.org/ai/52776169/8f6a346ff87bd90b64ae2f7d1dc36506/52776169-RailwAI-30.tar.gz",
        "sha256": "711e54fafb8dd5b7770bd77c83a71511fa82abc48f9427cae55b24ea6bb4c284",
        "file": "RailwAI-30.tar.gz",
    },
}


# Same GPL-3.0 source archive as outputs/hogex-baseline-20261011.
HOGEX_COMMIT = "6480b7638dbadb1585ed29bf2e213a6ad9be1c6b"
HOGEX_ASSET = {
    "url": f"https://codeload.github.com/rei-artist/AAAHogEx/tar.gz/{HOGEX_COMMIT}",
    "sha256": "7a49158ebd74681e1deda776b8014ad838acf5f60c16d2d9ab1092ecee44e476",
    "file": "AAAHogEx-v115.tar.gz",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(cache: Path, assets: dict | None = None) -> dict[str, Path]:
    cache.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for key, asset in (ASSETS if assets is None else assets).items():
        target = cache / asset["file"]
        if target.exists() and sha256(target) == asset["sha256"]:
            paths[key] = target
            continue
        print(f"fetching {asset['url']}", flush=True)
        tmp = target.with_suffix(target.suffix + ".part")
        # The OpenTTD CDN refuses Python's default User-Agent (HTTP 403); identify ourselves.
        request = urllib.request.Request(asset["url"], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as resp, tmp.open("wb") as out:
            shutil.copyfileobj(resp, out)
        digest = sha256(tmp)
        if digest != asset["sha256"]:
            tmp.unlink(missing_ok=True)
            raise SystemExit(f"{asset['file']}: sha256 {digest} != pinned {asset['sha256']}")
        tmp.replace(target)
        paths[key] = target
    return paths


def install(cache: Path, install_dir: Path) -> Path:
    """Extract into `install_dir`; returns the binary path. Idempotent."""
    paths = fetch(cache)
    binary = install_dir / "openttd"
    if not binary.exists():
        install_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(paths["openttd"], "r:xz") as tar:
            members = tar.getmembers()
            top = members[0].name.split("/", 1)[0]
            for member in members:
                if not member.name.startswith(top + "/"):
                    continue
                member.name = member.name[len(top) + 1 :]
                if member.name:
                    tar.extract(member, install_dir)
        binary.chmod(0o755)
    baseset = install_dir / "baseset"
    baseset.mkdir(exist_ok=True)
    for key in ("opengfx", "opensfx"):
        with zipfile.ZipFile(paths[key]) as zf:
            for name in zf.namelist():
                if name.endswith(".tar") and not (baseset / Path(name).name).exists():
                    (baseset / Path(name).name).write_bytes(zf.read(name))
    ai_dir = install_dir / "ai"
    ai_dir.mkdir(exist_ok=True)
    railwai = ai_dir / "RailwAI-30"
    if not railwai.exists():
        with tarfile.open(paths["railwai"], "r:gz") as tar:
            tar.extractall(ai_dir)
    (install_dir / "game").mkdir(exist_ok=True)
    return binary


def install_hogex(cache: Path, install_dir: Path, *, no_loans: bool = False) -> Path:
    """Install exactly the baseline HogEx version, only when a world requests it."""
    archive = fetch(cache, {"hogex": HOGEX_ASSET})["hogex"]
    ai_dir = install_dir / "ai"
    ai_dir.mkdir(parents=True, exist_ok=True)
    target = ai_dir / f"AAAHogEx-{HOGEX_COMMIT}"
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(ai_dir, filter="data")
    if not (target / "info.nut").is_file():
        raise RuntimeError("HogEx archive lacks info.nut")
    if no_loans:
        # v115 estimates inflation as current_max_loan / initial_max_loan.
        # Both become zero when borrowing is disabled. Inflation is disabled by
        # init_game, so the correct multiplier is 1.0, not 0/0 (NaN).
        main = target / "main.nut"
        source = main.read_text()
        old = "return AICompany.GetMaxLoanAmount().tofloat() / HogeAI.Get().GetMaxLoan().tofloat();"
        new = "return HogeAI.Get().GetMaxLoan() == 0 ? 1.0 : AICompany.GetMaxLoanAmount().tofloat() / HogeAI.Get().GetMaxLoan().tofloat();"
        if source.count(old) != 1:
            raise RuntimeError("Pinned HogEx inflation expression changed; refusing an unverified patch")
        main.write_text(source.replace(old, new))
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=Path("/opt/openttd/cache"))
    parser.add_argument("--install", type=Path, default=Path("/opt/openttd/install"))
    args = parser.parse_args()
    binary = install(args.cache, args.install)
    print(f"installed {binary}")


if __name__ == "__main__":
    sys.exit(main())
