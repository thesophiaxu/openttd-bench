"""The delivery accumulator (plan section 9.4): the sole consumer of the GameScript's
cargo-monitor deltas, accumulating monotonically per (company, cargo, town/industry).
Agents and the grader read the same totals."""

from __future__ import annotations

import threading
from typing import Any


class Accumulator:
    def __init__(self, labels: dict[int, str], monitored: list[int]) -> None:
        self._lock = threading.Lock()
        self._labels = dict(labels)
        self.monitored = [labels.get(c, f"C{c}") for c in monitored]
        self._totals: dict[int, dict[str, dict[str, Any]]] = {}
        self.deltas = 0

    def set_labels(self, labels: dict[int, str], monitored: list[int]) -> None:
        with self._lock:
            self._labels = dict(labels)
            self.monitored = [labels.get(c, f"C{c}") for c in monitored]

    def add(self, company: int, cargo: int, kind: str, target: int, amount: int) -> None:
        if amount <= 0:
            return
        label = self._labels.get(cargo, f"C{cargo}")
        with self._lock:
            row = self._totals.setdefault(company, {}).setdefault(label, {"total": 0, "towns": {}, "industries": {}})
            row["total"] += amount
            bucket = row["towns"] if kind == "town" else row["industries"]
            bucket[str(target)] = bucket.get(str(target), 0) + amount
            self.deltas += 1

    def view(self, company: int, label: str | None = None) -> dict[str, Any]:
        with self._lock:
            rows = self._totals.get(company, {})
            if label is not None:
                rows = {label: rows[label]} if label in rows else {}
            return {
                "company": company,
                "monitored": list(self.monitored),
                "cargos": {
                    k: {"total": v["total"], "towns": dict(v["towns"]), "industries": dict(v["industries"])}
                    for k, v in rows.items()
                },
            }

    def measurements(self, company: int) -> dict[str, Any]:
        """Grader-shaped measurements for one company."""
        with self._lock:
            rows = self._totals.get(company, {})
            return {
                "cargo_delivered": {k: v["total"] for k, v in rows.items()},
                "cargo_delivered_town": {k: dict(v["towns"]) for k, v in rows.items()},
                "cargo_delivered_industry": {k: dict(v["industries"]) for k, v in rows.items()},
            }


__all__ = ["Accumulator"]
