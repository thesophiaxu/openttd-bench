#!/usr/bin/env python3
"""Run explicit stages: parallel within a stage, strictly sequential between stages."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from scripts.watch_run import post


def run(path: Path) -> int:
    plan = json.loads(path.read_text())
    if plan["status"] != "prepared":
        raise RuntimeError("Series already started; refusing duplicate execution")
    with path.with_suffix(".claimed").open("x") as lock:
        lock.write(str(os.getpid()))
    active = []

    def save():
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(plan, indent=2) + "\n")
        temp.replace(path)

    def cancel(signum, _frame):
        plan.update(status="cancelled", finished_unix=time.time())
        save()
        # eval runs in its own process group under run_with_atif. Stop those
        # controllers before stopping the shell supervisors, allowing teardown.
        roots = [str(root) for _, root, _ in active]
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode()
                pid = int(entry.name)
                if ".venv/bin/eval " in command and "-m openttd_bench.run_with_atif" not in command and any(root in command for root in roots):
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (OSError, UnicodeDecodeError):
                pass
        for proc, _, _ in active:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        post("small-pineapple-good-morning", "OpenTTD model series cancelled", "No queued games will be launched. Existing artifacts retained.")
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    plan.update(status="running", started_unix=time.time(), pid=os.getpid())
    save()
    any_failed = False
    for index, stage in enumerate(plan["stages"], 1):
        plan["active_stage"] = index
        save()
        post("small-pineapple-good-morning", f"OpenTTD model series: stage {index}/{len(plan['stages'])}",
             "Starting " + " + ".join(plan["runs"][key]["label"] for key in stage))
        active = []
        for key in stage:
            item = plan["runs"][key]
            root = Path(item["out"])
            log = (root / "launch.log").open("w")
            proc = subprocess.Popen(["bash", "scripts/run_ledger_episode_monitored.sh", str(root)],
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            log.close()
            (root / "launch.pid").write_text(str(proc.pid) + "\n")
            item.update(status="launching", pid=proc.pid, started_unix=time.time())
            active.append((proc, root, key))
        save()
        # The user explicitly requested the whole first pair finish before Sol5.6,
        # and Sol5.6 finish before GPT5.5. No next-stage probes run early either.
        for proc, root, key in active:
            code = proc.wait()
            manifest = json.loads((root / "manifest.json").read_text())
            plan["runs"][key].update(status=manifest["status"], exit_code=code, finished_unix=time.time())
            any_failed |= code != 0 or manifest["status"] != "complete"
            save()
        active = []
    plan.update(status="complete_with_issues" if any_failed else "complete", finished_unix=time.time())
    save()
    post("small-pineapple-good-morning", "OpenTTD model series finished", "\n".join(
        f"{item['label']}: {item['status']}" for item in plan["runs"].values()))
    return int(any_failed)


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1])))
