import tomllib

import pytest
from verifiers.v1 import ModelContext
from verifiers.v1.clients import EvalClientConfig
from verifiers.v1.utils.loaders import load_harness

from codex_prime import CodexPrimeHarness, CodexPrimeHarnessConfig
from openttd_seat import seat_paths


@pytest.mark.parametrize("model", ["openai/gpt-6-astra", "openai/gpt-5.6-sol", "openai/gpt-5.5", "openai/gpt-5.4", "deepseek/deepseek-v4.1-flash-1017"])
def test_prime_codex_never_reads_oauth_and_preserves_model(model):
    harness = CodexPrimeHarness(CodexPrimeHarnessConfig(id="codex-prime", auth_file="/nonexistent/oauth.json"))
    paths = seat_paths("agent3")
    ctx = ModelContext(model=model, client=EvalClientConfig())
    files = harness.seat_files(ctx, paths, "http://proxy/v1/", "ephemeral")
    assert list(files) == ["/home/agent3/.codex/config.toml"]
    config = tomllib.loads(next(iter(files.values())).decode())
    assert config["model"] == model
    provider = config["model_providers"][config["model_provider"]]
    assert provider["base_url"] == "http://proxy/v1"
    assert provider["wire_api"] == "responses"
    assert provider["requires_openai_auth"] is False
    assert harness.seat_env(ctx, paths, "http://proxy/v1", "ephemeral")["OPENAI_API_KEY"] == "ephemeral"
    assert "pipefail" in harness.seat_script(paths)
    assert "codex exec" in harness.seat_script(paths)


def test_plugin_loading():
    assert isinstance(load_harness(CodexPrimeHarnessConfig(id="codex-prime")), CodexPrimeHarness)
