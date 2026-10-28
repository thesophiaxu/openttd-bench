"""Experiment (c) free_for_all: four LLM seats, every transport mode, scored per seat.

This file is the whole definition of the experiment: the world, the grading and the
seats' system prompts (`PROMPT`, handed to each `llm(...)` seat in `spec` with its player
name filled in). The prompt is plain text — edit it here.
"""

from __future__ import annotations

from openttd_bench.task_spec import MapSpec, Rules, TaskSpec
from openttd_bench.tasks.tasks_shared import cargo_table, llm, profit_rank_objective

NAME = "free_for_all"
PLAYERS = ("Player 1", "Player 2", "Player 3", "Player 4")


def spec(seed: int, model: str | None = None) -> TaskSpec:
    """A random temperate map; the four seats take slots 0-3 in founding order."""
    return TaskSpec(
        name=f"{NAME}-s{seed}",
        map=MapSpec(seed=seed, landscape="temperate", terrain_type=1, sea_level=1, town_density=2, industry_density=4),
        companies=[llm(player, model, PROMPT.replace("{player}", player)) for player in PLAYERS],
        rules=Rules(duration_years=10),
        objective=profit_rank_objective(),
        cargos=cargo_table("temperate"),
    )


PROMPT = """\
You are playing OpenTTD 15.3 through a shell and a Python library. You are {player}, one
of 4 transport companies on this map. The others: 3 other companies played by other AI
agents under exactly the same rules and interface as you.

GOAL:
- Earn $3,000,000 of cumulative OPERATING PROFIT, measured from the company ledger:
  transport revenue minus operating expenses (vehicle running costs, refits charged
  as running expenses, property expenses, and loan interest). Construction and vehicle
  purchases/sales are excluded; selling a vehicle does not erase its booked earnings
  or losses. This is not net income, ROI, or terminal wealth. Scored linearly up to
  the target (60% of the score).
- Finish FIRST among the 4 companies by operating profit: each rival you beat is worth
  1/3 of this term (40% of the score).

You are scored on the game state at the END of the horizon (year 1960;
the game starts in 1950), never on what you say and never on your best
moment along the way. Ending early forfeits everything not yet achieved: when
something fails, diagnose it and try again.

RULES
Allowed transport modes: rail and road. Road vehicles (buses, trucks) and
trains are fully supported through the `road`, `rail`, `vehicles` and `orders`
namespaces.
THE CLOCK IS A SHARED BARRIER. Time advances only while EVERY active company is
simultaneously inside a `wait`; while any rival is thinking or building, the clock is
stopped for everyone, and while you think it is stopped for them. Surveying and
construction cost zero game time. A `wait` therefore blocks while rivals think; after
about four minutes of that it returns `status: blocked` — not an error: do useful work
(your target stays registered for a while) and wait again. Never wait as an
exploratory move: wait only when you have something running that needs game time.
The clock never advances past year 1960; when every company is done the game is
run to the horizon automatically.

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
- You may use subagents if necessary.

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


__all__ = ["NAME", "PLAYERS", "PROMPT", "spec"]
