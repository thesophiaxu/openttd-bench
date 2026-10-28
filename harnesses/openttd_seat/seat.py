"""SeatHarness: the per-seat plumbing shared by every harness (plan: plans/harness-layout.md).

A seat is a Unix user on the shared box (`agentN`, minted by the env and handed over as
`OPENTTD_SEAT_USER`). This module owns the seat's home layout, the root-run preparation of
that home, the `setpriv` demotion that makes the control plane the authorization boundary
between seats, and the trace keys every harness records. A harness adds only its program:
how to install it (`setup`), which files it needs in the seat home (`seat_files`), the
shell line that runs it (`seat_script`), and optionally a summary of its session store
(`session_summary`).
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import Field
from verifiers.v1 import Harness, HarnessConfig, ModelContext, ProgramResult, Runtime, TaskData, Trace

SEAT_USER_ENV = "OPENTTD_SEAT_USER"
OUTPUT_TAIL = 3000


class SeatConfig(HarnessConfig):
    user: str = Field(default="agent1", pattern=r"^[a-z_][a-z0-9_-]*$")
    """Unix user to run as; a seat task overrides it through OPENTTD_SEAT_USER."""


ConfigT = TypeVar("ConfigT", bound=SeatConfig)


@dataclass(frozen=True)
class SeatPaths:
    user: str
    home: str
    workdir: str
    prompt_file: str


def seat_paths(user: str) -> SeatPaths:
    home = f"/home/{user}"
    return SeatPaths(user=user, home=home, workdir=f"{home}/work", prompt_file=f"{home}/task-prompt.md")


def demoted(user: str, argv: list[str]) -> list[str]:
    """The argv that runs `argv` as `user` with a fresh group set."""
    return ["setpriv", f"--reuid={user}", f"--regid={user}", "--init-groups", *argv]


class SeatHarness(Harness[ConfigT], Generic[ConfigT]):
    SUPPORTS_MCP = False
    SUPPORTS_RESUME = False

    # ---- hooks a harness implements -------------------------------------------------

    def seat_files(self, ctx: ModelContext, paths: SeatPaths, endpoint: str, secret: str) -> dict[str, bytes]:
        """Files to write into the seat home before launch (config, credentials), path -> bytes."""
        return {}

    def seat_env(self, ctx: ModelContext, paths: SeatPaths, endpoint: str, secret: str) -> dict[str, str]:
        """Variables exported inside the demoted shell (HOME is always set from `paths`)."""
        return {}

    def seat_script(self, paths: SeatPaths) -> str:
        """The shell line that runs the program on `paths.prompt_file` from `paths.workdir`."""
        raise NotImplementedError

    async def session_summary(self, runtime: Runtime, paths: SeatPaths) -> dict[str, Any]:
        """What the program's own session store says it did; runs as root after exit."""
        return {}

    # ---- the shared launch ------------------------------------------------------------

    def paths_for(self, runtime: Runtime) -> SeatPaths:
        return seat_paths(runtime.env.get(SEAT_USER_ENV) or self.config.user)

    async def prepare_home(self, runtime: Runtime, paths: SeatPaths, dirs: list[str]) -> None:
        wanted = " ".join(sorted({paths.workdir, *dirs}))
        prep = await runtime.run(
            [
                "sh",
                "-c",
                f"mkdir -p {wanted} && chown -R {paths.user}:{paths.user} {paths.home} && chmod 700 {paths.home}",
            ],
            {},
        )
        if prep.exit_code != 0:
            raise RuntimeError(f"seat home preparation failed: {prep.stderr.strip()[-500:]}")

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
        await self.prepare_home(runtime, paths, [posixpath.dirname(p) for p in files])
        exports = " ".join(
            f'{k}="{v}"' for k, v in {"HOME": paths.home, **self.seat_env(ctx, paths, endpoint, secret)}.items()
        )
        script = f"export {exports}; {self.seat_script(paths)}"
        result = await runtime.run_program(demoted(paths.user, ["bash", "-c", script]), self.config.resolved_env)
        trace.info["seat_user"] = paths.user
        trace.info["harness_output_tail"] = (result.stdout or "")[-OUTPUT_TAIL:]
        try:
            trace.info["harness_session"] = await self.session_summary(runtime, paths)
        except Exception as exc:  # the summary is diagnostics; never fail a scored episode over it
            trace.info["harness_session"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        return result
