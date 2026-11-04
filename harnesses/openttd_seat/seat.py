"""Shared CLI plumbing for an agent's isolated, root-run VM.

The historical SeatHarness/SeatPaths names are retained for plugin compatibility.
Task.setup installs the client credential file; the harness never sources it.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from verifiers.v1 import Harness, HarnessConfig, ModelContext, ProgramResult, Runtime, TaskData, Trace

OUTPUT_TAIL = 3000
CAPTURE_TIMEOUT = 30


class SeatConfig(HarnessConfig):
    pass


ConfigT = TypeVar("ConfigT", bound=SeatConfig)


@dataclass(frozen=True)
class SeatPaths:
    home: str
    workdir: str
    prompt_file: str


def seat_paths(home: str = "/root", workdir: str = "/work") -> SeatPaths:
    return SeatPaths(home=home, workdir=workdir, prompt_file=f"{home}/task-prompt.md")


class SeatHarness(Harness[ConfigT], Generic[ConfigT]):
    SUPPORTS_MCP = False
    SUPPORTS_RESUME = False

    def seat_files(self, ctx: ModelContext, paths: SeatPaths, endpoint: str, secret: str) -> dict[str, bytes]:
        """Config and model credentials to write before launch."""
        return {}

    def seat_env(self, ctx: ModelContext, paths: SeatPaths, endpoint: str, secret: str) -> dict[str, str]:
        """Model environment passed directly through the runtime API, not shell text."""
        return {}

    def seat_script(self, paths: SeatPaths) -> str:
        raise NotImplementedError

    async def session_summary(self, runtime: Runtime, paths: SeatPaths) -> dict[str, Any]:
        return {}

    def paths_for(self, runtime: Runtime) -> SeatPaths:
        return seat_paths(runtime.home or "/root", runtime.config.workdir or "/work")

    async def launch(
        self,
        ctx: ModelContext,
        trace: Trace,
        runtime: Runtime,
        endpoint: str,
        secret: str,
        mcp_urls: dict[str, str],
        data: TaskData,
    ) -> ProgramResult:
        paths = self.paths_for(runtime)
        _, prompt = self.resolve_text_prompt(data)
        await runtime.write(paths.prompt_file, (prompt or "").encode())
        files = self.seat_files(ctx, paths, endpoint, secret)
        for path, content in files.items():
            await runtime.write(path, content)
        env = {**self.config.resolved_env, "HOME": paths.home, **self.seat_env(ctx, paths, endpoint, secret)}
        script = f"mkdir -p {shlex.quote(paths.workdir)} || exit; {self.seat_script(paths)}"
        result = await runtime.run(["bash", "-c", script], env)
        # Do not copy model credentials into trace diagnostics, even if a CLI echoes them.
        secrets = [secret, *(v for k, v in env.items() if any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET")))]
        for path, content in files.items():
            if path.endswith("/auth.json"):
                auth = json.loads(content)
                secrets.extend(v for v in (auth.get("tokens") or {}).values() if isinstance(v, str))
        output = result.stdout or ""
        for value in sorted(filter(None, secrets), key=len, reverse=True):
            output = output.replace(value, "[REDACTED]")
        trace.info["harness_output_tail"] = output[-OUTPUT_TAIL:]
        return result

    async def cleanup(self, trace: Trace, runtime: Runtime) -> None:
        """Upstream calls this on success and abort, before releasing the VM."""
        if "harness_session" in trace.info:
            return
        try:
            summary = await asyncio.wait_for(self.session_summary(runtime, self.paths_for(runtime)), CAPTURE_TIMEOUT)
        except Exception as exc:  # diagnostics must not fail the episode or echo credential-bearing errors
            summary = {"error": f"session capture failed ({type(exc).__name__})"}
        trace.info["harness_session"] = summary
        trace.info["harness_capture_partial"] = "harness_output_tail" not in trace.info
