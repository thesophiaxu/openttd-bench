# openttd-bench v3

A multi-agent-by-default OpenTTD benchmark for LLM coding agents.

The supported upstream snapshot is **PrimeIntellect-ai/verifiers commit
`540f40faab8c376b85c6afa2f872683b0ed1d4d1`**.
New runs are **Prime-only**: one server VM plus one distinct VM per agent role,
managed by the environment's `ServicesSpec`. Each agent runs as root with home
`/root` and runtime workdir `/work`; isolation is by VM, not Unix user.
The private LAN is **all-to-all**, not a firewall boundary. Bearer-token company
authorization and server/admin secrets remain on the server; do not expose
unauthenticated services or secrets on an agent's LAN interface.

`[env.server]` is a `vf.PrimeConfig` owning the server image/resources. Role
`runtime` configs default to `python:3.12-slim-bookworm`, 1 CPU, 2 GB RAM,
5 GB disk, workdir `/work`. The migrated game configs give the server the old
shared-box resources (4 CPU, 8 GB RAM, 20 GB disk), preserving game rules and
model/harness budgets. `env.taskset.image`, `push`, and `runtime.vm` are obsolete.
Docker multi-agent execution and same-box probes explicitly fail; no local
Docker fallback is supported. Prime VM charges apply even with OAuth inference.

Agents use `ottd` and `openttd_client` goal-free primitives. All goal logic lives
in Python outside the game; the injected GameScript never knows the goal.

| Task | Seats | Opponents | Map | Modes | Objective | Horizon |
|---|---|---|---|---|---|---|
| `two_towns_rail` | 1 LLM | none | deterministic flat map, 2 founded towns | rail | `ratio_to_target(PASS delivered, 3000)` | 2 years |
| `versus_ai_rail` | 1 LLM | 3 × RailwAI | random (seeded) | rail | `0.6·ratio(operating profit, $3M) + 0.4·rank(place)` | 10 years |
| `free_for_all` | 4 LLM | none | random (seeded) | rail, road | same, per seat | 10 years |
| `mixed_hogex_realtime_10y` | 1–4 LLM | 0–2 × AAAHogEx | random (seeded) | rail, road | same, per seat | 10 years, real-time clock (75%: ~2 h 42 min; 25%: ~8 h 7 min) |

`mixed_hogex_realtime_10y` is configured by `[env.taskset.params]` (passed to its `spec`):
`llm_seats`, `hogex_seats`, `starting_cash`, `loans`, `allowed_modes`, `game_speed_percent` and
`prompt` (`"v1"`/`"v2"`). Omitted parameters give the plain four-LLM free-for-all; the
headline configs set `llm_seats = 2`, `hogex_seats = 2`, `starting_cash = 800_000`,
`loans = false`, `allowed_modes = ["rail", "road"]`.

## Layout

```
openttd_bench/   task_spec (the schema: world/grading split), objectives, tasks/ (one file per
                 experiment holding its world, grading and literal prompt; tasks_shared.py has the
                 cargo tables and helpers), env (server + distinct role VMs), taskset, grader, payload
                 builder, schema/ (the one command schema)
harnesses/       source root for seat harnesses: openttd_seat/ (root home, workdir,
                 trace metadata), codex_cli/, opencode/ and claude/
image/           everything that runs in the sandbox: init_game.py (sequential boot), patch_speed.py,
                 fetch_content.py, server/ (control plane), client/ (agent library + ottd CLI),
                 game/LLMEval (deity GameScript, namespaced), ai/LLMCompany (order-relay AI), Dockerfile
configs/         Prime-only run configs (stock images bootstrap content)
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

# Local unit tests: no provisioning or inference.
.venv/bin/python -m pytest tests --ignore=tests/test_seats.py

# real-time 10-year free-for-all (~3 h wall, one episode): the launcher sets the idle
# timeout, the per-seat output-token fuse (6M: it counts reasoning tokens) and PYTHONPATH
CONFIG=configs/prime/free-for-all-realtime-10y-promptv2-4opus55.toml scripts/run_ffa_realtime_10y.sh outputs/prime-ffa-realtime-10y-promptv2-4opus55  # Prime

# follow a long run: ntfy start/heartbeat/per-episode/finish notices
.venv/bin/python scripts/watch_run.py outputs/<run> --topic <ntfy topic> --pid <run pid> --heartbeat 30 --progress outputs/progress
```

Inference is billed to the Prime team wallet (`prime wallet`), not OpenRouter: budget ~$75 per
4-seat real-time game on Kimi K3 (~$10/seat-hour) and ~$100/hour on Claude Opus 5. Run 1 of the
real-time task died on an empty wallet (HTTP 402) — check the balance first.

## How an episode runs

1. `OpenTTDEnv.run` owns the deployment and `ServicesSpec`: a server VM from
   `env.server`, and N distinct Prime agent VMs from the role runtime configs.
   Retries get a fresh world; all VMs join the same private LAN.
2. The server receives the payload (`image/`, generated schema/cargo tables,
   `world.json`, `seats.json`). Its root bootstrap acquires pinned content,
   starts OpenTTD and the control plane, configures/funds companies, and marks
   the world ready. Server/admin secrets never go into agent bootstrap payloads.
3. Each role receives only its own company credential and client payload.
   Harnesses run concurrently as root in their own VMs, home `/root`, configured
   runtime workdir (default `/work`). Roles retire independently.
4. The env runs out the clock, grades the server snapshot, and captures the
   terminal save; deployment cleanup closes all owned VMs.

The env writes credential-free runtime inventory to
`$OPENTTD_RUN_DIR/live/runtimes/<server_backend.name>.json` on startup, trace mint,
and retirement. It contains `server: {sandbox_id, status}`,
`agents: {role: {company, sandbox_id, trace_id|null, home: "/root", workdir, status}}`,
`spec`, and `task_idx`. Statuses are `booting`, `running`, `stopped`, `closed`.
Collectors use these explicit role VM IDs, never the last sandbox ID in a log.
Error/activity output retains historical `seats` keys and `agentN-errors.jsonl`
filenames for watchers, adding role/company/trace/VM attribution.
Native backups include each active role, not just company 1.

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
`openttd_seat.SeatHarness` prepares an isolated VM's root home and configured
runtime workdir. There is no `agentN` account or `setpriv` demotion for new runs.
The trace retains `harness_output_tail` and `harness_session`; native Codex
captures are keyed by trace ID, not the common root username.

- `opencode-cli` — pinned `PrimeIntellect-ai/opencode` fork, `opencode run` on the prompt, provider
  JSON pointing at the interception endpoint (OpenAI-compatible dialect).
- `claude` — `@anthropic-ai/claude-code` via npm, `claude -p` on the prompt with
  `ANTHROPIC_BASE_URL` at the interception endpoint (Anthropic Messages dialect, which the
  proxy and Prime inference both serve for every model, Kimi K3 included). Named `claude`, not
  `claude_code`: verifiers' built-in `claude_code` (root, ACP) wins that id lookup.

Switching is config-only: `configs/prime/free-for-all-realtime-10y-promptv2-4opus55.toml` plays
all four seats with Claude Code on Opus 5.5, and `configs/prime/free-for-all-realtime-10y-4models.toml`
gives each seat its own `model` (`[env.pN] model = ...`).

`traces.jsonl` is written when an episode ends. During a run, use the explicit
role VM IDs in the inventory to inspect native stores: root's
`.local/share/opencode/storage`, `.claude/projects`, or `.codex/sessions`.
`scripts/collect_codex_live_errors.py` reads only native error events and tool
activity counts; `scripts/snapshot_codex_live.py` backs up native JSONL stores
without auth/config files. Credential access in runtime launch/probe scripts
happens only when explicitly executed, never as part of offline validation.
Do not use the historical same-box isolation probe for these VMs.

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

The launchers run `vf-run` through `openttd_bench.run_with_atif`. After the runner
exits (including nonzero exits), it converts the completed artifacts into
`outputs/<run>/trajectories/`. The wrapper forwards termination signals, preserves
the runner's failure exit status, and returns nonzero if conversion fails. SIGKILL or
host failure requires a manual backfill. Direct `vf-run` invocations must use the
wrapper for automatic conversion:

```sh
.venv/bin/python -m openttd_bench.run_with_atif outputs/my-run -- \
  .venv/bin/vf-run @ configs/prime/free-for-all-realtime-10y-promptv2-4opus55.toml --output-dir outputs/my-run --run.dir vf

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
`raw/codex/<trace.id>` before the VM disappears; verifiers traces live under `vf/`. For existing runs the converter follows
`harness_session.harvested_to`, also checking relocated `raw/codex/<harvest-name>`
and `outputs/archived/` paths. Without that pointer it prefers trace-ID captures,
then historical `agentN` trees. Legacy Docker harvest trees can still be supplied
with `--codex-dir`, but the Docker harvester itself now explicitly errors.

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
- **Seat concurrency** uses distinct role VMs; VM separation replaces historical
  per-user runtime views and shared-box locks.
- **Terminal save parsing** (OpenTTDLab) is not wired: no launch meter needs it; the save is
  captured as an audit artifact under `outputs/saves/`.

