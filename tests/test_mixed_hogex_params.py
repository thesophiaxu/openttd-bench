"""The config parameters of mixed_hogex_realtime_10y change only what they name."""

import pytest

from openttd_bench.grader import score
from openttd_bench.tasks.mixed_hogex_realtime_10y import spec
from openttd_bench.taskset import SOFT_BUDGET_FRACTION, agent_budget_seconds

NOLOANS = dict(starting_cash=800_000, loans=False, allowed_modes=["rail", "road"])
HEADLINE = dict(llm_seats=2, hogex_seats=2, **NOLOANS)


@pytest.mark.parametrize("seed", [101, 202, 303])
def test_slow_clock_matches_the_075x_series(seed):
    old, new = spec(seed, **HEADLINE), spec(seed, **HEADLINE, game_speed_percent=25)
    a, b = old.model_dump(), new.model_dump()
    b["rules"]["game_speed_percent"] = 75
    for company in b["companies"][:2]:
        company["prompt"] = company["prompt"].replace(
            "25% of normal OpenTTD speed", "75% of normal OpenTTD speed"
        ).replace("about 8 hours 7 minutes", "about 2 hours 40 minutes")
    assert a == b
    assert new.rules.horizon_wall_seconds == pytest.approx(3 * old.rules.horizon_wall_seconds)
    assert new.world()["rules"]["game_speed_percent"] == 25
    assert new.rules.horizon_wall_seconds < agent_budget_seconds(new) * SOFT_BUDGET_FRACTION


def test_only_loan_rule_and_announcement_change():
    old, new = spec(101, **{**HEADLINE, "loans": True}), spec(101, **HEADLINE)
    assert new.rules.max_loan == 0 and new.world()["rules"]["max_loan"] == 0
    assert "max_loan" not in old.world()["rules"]
    a, b = old.world(), new.world()
    b["rules"].pop("max_loan")
    assert a == b
    for c in new.companies:
        assert c.starting_cash == 800_000 and c.starting_loan == 0
    for o, n in zip(old.companies[:2], new.companies[:2]):
        assert n.prompt == o.prompt.replace(
            "RULES\n", "RULES\nBorrowing is disabled for ALL companies; every company's maximum loan is zero.\n")


def test_default_is_the_plain_four_llm_free_for_all():
    s = spec(101)
    assert s.llm_seats == len(s.companies) == 4 and s.rules.max_loan is None
    assert all(c.starting_cash == 2_000_000 for c in s.companies)
    assert "$2,000,000" not in s.companies[0].prompt and "Borrowing" not in s.companies[0].prompt
    assert "3 other companies played by other AI" in s.companies[0].prompt


def test_four_llm_no_loans_announces_funding():
    s = spec(101, **NOLOANS)
    for i, company in enumerate(s.companies):
        assert company.seat == "llm" and f"You are Player {i + 1}" in company.prompt
        assert "Every company starts with $800,000 cash and $0 loan." in company.prompt
        assert "HogEx" not in company.prompt


def test_two_llm_only_roster_and_rank():
    game = spec(101, "openai/gpt-6-astra", llm_seats=2, **NOLOANS)
    assert game.llm_seats == len(game.companies) == 2
    assert all(c.harness_model == "openai/gpt-6-astra" for c in game.companies)
    for c in game.companies:
        assert "one of 2 transport companies" in " ".join(c.prompt.split())
        assert "sole rival" in c.prompt and "HogEx" not in c.prompt
    final = {"companies": {"0": {"operating_profit": 4000000}, "1": {"operating_profit": 3000000}}}
    assert score(game, final, 0).score == 1
    assert score(game, final, 1).score == 0.6


@pytest.mark.parametrize(("llm", "hogex", "phrase"), [
    (1, 1, "Your opponent is one AAAHogEx v115 native NoAI company. Both companies have"),
    (2, 1, "one other LLM-controlled company and one AAAHogEx v115 native NoAI company. All three have"),
    (3, 1, "two other LLM-controlled companies and one AAAHogEx v115 native NoAI company. All four have"),
    (2, 2, "one other LLM-controlled company and two AAAHogEx v115 native NoAI companies. All four have"),
])
def test_native_rosters(llm, hogex, phrase):
    game = spec(101, **NOLOANS, llm_seats=llm, hogex_seats=hogex)
    assert [c.seat for c in game.companies] == ["llm"] * llm + ["ai"] * hogex
    assert [c.name for c in game.companies[llm:]] == (["HogEx"] if hogex == 1 else ["HogEx 1", "HogEx 2"])
    for c in game.companies[:llm]:
        flat = " ".join(c.prompt.split())
        assert phrase in flat and "OPPONENTS GET EXACTLY the same $800,000" in flat
        assert "Every company starts with" not in flat  # OPPONENTS GET EXACTLY covers funding


def test_three_company_rank_scaling():
    game = spec(101, **NOLOANS, llm_seats=2, hogex_seats=1)
    assert "1/2 of this term" in game.companies[0].prompt
    final = {"companies": {str(i): {"operating_profit": v} for i, v in enumerate([4000000, 3000000, 2000000])}}
    assert score(game, final, 0).score == 1
    assert score(game, final, 1).score == 0.8


@pytest.mark.parametrize("params", [
    dict(llm_seats=0, hogex_seats=2), dict(llm_seats=1), dict(llm_seats=4, hogex_seats=1),
    dict(prompt="v1", game_speed_percent=25),
])
def test_rejected_parameters(params):
    with pytest.raises(ValueError):
        spec(101, **params)


def test_unknown_parameter_is_an_error():
    with pytest.raises(TypeError):
        spec(101, no_loans=True)
