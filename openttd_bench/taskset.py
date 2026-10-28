"""The verifiers taskset: one `OpenTTDTask` per TaskSpec (per seed of an experiment)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from verifiers.v1 import Runtime, Task, TaskData, Taskset, TasksetConfig, TaskTimeout, Trace, stop

from openttd_bench import tasks
from openttd_bench.task_spec import TaskSpec
from openttd_bench.tasks.tasks_shared import SEEDS

DEFAULT_IMAGE = "openttd-bench:v3"
WORKDIR = "/work"
SOFT_BUDGET_FRACTION = 0.8
"""End the agent phase at this fraction of its wall budget so retirement, run-out and
scoring always run (an overrun raises HarnessError and skips finalize + scoring)."""


REALTIME_SLACK_SECONDS = 900
"""Real-time games: wall time allowed past the horizon for harness startup and wrap-up."""


def agent_budget_seconds(spec: TaskSpec) -> int:
    """Per-seat agent wall budget. Paused clock: a flat budget by horizon length. Real-time
    clock: the game itself lasts `horizon_wall_seconds`, so the budget covers that plus
    slack, sized so the soft stop still lands after the horizon."""
    if spec.rules.realtime:
        return int((spec.rules.horizon_wall_seconds + REALTIME_SLACK_SECONDS) / SOFT_BUDGET_FRACTION)
    return 7200 if spec.rules.duration_years <= 2 else 10800


class OpenTTDConfig(TasksetConfig):
    experiment: str = "two_towns_rail"
    """One of `openttd_bench.tasks.EXPERIMENTS` (a scenario module under `openttd_bench/tasks/`)."""
    params: dict[str, Any] = {}
    """Keyword arguments for the experiment's `spec` (e.g. seats, cash, loans, speed)."""
    image: str = DEFAULT_IMAGE
    """Box image. The local docker image caches the binaries; any stock python image
    works too — `init_game` bootstraps the pinned content at setup (Prime)."""
    seeds: list[int] = []
    """Map seeds (empty = the experiment's default four)."""
    model: str = ""
    """The model under evaluation, recorded on the spec for bookkeeping."""
    agent_seconds: int | None = None
    """Per-seat agent wall budget override (default: 7200 for 2-year tasks, 10800 for 10-year)."""
    boot_seconds: int = 900
    """How long the world may take to boot (bootstrap downloads included)."""
    run_out_seconds: int = 7200
    """Budget for running the clock out to the horizon after every seat is done."""
    save_dir: str = "outputs/saves"
    """Where terminal savegames are copied on the host."""
    progress_dir: str = "outputs/progress"
    """Where the env appends per-episode progress lines while the seats play."""
    launch_gate: str | None = None
    """Optional host file: provision/boot paused, but do not start agents until it exists."""
    require_model_activity: bool = False
    """Abort if a harness exits without intercepted calls or native Codex activity evidence."""
    progress_seconds: int = 600
    """Interval between progress lines (0 disables)."""


class OpenTTDTask(Task[TaskData, Any]):
    NEEDS_CONTAINER = True

    def __init__(self, data: TaskData, spec: TaskSpec, config=None) -> None:
        super().__init__(data, config)
        self.spec = spec


class OpenTTDSeatTask(OpenTTDTask):
    """One seat's rollout inside the shared box: per-seat env, retire on finish."""

    def __init__(self, data: TaskData, spec: TaskSpec, company: int, token: str, api_url: str, user: str) -> None:
        super().__init__(data, spec)
        self.company = company
        self.token = token
        self.api_url = api_url
        self.user = user

    def runtime_env(self) -> dict[str, str]:
        return {
            "OPENTTD_API_URL": self.api_url,
            "OPENTTD_API_TOKEN": self.token,
            "OPENTTD_SEAT": str(self.company),
            "OPENTTD_SEAT_USER": self.user,
        }

    async def finalize(self, trace: Trace, runtime: Runtime) -> None:
        """Retire the seat from the clock barrier; the company keeps operating."""
        from openttd_bench.env import box_api

        try:
            trace.info["openttd_retired"] = await box_api(runtime, "POST", f"{self.api_url}/api/retire", {}, self.token)
        except Exception as exc:  # noqa: BLE001 - the env retires again anyway
            trace.info["openttd_retire_error"] = str(exc)

    @stop
    async def out_of_wall_clock(self, trace: Trace) -> bool:
        now = time.monotonic()
        started = trace.info.setdefault("openttd_agent_started", now)
        return now - float(started) >= self.soft_budget_seconds

    @property
    def soft_budget_seconds(self) -> float:
        return float(self.data.timeout.agent or agent_budget_seconds(self.spec)) * SOFT_BUDGET_FRACTION


class OpenTTDTaskset(Taskset[OpenTTDTask, OpenTTDConfig]):
    def load(self) -> Iterator[OpenTTDTask]:
        experiment = tasks.experiment(self.config.experiment)
        seeds = tuple(self.config.seeds) or SEEDS
        for idx, seed in enumerate(seeds):
            spec = experiment.spec(seed, self.config.model or None, **self.config.params)
            agent_seconds = self.config.agent_seconds or agent_budget_seconds(spec)
            yield OpenTTDTask(
                TaskData(
                    idx=idx,
                    name=spec.name,
                    prompt=spec.companies[0].prompt,
                    image=self.config.image,
                    workdir=WORKDIR,
                    timeout=TaskTimeout(
                        setup=self.config.boot_seconds,
                        agent=agent_seconds,
                        finalize=self.config.run_out_seconds + 600,
                        scoring=300,
                    ),
                ),
                spec=spec,
            )


__all__ = ["OpenTTDConfig", "OpenTTDSeatTask", "OpenTTDTask", "OpenTTDTaskset", "agent_budget_seconds"]
