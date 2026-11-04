"""The verifiers taskset: one `OpenTTDTask` per TaskSpec (per seed of an experiment)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from verifiers.v1 import Runtime, Task, TaskData, Taskset, TasksetConfig, TaskTimeout, Trace, stop

from openttd_bench import tasks
from openttd_bench.task_spec import TaskSpec
from openttd_bench.tasks.tasks_shared import SEEDS

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
    progress_seconds: int = 600
    """Interval between progress lines (0 disables)."""


class OpenTTDTask(Task[TaskData, Any]):
    NEEDS_CONTAINER = True

    def __init__(self, data: TaskData, spec: TaskSpec, config=None) -> None:
        super().__init__(data, config)
        self.spec = spec


class OpenTTDSeatTask(OpenTTDTask):
    """One company in its own VM. Only its client and credential are staged here."""

    def __init__(
        self, data: TaskData, spec: TaskSpec, company: int, token: str, api_url: str, client_payload: bytes
    ) -> None:
        super().__init__(data, spec)
        self.company, self.token, self.api_url = company, token, api_url
        self.client_payload = client_payload

    def runtime_env(self) -> dict[str, str]:
        return {"HOME": "/root", "OPENTTD_API_URL": self.api_url, "OPENTTD_SEAT": str(self.company)}

    async def setup(self, trace: Trace, runtime: Runtime) -> None:
        await runtime.write("/tmp/openttd-client.tgz", self.client_payload)
        result = await runtime.run(["sh", "-ec", (
            "mkdir -p /opt/openttd /root && tar -xzf /tmp/openttd-client.tgz -C /opt/openttd && "
            "rm /tmp/openttd-client.tgz && cp /opt/openttd/client/ottd /usr/local/bin/ottd && "
            "chmod 755 /usr/local/bin/ottd && "
            "python3 -c 'import pathlib,site; "
            "pathlib.Path(site.getsitepackages()[0],\"openttd_client.pth\").write_text(\"/opt/openttd/client\\n\")'"
        )], {})
        if result.exit_code:
            raise RuntimeError(f"client install failed: {result.stderr[-500:]}")
        await runtime.write("/root/.openttd_seat", (
            f"OPENTTD_API_URL={self.api_url}\nOPENTTD_API_TOKEN={self.token}\nOPENTTD_SEAT={self.company}\n"
        ).encode())
        result = await runtime.run(["chmod", "600", "/root/.openttd_seat"], {})
        if result.exit_code:
            raise RuntimeError("could not protect client credential file")
        # Fail closed before any harness starts: correct identity, no server material,
        # admin HTTP denied, and the native game/admin sockets unreachable over the LAN.
        probe = """
import pathlib, socket, sys
from urllib.parse import urlsplit
from openttd_client.transport import Transport, OpenTTDError
t = Transport(timeout=15)
assert t.request('GET', '/api/state')['seat']['company'] == int(sys.argv[1])
assert not any(pathlib.Path('/opt/openttd', p).exists() for p in ('server', 'seats.json', 'world.json'))
try:
    t.request('GET', '/api/admin/snapshot')
except OpenTTDError as error:
    assert error.status == 403
else:
    raise AssertionError('admin API reachable from agent VM')
for port in sys.argv[2:]:
    try:
        with socket.create_connection((urlsplit(t.url).hostname, int(port)), timeout=3) as connection:
            # Prime's LAN proxy accepts TCP before dialing the destination: an
            # immediate EOF/reset means denied; an open/speaking socket is unsafe.
            assert connection.recv(1) == b'', f'native server port {port} reachable from agent VM'
    except (ConnectionRefusedError, ConnectionResetError):
        continue
"""
        result = await runtime.run(["python3", "-c", probe, str(self.company),
                                    str(self.spec.ports.game), str(self.spec.ports.admin)], {})
        if result.exit_code:
            raise RuntimeError(f"agent boundary check failed: {result.stderr[-500:]}")
        trace.info["openttd_client_ready"] = True

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
