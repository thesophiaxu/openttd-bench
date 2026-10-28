"""The experiments, one module each — the sole source of truth for a scenario.

A scenario module defines `NAME` and `spec(seed, model, **params)`: the world, the grading
and every llm seat's system prompt (written out in that file and attached to the seat's
`CompanySpec`), `params` coming from the config's `[env.taskset.params]`. `tasks_shared`
holds the data and helpers they have in common; nothing derives a prompt from a spec.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any, Protocol

from openttd_bench.task_spec import TaskSpec
from openttd_bench.tasks import free_for_all, mixed_hogex_realtime_10y, two_towns_rail, versus_ai_rail


class Experiment(Protocol):
    """What a scenario module provides."""

    NAME: str

    def spec(self, seed: int, model: str | None = None, **params: Any) -> TaskSpec: ...


EXPERIMENTS: dict[str, ModuleType] = {
    module.NAME: module
    for module in (two_towns_rail, versus_ai_rail, free_for_all, mixed_hogex_realtime_10y)
}


def experiment(name: str) -> Experiment:
    """The scenario module registered under `name` (the taskset's `experiment` knob)."""
    try:
        return EXPERIMENTS[name]  # type: ignore[return-value]  # a module satisfying the protocol
    except KeyError:
        raise ValueError(f"unknown experiment {name!r}; expected one of {sorted(EXPERIMENTS)}") from None


__all__ = [
    "EXPERIMENTS",
    "Experiment",
    "experiment",
    "free_for_all",
    "mixed_hogex_realtime_10y",
    "two_towns_rail",
    "versus_ai_rail",
]
