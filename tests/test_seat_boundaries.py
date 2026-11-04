"""Company authorization and independent agent failure semantics."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException, Request
from openttd_bench.env import settle
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


def test_admin_requires_loopback_and_separate_token(core):
    app = sys.modules["server.app"]
    local = Request({"type": "http", "client": ("127.0.0.1", 1234)})
    remote = Request({"type": "http", "client": ("10.0.0.2", 1234)})
    assert app.admin_auth(local, "Bearer a") == "admin"
    for request, credential, status in [(remote, "Bearer a", 403), (local, "Bearer t0", 401)]:
        with pytest.raises(HTTPException) as error:
            app.admin_auth(request, credential)
        assert error.value.status_code == status
    assert app.seat_auth("Bearer t0") == 0
    assert app.seat_auth("Bearer t1") == 1
    with pytest.raises(HTTPException):
        app.seat_auth("Bearer a")


def test_native_listeners_bind_only_to_loopback(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.syspath_prepend(str(IMAGE))
    boot = importlib.import_module("init_game")
    cfg = boot.write_cfg(SimpleNamespace(run=tmp_path, dev=True),
                         free_for_all.spec(101).world(), "admin", "game")
    assert "[server_bind_addresses]\n127.0.0.1\n" in cfg.read_text()


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
        return await settle(tasks)

    failures = asyncio.run(main())
    assert [str(f) for f in failures] == ["harness crashed"] and sorted(finished) == ["b", "c"]
