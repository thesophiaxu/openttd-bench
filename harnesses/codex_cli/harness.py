"""Codex CLI as a seat harness, on a ChatGPT login instead of API billing.

verifiers ships a `codex` harness that drives the CLI through an ACP adapter and the
interception proxy with an API key. This one runs `codex exec` as root in an isolated VM,
with the host's ChatGPT OAuth file copied into the agent's CODEX_HOME and
no OPENAI_* variables set — so the CLI talks to OpenAI directly and the run is metered by the
subscription. Nothing crosses the interception proxy: the trace carries no `calls`; the seat's
own session store (harvested to the host after exit, see `harvest_dir`) is the record.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from pathlib import Path

from pydantic import Field
from verifiers.v1 import ModelContext, Runtime, Trace
from verifiers.v1.harnesses.node import NODE_BIN_DIR, ensure_node

from openttd_seat import SeatConfig, SeatHarness, SeatPaths
from openttd_seat.store import codex_session_summary
from openttd_seat.seat import CAPTURE_TIMEOUT
from codex_cli.harvest import EXEC_LOG, harvest

CODEX_DIR = "/opt/codex-cli"
CODEX_BIN_DIR = f"{CODEX_DIR}/node_modules/.bin"

INSTALL = f"""\
set -e
export PATH="{NODE_BIN_DIR}:$PATH"
mkdir -p {CODEX_DIR}
if [ -f {CODEX_DIR}/.ready ] && [ "$(cat {CODEX_DIR}/.ready)" = "$CODEX_CLI_VERSION" ]; then exit 0; fi
rm -f {CODEX_DIR}/.ready
npm install --prefix {CODEX_DIR} --ignore-scripts --no-audit --no-fund --omit=dev "@openai/codex@$CODEX_CLI_VERSION" >/dev/null
echo "$CODEX_CLI_VERSION" > {CODEX_DIR}/.ready
"""


class CodexCliHarnessConfig(SeatConfig):
    version: str = Field(default="0.162.1", pattern=r"^[A-Za-z0-9._+-]+$")
    """Codex CLI release to install, pinned."""
    auth_file: str = "~/.codex/auth.json"
    """Host path of the Codex auth store (a `codex login` with the ChatGPT account); copied
    into every seat's CODEX_HOME."""
    model: str | None = None
    """Codex model id; default: the seat's model with any `provider/` prefix stripped."""
    reasoning_effort: str = Field(default="high", pattern=r"^[a-z]+$")
    harvest_dir: str = "outputs/raw/codex"
    """Host capture root: <harvest_dir>/<trace.id>. OPENTTD_RUN_DIR, when set,
    overrides it with <run_dir>/raw/codex. Only native JSONL evidence is exported."""


class CodexCliHarness(SeatHarness[CodexCliHarnessConfig]):
    APPENDS_SYSTEM_PROMPT = True
    USES_OAUTH = True

    async def setup(self, runtime: Runtime) -> None:
        await ensure_node(runtime)
        result = await runtime.run(
            ["sh", "-c", INSTALL], {**self.config.resolved_env, "CODEX_CLI_VERSION": self.config.version}
        )
        if result.exit_code != 0:
            raise RuntimeError(f"codex-cli install failed: {(result.stderr or result.stdout).strip()[-800:]}")

    def _model(self, ctx: ModelContext) -> str:
        return self.config.model or ctx.model.rpartition("/")[2]

    def seat_files(self, ctx: ModelContext, paths: SeatPaths, endpoint: str, secret: str) -> dict[str, bytes]:
        auth = Path(self.config.auth_file).expanduser().read_bytes()
        parsed = json.loads(auth)
        if (parsed.get("auth_mode") != "chatgpt" or parsed.get("OPENAI_API_KEY")
                or not (parsed.get("tokens") or {}).get("access_token")):
            raise ValueError("codex-cli requires ChatGPT OAuth credentials, not an API key")
        config = (
            f'model = "{self._model(ctx)}"\n'
            'model_provider = "openai"\n'
            f'model_reasoning_effort = "{self.config.reasoning_effort}"\n'
            'approval_policy = "never"\n'
            'sandbox_mode = "danger-full-access"\n'
            f'[projects."{paths.workdir}"]\ntrust_level = "trusted"\n'
        )
        return {f"{paths.home}/.codex/auth.json": auth, f"{paths.home}/.codex/config.toml": config.encode()}

    def seat_env(self, ctx: ModelContext, paths: SeatPaths, endpoint: str, secret: str) -> dict[str, str]:
        # Deliberately no OPENAI_API_KEY / OPENAI_BASE_URL: auth.json's ChatGPT tokens are the credential.
        return {
            "CODEX_HOME": f"{paths.home}/.codex",
            "NO_COLOR": "1",
        }

    def seat_script(self, paths: SeatPaths) -> str:
        log = shlex.quote(f"{paths.home}/{EXEC_LOG}")
        # Preserve CLI failure status through tee; OAuth must never pick up an API
        # credential/base URL inherited from a parent runtime.
        prefix = f'set -o pipefail; export PATH="{CODEX_BIN_DIR}:{NODE_BIN_DIR}:$PATH"; '
        if self.USES_OAUTH:
            prefix += "unset OPENAI_API_KEY OPENAI_BASE_URL CODEX_API_KEY; "
        return (
            prefix + f"cd {shlex.quote(paths.workdir)} && codex exec --skip-git-repo-check "
            f"--dangerously-bypass-approvals-and-sandbox --color never --json - "
            f"< {shlex.quote(paths.prompt_file)} 2>&1 | tee {log}"
        )

    async def session_summary(self, runtime: Runtime, paths: SeatPaths) -> dict:
        return await codex_session_summary(runtime, paths.home)

    async def cleanup(self, trace: Trace, runtime: Runtime) -> None:
        await super().cleanup(trace, runtime)
        summary = trace.info["harness_session"]
        if "harvested_to" in summary:
            return
        try:
            summary["harvested_to"] = await asyncio.wait_for(
                self._harvest(runtime, self.paths_for(runtime), trace.id), CAPTURE_TIMEOUT
            )
            summary.pop("harvest_error", None)
        except Exception as exc:  # retain summary even if export fails, and vice versa
            summary["harvest_error"] = f"native capture failed ({type(exc).__name__})"

    async def _harvest(self, runtime: Runtime, paths: SeatPaths, trace_id: str) -> str:
        run_dir = os.environ.get("OPENTTD_RUN_DIR")
        base = Path(run_dir) / "raw" / "codex" if run_dir else Path(self.config.harvest_dir)
        return await harvest(runtime, paths.home, trace_id, base)


__all__ = ["CodexCliHarness", "CodexCliHarnessConfig"]
