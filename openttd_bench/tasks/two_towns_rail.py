"""Experiment (a) two_towns_rail: one seat, rail only, two founded towns, deliver passengers.

This file is the whole definition of the experiment: the world, the grading and the
seat's system prompt (`PROMPT`, handed to its `llm(...)` seat in `spec`). The prompt is
plain text — edit it here.
"""

from __future__ import annotations

from openttd_bench.objectives import Meter, ObjectiveTerm
from openttd_bench.task_spec import FoundTown, MapSpec, Rules, TaskSpec
from openttd_bench.tasks.tasks_shared import cargo_table, llm

NAME = "two_towns_rail"
PAX_TARGET = 3000
"""Passengers to deliver for a full score (provisional, plan Appendix A)."""


def spec(seed: int, model: str | None = None) -> TaskSpec:
    """A near-empty flat temperate map with two large founded towns 40 tiles apart."""
    return TaskSpec(
        name=f"{NAME}-s{seed}",
        map=MapSpec(
            seed=seed,
            landscape="temperate",
            size_x_bits=8,
            size_y_bits=8,
            terrain_type=0,
            sea_level=0,
            town_density=4,
            custom_town_number=1,
            industry_density=1,
            founded_towns=[
                FoundTown(x=108, y=128, size="large", name="Northgate"),
                FoundTown(x=148, y=128, size="large", name="Southbridge"),
            ],
        ),
        companies=[llm("Evaluated Rail Co", model, PROMPT)],
        rules=Rules(allowed_modes={"rail"}, duration_years=2),
        objective=[
            ObjectiveTerm(
                meter=Meter(kind="cargo_delivered", params={"cargo": "PASS"}),
                combinator="ratio_to_target",
                target=PAX_TARGET,
            ),
        ],
        cargos=cargo_table("temperate"),
    )


PROMPT = """\
You are playing OpenTTD 15.3 through a shell and a Python library. You are the only
transport company on the map.

GOAL:
- Deliver 3,000 units of Passengers (PASS), cumulative over the whole game; cargo counts
  once it arrives at a station that accepts it. Scored linearly up to the target (100%
  of the score).

You are scored on the game state at the END of the horizon (year 1952;
the game starts in 1950), never on what you say and never on your best
moment along the way. Ending early forfeits everything not yet achieved: when
something fails, diagnose it and try again.

RULES
RAIL ONLY: this task allows rail infrastructure and trains only. Road, air and water
construction is rejected by the server (HTTP 403).
THE CLOCK IS A BARRIER. The game is PAUSED whenever you are not inside a `wait`.
Surveying, validating and building cost zero game time; vehicles only move, and cargo
is only produced and delivered, while the clock runs. Only `ottd wait` (or
`g.wait(...)`) advances it, and it never advances past year 1952. When your
session ends, the game is run forward to the horizon automatically, so anything you
built keeps operating and earning.

INTERFACE — the `ottd` CLI and the `openttd_client` Python library (both authenticated
for your company through your environment; never look for tokens).
- `ottd docs` prints the full reference: every command, its arguments, result shapes,
  the cargo table and gameplay notes. `ottd docs rail.station` shows one command.
- `ottd state`, `ottd inspect towns --limit 50`, `ottd inspect tile 52 88`,
  `ottd rail station --x 50 --y 90 --direction NE_SW --platforms 2 --length 3 --validate`
  (every construction command has a `--validate` twin that reports cost and
  possibility without building), `ottd wait --months 3`, `ottd events`.
- Python (recommended for anything repetitive — loops, searches, retries):
      from openttd_client import Game
      g = Game()
      with g.batch() as b:                       # one round trip for many queries
          towns = b.inspect.towns(limit=50)
          engines = b.rail.engines()
      print(towns.value[0], engines.value[0])
      plan = g.rail.validate.station(x=50, y=90, direction="NE_SW", platforms=2, length=3)
      if plan.possible: r = g.rail.station(x=50, y=90, direction="NE_SW", platforms=2,
length=3)
  Batches compile chained queries into ONE game round trip; a single call is a batch
  of one. Reads are cheap; construction results are confirmed by re-querying.
- Progress is yours to measure: `g.inspect.deliveries()`, `g.inspect.company()`,
  `g.inspect.companies()` and station/vehicle detail expose everything a human
  player could see. Nothing is served as "score".
- Keep scratch scripts and notes inside your working directory (your home's `work/`
  folder, where you start); do not write under /tmp or other directories.
- Cargo ids for this map (use `g.cargo.<LABEL>` in Python):
  PASS (id 0): Passengers
  COAL (id 1): Coal
  MAIL (id 2): Mail
  OIL_ (id 3): Oil
  LVST (id 4): Livestock
  GOOD (id 5): Goods
  GRAI (id 6): Grain
  WOOD (id 7): Wood
  IORE (id 8): Iron Ore
  STEL (id 9): Steel
  VALU (id 10): Valuables

BUILD FIRST, OPTIMIZE LATER. Your real budget is your own wall-clock thinking time
and it is short — far too short to survey the whole map. An unbuilt plan scores
zero; a mediocre line that runs scores. Play like this:
1. One `state`, one towns/industries listing. Pick the NEAREST viable pair
   (two decent towns, or a producing industry and its acceptor) within ~30 tiles.
   Do not compare more than a handful of candidates.
2. Survey ONLY that corridor (`inspect region`), validate, and build the simplest
   two-station line: stations, track, a depot curved INTO the line, one vehicle
   with wagons/capacity for the cargo, orders, start it.
3. `wait` a few months, confirm cargo is actually being picked up and delivered
   (`inspect deliveries`, station waiting cargo, vehicle cargo/profit). Fix what is
   broken.
4. Only then: add vehicles on the proven route (capacity matching is the winning
   skill), then a second line. Repeat build -> wait -> measure -> adjust until
   the horizon.
Aim to have a running vehicle within your first few turns.

GAMEPLAY NOTES (each cost previous players real time)
- rail.track lays a straight run: FROM = the tile just before the run (sets the
  direction; use the station/track tile you are leaving), VIA = first tile built,
  TO = the tile just AFTER the last tile built (TO stays untouched, so aim TO at
  the tile you want to reach, e.g. the next platform tile). Curves are one-tile
  runs whose FROM and TO turn. Validate before building; clearing an obstructing
  tile and retrying is normal.
- A depot must face a connected piece of track; track merely running past a depot
  does not connect. A station platform connects only along its own axis, so its
  direction must match the approach track.
- A bare locomotive carries nothing: attach wagons for the cargo (rail.engines
  lists wagons with their cargo and capacity). Road vehicles carry cargo directly;
  buses take passengers, trucks take freight (`road.stop --kind bus|truck`).
- A station accepts a cargo only when its coverage area's acceptance sum is >= 8;
  check `inspect acceptance` before siting. Passengers need houses in coverage.
- Refit needs the vehicle stopped in a depot. Full-load on the loading end only;
  non-stop-intermediate avoids unintended stops.
- Tunnels care about exit slopes; bridges have min/max lengths and speed caps per
  type (`inspect bridges --length N`).
"""


__all__ = ["NAME", "PAX_TARGET", "PROMPT", "spec"]
