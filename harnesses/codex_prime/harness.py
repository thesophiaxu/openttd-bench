"""Codex CLI with intercepted API billing and the benchmark's Unix-seat isolation.

Unlike codex-cli, this never reads/copies ChatGPT OAuth credentials. The seat gets
only its ephemeral interception token; Prime authentication stays on the host.
Uses Responses API end-to-end, preserving native session/exec-log harvesting.
"""
from __future__ import annotations

import json

from verifiers.v1 import ModelContext

from codex_cli.harness import CodexCliHarness, CodexCliHarnessConfig
from openttd_seat import SeatPaths


class CodexPrimeHarnessConfig(CodexCliHarnessConfig):
    """Shares pinned installation and harvest settings; auth_file is never used."""


class CodexPrimeHarness(CodexCliHarness):
    USES_OAUTH = False

    def __init__(self, config: CodexPrimeHarnessConfig) -> None:
        super().__init__(config)

    def seat_files(self, ctx: ModelContext, paths: SeatPaths, endpoint: str, secret: str) -> dict[str, bytes]:
        if self.config.model is not None and self.config.model != ctx.model:
            raise ValueError("Codex model override must equal the configured seat model")
        quote = json.dumps
        config = (
            f'model = {quote(ctx.model)}\n'
            'model_provider = "prime_proxy"\n'
            f'model_reasoning_effort = {quote(self.config.reasoning_effort)}\n'
            'model_reasoning_summary = "auto"\n'
            'model_context_window = 1050000\n'
            'model_auto_compact_token_limit = 900000\n'
            'approval_policy = "never"\n'
            'sandbox_mode = "danger-full-access"\n'
            '[model_providers.prime_proxy]\n'
            'name = "Prime interception proxy"\n'
            f'base_url = {quote(endpoint.rstrip("/"))}\n'
            'env_key = "OPENAI_API_KEY"\n'
            'wire_api = "responses"\n'
            'requires_openai_auth = false\n'
            f'[projects.{quote(paths.workdir)}]\ntrust_level = "trusted"\n'
        )
        return {f"{paths.home}/.codex/config.toml": config.encode()}

    def seat_env(self, ctx: ModelContext, paths: SeatPaths, endpoint: str, secret: str) -> dict[str, str]:
        return {**super().seat_env(ctx, paths, endpoint, secret), "OPENAI_API_KEY": secret}

    def seat_script(self, paths: SeatPaths) -> str:
        return super().seat_script(paths)


__all__ = ["CodexPrimeHarness", "CodexPrimeHarnessConfig"]
