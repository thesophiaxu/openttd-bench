import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "image"))

from server.schema import Schema, SchemaError  # noqa: E402

from openttd_bench.objectives import Meter, ObjectiveTerm, evaluate  # noqa: E402
from openttd_bench.schema import COMMANDS, ORDER_FLAGS, schema_dict  # noqa: E402
from openttd_bench.task_spec import CompanySpec, MapSpec, Rules, TaskSpec  # noqa: E402
from openttd_bench.tasks import two_towns_rail, versus_ai_rail  # noqa: E402
from openttd_bench.tasks.tasks_shared import cargo_table, profit_rank_objective  # noqa: E402


def test_gamescript_registry_matches_the_schema():
    names = set()
    for path in (ROOT / "image/game/LLMEval").rglob("*.nut"):
        names |= set(re.findall(r'Registry\.Add\("([a-z_.]+)"', path.read_text()))
    gs = {n for n, c in COMMANDS.items() if c.executor == "GS"}
    assert names == gs


def test_order_flags_match_the_engine_bits():
    assert ORDER_FLAGS["full_load"] == 64 and ORDER_FLAGS["full_load_any"] == 96
    assert ORDER_FLAGS["goto_nearest_depot"] == 256


def test_schema_coercion():
    schema = Schema(schema_dict())
    args = schema.coerce(
        "orders.append", {"vehicle": "12", "station": 3, "flags": ["full_load", "non_stop_intermediate"]}
    )
    assert args == {"vehicle": 12, "station": 3, "flags": 65}
    with pytest.raises(SchemaError):
        schema.coerce("inspect.tile", {"x": 1})
    with pytest.raises(SchemaError):
        schema.coerce("inspect.tile", {"x": 1, "y": 2, "z": 3})
    with pytest.raises(SchemaError):
        schema.coerce("rail.station", {"x": 1, "y": 2, "direction": "sideways"})
    assert schema.coerce("inspect.towns", {}) == {"offset": 0, "limit": 50}


def test_specs_are_deterministic_and_split_world_from_grading():
    a = two_towns_rail.spec(101)
    b = two_towns_rail.spec(101)
    assert a.spec_hash == b.spec_hash
    assert a.world() == b.world()
    assert "objective" not in json.dumps(a.world())
    assert "target" not in json.dumps(a.world())
    assert a.grading()["objective"][0]["target"] == two_towns_rail.PAX_TARGET
    assert a.world()["monitor"]["cargos"] == [0]
    assert versus_ai_rail.spec(202).world()["monitor"]["cargos"] == []
    assert two_towns_rail.spec(101).spec_hash != two_towns_rail.spec(202).spec_hash


def test_cargo_table_is_supplied_per_climate():
    temperate = two_towns_rail.spec(101).cargo_table()
    assert temperate[1]["label"] == "COAL" and temperate[3]["label"] == "OIL_"
    rubber = [
        ObjectiveTerm(
            meter=Meter(kind="cargo_delivered", params={"cargo": "RUBR"}), combinator="ratio_to_target", target=10
        )
    ]
    tropic = TaskSpec(
        name="t",
        map=MapSpec(seed=1, landscape="tropic"),
        companies=[CompanySpec(seat="llm", prompt="p")],
        rules=Rules(duration_years=1),
        objective=rubber,
        cargos=cargo_table("tropic"),
    ).cargo_table()
    assert tropic[1]["label"] == "RUBR"
    with pytest.raises(ValueError):  # the objective names a cargo the supplied table lacks
        TaskSpec(
            name="bad",
            map=MapSpec(seed=1),
            companies=[CompanySpec(seat="llm", prompt="p")],
            rules=Rules(duration_years=1),
            objective=rubber,
            cargos=cargo_table("temperate"),
        )
    with pytest.raises(ValueError):  # no cargo table at all
        TaskSpec(
            name="none",
            map=MapSpec(seed=1),
            companies=[CompanySpec(seat="llm", prompt="p")],
            rules=Rules(duration_years=1),
            objective=[ObjectiveTerm(meter=Meter(kind="operating_profit"), combinator="ratio_to_target", target=1)],
            cargos=[],
        )


def test_validation_rules():
    with pytest.raises(ValueError):  # an llm seat needs a prompt
        CompanySpec(seat="llm")
    with pytest.raises(ValueError):  # an ai company has none
        CompanySpec(seat="ai", ai_name="RailwAI", prompt="p")
    with pytest.raises(ValueError):
        TaskSpec(
            name="x",
            map=MapSpec(seed=1),
            companies=[CompanySpec(seat="ai", ai_name="RailwAI"), CompanySpec(seat="llm", prompt="p")],
            rules=Rules(duration_years=1),
            objective=[ObjectiveTerm(meter=Meter(kind="operating_profit"), combinator="ratio_to_target", target=1)],
            cargos=cargo_table("temperate"),
        )
    with pytest.raises(ValueError):
        ObjectiveTerm(meter=Meter(kind="operating_profit"), combinator="rank")


def test_scoring_combinators():
    objective = profit_rank_objective()
    measurements = {
        0: {"operating_profit": 1_500_000},
        1: {"operating_profit": 3_000_000},
        2: {"operating_profit": -5},
        3: {"operating_profit": 0},
    }
    out = evaluate(objective, 0, measurements)
    assert out.values["rank:place"] == 2
    assert abs(out.score - (0.6 * 0.5 + 0.4 * (2 / 3))) < 1e-9
    winner = evaluate(objective, 1, measurements)
    assert winner.score == pytest.approx(1.0)
    solo = evaluate(two_towns_rail.spec(101).objective, 0, {0: {"cargo_delivered": {"PASS": 1500}}})
    assert solo.score == pytest.approx(0.5)
