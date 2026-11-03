"""Where a seat's reach ends: retired seats, leftover processes, foreign harnesses, and one
seat's failure never taking the rivals' scores with it."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from verifiers.v1 import DockerConfig, ProgramResult, Runtime
from verifiers.v1.harnesses.bash import BashHarness, BashHarnessConfig

from claude.harness import ClaudeHarness, ClaudeHarnessConfig
from openttd_bench.env import GameBox, NoModelActivity, require_seat_harnesses, settle
from openttd_bench.schema import schema_json
from openttd_bench.tasks import free_for_all

IMAGE = Path(__file__).resolve().parent.parent / "image"


# -- the control plane refuses a retired seat -----------------------------------------------


@pytest.fixture
def core(tmp_path, monkeypatch):
    world = free_for_all.spec(101).world()
    world["ports"] = {"game": 1, "admin": 2, "api": 3}
    (tmp_path / "world.json").write_text(json.dumps(world))
    (tmp_path / "seats.json").write_text(json.dumps({"tokens": {"t0": 0, "t1": 1}, "admin": "a"}))
    (tmp_path / "schema.json").write_text(schema_json())
    for key, name in (("OPENTTD_WORLD", "world.json"), ("OPENTTD_SEATS", "seats.json"), ("OPENTTD_SCHEMA", "schema.json")):
        monkeypatch.setenv(key, str(tmp_path / name))
    monkeypatch.setenv("OPENTTD_ADMIN_PASSWORD", "x")
    monkeypatch.setenv("OPENTTD_RUN_DIR", str(tmp_path))
    monkeypatch.syspath_prepend(str(IMAGE))
    sys.modules.pop("server.app", None)
    app = importlib.import_module("server.app")  # builds a Core; nothing connects until startup
    app.core.gs_ready = True
    yield app.core
    sys.modules.pop("server.app", None)


def test_a_retired_seat_cannot_run_commands(core):
    items = [{"cmd": "inspect.deliveries", "args": {}}]  # served by the control plane itself
    assert core.run_batch(1, items, True)["results"][0]["ok"]  # an active seat: fine
    core.retire(1)
    with pytest.raises(HTTPException) as err:
        core.run_batch(1, items, True)
    assert err.value.status_code == 409 and "retired" in err.value.detail
    assert core.run_batch(0, items, True)["results"][0]["ok"]  # its rival is unaffected


# -- leftover processes are killed before the seat retires -------------------------------------


class ReapBox(Runtime):
    is_local = True

    def __init__(self, exit_codes: list[int]) -> None:
        super().__init__(name="reap")
        self.config = DockerConfig(image="x")
        self.exit_codes = exit_codes
        self.calls: list[list[str]] = []

    async def start(self) -> None:
        pass

    async def run(self, argv, env):
        self.calls.append(argv)
        return ProgramResult(self.exit_codes.pop(0), "", "")

    async def _read(self, path):
        raise FileNotFoundError(path)

    async def write(self, path, data):
        pass


def test_reap_kills_until_the_seat_user_runs_nothing():
    box = ReapBox([0, 0, 1])  # killed something twice (a forking loop), then nothing left
    asyncio.run(GameBox(box, free_for_all.spec(101), {"ports": {"api": 3}}, {}, 1).reap("agent2"))
    assert box.calls == [["pkill", "-KILL", "-u", "agent2"]] * 3


# -- only SeatHarnesses may play a seat --------------------------------------------------------


def test_a_root_harness_is_rejected_before_provisioning():
    seat = SimpleNamespace(harness=ClaudeHarness(ClaudeHarnessConfig(id="claude")))
    root = SimpleNamespace(harness=BashHarness(BashHarnessConfig(id="bash")))
    require_seat_harnesses(SimpleNamespace(p1=seat, p2=seat), ["p1", "p2"])
    with pytest.raises(TypeError, match="p2 uses harness 'BashHarness'"):
        require_seat_harnesses(SimpleNamespace(p1=seat, p2=root), ["p1", "p2"])


# -- one seat failing never cancels its rivals ----------------------------------------------------


def test_one_failed_seat_leaves_the_others_playing():
    finished: list[str] = []

    async def seat(name: str, delay: float, fail: BaseException | None = None) -> None:
        await asyncio.sleep(delay)
        if fail is not None:
            raise fail
        finished.append(name)

    async def main():
        tasks = [asyncio.create_task(seat("a", 0.01, RuntimeError("harness crashed"))),
                 asyncio.create_task(seat("b", 0.05)), asyncio.create_task(seat("c", 0.05))]
        return await settle(tasks, NoModelActivity)

    failures = asyncio.run(main())
    assert [str(f) for f in failures] == ["harness crashed"] and sorted(finished) == ["b", "c"]


def test_a_seat_without_model_activity_aborts_the_episode():
    cancelled: list[str] = []

    async def seat(name: str, delay: float, fail: BaseException | None = None) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            cancelled.append(name)
            raise
        if fail is not None:
            raise fail

    async def main():
        tasks = [asyncio.create_task(seat("a", 0.01, NoModelActivity("no calls"))),
                 asyncio.create_task(seat("b", 10))]
        return await settle(tasks, NoModelActivity)

    with pytest.raises(NoModelActivity):
        asyncio.run(main())
    assert cancelled == ["b"]
