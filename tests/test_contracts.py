"""Interception-contract and version-skew guards (plan decision 3, section 5.5).

Everything model-side rides the interception endpoint the framework hands the harness;
seat identity rides per-process environment through setpriv. These tests pin both.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from verifiers.v1 import (  # every framework touchpoint the package imports, in one place
    AgentConfig,
    DockerConfig,
    Env,
    ModelContext,
    ProgramResult,
    Runtime,
    TaskData,
    Taskset,
    TaskTimeout,
)
from verifiers.v1.clients import EvalClientConfig

from opencode.harness import OpenCodeHarness, OpenCodeHarnessConfig
from openttd_bench.env import OpenTTDEnv, OpenTTDEnvConfig, pick_ports
from openttd_bench.seats import SeatRuntime
from openttd_bench.tasks import free_for_all
from openttd_bench.taskset import OpenTTDConfig, OpenTTDSeatTask, OpenTTDTask, OpenTTDTaskset


class FakeBox(Runtime):
    """Records what reaches the box; no container."""

    is_local = True

    def __init__(self) -> None:
        super().__init__(name="fake")
        self.config = DockerConfig(image="x")
        self.info = self.config.model_copy()  # type: ignore[assignment]
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.files: dict[str, bytes] = {}

    async def start(self) -> None:
        pass

    async def run(self, argv, env):
        self.calls.append((argv, env))
        return ProgramResult(0, "", "")

    async def _read(self, path):
        return self.files[path]

    async def write(self, path, data):
        self.files[path] = data


def test_harness_config_uses_the_framework_endpoint_verbatim():
    harness = OpenCodeHarness(OpenCodeHarnessConfig(id="opencode"))
    ctx = ModelContext(model="moonshotai/kimi-k3", client=EvalClientConfig())
    config = json.loads(harness._build_config(ctx, "http://127.0.0.1:5555/v1", "secret-token"))
    provider = config["provider"]["moonshotai"]
    assert provider["options"]["baseURL"] == "http://127.0.0.1:5555/v1"  # no second /v1
    assert provider["options"]["apiKey"] == "secret-token"
    assert config["model"] == "moonshotai/moonshotai/kimi-k3"  # provider-local key = wire id
    assert "pinference" not in json.dumps(config)  # nothing hard-coded


def test_seat_launch_demotes_and_keeps_seat_env():
    harness = OpenCodeHarness(OpenCodeHarnessConfig(id="opencode"))
    box = FakeBox()
    view = SeatRuntime(box, "p3")
    view.env = {
        "OPENTTD_API_URL": "http://127.0.0.1:20003",
        "OPENTTD_API_TOKEN": "tok3",
        "OPENTTD_SEAT": "2",
        "OPENTTD_SEAT_USER": "agent3",
    }
    ctx = ModelContext(model="moonshotai/kimi-k3", client=EvalClientConfig())
    trace = SimpleNamespace(info={})  # the harness only writes trace.info
    data = TaskData(prompt="go", image="x", workdir="/work")
    asyncio.run(harness.launch(ctx, trace, view, "http://127.0.0.1:5555/v1", "s", {}, data))
    argv, env = next(call for call in box.calls if call[0][0] == "setpriv")
    assert argv[:4] == ["setpriv", "--reuid=agent3", "--regid=agent3", "--init-groups"]
    assert env["OPENTTD_API_TOKEN"] == "tok3" and env["OPENTTD_SEAT_USER"] == "agent3"
    assert "/home/agent3/.config/opencode/opencode.json" in box.files
    assert trace.info["seat_user"] == "agent3" and "harness_output_tail" in trace.info


def test_seat_task_env_and_soft_stop():
    spec = free_for_all.spec(101)
    task = OpenTTDSeatTask(
        TaskData(prompt="p", timeout=TaskTimeout(agent=1000)),
        spec,
        company=2,
        token="t",
        api_url="http://127.0.0.1:1",
        user="agent3",
    )
    env = task.runtime_env()
    assert env == {
        "OPENTTD_API_URL": "http://127.0.0.1:1",
        "OPENTTD_API_TOKEN": "t",
        "OPENTTD_SEAT": "2",
        "OPENTTD_SEAT_USER": "agent3",
    }
    assert task.soft_budget_seconds == 800


def test_taskset_yields_one_task_per_seed_with_budgets():
    tasks = list(OpenTTDTaskset(OpenTTDConfig(id="openttd_bench", experiment="versus_ai_rail")).load())
    assert all(t.data.prompt == t.spec.companies[0].prompt for t in tasks)
    assert len(tasks) == 4 and all(isinstance(t, OpenTTDTask) for t in tasks)
    assert tasks[0].data.timeout.agent == 10800 and tasks[0].data.image == "openttd-bench:v3"
    short = list(
        OpenTTDTaskset(
            OpenTTDConfig(id="openttd_bench", experiment="two_towns_rail", seeds=[7], agent_seconds=60)
        ).load()
    )
    assert len(short) == 1 and short[0].data.timeout.agent == 60 and short[0].spec.map.seed == 7


def test_env_declares_four_seats_and_rotates_roles():
    assert [n for n, f in OpenTTDEnvConfig.model_fields.items() if isinstance(f.default, AgentConfig)] == [
        "p1",
        "p2",
        "p3",
        "p4",
    ]
    ports = pick_ports(3)
    assert len({ports["game"], ports["admin"], ports["api"]}) == 3 and all(20000 <= p < 60100 for p in ports.values())
    assert issubclass(OpenTTDEnv, Env) and issubclass(OpenTTDTaskset, Taskset)
