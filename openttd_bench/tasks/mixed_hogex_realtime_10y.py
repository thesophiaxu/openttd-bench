"""Experiment mixed_hogex_realtime_10y: LLM seats, optionally against native AAAHogEx v115.

A random temperate map with a REAL-TIME clock: it runs continuously at `game_speed_percent`
from the first company's first action until year 1960, so thinking, surveying and
building all consume the horizon. Graded on operating profit and place.

The matchup and economy come from the config (`[env.taskset.params]`, passed to `spec`
as keyword arguments); omitted parameters give the plain four-LLM free-for-all:

    llm_seats = 2                       # LLM companies, seated first ("Player 1", ...)
    hogex_seats = 2                     # native AAAHogEx companies after them (NoAI API)
    starting_cash = 800_000             # every company, $0 loan; omitted = engine default, unannounced
    loans = false                       # false = every company's maximum loan is zero
    allowed_modes = ["rail", "road"]    # omitted = every mode
    game_speed_percent = 75             # 25 = three times the wall time
    prompt = "v2"                       # "v1": the first real-time prompt (75% speed only)

This file is the whole definition of the experiment: the world, the grading and the
seats' system prompt (`PROMPT`, filled in per seat by `prompt_for`). The prompt is plain
text — edit it here.
"""

from __future__ import annotations

from typing import Literal

from openttd_bench.task_spec import CompanySpec, MapSpec, Mode, Rules, TaskSpec
from openttd_bench.tasks.tasks_shared import cargo_table, profit_rank_objective

NAME = "mixed_hogex_realtime_10y"
HOGEX_CONFIG = {"disable_veh_ship": "1", "disable_veh_aircraft": "1"}
NUMBERS = {1: "one", 2: "two", 3: "three", 4: "four"}
# The whole game's wall time as the prompt words it, per speed (75% says "2 h 40" for ~2 h 42).
WALL_TIME = {75: "about 2 hours 40 minutes", 25: "about 8 hours 7 minutes"}


def spec(
    seed: int,
    model: str | None = None,
    *,
    llm_seats: int = 4,
    hogex_seats: int = 0,
    starting_cash: int | None = None,
    loans: bool = True,
    allowed_modes: list[Mode] | None = None,
    game_speed_percent: int = 75,
    prompt: Literal["v1", "v2"] = "v2",
) -> TaskSpec:
    """The seats take slots in founding order: LLM companies first, then HogEx."""
    if llm_seats < 1 or llm_seats + hogex_seats not in NUMBERS or llm_seats + hogex_seats < 2:
        raise ValueError(f"need 1+ LLM seats and 2-4 companies, got {llm_seats} LLM + {hogex_seats} HogEx")
    if prompt == "v1" and game_speed_percent != 75:
        raise ValueError("prompt v1 describes the 75% clock only")
    rules = Rules(duration_years=10, game_speed_percent=game_speed_percent, clock="realtime",
                  max_loan=None if loans else 0,
                  **({} if allowed_modes is None else {"allowed_modes": set(allowed_modes)}))
    funding = {} if starting_cash is None else {"starting_cash": starting_cash, "starting_loan": 0}
    players = [f"Player {i + 1}" for i in range(llm_seats)]
    text = prompt_for(prompt, llm_seats, hogex_seats, starting_cash, loans, game_speed_percent)
    companies = [
        CompanySpec(seat="llm", name=player, harness_model=model, prompt=text.replace("{player}", player), **funding)
        for player in players
    ]
    companies.extend(
        CompanySpec(seat="ai", name="HogEx" if hogex_seats == 1 else f"HogEx {i + 1}", ai_name="AAAHogEx",
                    ai_config=HOGEX_CONFIG, **funding)
        for i in range(hogex_seats)
    )
    return TaskSpec(
        name=f"{NAME}-s{seed}",
        map=MapSpec(seed=seed, landscape="temperate", terrain_type=1, sea_level=1, town_density=2, industry_density=4),
        companies=companies,
        rules=rules,
        objective=profit_rank_objective(),
        cargos=cargo_table("temperate"),
    )


def prompt_for(version: str, llm_seats: int, hogex_seats: int, starting_cash: int | None, loans: bool,
               speed: int) -> str:
    """The seats' prompt with everything but `{player}` filled in."""
    companies = llm_seats + hogex_seats
    cash = CompanySpec.model_fields["starting_cash"].default if starting_cash is None else starting_cash
    if hogex_seats and companies == 2:
        opponents = "Your opponent is one AAAHogEx v115 native NoAI company. Both companies have"
    elif hogex_seats:
        others = llm_seats - 1
        llms = [f"{NUMBERS[others]} other LLM-controlled compan{'y' if others == 1 else 'ies'}"] if others else []
        hogex = f"{NUMBERS[hogex_seats]} AAAHogEx v115 native\nNoAI compan{'y' if hogex_seats == 1 else 'ies'}"
        opponents = f"The others: {' and '.join([*llms, hogex])}. All {NUMBERS[companies]} have"
    elif companies == 2:
        opponents = ("Your opponent: one other company played by an LLM\n"
                     "agent under exactly the same rules and interface as you.")
    else:
        opponents = (f"The others: {companies - 1} other companies played by other AI\n"
                     "agents under exactly the same rules and interface as you.")
    if hogex_seats:
        opponents += (
            " the same economy, funding and rail/road restriction;\n"
            f"{'the native AI uses' if hogex_seats == 1 else 'native AIs use'} OpenTTD's NoAI API"
            " rather than your shell interface.\n"
            f"OPPONENTS GET EXACTLY the same ${cash:,} starting cash and $0 starting loan as you."
        )
    if companies == 2:
        rank = "beating your sole rival\n  earns this term"
    else:
        rank = f"each rival you beat is worth\n  1/{companies - 1} of this term"
    funding = ""
    if starting_cash is not None and not hogex_seats:  # with HogEx, OPPONENTS GET EXACTLY says it
        funding += f"Every company starts with ${starting_cash:,} cash and $0 loan.\n"
    if not loans:
        funding += "Borrowing is disabled for ALL companies; every company's maximum loan is zero.\n"
    if version == "v1":
        clock, auth, subagents, notes = CLOCK_V1, "; never look for tokens", SUBAGENTS_V1, NOTES_V1
    else:
        clock = CLOCK_V2.format(speed=speed, wall_time=WALL_TIME.get(speed) or wall_time(speed))
        auth, subagents, notes = "", "", ""
    return PROMPT.format(companies=companies, opponents=opponents, rank=rank, funding=funding, clock=clock,
                         auth=auth, subagents=subagents, notes=notes)


def wall_time(speed: int) -> str:
    """The whole game's wall time for a speed `WALL_TIME` does not word."""
    minutes = round(Rules(duration_years=10, game_speed_percent=speed, clock="realtime").horizon_wall_seconds / 60)
    return f"about {minutes // 60} hours {minutes % 60} minutes"


PROMPT = """\
You are playing OpenTTD 15.3 through a shell and a Python library. You are {{player}}, one
of {companies} transport companies on this map. {opponents}

GOAL:
- Earn $3,000,000 of cumulative OPERATING PROFIT, measured from the company ledger:
  transport revenue minus operating expenses (vehicle running costs, refits charged
  as running expenses, property expenses, and loan interest). Construction and vehicle
  purchases/sales are excluded; selling a vehicle does not erase its booked earnings
  or losses. This is not net income, ROI, or terminal wealth. Scored linearly up to
  the target (60% of the score).
- Finish FIRST among the {companies} companies by operating profit: {rank} (40% of the score).

You are scored on the game state at the END of the horizon (year 1960;
the game starts in 1950), never on what you say and never on your best
moment along the way. Ending early forfeits everything not yet achieved: when
something fails, diagnose it and try again.

RULES
{funding}Allowed transport modes: rail and road. Road vehicles (buses, trucks) and
trains are fully supported through the `road`, `rail`, `vehicles` and `orders`
namespaces.
{clock}

INTERFACE — the `ottd` CLI and the `openttd_client` Python library (both authenticated
for your company through your environment{auth}).
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
{subagents}
GAMEPLAY NOTES{notes}
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

CLOCK_V2 = """\
**THE CLOCK RUNS IN REAL TIME. The game does NOT pause while you're thinking, surveying,
or debugging**. It starts the moment the first company issues its first command and then
runs continuously at {speed}% of normal OpenTTD speed until year 1960. The whole
10-year game lasts {wall_time} in wall time.
The clock never advances past year 1960: `ottd state` then reports `game_over: true`,
the game is scored and every further command is refused — stop at that point.
You may use subagents as long as they help you achieving your goal; the grader does not reward
or penalize subagent use, but using subagents might make it easier to keep up with the pace of
the game.
Briefly summarize your reasoning before you act. Since model reasoning is not shown by default,
This helps subagents understand your thoughts and self-diagnosis for future runs."""

CLOCK_V1 = """\
THE CLOCK RUNS IN REAL TIME. The game does NOT pause while you think. It starts the
moment the first company issues its first command and then runs continuously at 75% of
normal OpenTTD speed until year 1960: one game day passes about every 2.7 seconds of wall
time, a game year about every 16 minutes, and the whole 10-year game lasts about 2 hours
40 minutes. Every minute you spend reasoning, surveying or debugging is a minute in which
your rivals' vehicles earn and yours do not; a company that has trains running by
mid-1950 has a decade of compounding ahead of a company that finishes its survey in
1952. Build something that earns FIRST — nearest good pair of towns or industries, one
line, one or two vehicles — then improve and expand while it runs. `wait` simply sleeps
until the requested game date (it may return `status: blocked` after a few minutes with
the date still ahead: that is not an error, keep working and wait again later); you never
need to wait for time to pass, and time never waits for you. The clock never advances
past year 1960: `ottd state` then reports `game_over: true`, the game is scored and every
further command is refused — stop at that point."""
SUBAGENTS_V1 = "- You may use subagents if necessary.\n"
NOTES_V1 = " (each cost previous players real time)"


__all__ = ["NAME", "PROMPT", "prompt_for", "spec"]
