#!/bin/bash
# One manifest-configured game; usable by the staged model-series controller.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=${1:?Pass the prepared run directory}
set -a
source "${ENV_FILE:-./.env}"
set +a
export PYTHONPATH="$PWD:$PWD/harnesses"
export PRIME_DISABLE_VERSION_CHECK=1
mapfile -t meta < <(.venv/bin/python - "$OUT" <<'PY'
import json,sys
from pathlib import Path
m=json.loads((Path(sys.argv[1])/'manifest.json').read_text())
print(m['config']); print(m['label']); print(m['llm_seats']); print(m['company_count']); print(m['codex_version']); print(m['seed'])
for label in m['seat_labels']: print(label)
PY
)
CONFIG=${meta[0]}
LABEL=${meta[1]}
LLM_SEATS=${meta[2]}
COMPANIES=${meta[3]}
CODEX_VERSION=${meta[4]}
SEED=${meta[5]}
labels=("${meta[@]:6}")
.venv/bin/python scripts/prepare_scheduled_ledger.py "$OUT"
(
  status=0
  .venv/bin/python -m openttd_bench.run_with_atif "$OUT" -- \
    .venv/bin/eval @ "$CONFIG" -n 1 -r 1 -c 1 --no-serve --no-rich --output-dir "$OUT" || status=$?
  .venv/bin/python scripts/finalize_ledger_episode.py "$OUT" "$status"
) >"$OUT/eval.log" 2>&1 &
eval_pid=$!
echo "$eval_pid" >"$OUT/eval.pid"
.venv/bin/python scripts/watch_run.py "$OUT" --topic small-pineapple-good-morning --pid "$eval_pid" \
  --label "$LABEL" --episodes 1 --heartbeat 15 --progress "$OUT/progress" \
  --seat-labels "${labels[@]}" \
  --start-note "$LABEL launching: ${labels[*]}. Seed $SEED, 0.75x, 800k each, no loans, corrected ledger; LLM seats use Codex $CODEX_VERSION high reasoning. About 2h43 game time. Progress every 15 minutes." \
  >"$OUT/watch.log" 2>&1 &
watch_pid=$!
echo "$watch_pid" >"$OUT/watch.pid"
(
  .venv/bin/python scripts/verify_mixed_start.py "$OUT" --pid "$eval_pid" --task-id "${TASK_ID:-codex-model-series}" --llm-seats "$LLM_SEATS" --companies "$COMPANIES" &&
  .venv/bin/python - "$LABEL" <<'PY'
import sys
from scripts.watch_run import post
post('small-pineapple-good-morning', 'OpenTTD: startup verified', sys.argv[1] + ': clock moving, all LLM seats active; initial funding, roster and ledger checks passed.')
PY
) >"$OUT/verification.json" 2>&1 &
verify_pid=$!
trap 'kill -TERM "$eval_pid" "$watch_pid" "$verify_pid" 2>/dev/null || true' TERM INT
status=0
wait "$eval_pid" || status=$?
for job in $(jobs -pr); do
  if [ "$job" = "$verify_pid" ]; then kill -TERM "$verify_pid" 2>/dev/null || true; fi
done
wait "$verify_pid" || true
wait "$watch_pid" || status=1
printf 'Episode ended: %s (exit %s)\n' "$OUT" "$status"
exit "$status"
