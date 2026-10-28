# OpenTTD bench trajectory viewer

An ATIF-only Next.js app using AI SDK messages and AI Elements. Native Codex and
Prime traces must first be converted; see the repo README's **ATIF trajectories**.

```sh
cd viewer
npm install
npm run dev
OUTPUTS_DIR=/path/to/outputs npm run dev # default: ../outputs
npm test
npm run build && npm start
```

- Discovers `outputs/<run>/trajectories/index.json`; ignores `archived/`, raw traces,
  and symlinks. Only the manifest's ATIF documents are read.
- Drop a standalone `.atif.json` (or a JSON array of ATIF trajectories) to view it
  locally. Native `traces.jsonl` is explicitly rejected.
- Episodes contain seats and independently selectable embedded subagents. The
  header shows benchmark outcomes, model/harness/runtime, game state and errors
  when present. Subagents do not inherit their parent's reward or success status.
- ATIF steps render text, reasoning, tool arguments, all observations and usage.
  Copied context is hidden by default and can be shown. Large trajectories load
  100 rendered messages at a time; no messages are discarded.
- The read API strips only archival `extra.source`/`extra.prime_source` copies from
  its presentation response. Full records, including encrypted payloads and native
  events, remain in the on-disk ATIF files. Nothing is converted in the viewer.

Bind to loopback when using `tailscale serve`.
