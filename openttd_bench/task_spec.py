"""TaskSpec: the schema of one deterministic OpenTTD game. Schema only.

The one abstraction shared by task definition, grader, control plane and image
tooling. It is importable without `verifiers` and renders three artifacts, all pure
functions of its fields:

- `world()`   — everything the game side may know (map, companies, rules, ports, the
                generic cargo *sampling* plan). Delivered to `init_game` and, inside the
                sandbox, to the GameScript as the `configure` bootstrap. **No objective.**
- `grading()` — the objective terms and horizon semantics. Never enters the sandbox.
- `cargo_table()` — the label<->id rows the experiment supplied (`cargos`), baked into
                the client library and asserted at init against the runtime.

Everything scenario-specific — the experiments themselves, their prompts, the cargo
tables per climate, opponent configs, targets — lives in `openttd_bench/tasks/`. This
module defines no prompt text and knows no cargo names; an llm seat's prompt is a field
of its `CompanySpec`, supplied by the experiment.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from openttd_bench.objectives import ObjectiveTerm, monitor_plan

ENGINE_VERSION = "15.3"
WORLD_FORMAT = 3

Landscape = Literal["temperate", "arctic", "tropic", "toyland"]
Mode = Literal["rail", "road", "air", "water"]
ALL_MODES: frozenset[str] = frozenset({"rail", "road", "air", "water"})


class CargoRow(BaseModel):
    """One cargo slot of the pinned engine + climate: the label is identity, the id is
    the slot the runtime exposes. Rows come from the experiment (`tasks_shared`)."""

    id: int
    label: str
    name: str
    town_cargo: bool = False
    """Delivered to towns (passengers, mail, goods, ...) rather than to industries."""

    @model_validator(mode="after")
    def _check(self) -> CargoRow:
        if len(self.label) != 4:
            raise ValueError(f"cargo label {self.label!r} must be 4 characters")
        if not 0 <= self.id < 64:
            raise ValueError(f"cargo id {self.id} is outside the engine's 64 slots")
        return self


class FoundTown(BaseModel):
    """A town founded by the deity GameScript at init (task (a)'s deterministic map)."""

    x: int
    y: int
    size: Literal["small", "medium", "large"] = "medium"
    city: bool = False
    name: str | None = None


class MapSpec(BaseModel):
    seed: int
    landscape: Landscape = "temperate"
    size_x_bits: int = 8
    size_y_bits: int = 8
    terrain_type: int = 1
    """difficulty.terrain_type: 0 very flat, 1 flat, 2 hilly, 3 mountainous, 4 alpinist."""
    sea_level: int = 1
    """difficulty.quantity_sea_lakes: 0 very low .. 3 high."""
    town_density: int = 2
    """difficulty.number_towns: 0 very low .. 3 high, 4 custom (`custom_town_number`)."""
    custom_town_number: int = 1
    industry_density: int = 4
    """difficulty.industry_density: 0 funding only, 1 minimal, 2 very low .. 5 high."""
    start_year: int = 1950
    founded_towns: list[FoundTown] = Field(default_factory=list)

    @property
    def size_x(self) -> int:
        return 1 << self.size_x_bits

    @property
    def size_y(self) -> int:
        return 1 << self.size_y_bits


class CompanySpec(BaseModel):
    seat: Literal["llm", "ai"]
    name: str | None = None
    harness_model: str | None = None
    """llm seats: the model under evaluation, recorded for bookkeeping only."""
    prompt: str | None = None
    """llm seats: the seat's system prompt, written by the experiment. Never enters
    `world()` (and so never the sandbox) and does not affect `spec_hash`."""
    ai_name: str | None = None
    """ai seats: the BaNaNaS AI to seat (`start_ai <ai_name> k=v,...`)."""
    ai_config: dict[str, str] = Field(default_factory=dict)
    starting_cash: int = 2_000_000
    starting_loan: int = 0

    @model_validator(mode="after")
    def _check(self) -> CompanySpec:
        if self.seat == "ai" and not self.ai_name:
            raise ValueError("an ai company needs ai_name")
        if self.seat == "llm" and self.ai_name:
            raise ValueError("an llm company cannot name an AI")
        if self.seat == "llm" and not (self.prompt and self.prompt.strip()):
            raise ValueError("an llm seat needs a prompt")
        if self.seat == "ai" and self.prompt is not None:
            raise ValueError("an ai company has no prompt")
        return self

    def start_ai_line(self) -> str:
        """The console line that seats this company (LLM seats are held by LLMCompany)."""
        if self.seat == "llm":
            return "start_ai LLMCompany"
        settings = ",".join(f"{k}={v}" for k, v in sorted(self.ai_config.items()))
        return f"start_ai {self.ai_name}" + (f" {settings}" if settings else "")


ClockMode = Literal["paused", "realtime"]

# The engine's fixed clock: 74 ticks per day, 27 ms per tick at 100% speed (the speed
# patch scales that interval by 100/percent), ~365.25 days per year.
TICKS_PER_DAY = 74
MILLISECONDS_PER_TICK = 27
DAYS_PER_YEAR = 365.25


class Rules(BaseModel):
    allowed_modes: set[Mode] = Field(default_factory=lambda: set(ALL_MODES))
    duration_years: int
    game_speed_percent: int = 400
    max_loan: int | None = Field(default=None, ge=0)
    """Explicit engine loan cap; None preserves the historical 500k configuration."""
    clock: ClockMode = "paused"
    """`paused`: the clock is a barrier — time advances only while every active seat waits,
    so thinking costs no game time. `realtime`: the clock starts at the first seat action and
    runs continuously at `game_speed_percent` until the horizon, thinking included; the
    control plane pauses the game once the horizon year arrives."""

    @model_validator(mode="after")
    def _check(self) -> Rules:
        if not self.allowed_modes:
            raise ValueError("allowed_modes must not be empty")
        if not 1 <= self.game_speed_percent <= 400:
            raise ValueError("game_speed_percent must be within 1..400")
        if self.duration_years < 1:
            raise ValueError("duration_years must be >= 1")
        return self

    @property
    def rail_only(self) -> bool:
        return self.allowed_modes == {"rail"}

    @property
    def realtime(self) -> bool:
        return self.clock == "realtime"

    @property
    def seconds_per_game_day(self) -> float:
        """Wall seconds one game day takes while the clock runs at `game_speed_percent`."""
        return TICKS_PER_DAY * MILLISECONDS_PER_TICK / 1000 * 100 / self.game_speed_percent

    @property
    def seconds_per_game_year(self) -> float:
        return self.seconds_per_game_day * DAYS_PER_YEAR

    @property
    def horizon_wall_seconds(self) -> float:
        """Wall seconds from clock start to the horizon while the clock runs continuously."""
        return self.seconds_per_game_year * self.duration_years


class NewGRF(BaseModel):
    """Future: a pinned NewGRF. Participates in `cargo_table()` once supported."""

    name: str
    content_id: int
    md5: str | None = None


class Ports(BaseModel):
    game: int = 3979
    admin: int = 3977
    api: int = 8000


class TaskSpec(BaseModel):
    name: str
    map: MapSpec
    companies: list[CompanySpec]
    """Order = company slots; llm seats first (they take the low slots)."""
    rules: Rules
    objective: list[ObjectiveTerm]
    """Grading-half only: never rendered into `world()`."""
    cargos: list[CargoRow]
    """The cargo table of this map's climate (+ NewGRFs, once supported), as the
    experiment supplied it; `cargo_table()` is its dict form."""
    newgrfs: list[NewGRF] = Field(default_factory=list)
    lifecycle: list[str] = Field(default_factory=list)
    """Names of optional extra init hooks (extension point; unused this iteration)."""
    ports: Ports = Field(default_factory=Ports)

    # -- validation -------------------------------------------------------------

    @model_validator(mode="after")
    def _check(self) -> TaskSpec:
        seats = [c for c in self.companies if c.seat == "llm"]
        if not seats:
            raise ValueError(f"{self.name}: at least one llm seat is required")
        first_ai = next((i for i, c in enumerate(self.companies) if c.seat == "ai"), len(self.companies))
        if any(c.seat == "llm" for c in self.companies[first_ai:]):
            raise ValueError(f"{self.name}: llm seats must precede ai seats")
        if len(self.companies) > 4:
            raise ValueError(f"{self.name}: at most 4 companies are supported")
        if not self.objective:
            raise ValueError(f"{self.name}: an objective needs at least one term")
        if self.newgrfs:
            raise ValueError(f"{self.name}: NewGRF cargo tables are not generated yet; drop newgrfs")
        for term in self.objective:
            term.check_against(self)
        for town in self.map.founded_towns:
            if not (2 <= town.x < self.map.size_x - 2 and 2 <= town.y < self.map.size_y - 2):
                raise ValueError(f"{self.name}: founded town {town} is outside the map")
        if not self.cargos:
            raise ValueError(f"{self.name}: cargos must list the climate's cargo table")
        if len({c.label for c in self.cargos}) != len(self.cargos) or len({c.id for c in self.cargos}) != len(
            self.cargos
        ):
            raise ValueError(f"{self.name}: cargo labels and ids must be unique")
        cargos = {c.label for c in self.cargos}
        for term in self.objective:
            label = term.meter.params.get("cargo")
            if label is not None and label not in cargos:
                raise ValueError(f"{self.name}: cargo {label!r} does not exist in this climate")
        return self

    # -- derived -----------------------------------------------------------------

    @property
    def llm_seats(self) -> int:
        return sum(1 for c in self.companies if c.seat == "llm")

    @property
    def horizon_year(self) -> int:
        return self.map.start_year + self.rules.duration_years

    def cargo_table(self) -> list[dict[str, Any]]:
        """The supplied cargo rows as dicts (payload, world and monitor plan form)."""
        return [row.model_dump() for row in self.cargos]

    def cargo_id(self, label: str) -> int:
        for row in self.cargo_table():
            if row["label"] == label:
                return int(row["id"])
        raise KeyError(label)

    def world(self) -> dict[str, Any]:
        """Everything the game side may know. Contains no objective."""
        return {
            "format": WORLD_FORMAT,
            "name": self.name,
            "engine": {"version": ENGINE_VERSION},
            "map": self.map.model_dump(),
            "companies": [
                {
                    "slot": i,
                    "seat": c.seat,
                    "name": c.name or (f"Player {i + 1}" if c.seat == "llm" else c.ai_name),
                    "ai_name": c.ai_name,
                    "ai_config": c.ai_config,
                    "starting_cash": c.starting_cash,
                    "starting_loan": c.starting_loan,
                }
                for i, c in enumerate(self.companies)
            ],
            "llm_seats": self.llm_seats,
            "rules": {
                "allowed_modes": sorted(self.rules.allowed_modes),
                "duration_years": self.rules.duration_years,
                "horizon_year": self.horizon_year,
                "game_speed_percent": self.rules.game_speed_percent,
                "clock": self.rules.clock,
                **({"max_loan": self.rules.max_loan} if self.rules.max_loan is not None else {}),
            },
            "monitor": monitor_plan(self.objective, self.cargo_table()),
            "cargo_table": self.cargo_table(),
            "newgrfs": [g.model_dump() for g in self.newgrfs],
            "lifecycle": list(self.lifecycle),
            "ports": self.ports.model_dump(),
        }

    def grading(self) -> dict[str, Any]:
        """The objective and horizon semantics. Never enters the sandbox."""
        return {
            "name": self.name,
            "objective": [t.model_dump() for t in self.objective],
            "start_year": self.map.start_year,
            "horizon_year": self.horizon_year,
            "llm_seats": self.llm_seats,
            "companies": len(self.companies),
        }

    @property
    def spec_hash(self) -> str:
        blob = json.dumps({"world": self.world(), "grading": self.grading()}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def spec_from_json(text: str) -> TaskSpec:
    return TaskSpec.model_validate_json(text)


__all__ = [
    "ALL_MODES",
    "CargoRow",
    "ClockMode",
    "CompanySpec",
    "ENGINE_VERSION",
    "FoundTown",
    "Landscape",
    "MapSpec",
    "Mode",
    "NewGRF",
    "Ports",
    "Rules",
    "TaskSpec",
    "WORLD_FORMAT",
    "spec_from_json",
]
