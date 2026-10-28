#!/bin/sh
# mixed_hogex_realtime_10y on Prime: ONE episode per seed, the clock running in real time at the
# config's speed (75%: ~2 h 42 min of game once the first seat acts; 25%: ~8 h 7 min). The
# per-seat agent budget derives from the horizon (see taskset.agent_budget_seconds).
# --env.p1.runtime.idle-timeout keeps the shared box alive for the whole episode (p1 provisions it;
# 12 h covers the 0.25x game plus boot and run-out);
# --env.pN.max-output-tokens is a cost fuse; it counts reasoning tokens (~4-5x stored output), so 6M for a ~3 h game.
# Usage: [VENV=path/to/.venv] [ENV_FILE=path/to/.env] [CONFIG=configs/prime/<file>.toml] scripts/run_ffa_realtime_10y.sh [outdir]
# Runs the code of the checkout that holds this script (PYTHONPATH incl. harnesses/), e.g. from a worktree.
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-outputs/prime-ffa-realtime-10y}
VENV=${VENV:-.venv}
ENV_FILE=${ENV_FILE:-./.env}
CONFIG=${CONFIG:-configs/prime/free-for-all-realtime-10y.toml}
set -a; . "$ENV_FILE"; set +a
export PYTHONPATH="$PWD:$PWD/harnesses"
exec "$VENV/bin/python" -m openttd_bench.run_with_atif "$OUT" -- "$VENV/bin/eval" @ "$CONFIG" -n 1 -r 1 -c 1 --no-serve --no-rich --output-dir "$OUT" \
  --env.p1.runtime.idle-timeout 43200 \
  --env.p1.max-output-tokens 6000000 --env.p2.max-output-tokens 6000000 \
  --env.p3.max-output-tokens 6000000 --env.p4.max-output-tokens 6000000
