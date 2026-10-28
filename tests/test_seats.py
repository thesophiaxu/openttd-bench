"""Two rollouts on ONE docker box must overlap in time and keep separate envs.

Needs a reachable docker daemon (run under `scripts/with_docker.sh`); skipped otherwise.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import time

import pytest
from verifiers.v1 import AgentConfig, DockerConfig, Task, TaskData, make_agent
from verifiers.v1.runtimes import provision_runtime

from openttd_bench.seats import SeatRuntime

IMAGE = "debian:bookworm-slim"


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "info"], capture_output=True, timeout=30)
    return probe.returncode == 0


class SeatTask(Task):
    NEEDS_CONTAINER = True

    def __init__(self, data: TaskData, seat: str) -> None:
        super().__init__(data)
        self.seat = seat

    def runtime_env(self) -> dict[str, str]:
        return {"OPENTTD_SEAT": self.seat, "OPENTTD_API_TOKEN": f"token-{self.seat}"}


@pytest.mark.skipif(not _docker_ok(), reason="docker daemon not reachable")
def test_two_seats_run_concurrently_on_one_box():
    asyncio.run(_run())


async def _run() -> None:
    box_config = DockerConfig(image=IMAGE, workdir="/tmp")
    agent = make_agent(
        AgentConfig(
            harness={"id": "sleepy_harness", "seconds": 5},
            model="test/none",
            runtime=box_config,
        )
    )
    tasks = {seat: SeatTask(TaskData(prompt="go", image=IMAGE, workdir="/tmp"), seat) for seat in ("p1", "p2")}
    async with provision_runtime(box_config) as box:
        views = {seat: SeatRuntime(box, seat) for seat in tasks}
        started = time.monotonic()
        traces = await asyncio.gather(*(agent.run(task, runtime=views[seat]) for seat, task in tasks.items()))
        elapsed = time.monotonic() - started
    outputs = {}
    for trace in traces:
        assert trace.ok, trace.errors
        out = trace.info["stdout"]
        match = re.search(r"seat=(\w+) token=(\S+) start=(\S+) end=(\S+)", out)
        assert match, out
        seat, token, start, end = match.groups()
        assert token == f"token-{seat}"
        outputs[seat] = (float(start), float(end))
    assert set(outputs) == {"p1", "p2"}
    # Overlap: each sleeps 5s; serialized they'd take >= 10s.
    overlap = min(e for _, e in outputs.values()) - max(s for s, _ in outputs.values())
    assert overlap > 3, f"rollouts did not overlap: {outputs}"
    assert elapsed < 9, f"two 5s rollouts took {elapsed:.1f}s"
