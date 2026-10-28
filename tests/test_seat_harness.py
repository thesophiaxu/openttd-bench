"""The shared seat layer (harnesses/openttd_seat) and the two harnesses built on it.

What every harness gets for free: the seat user from OPENTTD_SEAT_USER, the home layout,
the root-run home preparation after the files are written, the setpriv demotion, the
harness env passed through, and the standard trace keys. What each harness adds: its
files, its exported variables, and the program line.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from verifiers.v1 import DockerConfig, ModelContext, ProgramResult, Runtime, TaskData
from verifiers.v1.clients import EvalClientConfig

from claude.harness import ClaudeHarness, ClaudeHarnessConfig
from opencode.harness import OpenCodeHarness, OpenCodeHarnessConfig
from openttd_bench.seats import SeatRuntime
from openttd_seat import SeatConfig, SeatHarness, demoted, seat_paths
from openttd_seat.seat import SEAT_USER_ENV


class FakeBox(Runtime):
    is_local = True

    def __init__(self, summary: dict | None = None) -> None:
        super().__init__(name="fake")
        self.config = DockerConfig(image="x")
        self.info = self.config.model_copy()  # type: ignore[assignment]
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.files: dict[str, bytes] = {}
        self.order: list[str] = []
        self.summary = summary

    async def start(self) -> None:
        pass

    async def run(self, argv, env):
        self.calls.append((argv, env))
        self.order.append("run:" + argv[0])
        if argv[:2] == ["python3", "-c"] and self.summary is not None:
            return ProgramResult(0, json.dumps(self.summary), "")
        return ProgramResult(0, "program output", "")

    async def _read(self, path):
        return self.files[path]

    async def write(self, path, data):
        self.files[path] = data
        self.order.append("write:" + path)


class EchoConfig(SeatConfig):
    pass


class EchoHarness(SeatHarness[EchoConfig]):
    """The smallest harness: one file, one variable, one line."""

    def seat_files(self, ctx, paths, endpoint, secret):
        return {f"{paths.home}/.echo/config": f"{endpoint} {secret}".encode()}

    def seat_env(self, ctx, paths, endpoint, secret):
        return {"ECHO_MODEL": ctx.model}

    def seat_script(self, paths):
        return f"cat {paths.prompt_file}"


def seat_view(box: Runtime, role: str, user: str) -> SeatRuntime:
    view = SeatRuntime(box, role)
    view.env = {
        "OPENTTD_API_URL": "http://127.0.0.1:20003",
        "OPENTTD_API_TOKEN": "tok",
        "OPENTTD_SEAT": "2",
        SEAT_USER_ENV: user,
    }
    return view


def launch(harness, box, user="agent3", model="moonshotai/kimi-k3", endpoint="http://127.0.0.1:5555/v1"):
    ctx = ModelContext(model=model, client=EvalClientConfig())
    trace = SimpleNamespace(info={})
    data = TaskData(prompt="go build", image="x", workdir="/work")
    result = asyncio.run(harness.launch(ctx, trace, seat_view(box, "p3", user), endpoint, "s3cret", {}, data))
    return result, trace


def test_helpers():
    assert seat_paths("agent2").home == "/home/agent2"
    assert seat_paths("agent2").workdir == "/home/agent2/work"
    assert seat_paths("agent2").prompt_file == "/home/agent2/task-prompt.md"
    assert demoted("agent2", ["sh", "-c", "id"]) == [
        "setpriv",
        "--reuid=agent2",
        "--regid=agent2",
        "--init-groups",
        "sh",
        "-c",
        "id",
    ]


def test_seat_layer_writes_then_prepares_then_demotes():
    box = FakeBox()
    harness = EchoHarness(EchoConfig(id="echo", env={"EXTRA": "1"}))
    result, trace = launch(harness, box)
    # Files land before the root-run preparation that hands the home to the seat user.
    assert box.files["/home/agent3/task-prompt.md"] == b"go build"
    assert box.files["/home/agent3/.echo/config"] == b"http://127.0.0.1:5555/v1 s3cret"
    prep_index = box.order.index("run:sh")
    assert all(box.order.index(f"write:{p}") < prep_index for p in box.files)
    prep_argv, _ = box.calls[0]
    assert "mkdir -p /home/agent3/.echo /home/agent3/work" in prep_argv[2]
    assert "chown -R agent3:agent3 /home/agent3" in prep_argv[2] and "chmod 700 /home/agent3" in prep_argv[2]
    # The program runs demoted, with HOME plus the harness variables exported and the seat env kept.
    argv, env = box.calls[1]
    assert argv[:4] == ["setpriv", "--reuid=agent3", "--regid=agent3", "--init-groups"]
    assert argv[4:6] == ["bash", "-c"]
    assert argv[6].startswith('export HOME="/home/agent3" ECHO_MODEL="moonshotai/kimi-k3"; ')
    assert argv[6].endswith("cat /home/agent3/task-prompt.md")
    assert env["OPENTTD_API_TOKEN"] == "tok" and env[SEAT_USER_ENV] == "agent3" and env["EXTRA"] == "1"
    assert result.exit_code == 0
    assert trace.info["seat_user"] == "agent3"
    assert trace.info["harness_output_tail"] == "program output"
    assert trace.info["harness_session"] == {}


def test_seat_user_falls_back_to_config():
    box = FakeBox()
    harness = EchoHarness(EchoConfig(id="echo", user="agent1"))
    view = SeatRuntime(box, "p1")
    view.env = {}
    assert harness.paths_for(view).user == "agent1"


def test_session_summary_failure_never_fails_the_launch():
    class Broken(EchoHarness):
        async def session_summary(self, runtime, paths):
            raise OSError("store missing")

    result, trace = launch(Broken(EchoConfig(id="echo")), FakeBox())
    assert result.exit_code == 0
    assert trace.info["harness_session"]["error"].startswith("OSError: store missing")


def test_opencode_files_env_script_and_summary():
    summary = {
        "store": "opencode",
        "assistant_messages": 7,
        "output_tokens": 1234,
        "reasoning_tokens": 99,
        "tool_calls": 5,
        "tools": {"bash": 5},
    }
    box = FakeBox(summary=summary)
    harness = OpenCodeHarness(OpenCodeHarnessConfig(id="opencode"))
    _, trace = launch(harness, box)
    config = json.loads(box.files["/home/agent3/.config/opencode/opencode.json"])
    assert config["provider"]["moonshotai"]["options"]["baseURL"] == "http://127.0.0.1:5555/v1"
    argv, _ = box.calls[1]
    script = argv[6]
    assert 'PATH="/opt/opencode/bin:$PATH"' in script and 'OPENCODE_DISABLE_FILETIME_CHECK="true"' in script
    assert script.endswith("cd /home/agent3/work && cat /home/agent3/task-prompt.md | opencode run 2>&1")
    # The store walk runs as root on the seat home and lands in the trace.
    summary_argv, _ = box.calls[2]
    assert summary_argv[:2] == ["python3", "-c"] and summary_argv[3] == "/home/agent3"
    assert trace.info["harness_session"] == summary
    assert "opencode_output_tail" not in trace.info  # renamed: harness_output_tail


def test_claude_env_and_script():
    box = FakeBox(summary={"store": "claude_code"})
    harness = ClaudeHarness(ClaudeHarnessConfig(id="claude"))
    _, trace = launch(harness, box, model="anthropic/claude-sonnet-4.5", endpoint="http://127.0.0.1:5555/v1")
    argv, _ = box.calls[1]
    script = argv[6]
    assert 'ANTHROPIC_BASE_URL="http://127.0.0.1:5555"' in script  # dialect route is <base>/v1/messages
    assert 'ANTHROPIC_API_KEY="s3cret"' in script and 'ANTHROPIC_MODEL="anthropic/claude-sonnet-4.5"' in script
    assert 'CLAUDE_CONFIG_DIR="/home/agent3/.claude"' in script
    assert script.endswith("claude -p --dangerously-skip-permissions --output-format text 2>&1")
    assert "/home/agent3/.claude/.keep" in box.files
    assert "mkdir -p /home/agent3/.claude /home/agent3/work" in box.calls[0][0][2]
    assert trace.info["harness_session"] == {"store": "claude_code"}


@pytest.mark.parametrize("harness_id, module", [("opencode", "opencode"), ("claude", "claude")])
def test_harness_ids_load_through_verifiers(harness_id, module):
    from verifiers.v1.utils.loaders import harness_class

    cls = harness_class(harness_id)
    assert cls.__module__.startswith(module)
    assert issubclass(cls, SeatHarness)
