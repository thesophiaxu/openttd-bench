"""Conditions of the mixed native/LLM head-to-head and pinned content."""
import importlib.util
import json
import shutil
from pathlib import Path

from openttd_bench.tasks.mixed_hogex_realtime_10y import spec as mixed

HEADLINE = dict(llm_seats=2, hogex_seats=2, starting_cash=800_000, loans=False, allowed_modes=["rail", "road"])


def spec(seed, model=None, **params):
    return mixed(seed, model, **{**HEADLINE, **params})

ROOT = Path(__file__).resolve().parent.parent


def load_image_module(name):
    import sys
    module_spec = importlib.util.spec_from_file_location(name, ROOT / "image" / f"{name}.py")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[name] = module
    module_spec.loader.exec_module(module)
    return module


def test_mixed_conditions_and_prompt():
    s = spec(101)
    ref = mixed(101)
    assert s.map == ref.map and s.objective == ref.objective and s.cargos == ref.cargos
    assert s.rules.clock == "realtime" and s.rules.game_speed_percent == 75
    assert s.horizon_year == 1960 and s.rules.allowed_modes == {"rail", "road"}
    assert s.llm_seats == 2
    assert [c.seat for c in s.companies] == ["llm", "llm", "ai", "ai"]
    assert all(c.starting_cash == 800_000 and c.starting_loan == 0 for c in s.companies)
    for c in s.companies[:2]:
        assert "$800,000" in c.prompt and c.name in c.prompt
        assert "Borrowing is disabled for ALL companies" in c.prompt
        assert "AAAHogEx v115" in c.prompt
        assert "same rules and interface as you" not in c.prompt
        # Only the matchup/funding description and the rank weight differ.
        tail = lambda p: p[p.index("You are scored"):].replace(
            "Borrowing is disabled for ALL companies; every company's maximum loan is zero.\n", "")
        assert tail(c.prompt) == tail(ref.companies[0].prompt.replace("Player 1", c.name))
    for c in s.companies[2:]:
        assert c.start_ai_line() == "start_ai AAAHogEx disable_veh_aircraft=1,disable_veh_ship=1"


def test_engine_mode_restrictions(tmp_path):
    init = load_image_module("init_game")
    paths = init.Paths.make(tmp_path / "root", tmp_path / "runtime")
    config = init.write_cfg(paths, spec(101).world(), "not-a-real-secret").read_text()
    assert "max_aircraft = 0" in config and "max_ships = 0" in config
    assert "max_trains = 0" not in config and "max_roadveh = 0" not in config
    assert "script_max_opcode_till_suspend = 250000" in config
    assert "script_max_memory_megabytes = 2048" in config


def test_hogex_matches_baseline_archive(tmp_path):
    import pytest
    content = load_image_module("fetch_content")
    source = ROOT / "outputs/hogex-baseline-20261011/assets/AAAHogEx-v115.tar.gz"
    if not source.exists():
        pytest.skip("local baseline archive not available")
    cache = tmp_path / "cache"
    cache.mkdir()
    shutil.copy2(source, cache / content.HOGEX_ASSET["file"])
    target = content.install_hogex(cache, tmp_path / "install")
    assert target.name == f"AAAHogEx-{content.HOGEX_COMMIT}"
    assert "115" in (target / "info.nut").read_text()
    original = (target / "main.nut").read_text()
    patched = content.install_hogex(cache, tmp_path / "install", no_loans=True)
    changed = (patched / "main.nut").read_text()
    assert "GetMaxLoan() == 0 ? 1.0" in changed
    # Only the no-loan inflation edge case changes, not the strategy.
    assert len([1 for a, b in zip(original.splitlines(), changed.splitlines()) if a != b]) == 1
    restored = content.install_hogex(cache, tmp_path / "install")
    assert (restored / "main.nut").read_text() == original


def test_opencode_wire_model_ids():
    from opencode.harness import OpenCodeHarness, OpenCodeHarnessConfig
    from verifiers.v1 import ModelContext
    from verifiers.v1.clients import EvalClientConfig

    harness = OpenCodeHarness(OpenCodeHarnessConfig(id="opencode", provider_key="prime"))
    for model in ("openai/gpt-6-astra", "anthropic/claude-opus-5.5"):
        cfg = json.loads(harness._build_config(
            ModelContext(model=model, client=EvalClientConfig()), "http://localhost/v1", "test"))
        provider = "prime"
        assert cfg["provider"][provider]["npm"] == "@ai-sdk/openai-compatible"
        assert model in cfg["provider"][provider]["models"]
        assert cfg["model"] == f"{provider}/{model}"
