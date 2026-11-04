"""CLI harnesses use the isolated VM directly; cleanup owns native diagnostics."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from verifiers.v1 import ModelContext, ProgramResult, Runtime, TaskData
from verifiers.v1.clients import EvalClientConfig

from claude.harness import ClaudeHarness, ClaudeHarnessConfig
from opencode.harness import OpenCodeHarness, OpenCodeHarnessConfig
from openttd_seat import SeatConfig, SeatHarness, seat_paths


class FakeBox:
    """Duck recorder: upstream Runtime is an immutable backend binding."""

    def __init__(self, summary=None, home=None, workdir=None):
        self.home = home
        self.config = SimpleNamespace(workdir=workdir)
        self.calls = []
        self.files = {}
        self.summary = {} if summary is None else summary

    async def run(self, argv, env):
        self.calls.append((argv, env))
        if argv[:2] == ["python3", "-c"]:
            return ProgramResult(0, json.dumps(self.summary), "")
        return ProgramResult(0, "program output", "")

    async def write(self, path, data):
        self.files[path] = data


class EchoConfig(SeatConfig):
    pass


class EchoHarness(SeatHarness[EchoConfig]):
    def seat_files(self, ctx, paths, endpoint, secret):
        return {f"{paths.home}/.echo/config": f"{endpoint} {secret}".encode()}

    def seat_env(self, ctx, paths, endpoint, secret):
        return {"ECHO_MODEL": ctx.model, "MODEL_TOKEN": secret}

    def seat_script(self, paths):
        return f"cat {paths.prompt_file}"


def launch(harness, box, model="moonshotai/kimi-k3", endpoint="http://127.0.0.1:5555/v1", cleanup=True):
    ctx = ModelContext(model=model, client=EvalClientConfig())
    trace = SimpleNamespace(id="trace-123", info={})
    data = TaskData(prompt="go build", image="x", workdir="/work")

    async def run():
        try:
            return await harness.launch(ctx, trace, box, endpoint, "s3cret", {}, data)
        finally:
            if cleanup:
                await harness.cleanup(trace, box)

    return asyncio.run(run()), trace


def test_helpers_and_runtime_paths():
    assert seat_paths().home == "/root"
    assert seat_paths().workdir == "/work"
    assert seat_paths().prompt_file == "/root/task-prompt.md"
    assert "user" not in SeatConfig.model_fields
    harness = EchoHarness(EchoConfig(id="echo"))
    paths = harness.paths_for(FakeBox(home="/custom home", workdir="/workspace"))
    assert paths.home == "/custom home" and paths.workdir == "/workspace"


def test_launch_writes_then_runs_root_without_sourcing_credentials():
    box = FakeBox()
    harness = EchoHarness(EchoConfig(id="echo", env={"EXTRA": "1"}))
    result, trace = launch(harness, box, cleanup=False)
    assert box.files["/root/task-prompt.md"] == b"go build"
    assert box.files["/root/.echo/config"] == b"http://127.0.0.1:5555/v1 s3cret"
    assert len(box.calls) == 1
    argv, env = box.calls[0]
    assert argv[:2] == ["bash", "-c"]
    assert "cat /root/task-prompt.md" in argv[2]
    assert "mkdir -p /work" in argv[2]
    assert env == {"EXTRA": "1", "HOME": "/root", "ECHO_MODEL": "moonshotai/kimi-k3", "MODEL_TOKEN": "s3cret"}
    for obsolete in ("setpriv", "chown", ".seat_env", ".openttd_seat"):
        assert obsolete not in argv[2]
        assert not any(obsolete in p for p in box.files)
    assert "s3cret" not in " ".join(argv)
    assert result.exit_code == 0
    assert "seat_user" not in trace.info
    assert "harness_session" not in trace.info
    assert trace.info["harness_output_tail"] == "program output"
    asyncio.run(harness.cleanup(trace, box))
    assert trace.info["harness_session"] == {}


def test_frozen_runtime_merges_task_env_without_mutation():
    class Backend:
        config = SimpleNamespace(workdir="/work")

        def __init__(self):
            self.box = FakeBox()

        async def run(self, binding, argv, env):
            return await self.box.run(argv, binding.process_env(env))

        async def write(self, binding, path, data):
            await self.box.write(path, data)

    backend = Backend()
    runtime = Runtime(backend, env={"HOME": "/root", "OPENTTD_API_URL": "http://control", "OPENTTD_SEAT": "1"})
    launch(EchoHarness(EchoConfig(id="echo")), runtime)
    env = backend.box.calls[0][1]
    assert env["OPENTTD_API_URL"] == "http://control"
    assert env["MODEL_TOKEN"] == "s3cret"
    assert "OPENTTD_API_TOKEN" not in env
    assert "MODEL_TOKEN" not in runtime.env


def test_session_summary_failure_is_diagnostic_and_redacted():
    class Broken(EchoHarness):
        async def session_summary(self, runtime, paths):
            raise OSError("secret token s3cret")

    result, trace = launch(Broken(EchoConfig(id="echo")), FakeBox())
    assert result.exit_code == 0
    assert "OSError" in trace.info["harness_session"]["error"]
    assert "s3cret" not in str(trace.info)


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_cleanup_collects_after_launch_failure(failure):
    class FailedBox(FakeBox):
        async def run(self, argv, env):
            if argv[0] == "bash":
                raise failure()
            return await super().run(argv, env)

    harness = OpenCodeHarness(OpenCodeHarnessConfig(id="opencode"))
    box = FailedBox(summary={"store": "opencode", "tool_calls": 3})
    trace = SimpleNamespace(id="failed-trace", info={})

    async def run():
        try:
            with pytest.raises(failure):
                await harness.launch(ModelContext(model="test", client=EvalClientConfig()), trace, box,
                                     "http://proxy/v1", "secret", {}, TaskData(prompt="go", image="x"))
        finally:
            await harness.cleanup(trace, box)
        await harness.cleanup(trace, box)  # idempotent

    asyncio.run(run())
    assert trace.info["harness_session"] == box.summary
    assert len(box.calls) == 1


def test_summary_timeout_is_diagnostic(monkeypatch):
    from openttd_seat import seat

    class Hung(EchoHarness):
        async def session_summary(self, runtime, paths):
            await asyncio.Event().wait()

    monkeypatch.setattr(seat, "CAPTURE_TIMEOUT", 0.01)
    _, trace = launch(Hung(EchoConfig(id="echo")), FakeBox())
    assert "TimeoutError" in trace.info["harness_session"]["error"]


def test_opencode_files_env_script_and_summary():
    summary = {"store": "opencode", "assistant_messages": 7, "output_tokens": 1234, "tool_calls": 5}
    box = FakeBox(summary=summary)
    _, trace = launch(OpenCodeHarness(OpenCodeHarnessConfig(id="opencode")), box)
    config = json.loads(box.files["/root/.config/opencode/opencode.json"])
    assert config["provider"]["moonshotai"]["options"]["baseURL"] == "http://127.0.0.1:5555/v1"
    argv, env = box.calls[0]
    assert 'PATH="/opt/opencode/bin:$PATH"' in argv[2]
    assert env["OPENCODE_DISABLE_FILETIME_CHECK"] == "true"
    assert "$PATH" not in str(env)
    assert argv[2].endswith("cd /work && cat /root/task-prompt.md | opencode run 2>&1")
    assert box.calls[1][0][-1] == "/root"
    assert trace.info["harness_session"] == summary


def test_claude_env_and_script():
    box = FakeBox(summary={"store": "claude_code"})
    _, trace = launch(ClaudeHarness(ClaudeHarnessConfig(id="claude")), box, model="anthropic/claude-sonnet-4.5")
    argv, env = box.calls[0]
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:5555"
    assert env["ANTHROPIC_API_KEY"] == "s3cret"
    assert env["ANTHROPIC_MODEL"] == "anthropic/claude-sonnet-4.5"
    assert env["CLAUDE_CONFIG_DIR"] == "/root/.claude"
    assert "$PATH" not in str(env)
    assert "s3cret" not in str(argv)
    assert argv[2].endswith("claude -p --dangerously-skip-permissions --output-format text 2>&1")
    assert "/root/.claude/.keep" in box.files
    assert trace.info["harness_session"] == {"store": "claude_code"}


def test_output_tail_redacts_model_token():
    class EchoSecret(FakeBox):
        async def run(self, argv, env):
            return ProgramResult(0, "s3cret" * 1000, "")

    _, trace = launch(EchoHarness(EchoConfig(id="echo")), EchoSecret())
    assert "s3cret" not in trace.info["harness_output_tail"]
    assert len(trace.info["harness_output_tail"]) <= 3000


@pytest.mark.parametrize("harness_id, module", [("opencode-cli", "opencode"), ("claude", "claude")])
def test_harness_ids_load_through_verifiers(harness_id, module):
    from verifiers.v1.utils.loaders import harness_class

    cls = harness_class(harness_id)
    assert cls.__module__.startswith(module)
    assert issubclass(cls, SeatHarness)


def test_client_reads_url_and_token_from_file(tmp_path, monkeypatch):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "image" / "client"))
    from openttd_client.transport import Transport

    (tmp_path / ".openttd_seat").write_text("OPENTTD_API_URL=http://x\nOPENTTD_API_TOKEN=abc123\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENTTD_API_URL", raising=False)
    monkeypatch.delenv("OPENTTD_API_TOKEN", raising=False)
    transport = Transport()
    assert transport.token == "abc123"
    assert transport.url == "http://x"
    monkeypatch.setenv("OPENTTD_API_TOKEN", "from-env")
    assert Transport().token == "from-env"
