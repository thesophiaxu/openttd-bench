"""Each experiment module is the sole source of truth for its scenario: the literal prompt it
attaches to every llm seat must agree with its own spec (horizon, cargo ids, seat names,
targets, rules)."""

import re
import tomllib
from pathlib import Path

import pytest

from openttd_bench.task_spec import ENGINE_VERSION
from openttd_bench.tasks import EXPERIMENTS, experiment, free_for_all, mixed_hogex_realtime_10y, two_towns_rail, versus_ai_rail
from openttd_bench.tasks.tasks_shared import PROFIT_TARGET, SEEDS
from openttd_bench.taskset import agent_budget_seconds

ROOT = Path(__file__).resolve().parent.parent
CARGO_LINE = re.compile(r"^  ([A-Z_]{4}) \(id (\d+)\): (.+)$", re.M)


def test_registry_names_modules_and_configs_agree():
    assert set(EXPERIMENTS) == {"two_towns_rail", "versus_ai_rail", "free_for_all", "mixed_hogex_realtime_10y"}
    for name, module in EXPERIMENTS.items():
        assert module.NAME == name and module.__name__.rsplit(".", 1)[-1] == name
        assert module.spec(SEEDS[0]).name == f"{name}-s{SEEDS[0]}"
    for path in (ROOT / "configs").rglob("*.toml"):
        taskset = tomllib.loads(path.read_text())["env"]["taskset"]
        assert taskset["experiment"] in EXPERIMENTS
        experiment(taskset["experiment"]).spec(SEEDS[0], **taskset.get("params", {}))
    with pytest.raises(ValueError):
        experiment("nope")


CONFIG_PARAMS = sorted(
    {
        (taskset["experiment"], tuple(sorted((k, tuple(v) if isinstance(v, list) else v)
                                             for k, v in taskset.get("params", {}).items())))
        for taskset in (tomllib.loads(p.read_text())["env"]["taskset"] for p in (ROOT / "configs").rglob("*.toml"))
    }
    | {(name, ()) for name in EXPERIMENTS}
    | {("mixed_hogex_realtime_10y", (("hogex_seats", 1), ("llm_seats", 3))),
       ("mixed_hogex_realtime_10y", (("hogex_seats", 1), ("llm_seats", 2))),
       ("mixed_hogex_realtime_10y", (("hogex_seats", 2), ("llm_seats", 1)))}
)


@pytest.mark.parametrize(("name", "params"), CONFIG_PARAMS)
def test_seat_prompts_match_their_own_spec(name, params):
    module = experiment(name)
    params = {k: list(v) if isinstance(v, tuple) else v for k, v in params}
    spec = module.spec(SEEDS[0], "some/model", **params)
    assert module.spec(SEEDS[1], "some/model", **params).spec_hash != spec.spec_hash
    # Check fields, not substrings: the experiment name itself may contain 'promptv2'.
    assert "prompt" not in spec.world()
    assert all("prompt" not in c for c in spec.world()["companies"])
    rivals_ai = [c for c in spec.companies if c.seat == "ai"]
    for company in spec.companies:
        if company.seat != "llm":
            assert company.prompt is None
            continue
        flat = " ".join(company.prompt.split())  # phrases may wrap across lines in the file
        assert f"OpenTTD {ENGINE_VERSION}" in flat
        assert f"year {spec.horizon_year}" in flat and f"starts in {spec.map.start_year}" in flat
        assert f"past year {spec.horizon_year}" in flat
        listed = [(label, int(cid), cname) for label, cid, cname in CARGO_LINE.findall(company.prompt)]
        assert listed == [(r["label"], r["id"], r["name"]) for r in spec.cargo_table()]
        assert ("RAIL ONLY" in flat) == spec.rules.rail_only
        assert ("SHARED BARRIER" in flat) == (len(spec.companies) > 1 and not spec.rules.realtime)
        assert ("RUNS IN REAL TIME" in flat) == spec.rules.realtime
        if spec.rules.realtime:
            assert f"{spec.rules.game_speed_percent}% of normal" in flat
            if spec.rules.game_speed_percent == 25:
                assert "8 hours 7 minutes" in flat
                assert spec.rules.horizon_wall_seconds / 60 == pytest.approx(487, abs=1)
            elif params.get("prompt") != "v1":
                assert "2 hours 40 minutes" in flat
                assert spec.rules.horizon_wall_seconds / 60 == pytest.approx(160, abs=3)
            else:
                assert f"{spec.rules.seconds_per_game_day:.1f} seconds" in flat
                assert f"{round(spec.rules.seconds_per_game_year / 60)} minutes" in flat
            assert "game_over" in flat
        assert ("OPPONENTS GET EXACTLY" in flat) == bool(rivals_ai)
        for rival in rivals_ai:
            assert rival.ai_name in flat
        if len(spec.companies) > 1:
            assert company.name in flat
            assert f"one of {len(spec.companies)} transport companies" in flat
        for term in spec.objective:
            if term.target is not None:
                assert f"{int(term.target):,}" in flat


def test_experiment_specifics():
    solo = two_towns_rail.spec(101).companies[0].prompt
    assert f"{two_towns_rail.PAX_TARGET:,}" in solo and "Passengers (PASS)" in solo
    versus = versus_ai_rail.spec(101).companies[0].prompt
    assert f"${PROFIT_TARGET:,}" in versus and "RailwAI" in versus
    seats = [c.prompt for c in free_for_all.spec(101).companies]
    assert [c.name for c in free_for_all.spec(101).companies] == list(free_for_all.PLAYERS)
    assert seats[0] != seats[3] and seats[0].replace("Player 1", "Player 4") == seats[3]
    assert "{player}" not in seats[2] and "Player 3" in seats[2]


def test_realtime_free_for_all_matches_free_for_all_except_the_clock():
    paused, realtime = free_for_all.spec(101), mixed_hogex_realtime_10y.spec(101, prompt="v1")
    assert realtime.rules.clock == "realtime" and realtime.rules.game_speed_percent == 75
    assert realtime.rules.duration_years == paused.rules.duration_years == 10
    assert realtime.world()["rules"]["clock"] == "realtime" and paused.world()["rules"]["clock"] == "paused"
    assert realtime.map == paused.map and realtime.objective == paused.objective and realtime.cargos == paused.cargos
    assert [c.name for c in realtime.companies] == [c.name for c in paused.companies]
    assert realtime.spec_hash != paused.spec_hash
    # 74 ticks/day x 27 ms x 100/75 = 2.664 s per day; ten years is ~2 h 42 min of wall clock.
    assert realtime.rules.seconds_per_game_day == pytest.approx(2.664)
    assert realtime.rules.horizon_wall_seconds == pytest.approx(9730, abs=5)
    assert agent_budget_seconds(realtime) > realtime.rules.horizon_wall_seconds + 600
    assert agent_budget_seconds(paused) == 10800
