#!/usr/bin/env python3
"""Claim and preflight a scheduled ledger run. No inference before not_before_unix."""
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from scripts.watch_run import post


def ensure_codex(version: str, log) -> Path:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", version):
        raise ValueError("Codex must use an exact pinned release")
    prefix = Path("/tmp") / f"openttd-codex-{version}"
    prefix.mkdir(parents=True, exist_ok=True)
    binary = prefix / "node_modules/.bin/codex"
    with (prefix / ".install.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not binary.exists():
            subprocess.run(["npm", "install", "--prefix", str(prefix), "--ignore-scripts",
                            "--no-audit", "--no-fund", "--omit=dev", f"@openai/codex@{version}"],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        actual = subprocess.check_output([str(binary), "--version"], text=True, timeout=20).strip()
        if actual != f"codex-cli {version}":
            raise RuntimeError(f"Codex binary version mismatch: {actual!r}")
    return binary


def main(root: Path) -> None:
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    if time.time() < manifest["not_before_unix"]:
        raise RuntimeError("Scheduled start time has not arrived; refusing early inference")
    if manifest["status"] != "scheduled" or (root / "eval.pid").exists():
        raise RuntimeError("Run is no longer scheduled or has already launched")
    with (root / "launch-claimed").open("x") as file:
        file.write(f"{os.getpid()} {time.time()}\n")
    manifest.update(status="preflight", launched_unix=time.time())
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    try:
        dependency = manifest.get("launch_after_run")
        if dependency:
            manifest["status"] = "waiting_dependency"
            path.write_text(json.dumps(manifest, indent=2) + "\n")
            dependency_path = Path(dependency) / "manifest.json"
            terminal = {"complete", "complete_with_caveat", "failed", "preflight_failed", "cancelled", "cancelled_before_launch"}
            print(f"Queued: waiting for {dependency} to finish before any probes or game startup.", flush=True)
            while True:
                try:
                    predecessor = json.loads(dependency_path.read_text())
                except json.JSONDecodeError:
                    time.sleep(5)
                    continue
                if predecessor.get("status") in terminal:
                    break
                time.sleep(5)
            manifest.update(status="preflight", dependency_finished_status=predecessor["status"])
            path.write_text(json.dumps(manifest, indent=2) + "\n")
        with (root / "preflight.log").open("w") as log:
            binary = ensure_codex(manifest["codex_version"], log)
            subprocess.run([sys.executable, "-m", "pytest", "-q"], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
            auth_mode = manifest.get("auth_mode", "prime-api")
            if auth_mode not in ("prime-api", "chatgpt"):
                raise ValueError("Unsupported inference authentication mode")
            probe_script = "scripts/probe_codex_oauth.py" if auth_mode == "chatgpt" else "scripts/probe_codex_prime.py"
            subprocess.run([sys.executable, probe_script, "--binary", str(binary),
                            "--out", str(root / "codex-preflight.json"), *dict.fromkeys(manifest["models"])],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=240)
        files = [p for folder in ["image", "openttd_bench", "harnesses", "scripts", "tests"]
                 for p in Path(folder).rglob("*") if p.suffix in {".py", ".nut", ".sh"} and "__pycache__" not in p.parts]
        files += [Path("pyproject.toml"), Path(manifest["config"])]
        hashes = {}
        for source in files:
            dest = root / "source" / source
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            hashes[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
        (root / "source-sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
        manifest.update(status="launching", preflight_passed_unix=time.time())
        path.write_text(json.dumps(manifest, indent=2) + "\n")
    except Exception as exc:
        manifest.update(status="preflight_failed", finished_unix=time.time(), error=str(exc))
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        post("small-pineapple-good-morning", "OpenTTD scheduled run: preflight FAILED",
             f"No game launched. Models: {', '.join(dict.fromkeys(manifest['models']))}. Inspect {root}/preflight.log; no automatic replacement or retry.")
        raise


if __name__ == "__main__":
    main(Path(sys.argv[1]))
