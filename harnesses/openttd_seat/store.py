"""Session-store summaries: what a seat's program did, read from its own on-disk store.

Runs as root inside the box after the program exits (the seat home is 700), and returns a
small JSON-able dict for `trace.info["harness_session"]`: enough for a summarizer or the
viewer to say how many turns, tokens and tool calls a seat used without opening the sandbox.
"""

from __future__ import annotations

import json
from typing import Any

from verifiers.v1 import Runtime

# opencode: ~/.local/share/opencode/storage/message/<session>/<msg>.json carries role, tokens,
# time; part/<session>/<msg>/<part>.json carries text/tool parts.
OPENCODE_SUMMARY = r"""
import glob, json, os, sys
base = sys.argv[1] + "/.local/share/opencode/storage"
out = {"store": "opencode", "messages": 0, "assistant_messages": 0, "output_tokens": 0, "reasoning_tokens": 0,
       "input_tokens": 0, "cache_read_tokens": 0, "tool_calls": 0, "tools": {}, "first_ms": None, "last_ms": None}
for f in glob.glob(base + "/message/*/*.json"):
    try:
        m = json.load(open(f))
    except Exception:
        continue
    out["messages"] += 1
    if m.get("role") == "assistant":
        out["assistant_messages"] += 1
    tk = m.get("tokens") or {}
    out["output_tokens"] += tk.get("output") or 0
    out["reasoning_tokens"] += tk.get("reasoning") or 0
    out["input_tokens"] += tk.get("input") or 0
    out["cache_read_tokens"] += (tk.get("cache") or {}).get("read") or 0
    t = (m.get("time") or {}).get("created")
    if t:
        out["first_ms"] = t if out["first_ms"] is None else min(out["first_ms"], t)
        out["last_ms"] = t if out["last_ms"] is None else max(out["last_ms"], t)
for f in glob.glob(base + "/part/*/*/*.json"):
    try:
        p = json.load(open(f))
    except Exception:
        continue
    if p.get("type") == "tool":
        out["tool_calls"] += 1
        name = p.get("tool") or "?"
        out["tools"][name] = out["tools"].get(name, 0) + 1
print(json.dumps(out))
"""

# Claude Code: ~/.claude/projects/<slug>/<session>.jsonl, one record per event; assistant
# records carry message.usage and content blocks (tool_use for tool calls).
CLAUDE_SUMMARY = r"""
import glob, json, sys
base = sys.argv[1] + "/.claude/projects"
out = {"store": "claude_code", "messages": 0, "assistant_messages": 0, "output_tokens": 0, "reasoning_tokens": 0,
       "input_tokens": 0, "cache_read_tokens": 0, "tool_calls": 0, "tools": {}, "first_ms": None, "last_ms": None}
def ms(ts):
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return None
for f in glob.glob(base + "/*/*.jsonl"):
    for line in open(f, errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("type") not in ("user", "assistant"):
            continue
        out["messages"] += 1
        t = ms(r.get("timestamp") or "")
        if t:
            out["first_ms"] = t if out["first_ms"] is None else min(out["first_ms"], t)
            out["last_ms"] = t if out["last_ms"] is None else max(out["last_ms"], t)
        msg = r.get("message") or {}
        if r["type"] != "assistant":
            continue
        out["assistant_messages"] += 1
        u = msg.get("usage") or {}
        out["output_tokens"] += u.get("output_tokens") or 0
        out["input_tokens"] += u.get("input_tokens") or 0
        out["cache_read_tokens"] += u.get("cache_read_input_tokens") or 0
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                out["tool_calls"] += 1
                name = block.get("name") or "?"
                out["tools"][name] = out["tools"].get(name, 0) + 1
print(json.dumps(out))
"""

# Codex CLI: $CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl, one record per event. `token_count`
# event_msg payloads carry the cumulative usage (last one per file wins); response_item
# payloads carry messages and function calls.
CODEX_SUMMARY = r"""
import glob, json, sys
base = sys.argv[1] + "/.codex/sessions"
out = {"store": "codex_cli", "sessions": 0, "messages": 0, "assistant_messages": 0, "output_tokens": 0,
       "reasoning_tokens": 0, "input_tokens": 0, "cache_read_tokens": 0, "tool_calls": 0, "tools": {},
       "first_ms": None, "last_ms": None}
def ms(ts):
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return None
for f in glob.glob(base + "/**/*.jsonl", recursive=True):
    out["sessions"] += 1
    total = None
    for line in open(f, errors="replace"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        p = r.get("payload") or {}
        t = ms(r.get("timestamp") or "")
        if t:
            out["first_ms"] = t if out["first_ms"] is None else min(out["first_ms"], t)
            out["last_ms"] = t if out["last_ms"] is None else max(out["last_ms"], t)
        if r.get("type") == "event_msg" and p.get("type") == "token_count":
            total = (p.get("info") or {}).get("total_token_usage") or total
        elif r.get("type") == "response_item":
            kind = p.get("type")
            if kind == "message":
                out["messages"] += 1
                if p.get("role") == "assistant":
                    out["assistant_messages"] += 1
            elif kind in ("function_call", "custom_tool_call", "local_shell_call"):
                out["tool_calls"] += 1
                name = p.get("name") or kind
                out["tools"][name] = out["tools"].get(name, 0) + 1
    if total:
        out["input_tokens"] += total.get("input_tokens") or 0
        out["cache_read_tokens"] += total.get("cached_input_tokens") or 0
        out["output_tokens"] += total.get("output_tokens") or 0
        out["reasoning_tokens"] += total.get("reasoning_output_tokens") or 0
print(json.dumps(out))
"""


async def _run_summary(runtime: Runtime, script: str, home: str) -> dict[str, Any]:
    result = await runtime.run(["python3", "-c", script, home], {})
    if result.exit_code != 0:
        return {"error": (result.stderr or result.stdout).strip()[-300:]}
    return json.loads(result.stdout.strip().splitlines()[-1])


async def opencode_session_summary(runtime: Runtime, home: str) -> dict[str, Any]:
    return await _run_summary(runtime, OPENCODE_SUMMARY, home)


async def claude_session_summary(runtime: Runtime, home: str) -> dict[str, Any]:
    return await _run_summary(runtime, CLAUDE_SUMMARY, home)


async def codex_session_summary(runtime: Runtime, home: str) -> dict[str, Any]:
    return await _run_summary(runtime, CODEX_SUMMARY, home)
