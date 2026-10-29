# openttd-bench

Code for [OpenTTD-Bench](https://openttd-bench.pages.dev). See blogpost for implementation details, caveats, and raw trajectory data.

## Reproduction

```sh
uv venv .venv && VIRTUAL_ENV=$PWD/.venv uv pip install -e ".[dev]"
set -a; . ./.env; set +a                       # PRIME_TEAM_ID for team billing
scripts/with_docker.sh 'docker build -f image/Dockerfile -t openttd-bench:v3 .'

# unit tests (docker-free), then the docker seat-concurrency test
.venv/bin/python -m pytest tests --ignore=tests/test_seats.py
scripts/with_docker.sh '.venv/bin/python -m pytest tests/test_seats.py'

# real-time 10-year game (one episode; ~3 h wall at 0.75x, ~8 h at 0.25x): the launcher sets
# the idle timeout (12 h), the per-seat output-token fuse (6M: it counts reasoning tokens) and
# PYTHONPATH. Runs on Prime Intellect sandboxes. Seats, cash, loans and speed come from the
# config's [env.taskset.params] (see openttd_bench/tasks/mixed_hogex_realtime_10y.py).
CONFIG=<config_file> scripts/run_ffa_realtime_10y.sh outputs/prime-ffa-realtime-10y-promptv2-4opus55

# follow a long run: ntfy start/heartbeat/per-episode/finish notices
.venv/bin/python scripts/watch_run.py outputs/<run> --topic <ntfy topic> --pid <eval pid> --heartbeat 30 --progress outputs/progress
```

By default, inference is billed through Prime Intellect, and only Claude Code, Codex, and OpenCode are supported; ask your coding agent to create adapters.

### Configs used in blogpost

**Headline run (Opus 5.5 vs. GPT-6 Astra vs. Baseline)**:
- `configs/prime/mixed-hogex-noloans-ledger-v1-075x-s101-20261013.toml`
- `configs/prime/mixed-hogex-noloans-ledger-v1-075x-s202-20261013.toml`
- `configs/prime/mixed-hogex-noloans-ledger-v1-075x-s303-20261013.toml`

**Slow clock (same matchup at 0.25x speed, ~8 h of game)**:
- `configs/prime/mixed-hogex-noloans-ledger-v1-025x-s101-20261013.toml`

**GPT models performance over time**:
- `configs/prime/oauth-sol61-vs-hogex-codex01621-ledger-v1-075x-s101-20261015.toml`
- `configs/prime/oauth-astra-vs-hogex-codex01621-ledger-v1-075x-s101-20261015.toml # GPT-6 Astra`
- `configs/prime/oauth-sol56-vs-hogex-codex01621-ledger-v1-075x-s101-20261015.toml`
- `configs/prime/gpt55-vs-hogex-codex01621-ledger-v1-075x-s101-20261018.toml`
- `configs/prime/gpt54-vs-hogex-codex01621-ledger-v1-075x-s101-20261018.toml `


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

## License

CC0-1.0.

OpenTTD, OpenGFX, OpenSFX, RailwAI and AAAHogEx are downloaded at build time under their own licenses (mostly GPL).