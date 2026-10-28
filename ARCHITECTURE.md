# openttd-bench v3

A multi-agent-by-default OpenTTD benchmark for LLM coding agents, built on the Prime
Intellect **verifiers** framework (0.3.1).

Per episode: **one sandbox** (docker container or Prime VM). The pinned OpenTTD 15.3
dedicated server and an HTTP control plane run as root; each agent seat runs as its own
Unix user (`agent1..agent4`); the control plane is the authorization boundary (bearer
token → company). Agents use a CLI (`ottd`) and a Python library (`openttd_client`)
built on goal-free lookup/build primitives. All goal logic lives in Python outside the
game; the injected GameScript never knows the goal.

| Task | Seats | Opponents | Map | Modes | Objective | Horizon |
|---|---|---|---|---|---|---|
| `two_towns_rail` | 1 LLM | none | deterministic flat map, 2 founded towns | rail | `ratio_to_target(PASS delivered, 3000)` | 2 years |
| `versus_ai_rail` | 1 LLM | 3 × RailwAI | random (seeded) | rail | `0.6·ratio(operating profit, $3M) + 0.4·rank(place)` | 10 years |
| `free_for_all` | 4 LLM | none | random (seeded) | all | same, per seat | 10 years |
| `mixed_hogex_realtime_10y` | 1–4 LLM | 0–2 × AAAHogEx | random (seeded) | all, or per config | same, per seat | 10 years, real-time clock (75%: ~2 h 42 min; 25%: ~8 h 7 min) |

`mixed_hogex_realtime_10y` is configured by `[env.taskset.params]` (passed to its `spec`):
`llm_seats`, `hogex_seats`, `starting_cash`, `loans`, `allowed_modes`, `game_speed_percent` and
`prompt` (`"v1"`/`"v2"`). Omitted parameters give the plain four-LLM free-for-all; the
headline configs set `llm_seats = 2`, `hogex_seats = 2`, `starting_cash = 800_000`,
`loans = false`, `allowed_modes = ["rail", "road"]`.

## Layout

```
openttd_bench/   task_spec (the schema: world/grading split), objectives, tasks/ (one file per
                 experiment holding its world, grading and literal prompt; tasks_shared.py has the
                 cargo tables and helpers), env (per-seat runtime views), taskset, grader, payload
                 builder, schema/ (the one command schema)
harnesses/       source root for seat harnesses: openttd_seat/ (the shared seat layer: user, home,
                 setpriv demotion, trace keys), opencode/ and claude/ (one program each)
image/           everything that runs in the sandbox: init_game.py (sequential boot), patch_speed.py,
                 fetch_content.py, server/ (control plane), client/ (agent library + ottd CLI),
                 game/LLMEval (deity GameScript, namespaced), ai/LLMCompany (order-relay AI), Dockerfile
configs/         run configs: docker (local) and prime/ (Prime VM, stock image bootstraps content)
tests/           unit tests (barrier, policy, schema/spec, contracts, seats) + tests/stress/rig.py
scripts/         run_ffa_realtime_10y.sh / run_ffa_realtime_10y_docker.sh (FFA launchers),
                 run_model_series.py + run_ledger_episode_monitored.sh (scheduled ledger series
                 and duels, with prepare/verify/finalize helpers), watch_run.py (ntfy progress),
                 watch_429.py / collect_codex_live_errors.py / snapshot_codex_live.py (Codex
                 monitoring), harvest_codex.sh, with_docker.sh
viewer/          self-contained trajectory viewer (Next.js + Vercel AI SDK/AI Elements), see Viewer below
outputs/         active runs (native results + `trajectories/` ATIF), `archived/`, shared
                 `progress/`, `saves/`; not tracked
```

## Running

```sh
uv venv .venv && VIRTUAL_ENV=$PWD/.venv uv pip install -e ".[dev]"
set -a; . ./.env; set +a                       # PRIME_TEAM_ID for team billing
scripts/with_docker.sh 'docker build -f image/Dockerfile -t openttd-bench:v3 .'

# unit tests (docker-free), then the docker seat-concurrency test
.venv/bin/python -m pytest tests --ignore=tests/test_seats.py
scripts/with_docker.sh '.venv/bin/python -m pytest tests/test_seats.py'

# real-time 10-year free-for-all (~3 h wall, one episode): the launcher sets the idle
# timeout, the per-seat output-token fuse (6M: it counts reasoning tokens) and PYTHONPATH
CONFIG=configs/prime/free-for-all-realtime-10y-promptv2-4opus55.toml scripts/run_ffa_realtime_10y.sh outputs/prime-ffa-realtime-10y-promptv2-4opus55  # Prime
CONFIG=configs/free-for-all-realtime-10y-promptv2-4codex.toml scripts/run_ffa_realtime_10y_docker.sh outputs/docker-ffa-realtime-10y-promptv2-4codex  # local docker

# follow a long run: ntfy start/heartbeat/per-episode/finish notices
.venv/bin/python scripts/watch_run.py outputs/<run> --topic <ntfy topic> --pid <eval pid> --heartbeat 30 --progress outputs/progress
```

Inference is billed to the Prime team wallet (`prime wallet`), not OpenRouter: budget ~$75 per
4-seat real-time game on Kimi K3 (~$10/seat-hour) and ~$100/hour on Claude Opus 5. Run 1 of the
real-time task died on an empty wallet (HTTP 402) — check the balance first.

## How an episode runs

1. `OpenTTDEnv.run` provisions one box from seat `p1`'s runtime policy (retries get a fresh
   world), uploads the payload (`image/` + generated `schema.json`, cargo table, `world.json`,
   `seats.json`), and starts `init_game.py` in the background.
2. `init_game` (root, sequential): acquire pinned content if missing (a stock image bootstraps
   itself), speed-patch the binary, write `openttd.cfg`, stage the scripts, launch the server,
   start the control plane, pause, `configure` the GameScript, verify the cargo table, found
   towns, seat companies (LLM seats first), fund them, write per-user seat files, mark boot
   complete.
3. Every LLM seat runs concurrently in the same box through a `SeatRuntime` view (its own
   env + borrow lock), its harness demoting to `agentN`. Seats retire on finish.
4. The env runs the clock out to the horizon, takes the grading snapshot (control-plane
   accumulator + GameScript company measurements + economy mirror), captures the terminal
   save, and `finalize` scores every seat with `openttd_bench.grader`.

While the seats play, the env appends a progress line (game date, per-company profit,
vehicles, stations) every `progress_seconds` (default 600) to
`outputs/progress/<spec>-<idx>.jsonl`; `scripts/watch_run.py --heartbeat N` posts the latest
one to ntfy every N minutes.

## Clock modes (`Rules.clock`)

- `paused` (default): the clock is a barrier — time advances only while every active seat
  is inside `/api/wait`; reading, validating and construction cost no game time.
- `realtime`: the clock starts at the first seat's first command (or the admin's
  `start_clock`) and runs continuously at `game_speed_percent` until the horizon year, when
  the control plane pauses the game for good; later commands get 409 and `/api/state`
  reports `game_over`. `wait` is a plain sleep to a date. Wall time is deterministic:
  74 ticks/day × 27 ms × 100/percent (at 75%: 2.66 s per day, ~16 min per year), and the
  per-seat agent budget derives from it (`taskset.agent_budget_seconds`). Experiment
  `mixed_hogex_realtime_10y` with default params is `free_for_all` with this clock at 75% speed.

## Harnesses (`[env.pN.harness] id`)

A harness is the program that plays a seat. `harnesses/` is a source root (verifiers loads a
harness by top-level module name, so each one is its own package there; `pyproject.toml` lists
them and `tests/conftest.py` / the launchers put `harnesses/` on the path for uninstalled runs).
`openttd_seat.SeatHarness` owns everything a seat needs regardless of program: the Unix user
from `OPENTTD_SEAT_USER`, the `/home/<user>{,/work,/task-prompt.md}` layout, the root-run
`mkdir/chown/chmod 700` preparation after the harness's files are written, the `setpriv`
demotion, and the trace keys `seat_user`, `harness_output_tail`, `harness_session`. A harness
adds `setup` (root-installed, lock-guarded binary), `seat_files` (config to write into the
home), `seat_env` (variables exported in the demoted shell), `seat_script` (the program line)
and optionally `session_summary` (its own session store → turns/tokens/tool calls in the trace).

- `opencode` — pinned `PrimeIntellect-ai/opencode` fork, `opencode run` on the prompt, provider
  JSON pointing at the interception endpoint (OpenAI-compatible dialect).
- `claude` — `@anthropic-ai/claude-code` via npm, `claude -p` on the prompt with
  `ANTHROPIC_BASE_URL` at the interception endpoint (Anthropic Messages dialect, which the
  proxy and Prime inference both serve for every model, Kimi K3 included). Named `claude`, not
  `claude_code`: verifiers' built-in `claude_code` (root, ACP) wins that id lookup.

Switching is config-only: `configs/prime/free-for-all-realtime-10y-promptv2-4opus55.toml` plays
all four seats with Claude Code on Opus 5.5, and `configs/prime/free-for-all-realtime-10y-4models.toml`
gives each seat its own `model` (`[env.pN] model = ...`).

`traces.jsonl` is written only when the episode ends. While a run is live, the programs' own
session stores are the way to see what a seat is doing: opencode under
`~agentN/.local/share/opencode/storage/{message,part}`, Claude Code under
`~agentN/.claude/projects/<slug>/*.jsonl` (subagents under `subagents/`). Read them with
`prime sandbox run <id> -- python3 ...`, or pull them out with `prime sandbox download <id>
<remote> <local>` (tar them in the box first; base64 over `sandbox run` output gets the CLI
banner mixed in). After the run, `harness_session` in each trace carries the counts.

Stopping inference mid-game is a cost lever that keeps the score: kill the seats' programs in
the box (`claude` / `opencode` processes owned by `agentN`) after saving via
`POST /api/admin/save` with the admin token from `/opt/openttd/seats.json`. The harnesses
return exit 143 (traces show `stop=error`), the env retires the seats, runs the clock out to
the horizon at sandbox cost only, and scores the world as it stands. The Opus 5 run below was
stopped this way at 1955.

## Current accounting (`company-quarterly-v1`)

New games accumulate OpenTTD company quarterly income plus signed expenses,
including completed quarters exactly once. This survives vehicle sales by either
LLMs or native AIs. The metric includes transport revenue, running-category costs
(including refits booked there), property expenses, and loan interest. It excludes
construction, vehicle purchases/sales, and other-category transfers. This is a
company operating-account subtotal, **not net income, ROI, terminal wealth, or a
per-transaction/per-vehicle ledger**.

Snapshots retain the accounting version, income, signed expenses, validity and
quarter identity. Missing history, backwards dates, or company disappearance make
accounting invalid rather than silently grading stale totals. GameScript saves now
retain meter/funding state; complete control-plane resume remains unsupported.

## Accounting caveat for historical runs

**Historical operating-profit scores are not a complete transaction ledger.** Runs
recorded before the accounting fix used an annual accumulator over the profit
counters of surviving primary vehicles. Vehicle disposal could remove unbanked
profits or losses. Sales occurred in all three original no-loan seeds; the resulting
measurement error and its effect on rankings have **not** been quantified.
Recomputing scores from those snapshots checks consistency with the recorded
measurements, not independent correctness of the accounting. Preserve those runs
as historical results; do not silently relabel them as corrected measurements.

Operating profit excludes construction and vehicle-purchase expenditure. It is not
net income, ROI, or terminal wealth, even when disposal-safe accounting is used.

## Results so far (`free_for_all` family, seed 101, operating profit at the 1960 horizon)

| Run | Clock | Seats | Winner | 2nd / 3rd / 4th | Cost |
|---|---|---|---|---|---|
| RailwAI baseline (`versus_ai_rail` opponents) | paused | 3 × RailwAI v30 | $3.8M–$9.96M per winner | — | — |
| Kimi K3 / opencode, run 1 (2026-09-08) | real-time 75% | 4 | $1.66M (0.732) | $328k / $315k / $66k | $86; leader hit the 1.2M token fuse at 1955, others died on 402 at 1957 |
| Kimi K3 / opencode, run 2 (2026-09-09) | real-time 75% | 4 | $581k (0.516) | $7k / -$20k / -$90k | $74; clean |
| Claude Opus 5 / claude (2026-09-10) | real-time 75%, inference stopped at 1955 | 4 | $1.92M (0.784) | $1.39M / $543k / $425k | $139 for 82 min |

Same seed, same prompt: the two Kimi runs differ by 3× on the winner, so single episodes are
noisy. Opus had every seat profitable by 1952 and beat the Kimi record with five idle years;
it also used Claude Code's Write/Edit and `Agent` subagents, which no Kimi seat did. No LLM
seat has yet reached a RailwAI winner.

## ATIF trajectories

The FFA launchers run evaluations through `openttd_bench.run_with_atif`. After eval
exits (including nonzero exits), it converts the completed artifacts into
`outputs/<run>/trajectories/`. The wrapper forwards termination signals, preserves
an eval failure's exit status, and returns nonzero if conversion fails. SIGKILL or
host failure requires a manual backfill. Direct `eval` invocations must use the
wrapper for automatic conversion:

```sh
.venv/bin/python -m openttd_bench.run_with_atif outputs/my-run -- \
  .venv/bin/eval @ configs/prime/free-for-all-realtime-10y-promptv2-4opus55.toml --output-dir outputs/my-run --no-serve

# Backfill Prime traces and/or the Codex harvest referenced by each seat:
.venv/bin/python -m openttd_bench.atif outputs/my-run
# Standalone native Codex session tree, or a fallback tree containing agentN directories:
.venv/bin/python -m openttd_bench.atif outputs/my-run --codex-dir /path/to/session-tree
```

Each `.atif.json` is an ATIF-v1.8 trajectory with embedded subagent trajectories.
`index.json` groups roots by episode. Content-addressed files are written before
an atomic manifest replacement; rerunning unchanged inputs is idempotent. Previous
generations are retained so concurrent readers can finish safely.

Conversion is information-preserving at the JSON-record level: `extra.source`
retains complete Prime traces or Codex records, including unknown fields/events,
parent graphs, token arrays, encrypted reasoning, and copied context. Episode
metadata is in `extra.episode`, benchmark metadata in `extra.benchmark`, and Codex
exec logs in `extra.source.artifacts`. Codex seats additionally retain their Prime
trace in `extra.prime_source`. JSON whitespace is not retained. Normalized steps
are a presentation of these records, not a replacement for the archival source.

Codex subagents are linked using native parent IDs and spawn results. Prime lacks
thread IDs: exact Agent/Task prompt matches identify subagents, while other context
roots (including compaction) remain in the seat trajectory. All original nodes and
calls are retained even when a child cannot be identified. Copied assistant context
is marked explicitly; usage mirrors are not double-counted. Missing Codex harvests,
conflicting snapshots, and malformed JSON fail visibly instead of producing empty
conversations.

The wrapper sets `OPENTTD_RUN_DIR`, so new Codex harvests go under the run's
`raw/codex/` before the sandbox disappears. For existing runs the converter follows
`harness_session.harvested_to`, also checking relocated `raw/codex/<harvest-name>`
and `outputs/archived/` paths. The crash-safe `scripts/harvest_codex.sh` can still
be used independently; supply its finished seat tree with `--codex-dir` if needed.

## Viewer

`viewer/` reads **only ATIF**, discovering `outputs/<run>/trajectories/index.json`.
It shows per-seat scores, independently selectable subagents, reasoning, tools,
usage and harness badges. `outputs/archived/` and symlinks are skipped;
`OUTPUTS_DIR` overrides the root. Standalone ATIF JSON files can also be dropped
onto the page. See `viewer/README.md` for tests and presentation details.

```sh
cd viewer && npm install
OUTPUTS_DIR=$PWD/../outputs npm run dev -- -p 3100 -H 127.0.0.1   # dev server, loopback only
```

Bind the viewer to `127.0.0.1` when fronting it with `tailscale serve`: tailscaled itself
listens on the tailnet address for the served port, and Node's default `::` bind collides.

## Deviations from the plan (all deliberate, each verified)

- **Squirrel closures**: OpenTTD's Squirrel captures no outer variables in nested functions,
  so GameScript mutators pass their values explicitly (`function(ctx, a)`).
- **Order-ticket latency** is 8–14 game ticks rather than the 5-tick bar: AIs run every
  4th frame at the default `competitor_speed`, and raising it would also strengthen
  RailwAI. Wall-clock cost is ~0.1–0.3 s per ticket.
- **GameScript readiness** is a control-plane `ping`, not a push (the push is lost if the
  script starts before the admin connects).
- **Seat concurrency** uses per-seat runtime views instead of a verifiers patch (the
  framework serializes borrowers of one runtime to protect the runtime-wide env).
- **Terminal save parsing** (OpenTTDLab) is not wired: no launch meter needs it; the save is
  captured as an audit artifact under `outputs/saves/`.

