"""OpenCode as a seat harness (no built-in exists; plan 5.4, plans/harness-layout.md).

`setup` downloads the pinned fork binary once per box (root-installed, world-executable,
under a lock so concurrent seats don't race); the seat layer (`openttd_seat`) writes the
prompt and this harness's provider config into the seat's home and runs the program
demoted to the seat's Unix user.
"""

from __future__ import annotations

import json
import shlex

from pydantic import Field
from verifiers.v1 import ModelContext, Runtime

from openttd_seat import SeatConfig, SeatHarness, SeatPaths
from openttd_seat.store import opencode_session_summary

RELEASE_REPO = "PrimeIntellect-ai/opencode"
BIN_DIR = "/opt/opencode/bin"

INSTALL = f"""\
set -e
mkdir -p {BIN_DIR}
exec 9>/tmp/opencode-install.lock
flock 9
if [ -x {BIN_DIR}/opencode ]; then exit 0; fi
case "$(uname -m)" in
  x86_64) OPENCODE_ARCH=x64 ;;
  aarch64|arm64) OPENCODE_ARCH=arm64 ;;
  *) echo "Unsupported architecture: $(uname -m)"; exit 1 ;;
esac
URL="https://github.com/{RELEASE_REPO}/releases/download/v$OPENCODE_VERSION/opencode-linux-$OPENCODE_ARCH.tar.gz"
if command -v curl >/dev/null 2>&1; then curl -fsSL "$URL" -o /tmp/opencode.tar.gz;
else python3 -c "import urllib.request,sys; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" "$URL" /tmp/opencode.tar.gz; fi
tar -xzf /tmp/opencode.tar.gz -C /tmp
install -m 755 /tmp/opencode {BIN_DIR}/opencode
rm -f /tmp/opencode.tar.gz /tmp/opencode
chmod -R a+rX /opt/opencode
"""


class OpenCodeHarnessConfig(SeatConfig):
    version: str = Field(default="1.1.63-rl2", pattern=r"^[A-Za-z0-9._+-]+$")
    """OpenCode fork release, pinned."""
    provider_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")
    """Use a neutral key (e.g. prime) to avoid vendor-specific wire transforms."""


class OpenCodeHarness(SeatHarness[OpenCodeHarnessConfig]):
    async def setup(self, runtime: Runtime) -> None:
        result = await runtime.run(
            ["sh", "-c", INSTALL], {**self.config.resolved_env, "OPENCODE_VERSION": self.config.version}
        )
        if result.exit_code != 0:
            raise RuntimeError(f"opencode install failed: {(result.stderr or result.stdout).strip()[-800:]}")

    def _build_config(self, ctx: ModelContext, endpoint: str, secret: str) -> str:
        # With @ai-sdk/openai-compatible the provider-local model KEY is the wire model id,
        # so the key is the full id (moonshotai/kimi-k3) and the ref is provider/full-id.
        provider_key = self.config.provider_key or ctx.model.partition("/")[0] or "openai-compatible"
        return json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    provider_key: {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": provider_key,
                        "options": {"baseURL": endpoint.rstrip("/"), "apiKey": secret, "timeout": 3_600_000},
                        "models": {ctx.model: {"name": ctx.model, "interleaved": {"field": "reasoning_content"}}},
                    }
                },
                "model": f"{provider_key}/{ctx.model}",
                "small_model": f"{provider_key}/{ctx.model}",
                "agent": {"title": {"disable": True}},
                "compaction": {"auto": False, "prune": False},
                # `opencode run` is non-interactive: a permission prompt is auto-rejected, which
                # derailed seats that wrote scratch files under /tmp. Everything the seat can
                # touch is already bounded by its Unix user, so allow all tool permissions.
                "permission": {
                    "read": "allow",
                    "edit": "allow",
                    "glob": "allow",
                    "grep": "allow",
                    "list": "allow",
                    "bash": "allow",
                    "task": "allow",
                    "external_directory": "allow",
                    "webfetch": "allow",
                    "doom_loop": "allow",
                },
            },
            indent=2,
        )

    def seat_files(self, ctx: ModelContext, paths: SeatPaths, endpoint: str, secret: str) -> dict[str, bytes]:
        return {f"{paths.home}/.config/opencode/opencode.json": self._build_config(ctx, endpoint, secret).encode()}

    def seat_env(self, ctx: ModelContext, paths: SeatPaths, endpoint: str, secret: str) -> dict[str, str]:
        return {"PATH": f"{BIN_DIR}:$PATH", "OPENCODE_DISABLE_FILETIME_CHECK": "true"}

    def seat_script(self, paths: SeatPaths) -> str:
        # `opencode run` exits 0 even on provider errors: the seat layer keeps the output tail.
        return f"cd {shlex.quote(paths.workdir)} && cat {shlex.quote(paths.prompt_file)} | opencode run 2>&1"

    async def session_summary(self, runtime: Runtime, paths: SeatPaths) -> dict:
        return await opencode_session_summary(runtime, paths.home)


__all__ = ["OpenCodeHarness", "OpenCodeHarnessConfig"]
