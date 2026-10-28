"""A model-free harness for framework plumbing tests: sleeps, then echoes its env."""

from __future__ import annotations

from verifiers.v1 import (
    Harness,
    HarnessConfig,
    ModelContext,
    ProgramResult,
    Runtime,
    TaskData,
    Trace,
)


class SleepyConfig(HarnessConfig):
    seconds: float = 4.0


class SleepyHarness(Harness[SleepyConfig]):
    NEEDS_CONTAINER = True

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
        script = (
            f"start=$(date +%s.%N); sleep {self.config.seconds}; "
            'echo "seat=$OPENTTD_SEAT token=$OPENTTD_API_TOKEN start=$start end=$(date +%s.%N)"'
        )
        result = await runtime.run_program(["sh", "-c", script], self.config.resolved_env)
        trace.info["stdout"] = result.stdout
        return result


__all__ = ["SleepyHarness"]
