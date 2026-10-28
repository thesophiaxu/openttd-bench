"""Claude Code as a seat harness (plans/harness-layout.md).

verifiers ships a `claude_code` harness, but it runs as root through an ACP adapter with a
per-trace config dir; a seat needs the program demoted to its Unix user with its state in
the seat home. This one installs the CLI once per box (root, world-readable, under the same
lock discipline as the built-in) and runs `claude -p` non-interactively on the seat prompt,
pointed at the interception endpoint through the Anthropic env vars.
"""

from __future__ import annotations

import shlex

from pydantic import Field
from verifiers.v1 import ModelContext, Runtime
from verifiers.v1.harnesses.node import NODE_BIN_DIR, ensure_node

from openttd_seat import SeatConfig, SeatHarness, SeatPaths
from openttd_seat.store import claude_session_summary

CLAUDE_DIR = "/opt/claude-code"
CLAUDE_BIN_DIR = f"{CLAUDE_DIR}/node_modules/.bin"

INSTALL = f"""\
set -e
export PATH="{NODE_BIN_DIR}:$PATH"
mkdir -p {CLAUDE_DIR}
exec 9>/tmp/claude-code-install.lock
flock 9
if [ -f {CLAUDE_DIR}/.ready ] && [ "$(cat {CLAUDE_DIR}/.ready)" = "$CLAUDE_CODE_VERSION" ]; then exit 0; fi
rm -f {CLAUDE_DIR}/.ready
npm install --prefix {CLAUDE_DIR} --no-audit --no-fund --omit=dev "@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION" >/dev/null
chmod -R a+rX {CLAUDE_DIR} {NODE_BIN_DIR}/..
echo "$CLAUDE_CODE_VERSION" > {CLAUDE_DIR}/.ready
"""


class ClaudeHarnessConfig(SeatConfig):
    version: str = Field(default="2.1.232", pattern=r"^[A-Za-z0-9._+-]+$")
    """Claude Code release to install, pinned (same default as verifiers' built-in)."""


class ClaudeHarness(SeatHarness[ClaudeHarnessConfig]):
    APPENDS_SYSTEM_PROMPT = True

    async def setup(self, runtime: Runtime) -> None:
        await ensure_node(runtime)
        result = await runtime.run(
            ["sh", "-c", INSTALL], {**self.config.resolved_env, "CLAUDE_CODE_VERSION": self.config.version}
        )
        if result.exit_code != 0:
            raise RuntimeError(f"claude-code install failed: {(result.stderr or result.stdout).strip()[-800:]}")

    def seat_env(self, ctx: ModelContext, paths: SeatPaths, endpoint: str, secret: str) -> dict[str, str]:
        return {
            "PATH": f"{CLAUDE_BIN_DIR}:{NODE_BIN_DIR}:$PATH",
            # The proxy serves the Anthropic dialect at <endpoint minus /v1>/v1/messages.
            "ANTHROPIC_BASE_URL": endpoint.rstrip("/").removesuffix("/v1"),
            "ANTHROPIC_API_KEY": secret,
            "ANTHROPIC_MODEL": ctx.model,
            "CLAUDE_CONFIG_DIR": f"{paths.home}/.claude",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "IS_SANDBOX": "1",
        }

    def seat_files(self, ctx: ModelContext, paths: SeatPaths, endpoint: str, secret: str) -> dict[str, bytes]:
        # An existing config dir skips the first-run onboarding; the file itself can be empty JSON.
        return {f"{paths.home}/.claude/.keep": b""}

    def seat_script(self, paths: SeatPaths) -> str:
        # Non-interactive: the whole task prompt is the first user turn, permissions are
        # already bounded by the seat's Unix user, and the box has no other seat's material.
        return (
            f"cd {shlex.quote(paths.workdir)} && cat {shlex.quote(paths.prompt_file)} | "
            f"claude -p --dangerously-skip-permissions --output-format text 2>&1"
        )

    async def session_summary(self, runtime: Runtime, paths: SeatPaths) -> dict:
        return await claude_session_summary(runtime, paths.home)


__all__ = ["ClaudeHarness", "ClaudeHarnessConfig"]
