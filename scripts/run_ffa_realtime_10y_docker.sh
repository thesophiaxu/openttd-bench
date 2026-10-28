#!/bin/sh
# mixed_hogex_realtime_10y on LOCAL docker (one container per episode, this host's CPU/RAM).
# Same shape as run_ffa_realtime_10y.sh minus the Prime-only idle-timeout flag; runs under
# `sg docker` (with_docker.sh) because this login shell predates the docker group membership.
# Usage: [VENV=path/to/.venv] [ENV_FILE=path/to/.env] [CONFIG=configs/<file>.toml] scripts/run_ffa_realtime_10y_docker.sh [outdir]
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-outputs/docker-ffa-realtime-10y}
VENV=${VENV:-.venv}
ENV_FILE=${ENV_FILE:-./.env}
CONFIG=${CONFIG:-configs/free-for-all-realtime-10y.toml}
set -a; . "$ENV_FILE"; set +a
export PYTHONPATH="$PWD:$PWD/harnesses"
exec sg docker -c "exec '$VENV/bin/python' -m openttd_bench.run_with_atif '$OUT' -- '$VENV/bin/eval' @ '$CONFIG' -n 1 -r 1 -c 1 --no-serve --no-rich --output-dir '$OUT' \
  --env.p1.max-output-tokens 6000000 --env.p2.max-output-tokens 6000000 \
  --env.p3.max-output-tokens 6000000 --env.p4.max-output-tokens 6000000"
