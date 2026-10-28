"""Data and helpers shared by the experiments in this package.

Cargo tables per climate, the opponent AI configuration, common targets and the small
constructors the scenario files use. No prompt text lives here: each experiment writes
its own prompt in its own file.
"""

from __future__ import annotations

from openttd_bench.objectives import Meter, ObjectiveTerm
from openttd_bench.task_spec import CargoRow, CompanySpec, Landscape

SEEDS = (101, 202, 303, 404)
"""The default map seeds of every experiment (`--env.taskset.seeds` overrides)."""

# --- cargo -----------------------------------------------------------------------------------

# The engine's default cargo slots per climate with no NewGRFs (src/table/cargo_const.h,
# `_default_climate_cargo`). Labels are identity; ids are slots. `None` = unused slot.
DEFAULT_CLIMATE_CARGO: dict[str, list[str | None]] = {
    "temperate": ["PASS", "COAL", "MAIL", "OIL_", "LVST", "GOOD", "GRAI", "WOOD", "IORE", "STEL", "VALU", None],
    "arctic": ["PASS", "COAL", "MAIL", "OIL_", "LVST", "GOOD", "WHEA", "WOOD", None, "PAPR", "GOLD", "FOOD"],
    "tropic": ["PASS", "RUBR", "MAIL", "OIL_", "FRUT", "GOOD", "MAIZ", "WOOD", "CORE", "WATR", "DIAM", "FOOD"],
    "toyland": ["PASS", "SUGR", "MAIL", "TOYS", "BATT", "SWET", "TOFF", "COLA", "CTCD", "BUBL", "PLST", "FZDR"],
}
CARGO_NAMES: dict[str, str] = {
    "PASS": "Passengers",
    "COAL": "Coal",
    "MAIL": "Mail",
    "OIL_": "Oil",
    "LVST": "Livestock",
    "GOOD": "Goods",
    "GRAI": "Grain",
    "WOOD": "Wood",
    "IORE": "Iron Ore",
    "STEL": "Steel",
    "VALU": "Valuables",
    "WHEA": "Wheat",
    "PAPR": "Paper",
    "GOLD": "Gold",
    "FOOD": "Food",
    "RUBR": "Rubber",
    "FRUT": "Fruit",
    "MAIZ": "Maize",
    "CORE": "Copper Ore",
    "WATR": "Water",
    "DIAM": "Diamonds",
    "SUGR": "Sugar",
    "TOYS": "Toys",
    "BATT": "Batteries",
    "SWET": "Sweets",
    "TOFF": "Toffee",
    "COLA": "Cola",
    "CTCD": "Candyfloss",
    "BUBL": "Bubbles",
    "PLST": "Plastic",
    "FZDR": "Fizzy Drinks",
}
# Cargos delivered to towns (used to pick monitor kinds).
TOWN_CARGOS = {"PASS", "MAIL", "GOOD", "FOOD", "WATR", "SWET", "FZDR"}


def cargo_table(landscape: Landscape) -> list[CargoRow]:
    """The label<->id rows of a climate without NewGRFs."""
    return [
        CargoRow(id=slot, label=label, name=CARGO_NAMES.get(label, label), town_cargo=label in TOWN_CARGOS)
        for slot, label in enumerate(DEFAULT_CLIMATE_CARGO[landscape])
        if label is not None
    ]


# --- companies -------------------------------------------------------------------------------

# RailwAI v30 (BaNaNaS content id 419267), restricted to rail so opponents compete for
# the same routes the evaluated company can use.
RAILWAI = {
    "use_trains": "1",
    "use_road": "0",
    "use_trams": "0",
    "use_ships": "0",
    "use_planes": "0",
    "use_pass": "1",
    "use_cargo": "1",
    "expansion_drift": "5",
    "plant_trees": "0",
    "start_date": "0",
}


def llm(name: str, model: str | None, prompt: str) -> CompanySpec:
    """An LLM seat: its company name, the model under evaluation and the prompt it gets."""
    return CompanySpec(seat="llm", name=name, harness_model=model, prompt=prompt)


def railwai(index: int) -> CompanySpec:
    return CompanySpec(seat="ai", name=f"RailwAI {index}", ai_name="RailwAI", ai_config=RAILWAI)


# --- objectives ------------------------------------------------------------------------------

# Provisional target seeded from measured prior data (plan Appendix A).
PROFIT_TARGET = 3_000_000


def profit_rank_objective() -> list[ObjectiveTerm]:
    """0.6 x operating profit against `PROFIT_TARGET` + 0.4 x place by operating profit."""
    return [
        ObjectiveTerm(
            meter=Meter(kind="operating_profit"), combinator="ratio_to_target", target=PROFIT_TARGET, weight=0.6
        ),
        ObjectiveTerm(meter=Meter(kind="place", params={"by": "operating_profit"}), combinator="rank", weight=0.4),
    ]


__all__ = [
    "CARGO_NAMES",
    "DEFAULT_CLIMATE_CARGO",
    "PROFIT_TARGET",
    "RAILWAI",
    "SEEDS",
    "TOWN_CARGOS",
    "cargo_table",
    "llm",
    "profit_rank_objective",
    "railwai",
]
