"""Grading: `spec.grading()` evaluated per seat over the control plane's final snapshot."""

from __future__ import annotations

from typing import Any

from openttd_bench.objectives import Measurements, Outcome, evaluate
from openttd_bench.task_spec import TaskSpec


def measurements_from_snapshot(snapshot: dict[str, Any]) -> dict[int, Measurements]:
    return {int(company): dict(row) for company, row in snapshot.get("companies", {}).items()}


def score(spec: TaskSpec, snapshot: dict[str, Any], company: int) -> Outcome:
    all_measurements = measurements_from_snapshot(snapshot)
    all_measurements.setdefault(company, {})
    return evaluate(spec.objective, company, all_measurements)


def metrics(spec: TaskSpec, snapshot: dict[str, Any], company: int, outcome: Outcome) -> dict[str, float]:
    rows = measurements_from_snapshot(snapshot)
    mine = rows.get(company, {})
    out: dict[str, float] = {
        "objective_score": outcome.score,
        "company": float(company),
        "final_year": float((snapshot.get("date") or {}).get("year") or 0),
        "horizon_reached": float(((snapshot.get("date") or {}).get("year") or 0) >= spec.horizon_year),
        "operating_profit": float(mine.get("operating_profit") or 0),
        "company_value": float(mine.get("company_value") or 0),
        "cash": float(mine.get("cash") or 0),
        "stations": float(mine.get("stations") or 0),
        "rail_pieces": float(mine.get("rail_pieces") or 0),
        "road_pieces": float(mine.get("road_pieces") or 0),
    }
    vehicles = mine.get("vehicles") or {}
    if isinstance(vehicles, dict):
        out["vehicles"] = float(sum(int(v) for v in vehicles.values()))
    for label, units in (mine.get("cargo_delivered") or {}).items():
        out[f"delivered_{label.strip('_').lower()}"] = float(units)
    for name, value in outcome.terms.items():
        out[f"term/{name}"] = float(value)
    for name, value in outcome.values.items():
        out[f"value/{name}"] = float(value)
    return out


__all__ = ["measurements_from_snapshot", "metrics", "score"]
