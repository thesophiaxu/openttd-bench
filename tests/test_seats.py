"""Episode ownership: distinct VMs, concurrent roles, failure and cancellation cleanup."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
import verifiers.v1 as vf

import openttd_bench.env as module
from openttd_bench.env import OpenTTDEnv, OpenTTDEnvConfig
from openttd_bench.tasks import free_for_all
from openttd_bench.taskset import OpenTTDConfig, OpenTTDTask


@pytest.mark.parametrize("failure", [None, "agent", "cancel", "provision"])
def test_episode_owns_distinct_targets_through_failures(tmp_path, monkeypatch, failure):
    events, deployments, runs = [], [], []
    spec = free_for_all.spec(101)
    spec = spec.model_copy(update={"companies": spec.companies[:2]})
    task = OpenTTDTask(vf.TaskData(idx=1, prompt="go"), spec)
    monkeypatch.setenv("OPENTTD_RUN_DIR", str(tmp_path))

    class Backend:
        def __init__(self, config, name="episode"):
            self.config, self.name = config, name
            self.info = SimpleNamespace(id=name)
            self.stopped = False

        async def stop(self):
            if not self.stopped:
                events.append(("stop", self.name))
                self.stopped = True

        def bind(self):
            return SimpleNamespace(backend=self, info=self.info, config=self.config)

    class Deployment:
        def __init__(self, backend, spec):
            self.backend, self.spec = backend, spec
            self.target_backends = {"main": backend}
            self.hosts = {"main": "server.sandbox.internal"}
            deployments.append(self)

        @property
        def workspace(self):
            return self.backend.bind()

        def target(self, role):
            return self.target_backends[role].bind()

        async def start(self):
            for role, config in self.spec.services.items():
                self.target_backends[role] = Backend(config, role)
                if failure == "provision":
                    raise RuntimeError("partial provision")

        async def close(self):
            for backend in self.target_backends.values():
                await backend.stop()

    class Game:
        def __init__(self, box, *args):
            self.box = box

        async def boot(self):
            return {"date": "1950"}

        async def snapshot(self):
            return {"companies": {str(i): {"cash": c.starting_cash, "loan": c.starting_loan,
                                           "max_loan": 500000} for i, c in enumerate(spec.companies)}}

        async def save(self, name):
            return {"exists": False}

        async def retire(self, company):
            assert not self.box.backend.stopped
            events.append(("retire", company))

        async def run_out(self, timeout):
            assert not self.box.backend.stopped
            assert all(b.stopped for r, b in deployments[0].target_backends.items() if r != "main")
            events.append(("run_out", None))
            return {"status": "horizon"}

    monkeypatch.setattr(module, "make_backend", Backend)
    monkeypatch.setattr(module.vf, "Deployment", Deployment)
    monkeypatch.setattr(module, "GameBox", Game)
    monkeypatch.setattr(module.grader, "check_scorable", lambda *a: None)
    monkeypatch.setattr(module.grader, "score", lambda *a: 1.0)
    monkeypatch.setattr(module.grader, "metrics", lambda *a: {"score": 1.0})
    env = object.__new__(OpenTTDEnv)
    env.config = OpenTTDEnvConfig(taskset={"id": "openttd_bench"}, max_concurrent_agents=2)
    env.taskset = SimpleNamespace(config=OpenTTDConfig(
        id="openttd_bench", save_dir=str(tmp_path / "saves"), progress_seconds=0,
    ))

    async def scenario():
        ready = asyncio.Event()

        class Agent:
            def __init__(self, role, cpu):
                self.role, self.runtime_config = role, vf.PrimeConfig(cpu=cpu)

            async def run(self, seat_task, runtime, on_trace):
                trace = SimpleNamespace(id=f"trace-{self.role}", info={}, calls=[1])
                on_trace(trace)
                runs.append((self.role, seat_task.company, runtime.info.id, trace))
                assert runtime.config.cpu == self.runtime_config.cpu
                if len(runs) == 2:
                    ready.set()
                await ready.wait()
                if failure == "cancel":
                    await asyncio.Event().wait()
                if self.role == "p1":
                    if failure == "agent":
                        raise RuntimeError("one agent failed")
                await asyncio.sleep(0)
                return trace

        agents = SimpleNamespace(p1=Agent("p1", 1), p2=Agent("p2", 2))
        running = asyncio.create_task(env.run(task, agents))
        if failure == "cancel":
            await ready.wait()
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
        elif failure == "provision":
            with pytest.raises(RuntimeError, match="partial provision"):
                await running
        else:
            await running

    asyncio.run(scenario())
    assert all(b.stopped for b in deployments[0].target_backends.values())
    if failure != "provision":
        assert {(r, c, box) for r, c, box, _ in runs} == {("p1", 1, "p1"), ("p2", 0, "p2")}
        for role, company, _, trace in runs:
            assert trace.info["openttd_role"] == role and trace.info["openttd_company"] == company
            assert events.index(("retire", company)) < events.index(("stop", role))
    if failure in (None, "agent"):
        assert events[-1] == ("stop", "episode")
        assert ("run_out", None) in events
    inventory = json.loads(next((tmp_path / "live/runtimes").glob("*.json")).read_text())
    assert inventory["server"]["status"] == "closed"
    assert "token" not in json.dumps(inventory)


def test_non_prime_role_rejected_before_any_provisioning(tmp_path, monkeypatch):
    env = object.__new__(OpenTTDEnv)
    env.config = OpenTTDEnvConfig(taskset={"id": "openttd_bench"}, max_concurrent_agents=4)
    env.taskset = SimpleNamespace(config=OpenTTDConfig(id="openttd_bench"))
    agents = SimpleNamespace(**{r: SimpleNamespace(runtime_config=vf.DockerConfig()) for r in module.ROLES})
    monkeypatch.setattr(module, "make_backend", lambda *a: pytest.fail("must not provision"))
    with pytest.raises(ValueError, match="require Prime"):
        asyncio.run(env.run(OpenTTDTask(vf.TaskData(idx=0), free_for_all.spec(101)), agents))
