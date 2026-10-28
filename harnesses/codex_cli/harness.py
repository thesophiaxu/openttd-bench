"""Codex CLI as a seat harness, on a ChatGPT login instead of API billing.

verifiers ships a `codex` harness that drives the CLI through an ACP adapter and the
interception proxy with an API key. This one runs `codex exec` non-interactively, demoted to
the seat's Unix user, with the host's ChatGPT OAuth file copied into a per-seat CODEX_HOME and
no OPENAI_* variables set — so the CLI talks to OpenAI directly and the run is metered by the
subscription. Nothing crosses the interception proxy: the trace carries no `calls`; the seat's
own session store (harvested to the host after exit, see `harvest_dir`) is the record.
"""

from __future__ import annotations

import json
import os
import posixpath
import shlex
import tarfile
import time
from io import BytesIO
from pathlib import Path

from pydantic import Field
from verifiers.v1 import ModelContext, Runtime
from verifiers.v1.harnesses.node import NODE_BIN_DIR, ensure_node

from openttd_seat import SeatConfig, SeatHarness, SeatPaths
from openttd_seat.store import codex_session_summary

CODEX_DIR = "/opt/codex-cli"
CODEX_BIN_DIR = f"{CODEX_DIR}/node_modules/.bin"
EXEC_LOG = "codex-exec.jsonl"

INSTALL = f"""\
set -e
export PATH="{NODE_BIN_DIR}:$PATH"
mkdir -p {CODEX_DIR}
exec 9>/tmp/codex-cli-install.lock
flock 9
if [ -f {CODEX_DIR}/.ready ] && [ "$(cat {CODEX_DIR}/.ready)" = "$CODEX_CLI_VERSION" ]; then exit 0; fi
rm -f {CODEX_DIR}/.ready
npm install --prefix {CODEX_DIR} --ignore-scripts --no-audit --no-fund --omit=dev "@openai/codex@$CODEX_CLI_VERSION" >/dev/null
chmod -R a+rX {CODEX_DIR} {NODE_BIN_DIR}/..
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
    harvest_dir: str = "outputs/codex-sessions"
    """Host directory that receives each seat's CODEX_HOME session files and exec log after
    the program exits (`<harvest_dir>/<user>-<unix nanoseconds>/`). The post-run wrapper
    overrides this with `$OPENTTD_RUN_DIR/raw/codex/` to keep artifacts run-local.
    A host-side `docker cp` loop (scripts/harvest_codex.sh) covers the box dying before that."""


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
            "PATH": f"{CODEX_BIN_DIR}:{NODE_BIN_DIR}:$PATH",
            "CODEX_HOME": f"{paths.home}/.codex",
            "NO_COLOR": "1",
        }

    def seat_script(self, paths: SeatPaths) -> str:
        log = shlex.quote(f"{paths.home}/{EXEC_LOG}")
        # Preserve CLI failure status through tee; OAuth must never pick up an API
        # credential/base URL inherited from a parent runtime.
        prefix = "set -o pipefail; "
        if self.USES_OAUTH:
            prefix += "unset OPENAI_API_KEY OPENAI_BASE_URL CODEX_API_KEY; "
        return (
            prefix + f"cd {shlex.quote(paths.workdir)} && codex exec --skip-git-repo-check "
            f"--dangerously-bypass-approvals-and-sandbox --color never --json - "
            f"< {shlex.quote(paths.prompt_file)} 2>&1 | tee {log}"
        )

    async def session_summary(self, runtime: Runtime, paths: SeatPaths) -> dict:
        summary = await codex_session_summary(runtime, paths.home)
        try:
            summary["harvested_to"] = await self._harvest(runtime, paths)
        except Exception as exc:  # diagnostics only
            summary["harvest_error"] = f"{type(exc).__name__}: {exc}"[:300]
        return summary

    async def _harvest(self, runtime: Runtime, paths: SeatPaths) -> str:
        """Pull CODEX_HOME/sessions and the exec log to the host as one tar read."""
        result = await runtime.run(
            [
                "sh",
                "-c",
                f"cd {shlex.quote(paths.home)} && tar -cf /tmp/harvest-{paths.user}.tar .codex/sessions {EXEC_LOG}",
            ],
            {},
        )
        if result.exit_code != 0:
            raise RuntimeError((result.stderr or result.stdout).strip()[-300:])
        data = await runtime.read(f"/tmp/harvest-{paths.user}.tar")
        run_dir = os.environ.get("OPENTTD_RUN_DIR")
        base = Path(run_dir) / "raw" / "codex" if run_dir else Path(self.config.harvest_dir)
        dest = base / f"{paths.user}-{time.time_ns()}"
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=BytesIO(data)) as tar:
            tar.extractall(dest, filter="data")
        return posixpath.join(str(dest))


__all__ = ["CodexCliHarness", "CodexCliHarnessConfig"]
