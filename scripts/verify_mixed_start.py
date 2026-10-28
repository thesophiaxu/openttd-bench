#!/usr/bin/env python3
"""One-shot readiness check: funding, moving clock, all configured LLM seats active."""
import argparse
import json
import os
from pathlib import Path
import time

parser = argparse.ArgumentParser()
parser.add_argument("root", type=Path)
parser.add_argument("--pid", type=int, required=True)
parser.add_argument("--task-id", required=True)
parser.add_argument("--llm-seats", type=int, default=2, choices=range(1, 5))
parser.add_argument("--companies", type=int, default=4, choices=range(1, 5))
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
            assert set(clock["active"]) == set(range(args.llm_seats)), f"Missing active LLM seat: {clock}"
            world = json.loads(next((args.root / "saves").glob("*/world.json")).read_text())
            assert world["rules"]["game_speed_percent"] == m["speed_percent"]
            assert world["llm_seats"] == args.llm_seats
            initial = json.loads(next((args.root / "saves").glob("*/initial-snapshot.json")).read_text())
            if m.get("accounting_version"):
                from openttd_bench.accounting import validate_accounting
                validate_accounting(initial, range(args.companies))
                validate_accounting(row, range(args.companies))
            assert initial["date"]["text"] == "1950-01-01"
            assert len(initial["companies"]) == args.companies
            assert all(c["cash"] == 800000 and c["loan"] == 0 for c in initial["companies"].values())
            if m.get("max_loan") == 0:
                assert all(c.get("max_loan") == 0 for c in initial["companies"].values())
                assert all(c.get("loan") == c.get("max_loan") == 0 for c in row["companies"].values())
            m.update(status="running", startup_verified_unix=time.time(), initial_date=initial["date"])
            manifest.write_text(json.dumps(m, indent=2) + "\n")
            print(json.dumps({"event": "startup_confirmed", "all_llm_seats_active": True, "llm_seats": args.llm_seats,
                              "initial_funding_verified": True, "progress": row}, indent=2), flush=True)
            raise SystemExit(0)
    time.sleep(10)
raise SystemExit("Startup verification timed out; inspect main task and sandbox.")
