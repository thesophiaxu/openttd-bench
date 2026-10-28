#!/usr/bin/env python3
"""Fail-closed post-run verification for manifest-backed corrected-ledger episodes."""
import hashlib
import json
import sys
import time
from pathlib import Path

from openttd_bench.accounting import validate_accounting
from openttd_bench.activity import has_model_activity
from openttd_bench.grader import score
from openttd_bench.task_spec import TaskSpec


def verify_native_sources(doc: dict, root: Path) -> None:
    source = doc.get("extra", {}).get("source", {})
    if source.get("format") == "codex":
        path = Path(source["path"]).resolve()
        assert path.is_relative_to(root.resolve()), "Native source must be run-local"
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source["sha256"]
        assert [json.loads(line) for line in raw.decode().splitlines() if line.strip()] == source["records"]
        for artifact in source.get("artifacts", []):
            path = Path(artifact["path"]).resolve()
            assert path.is_relative_to(root.resolve())
            assert path.read_text() == artifact["content"]
    for child in doc.get("subagent_trajectories", []):
        verify_native_sources(child, root)


def finalize(root: Path, exit_code: int) -> None:
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    try:
        assert exit_code == 0, f"eval/export process exited {exit_code}"
        spec = TaskSpec.model_validate_json((root / "planned-spec.json").read_text())
        saves = list((root / "saves").glob("*/final-snapshot.json"))
        assert len(saves) == 1
        final = json.loads(saves[0].read_text())
        initial = json.loads((saves[0].parent / "initial-snapshot.json").read_text())
        rows = [json.loads(line) for p in (root / "progress").glob("*.jsonl") for line in p.read_text().splitlines() if line.strip()]
        for row in [initial, *rows, final]:
            validate_accounting(row, range(len(spec.companies)))
            if spec.rules.max_loan == 0:
                assert all(c["loan"] == c["max_loan"] == 0 for c in row["companies"].values())
        for i, company in enumerate(spec.companies):
            assert initial["companies"][str(i)]["cash"] == company.starting_cash
        assert initial["date"]["text"] == f"{spec.map.start_year}-01-01"
        assert final["date"]["text"] == f"{spec.horizon_year}-01-01" and final["clock"]["horizon_reached"]
        assert any(p.stat().st_size > 0 for p in (root / "saves").glob("*.sav"))
        results = json.loads((saves[0].parent / "results.json").read_text())
        assert len(results) == len(spec.companies)
        for result in results:
            assert abs(score(spec, final, result["seat"] - 1).score - result["objective_score"]) < 1e-10
        episode = json.loads(next(root.glob("*/traces.jsonl")).read_text().splitlines()[0])
        traces = episode["traces"]
        assert episode["ok"] and len(traces) == spec.llm_seats
        assert {t["info"]["openttd_company"] for t in traces} == set(range(spec.llm_seats))
        assert all(t["ok"] and not t["errors"] and has_model_activity(t["calls"], t["info"]) for t in traces)
        if manifest.get("auth_mode") == "chatgpt":
            assert all(not t["calls"] for t in traces), "OAuth run unexpectedly used intercepted inference"
            assert all(t["info"].get("harness_session", {}).get("harvested_to") for t in traces)
        by_id = {t["id"]: t for t in traces}
        index = json.loads((root / "trajectories/index.json").read_text())
        assert len(index["trajectories"]) == spec.llm_seats
        represented = set()
        for item in index["trajectories"]:
            raw = (root / "trajectories" / item["file"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == item["sha256"]
            trajectory = json.loads(raw)
            extra = trajectory["extra"]
            source = extra.get("prime_source") or extra["source"]
            original = source["trace"]
            assert original == by_id[original["id"]]
            represented.add(original["id"])
            verify_native_sources(trajectory, root)
        assert represented == set(by_id)
        early = [t["info"]["openttd_company"] for t in traces
                 if not t["info"].get("openttd_retired", {}).get("clock", {}).get("horizon_reached")]
        status = "complete_with_caveat" if early else "complete"
        validation = {"status": status, "early_or_unverified_retirements": early,
                      "progress_samples": len(rows), "lossless_source_verified": True, "results": results}
        (root / "completion-validation.json").write_text(json.dumps(validation, indent=2) + "\n")
        manifest.update(status=status, results=results, early_or_unverified_retirements=early)
        print(json.dumps(validation, indent=2))
    except Exception as exc:
        manifest.update(status="failed", error=repr(exc))
        raise
    finally:
        manifest["finished_unix"] = time.time()
        path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    finalize(Path(sys.argv[1]), int(sys.argv[2]))
