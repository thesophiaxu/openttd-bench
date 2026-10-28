#!/bin/sh
# Crash-safe copy of every seat's Codex session store out of the running episode container(s).
# Every INTERVAL seconds, `docker cp` each seat's CODEX_HOME/sessions and codex-exec.jsonl into
# DEST/<container>/<seat>/ (rsync-style overwrite, so the copy is at most INTERVAL stale if the
# box dies). Exits once PID (the eval) is gone, after one last pass. Needs docker access (sg docker).
# Usage: scripts/harvest_codex.sh DEST PID [INTERVAL=30] [IMAGE=openttd-bench:v3]
DEST=$1; PID=$2; INTERVAL=${3:-30}; IMAGE=${4:-openttd-bench:v3}
[ -n "$DEST" ] && [ -n "$PID" ] || { echo "usage: $0 DEST PID [INTERVAL] [IMAGE]" >&2; exit 2; }
mkdir -p "$DEST"
pass() {
  for c in $(docker ps --filter "ancestor=$IMAGE" --format '{{.Names}}' 2>/dev/null); do
    for seat in agent1 agent2 agent3 agent4; do
      d="$DEST/$c/$seat"; mkdir -p "$d"
      docker cp "$c:/home/$seat/.codex/sessions" "$d/" >/dev/null 2>&1 || true
      docker cp "$c:/home/$seat/codex-exec.jsonl" "$d/" >/dev/null 2>&1 || true
    done
  done
  echo "$(date -u +%FT%TZ) pass: $(find "$DEST" -name '*.jsonl' | wc -l) files, $(du -sh "$DEST" | cut -f1)"
}
while kill -0 "$PID" 2>/dev/null; do pass; sleep "$INTERVAL"; done
pass
echo "eval $PID gone; final pass done"
