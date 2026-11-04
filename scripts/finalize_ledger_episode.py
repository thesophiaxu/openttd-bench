#!/usr/bin/env python3
"""Fail-closed post-run verification for manifest-backed corrected-ledger episodes."""
import hashlib
import json
import sys
import time
from pathlib import Path

from openttd_bench.grader import check_scorable, score
from openttd_bench.task_spec import TaskSpec


class VerificationError(Exception):
    """The run directory fails post-run verification."""


def require(condition: object, message: str) -> None:
    """`assert` that survives `python -O`."""
    if not condition:
        raise VerificationError(message)


def verify_native_sources(doc: dict, root: Path) -> None:
    source = doc.get("extra", {}).get("source", {})
    if source.get("format") == "codex":
        path = Path(source["path"]).resolve()
        require(path.is_relative_to(root.resolve()), "Native source must be run-local")
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == source["sha256"], "native source hash does not match its recorded sha256")
        require([json.loads(line) for line in raw.decode().splitlines() if line.strip()] == source["records"], "native source records differ from the exported records")
        for artifact in source.get("artifacts", []):
            path = Path(artifact["path"]).resolve()
            require(path.is_relative_to(root.resolve()), "native artifact must be run-local")
            require(path.read_text() == artifact["content"], "native artifact content differs from the export")
    for child in doc.get("subagent_trajectories", []):
        verify_native_sources(child, root)


def finalize(root: Path, exit_code: int) -> None:
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    try:
        require(exit_code == 0, f"eval/export process exited {exit_code}")
        spec = TaskSpec.model_validate_json((root / "planned-spec.json").read_text())
        saves = list((root / "saves").glob("*/final-snapshot.json"))
        require(len(saves) == 1, f"expected exactly one final snapshot, found {len(saves)}")
        final = json.loads(saves[0].read_text())
        initial = json.loads((saves[0].parent / "initial-snapshot.json").read_text())
        rows = [json.loads(line) for p in (root / "progress").glob("*.jsonl") for line in p.read_text().splitlines() if line.strip()]
        check_scorable(spec, initial)
        check_scorable(spec, final)
        start, horizon = f"{spec.map.start_year}-01-01", f"{spec.horizon_year}-01-01"
        require(initial["date"]["text"] == start, f"initial date is {initial['date']}, expected {start}")
        require(final["date"]["text"] == horizon and final["clock"]["horizon_reached"],
                f"final snapshot did not reach the {horizon} horizon: {final['date']}")
        require(any(p.stat().st_size > 0 for p in (root / "saves").glob("*.sav")), "no non-empty .sav file under saves/")
        results = json.loads((saves[0].parent / "results.json").read_text())
        require(len(results) == len(spec.companies), f"results.json has {len(results)} rows for {len(spec.companies)} companies")
        for result in results:
            require(abs(score(spec, final, result["seat"] - 1).score - result["objective_score"]) < 1e-10, f"seat {result['seat']}: recomputed score differs from results.json")
        episode = json.loads(next(root.glob("*/traces.jsonl")).read_text().splitlines()[0])
        traces = episode["traces"]
        require(episode["ok"] and len(traces) == spec.llm_seats, "episode failed or trace count differs from the LLM seat count")
        require({t["info"]["openttd_company"] for t in traces} == set(range(spec.llm_seats)), "traces do not cover every LLM company exactly once")
        require(all(t["ok"] and not t["errors"] for t in traces), "a trace failed or has errors")
        if manifest.get("auth_mode") == "chatgpt":
            require(all(not t["calls"] for t in traces), "OAuth run unexpectedly used intercepted inference")
            require(all(t["info"].get("harness_session", {}).get("harvested_to") for t in traces), "OAuth trace has no harvested native session")
        by_id = {t["id"]: t for t in traces}
        index = json.loads((root / "trajectories/index.json").read_text())
        require(len(index["trajectories"]) == spec.llm_seats, "trajectory index count differs from the LLM seat count")
        represented = set()
        for item in index["trajectories"]:
            raw = (root / "trajectories" / item["file"]).read_bytes()
            require(hashlib.sha256(raw).hexdigest() == item["sha256"], f"trajectory {item['file']} hash does not match the index")
            trajectory = json.loads(raw)
            extra = trajectory["extra"]
            source = extra.get("prime_source") or extra["source"]
            original = source["trace"]
            require(original == by_id[original["id"]], "trajectory source trace differs from traces.jsonl")
            represented.add(original["id"])
            verify_native_sources(trajectory, root)
        require(represented == set(by_id), "trajectories do not represent every trace")
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
