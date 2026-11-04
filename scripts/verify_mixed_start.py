#!/usr/bin/env python3
"""One-shot readiness check: moving clock, all configured LLM seats active, correct start date."""
import argparse
import json
import os
from pathlib import Path
import time

from openttd_bench.task_spec import TaskSpec

parser = argparse.ArgumentParser()
parser.add_argument("root", type=Path)
parser.add_argument("--pid", type=int, required=True)
parser.add_argument("--task-id", required=True)
parser.add_argument("--llm-seats", type=int, default=2, choices=range(1, 5))
args = parser.parse_args()
manifest = args.root / "manifest.json"
m = json.loads(manifest.read_text())
m.update(status="launching", task_id=args.task_id, pid=args.pid, launched_unix=time.time())
manifest.write_text(json.dumps(m, indent=2) + "\n")
deadline = time.monotonic() + 1500
while time.monotonic() < deadline:
    try:
        os.kill(args.pid, 0)
    except ProcessLookupError:
        raise SystemExit("Evaluation exited before readiness; inspect the main task output.")
    for path in (args.root / "progress").glob("*.jsonl"):
        rows = path.read_text().splitlines()
        if not rows:
            continue
        try:
            row = json.loads(rows[-1])
        except json.JSONDecodeError:
            continue
        clock = row.get("clock", {})
        if clock.get("started"):
            if set(clock["active"]) != set(range(args.llm_seats)):
                raise SystemExit(f"Missing active LLM seat: {clock}")
            audit = next((args.root / "saves").glob("*/world.json")).parent
            spec = TaskSpec.model_validate_json((audit / "spec.json").read_text())
            initial = json.loads((audit / "initial-snapshot.json").read_text())
            if initial["date"]["text"] != f"{spec.map.start_year}-01-01":
                raise SystemExit(f"initial date is {initial['date']}, expected {spec.map.start_year}-01-01")
            m.update(status="running", startup_verified_unix=time.time(), initial_date=initial["date"])
            manifest.write_text(json.dumps(m, indent=2) + "\n")
            print(json.dumps({"event": "startup_confirmed", "all_llm_seats_active": True, "llm_seats": args.llm_seats,
                              "progress": row}, indent=2), flush=True)
            raise SystemExit(0)
    time.sleep(10)
raise SystemExit("Startup verification timed out; inspect main task and sandbox.")
