#!/bin/sh
# Historical entry point: never silently fall back to a shared container.
printf "%s\n" "Docker multi-agent runs/harvesting are unsupported. Use Prime isolated VMs and scripts/snapshot_codex_live.py." >&2
exit 2
