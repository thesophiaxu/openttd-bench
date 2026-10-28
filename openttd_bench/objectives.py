"""Objectives: named Meters (pure reads) + a fixed combinator set (decision 13).

    score(seat) = clamp01( sum_i weight_i * combinator_i(meter_i(seat)) )

Meters read grading-time sources: the control plane's delivery accumulator, the admin
economy mirror, the GameScript's company measurements (operating profit), and — for
future goals — the terminal save. This module never touches the game; `Measurements`
is a plain dict the grader assembles from the final snapshot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from openttd_bench.task_spec import TaskSpec

MeterKind = Literal[
    "cargo_delivered",  # accumulator: (company, cargo[, town | industry])
    "operating_profit",  # GS company quarterly ledger (income + signed operating expenses)
    "company_value",  # economy mirror
    "place",  # rank among all companies by another meter
    "town_population",  # future: town growth goals
    "exclusive_rights_months",  # future: charter goals
]
Combinator = Literal["ratio_to_target", "rank", "threshold"]


class Meter(BaseModel):
    kind: MeterKind
    params: dict[str, Any] = Field(default_factory=dict)
    """cargo_delivered: {cargo: LABEL[, town: id | industry: id]};
    place: {by: <meter kind>, default operating_profit}; town_population: {town: id};
    exclusive_rights_months: {town: id}."""

    def label(self) -> str:
        if self.kind == "cargo_delivered":
            return f"cargo_delivered_{self.params.get('cargo', '?').strip('_').lower()}"
        if self.kind == "place":
            return "place"
        return self.kind


class ObjectiveTerm(BaseModel):
    meter: Meter
    combinator: Combinator
    target: float | None = None
    weight: float = 1.0

    @model_validator(mode="after")
    def _check(self) -> ObjectiveTerm:
        if self.combinator in ("ratio_to_target", "threshold"):
            if self.target is None or self.target <= 0:
                raise ValueError(f"{self.combinator} needs a positive target")
        if self.combinator == "rank" and self.meter.kind != "place":
            raise ValueError("rank composes only with the place meter")
        if self.meter.kind == "place" and self.combinator != "rank":
            raise ValueError("the place meter composes only with rank")
        if self.meter.kind == "cargo_delivered" and "cargo" not in self.meter.params:
            raise ValueError("cargo_delivered needs params.cargo (a 4-char label)")
        if self.weight <= 0:
            raise ValueError("weight must be positive")
        return self

    def check_against(self, spec: TaskSpec) -> None:
        """Rules/objective consistency (e.g. a rank term needs rivals)."""
        if self.combinator == "rank" and len(spec.companies) < 2:
            raise ValueError("a rank term needs at least two companies")

    def name(self) -> str:
        return f"{self.combinator}:{self.meter.label()}"


class Outcome(BaseModel):
    score: float
    terms: dict[str, float]
    """Per-term combinator outputs (0..1)."""
    values: dict[str, float]
    """Per-term raw meter values."""


Measurements = dict[str, Any]
"""One company's grading-time measurements:
    {"cargo_delivered": {LABEL: units, ...}, "cargo_delivered_town": {LABEL: {town_id: units}},
     "cargo_delivered_industry": {LABEL: {industry_id: units}},
     "operating_profit": money, "company_value": money,
     "town_population": {town_id: pop}, "exclusive_rights_months": {town_id: months}}"""


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def meter_value(meter: Meter, seat: int, all_measurements: dict[int, Measurements]) -> float:
    """Evaluate one meter for `seat` given every company's measurements."""
    mine = all_measurements.get(seat, {})
    kind = meter.kind
    if kind == "cargo_delivered":
        cargo = meter.params["cargo"]
        if "town" in meter.params:
            return float(mine.get("cargo_delivered_town", {}).get(cargo, {}).get(str(meter.params["town"]), 0))
        if "industry" in meter.params:
            return float(mine.get("cargo_delivered_industry", {}).get(cargo, {}).get(str(meter.params["industry"]), 0))
        return float(mine.get("cargo_delivered", {}).get(cargo, 0))
    if kind in ("operating_profit", "company_value"):
        return float(mine.get(kind, 0))
    if kind == "town_population":
        return float(mine.get("town_population", {}).get(str(meter.params.get("town")), 0))
    if kind == "exclusive_rights_months":
        return float(mine.get("exclusive_rights_months", {}).get(str(meter.params.get("town")), 0))
    if kind == "place":
        by = Meter(kind=meter.params.get("by", "operating_profit"))
        own = meter_value(by, seat, all_measurements)
        others = [meter_value(by, c, all_measurements) for c in all_measurements if c != seat]
        return float(1 + sum(1 for v in others if v >= own))
    raise ValueError(f"unknown meter kind {kind!r}")


def combine(term: ObjectiveTerm, value: float, companies: int) -> float:
    if term.combinator == "ratio_to_target":
        return _clamp01(value / float(term.target))
    if term.combinator == "threshold":
        return 1.0 if value >= float(term.target) else 0.0
    if term.combinator == "rank":
        if companies <= 1:
            return 1.0
        beaten = companies - value  # value = place (1 = first)
        return _clamp01(beaten / (companies - 1))
    raise ValueError(term.combinator)


def evaluate(objective: list[ObjectiveTerm], seat: int, all_measurements: dict[int, Measurements]) -> Outcome:
    terms: dict[str, float] = {}
    values: dict[str, float] = {}
    total = 0.0
    companies = len(all_measurements)
    for term in objective:
        value = meter_value(term.meter, seat, all_measurements)
        part = combine(term, value, companies)
        key = term.name()
        terms[key] = part
        values[key] = value
        total += term.weight * part
    return Outcome(score=_clamp01(total), terms=terms, values=values)


def monitor_plan(objective: list[ObjectiveTerm], cargo_table: list[dict[str, Any]]) -> dict[str, Any]:
    """The generic sampling plan derived from the objective's meters: which cargo ids
    the accumulator polls, at what cadence. Carries no target and no goal."""
    labels = sorted({t.meter.params["cargo"] for t in objective if t.meter.kind == "cargo_delivered"})
    ids = {row["label"]: int(row["id"]) for row in cargo_table}
    return {
        "cargos": [ids[label] for label in labels],
        "kinds": ["town", "industry"] if labels else [],
        "cadence_days": 1,
    }


__all__ = [
    "Combinator",
    "Measurements",
    "Meter",
    "MeterKind",
    "ObjectiveTerm",
    "Outcome",
    "combine",
    "evaluate",
    "meter_value",
    "monitor_plan",
]
