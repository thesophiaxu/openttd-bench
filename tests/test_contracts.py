"""Framework, task placement, public payload and connection contracts."""
from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from verifiers.v1 import AgentConfig, Env, EvalClientConfig, ModelContext, ProgramResult, TaskData, Taskset, TaskTimeout

from opencode.harness import OpenCodeHarness, OpenCodeHarnessConfig
from openttd_bench import grader
from openttd_bench.env import OpenTTDEnv, OpenTTDEnvConfig
from openttd_bench.payload import build_client_payload, build_server_payload
from openttd_bench.tasks import free_for_all
from openttd_bench.taskset import OpenTTDConfig, OpenTTDSeatTask, OpenTTDTask, OpenTTDTaskset


def test_harness_config_uses_the_framework_endpoint_verbatim():
    harness = OpenCodeHarness(OpenCodeHarnessConfig(id="opencode"))
    ctx = ModelContext(model="moonshotai/kimi-k3", client=EvalClientConfig())
    config = json.loads(harness._build_config(ctx, "http://127.0.0.1:5555/v1", "secret-token"))
    provider = config["provider"]["moonshotai"]
    assert provider["options"]["baseURL"] == "http://127.0.0.1:5555/v1"
    assert provider["options"]["apiKey"] == "secret-token"
    assert config["model"] == "moonshotai/moonshotai/kimi-k3"
    assert "pinference" not in json.dumps(config)


def seat_task(company=2):
    spec = free_for_all.spec(101)
    return OpenTTDSeatTask(TaskData(prompt="p", timeout=TaskTimeout(agent=1000)), spec, company=company,
                          token=f"token-{company}", api_url="http://server.sandbox.internal:8000",
                          client_payload=build_client_payload(spec))


def test_seat_task_env_and_soft_stop():
    task = seat_task()
    assert task.runtime_env() == {"HOME": "/root", "OPENTTD_API_URL": task.api_url, "OPENTTD_SEAT": "2"}
    assert task.soft_budget_seconds == 800
    assert "token-2" not in task.data.model_dump_json()


def test_client_payload_has_no_server_or_other_seat_material():
    task = seat_task()
    with tarfile.open(fileobj=io.BytesIO(task.client_payload)) as archive:
        assert all(name.startswith("client") for name in archive.getnames())
        assert "client/openttd_client/schema.json" in archive.getnames()
    server = build_server_payload(task.spec, {"admin": "private-admin", "tokens": {"other-token": 0}})
    with tarfile.open(fileobj=io.BytesIO(server)) as archive:
        assert "seats.json" in archive.getnames()
        assert not any(name.startswith("client") for name in archive.getnames())


def test_task_setup_stages_only_own_credential():
    class Recorder:
        def __init__(self):
            self.files, self.calls = {}, []

        async def write(self, path, data):
            self.files[path] = data

        async def run(self, argv, env):
            self.calls.append((argv, env))
            return ProgramResult(0, "", "")

    box = Recorder()
    asyncio.run(seat_task().setup(SimpleNamespace(info={}), box))
    assert b"token-2" in box.files["/root/.openttd_seat"]
    assert "token-2" not in repr(box.calls)
    assert "/tmp/openttd-client.tgz" in box.files
    assert not any("seats.json" in path for path in box.files)


@pytest.mark.parametrize("response", [b"", b"native data", TimeoutError("open listener")])
def test_boundary_probe_handles_prime_proxy_tcp_ack(monkeypatch, response):
    import ast
    import inspect
    import socket
    import sys
    from types import ModuleType

    tree = ast.parse(inspect.getsource(inspect.getmodule(OpenTTDSeatTask)))
    probe = next(n.value.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "probe" for t in n.targets))
    class Denied(Exception):
        status = 403
    class Transport:
        url = "http://server.internal:8000"
        def __init__(self, **kw): pass
        def request(self, method, path):
            if path == "/api/state": return {"seat": {"company": 2}}
            raise Denied()
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def recv(self, size):
            if isinstance(response, Exception): raise response
            return response
    module = ModuleType("openttd_client.transport")
    module.Transport, module.OpenTTDError = Transport, Denied
    monkeypatch.setitem(sys.modules, "openttd_client", ModuleType("openttd_client"))
    monkeypatch.setitem(sys.modules, "openttd_client.transport", module)
    monkeypatch.setattr(sys, "argv", ["probe", "2", "3979", "3977"])
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: Connection())
    if response == b"":
        exec(probe, {})
    else:
        with pytest.raises((AssertionError, TimeoutError)):
            exec(probe, {})


def test_taskset_yields_one_task_per_seed_without_server_placement():
    tasks = list(OpenTTDTaskset(OpenTTDConfig(id="openttd_bench", experiment="versus_ai_rail")).load())
    assert all(t.data.prompt == t.spec.companies[0].prompt for t in tasks)
    assert len(tasks) == 4 and all(isinstance(t, OpenTTDTask) for t in tasks)
    assert tasks[0].data.timeout.agent == 10800 and tasks[0].data.image is None
    short = list(OpenTTDTaskset(OpenTTDConfig(id="openttd_bench", experiment="two_towns_rail", seeds=[7], agent_seconds=60)).load())
    assert len(short) == 1 and short[0].data.timeout.agent == 60 and short[0].spec.map.seed == 7


def test_env_declares_four_roles_and_separate_server():
    assert [n for n, f in OpenTTDEnvConfig.model_fields.items() if isinstance(f.default, AgentConfig)] == ["p1", "p2", "p3", "p4"]
    assert OpenTTDEnvConfig.model_fields["server"].default.type == "prime"
    assert issubclass(OpenTTDEnv, Env) and issubclass(OpenTTDTaskset, Taskset)


def test_connection_file_restores_url_and_token_without_inherited_env(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "image/client/openttd_client/transport.py"
    spec = importlib.util.spec_from_file_location("smoke_transport", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENTTD_API_URL", raising=False)
    monkeypatch.delenv("OPENTTD_API_TOKEN", raising=False)
    (tmp_path / ".openttd_seat").write_text("OPENTTD_API_URL=http://game.internal:8000\nOPENTTD_API_TOKEN=mine\n")
    transport = mod.Transport()
    assert (transport.url, transport.token) == ("http://game.internal:8000", "mine")
    monkeypatch.setenv("OPENTTD_API_URL", "http://override:8000")
    monkeypatch.setenv("OPENTTD_API_TOKEN", "env")
    assert mod.Transport().url == "http://override:8000"
    assert mod.Transport("http://explicit", "").token == ""
    assert mod.Transport("http://explicit", "arg").url == "http://explicit"


def test_grader_refuses_stale_or_partial_snapshots():
    spec = free_for_all.spec(101)
    companies = {str(c): {} for c in range(len(spec.companies))}
    grader.check_scorable(spec, {"accounting_snapshot": {"fresh": True}, "companies": companies})
    with pytest.raises(ValueError, match="stale"):
        grader.check_scorable(spec, {"accounting_snapshot": {"fresh": False}, "companies": companies})
    with pytest.raises(ValueError, match="expected"):
        grader.check_scorable(spec, {"accounting_snapshot": {"fresh": True}, "companies": {"0": {}}})
