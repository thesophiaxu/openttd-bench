"""Lossless Prime/Codex -> ATIF-v1.8 conversion (stdlib only).

Normalized steps are a view, not the archival record: extra.source retains every
original field/record, including copied context, encrypted reasoning and unknown
events. Prime parent graphs and Codex subagent sessions are never flattened away.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "ATIF-v1.8"


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text(v.get("text", v)) if isinstance(v, dict) else text(v) for v in value)
    if isinstance(value, dict) and value.get("type") == "encrypted_content":
        return "[Encrypted content retained in extra.source]"
    return json.dumps(value, ensure_ascii=False)


def arguments(value: Any) -> dict:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return {"raw": value}
    else:
        decoded = value
    return decoded if isinstance(decoded, dict) else {"raw": decoded}


def timestamp(value: Any) -> str | None:
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    return value


def step(source: str, message: Any = "", **kwargs: Any) -> dict:
    return {"step_id": 0, "source": source, "message": text(message),
            **{k: v for k, v in kwargs.items() if v is not None}}


def finish(doc: dict) -> dict:
    if not doc["steps"]:
        doc["steps"] = [step("system", "No conversation records captured.", extra={"synthetic": True})]
    for i, s in enumerate(doc["steps"], 1):
        s["step_id"] = i
    metrics = [s["metrics"] for s in doc["steps"] if s.get("metrics") and not s.get("is_copied_context")]
    doc["final_metrics"] = {"total_steps": len(doc["steps"])}
    for key in ("prompt_tokens", "completion_tokens", "cached_tokens"):
        values = [m[key] for m in metrics if key in m]
        if values:
            doc["final_metrics"]["total_" + key] = sum(values)
    return doc


def usage_metrics(usage: dict) -> dict:
    result = {"extra": {"usage": usage}}
    for dest, keys in {"prompt_tokens": ("prompt_tokens", "input_tokens"),
                       "completion_tokens": ("completion_tokens", "output_tokens"),
                       "cached_tokens": ("cached_input_tokens",)}.items():
        for key in keys:
            if key in usage:
                result[dest] = usage[key]
                break
    return result


def tool_call(call: dict) -> dict:
    fn = call.get("function") or call
    return {"tool_call_id": call["id"], "function_name": fn.get("name", "tool"),
            "arguments": arguments(fn.get("arguments", {}))}


def add_result(s: dict, content: Any, call_id: str | None = None, **extra: Any) -> dict:
    result = {"content": text(content)}
    if call_id is not None:
        result["source_call_id"] = call_id
    if extra:
        result["extra"] = extra
    s.setdefault("observation", {"results": []})["results"].append(result)
    return result


def prime_trajectory(trace: dict, episode: dict | None = None) -> dict:
    """Partition explicitly identifiable subagents; preserve other context roots in-place.

    Prime has no thread IDs. Only an exact Agent prompt match identifies a child;
    unrelated/restarted/compacted roots are not guessed to be subagents.
    """
    cfg = trace.get("agent", {}).get("config", {})
    harness = cfg.get("harness", {})
    tid = trace["id"]
    doc = {"schema_version": VERSION, "trajectory_id": tid, "session_id": tid,
           "agent": {"name": harness.get("id", "prime"), "version": harness.get("version", "unknown"),
                     "model_name": cfg.get("model")}, "steps": [],
           "extra": {"benchmark": {k: v for k, v in trace.items() if k not in ("nodes", "calls")},
                     "episode": episode or {"id": tid},
                     "source": {"format": "prime", "trace": trace}}}
    nodes = trace.get("nodes", [])
    roots = {}
    for i, n in enumerate(nodes):
        parent = n.get("parent")
        if parent is not None and parent not in roots:
            raise ValueError(f"{tid}: invalid parent {parent} at node {i}")
        roots[i] = roots[parent] if parent is not None else i
    child_roots: dict[int, tuple[int, str, str]] = {}
    for i, n in enumerate(nodes):
        if not n.get("sampled"):
            continue
        for call in n["message"].get("tool_calls") or []:
            fn = call.get("function") or call
            if fn.get("name") not in ("Agent", "Task"):
                continue
            args = arguments(fn.get("arguments"))
            prompt = args.get("prompt")
            if not isinstance(prompt, str) or not prompt:
                continue
            matches = {roots[j] for j, m in enumerate(nodes) if j > i and roots[j] != roots[i]
                       and m["message"]["role"] == "user" and prompt in text(m["message"].get("content"))}
            if len(matches) == 1:
                child_roots[next(iter(matches))] = (i, call["id"], args.get("description", "subagent"))
    docs = {r: {"schema_version": VERSION, "trajectory_id": f"{tid}-branch-{r}", "session_id": tid,
                "agent": {**doc["agent"], "name": name}, "steps": [],
                "extra": {"parent_trajectory_id": tid, "prime_root_node": r}}
            for r, (_, _, name) in child_roots.items()}
    calls = defaultdict(list)
    failed_calls = []
    for index, c in enumerate(trace.get("calls", [])):
        if c.get("node") is None:
            # A transport failure may produce no assistant node. Keep the exact
            # record and surface it without inventing a node/session association.
            if not c.get("error"):
                raise ValueError(f"{tid}: call {index} has neither a node nor an error")
            failed_calls.append((index, c))
        else:
            calls[c["node"]].append(c)
    by_node: dict[int, dict] = {}
    call_steps: dict[tuple[int, str], dict] = {}
    for i, n in enumerate(nodes):
        m = n["message"]
        role = m["role"]
        owner = docs.get(roots[i], doc)
        common = {"timestamp": timestamp(n.get("timestamp")),
                  "extra": {"prime_node": i, "prime_parent": n.get("parent"), "prime_root": roots[i]}}
        if role == "tool":
            cid = m.get("tool_call_id")
            # Resolve within this context graph, never across independent subagents.
            s = call_steps.get((roots[i], cid))
            if s is None:
                s = step("system", **common)
                owner["steps"].append(s)
                add_result(s, m.get("content"), original_call_id=cid, prime_node=i)
            else:
                add_result(s, m.get("content"), cid, prime_node=i, timestamp=n.get("timestamp"))
            by_node[i] = s
            continue
        s = step("agent" if role == "assistant" else "user" if role == "user" else "system",
                 m.get("content"), **common)
        if role == "assistant":
            s["is_copied_context"] = not n.get("sampled", False)
            if m.get("reasoning_content"):
                s["reasoning_content"] = text(m["reasoning_content"])
            if m.get("tool_calls"):
                s["tool_calls"] = [tool_call(c) for c in m["tool_calls"]]
                for c in s["tool_calls"]:
                    call_steps[(roots[i], c["tool_call_id"])] = s
            if calls[i]:
                cs = calls[i]
                s["llm_call_count"] = len(cs)
                s["model_name"] = cs[-1].get("model", cfg.get("model"))
                ms = [usage_metrics(c.get("usage", {})) for c in cs]
                s["metrics"] = {k: sum(m.get(k, 0) for m in ms) for k in
                                ("prompt_tokens", "completion_tokens", "cached_tokens") if any(k in m for m in ms)}
                s["metrics"]["extra"] = {"calls": cs}
        owner["steps"].append(s)
        by_node[i] = s
    # Proxy graphs often attach a result to the harness's re-sent assistant,
    # rather than to its sampled original (sometimes across a new context root).
    # Surface those observations on the sampled call too, without crossing agents.
    for owner in [doc, *docs.values()]:
        observations = defaultdict(list)
        for s in owner["steps"]:
            for result in s.get("observation", {}).get("results", []):
                cid = result.get("source_call_id")
                if cid:
                    observations[cid].append(result)
        for s in owner["steps"]:
            if s["source"] != "agent" or s.get("is_copied_context"):
                continue
            for c in s.get("tool_calls", []):
                existing = s.get("observation", {}).get("results", [])
                if not any(r.get("source_call_id") == c["tool_call_id"] for r in existing):
                    seen_content = set()
                    for result in observations[c["tool_call_id"]]:
                        if result["content"] not in seen_content:
                            s.setdefault("observation", {"results": []})["results"].append(dict(result))
                            seen_content.add(result["content"])
    for r, (parent_node, cid, _) in reversed(list(child_roots.items())):
        parent_doc = docs.get(roots[parent_node], doc)
        child = finish(docs[r])
        child["extra"]["parent_trajectory_id"] = parent_doc["trajectory_id"]
        parent_doc.setdefault("subagent_trajectories", []).append(child)
        s = by_node[parent_node]
        results = s.get("observation", {}).get("results", [])
        result = next((v for v in results if v.get("source_call_id") == cid), None)
        if result is None:
            result = add_result(s, "", cid)
        result.setdefault("subagent_trajectory_ref", []).append({"trajectory_id": child["trajectory_id"]})
    for index, c in failed_calls:
        doc["steps"].append(step(
            "system", f"Model request failed before an assistant node was recorded: {text(c['error'])}",
            timestamp=timestamp(c.get("time", {}).get("start")),
            llm_call_count=1, model_name=c.get("model"),
            metrics=usage_metrics(c["usage"]) if c.get("usage") else None,
            extra={"prime_call_index": index, "unattached_failed_call": c},
        ))
    return finish(doc)


def codex_trajectory(records: list[dict]) -> dict:
    meta = next(r["payload"] for r in records if r.get("type") == "session_meta")
    tid = meta["id"]
    origin = meta.get("source", {})
    spawn = origin.get("subagent", {}).get("thread_spawn", {}) if isinstance(origin, dict) and isinstance(origin.get("subagent"), dict) else {}
    parent = meta.get("parent_thread_id") or spawn.get("parent_thread_id")
    doc = {"schema_version": VERSION, "trajectory_id": tid, "session_id": meta.get("session_id", tid),
           "agent": {"name": meta.get("agent_nickname") or "codex-cli", "version": meta.get("cli_version", "unknown")},
           "steps": [], "extra": {"parent_trajectory_id": parent,
                                     "source": {"format": "codex", "records": records}}}
    steps = doc["steps"]
    if meta.get("base_instructions"):
        steps.append(step("system", meta["base_instructions"].get("text", "")))
    call_steps = {}
    last_agent = None
    model = None
    usage_records = any(r.get("type") == "token_usage_record" for r in records)
    seen_usage = set()
    for i, r in enumerate(records):
        p = r.get("payload", {})
        kind = p.get("type")
        common = {"timestamp": r.get("timestamp"), "extra": {"codex_record": i}}
        copied = r.get("ordinal", i) < meta.get("subagent_history_start_ordinal", -1)
        if r["type"] == "turn_context":
            model = p.get("model", model)
            if model:
                doc["agent"]["model_name"] = model
        elif r["type"] == "response_item":
            if kind in ("function_call_output", "custom_tool_call_output"):
                cid = p.get("call_id")
                s = call_steps.get(cid)
                if s is None:
                    s = step("system", **common)
                    steps.append(s)
                    add_result(s, p.get("output"), original_call_id=cid, codex_record=i)
                else:
                    add_result(s, p.get("output"), cid, codex_record=i, timestamp=r.get("timestamp"))
                continue
            if kind in ("message", "agent_message"):
                role = p.get("role", "user")
                s = step("agent" if role == "assistant" else "user" if role == "user" else "system", p.get("content"), **common)
            elif kind == "reasoning":
                s = step("agent", reasoning_content=text(p.get("summary") or p.get("content")), **common)
                if p.get("encrypted_content"):
                    s["extra"]["encrypted_reasoning"] = True
                    if not s.get("reasoning_content"):
                        s["message"] = "[Encrypted reasoning retained in extra.source]"
            elif kind in ("function_call", "custom_tool_call"):
                s = step("agent", tool_calls=[{"tool_call_id": p["call_id"], "function_name": p["name"],
                         "arguments": arguments(p.get("arguments", p.get("input", {})))}], **common)
                call_steps[p["call_id"]] = s
            else:
                s = step("system", text(p), **common)
            if copied:
                s["is_copied_context"] = True
            if s["source"] == "agent":
                last_agent = s
                if model:
                    s["model_name"] = model
            steps.append(s)
        elif r["type"] == "compacted":
            steps.append(step("system", p.get("message", "Context compacted"), **common))
        elif r["type"] == "token_usage_record" or (not usage_records and r["type"] == "event_msg" and kind == "token_count"):
            u = p.get("usage") if usage_records else (p.get("info") or {}).get("last_token_usage")
            identity = p.get("response_id") if usage_records else json.dumps((p.get("info") or {}).get("total_token_usage"), sort_keys=True)
            if u and last_agent is not None and (identity is None or identity not in seen_usage):
                seen_usage.add(identity)
                m = usage_metrics(u)
                if "metrics" in last_agent:
                    old = last_agent["metrics"]
                    for k in ("prompt_tokens", "completion_tokens", "cached_tokens"):
                        if k in old:
                            m[k] = m.get(k, 0) + old[k]
                last_agent["metrics"] = m
    return finish(doc)


def codex_forest(directory: Path) -> list[dict]:
    docs = {}
    for file in sorted(directory.rglob("*.jsonl")):
        if file.name == "codex-exec.jsonl":
            continue
        raw = file.read_text()
        records = [json.loads(line) for line in raw.splitlines() if line.strip()]
        if not records or not any(r.get("type") == "session_meta" for r in records):
            continue
        doc = codex_trajectory(records)
        doc["extra"]["source"].update(path=str(file), sha256=hashlib.sha256(file.read_bytes()).hexdigest())
        tid = doc["trajectory_id"]
        if tid in docs:
            # Crash-safe snapshots can duplicate the final harvest. Only accept prefixes.
            old = docs[tid]["extra"]["source"]["records"]
            if old == records[:len(old)]:
                docs[tid] = doc
            elif records != old[:len(records)]:
                raise ValueError(f"Conflicting Codex snapshots for {tid}")
        else:
            docs[tid] = doc
    roots = []
    for tid, doc in docs.items():
        parent = doc["extra"]["parent_trajectory_id"]
        if parent in docs:
            # Reject cycles, which would otherwise vanish from the output forest.
            seen = {tid}
            cursor = parent
            while cursor in docs:
                if cursor in seen:
                    raise ValueError(f"Codex parent cycle: {tid}")
                seen.add(cursor)
                cursor = docs[cursor]["extra"]["parent_trajectory_id"]
            docs[parent].setdefault("subagent_trajectories", []).append(doc)
            meta = next(r["payload"] for r in doc["extra"]["source"]["records"] if r["type"] == "session_meta")
            agent_path = meta.get("agent_path")
            for s in docs[parent]["steps"]:
                if not any(c["function_name"] == "spawn_agent" for c in s.get("tool_calls", [])):
                    continue
                for result in s.get("observation", {}).get("results", []):
                    output = arguments(result.get("content"))
                    if output.get("agent_id") == tid or (agent_path and output.get("task_name") == agent_path):
                        result.setdefault("subagent_trajectory_ref", []).append({"trajectory_id": tid})
        else:
            roots.append(doc)
    artifacts = [{"path": str(p), "content": p.read_text()} for p in sorted(directory.rglob("codex-exec.jsonl"))]
    if roots and artifacts:
        roots[0]["extra"]["source"]["artifacts"] = artifacts
    return roots


def resolve_harvest(path: str, run: Path) -> Path:
    p = Path(path)
    if p.exists():
        return p
    relocated = run / "raw" / "codex" / p.name
    if relocated.is_dir():
        return relocated
    # Old runs can be moved as a unit under outputs/archived.
    parts = p.parts
    if "outputs" in parts:
        p = run.parent / "archived" / Path(*parts[parts.index("outputs") + 1:])
    return p


def convert_run(run: Path, codex_dirs: list[Path] | None = None) -> dict:
    run = run.resolve()
    documents = []
    consumed_dirs: set[Path] = set()
    sources = sorted(p for p in run.rglob("traces.jsonl") if "trajectories" not in p.relative_to(run).parts)
    for file in sources:
        for line_number, line in enumerate(file.read_text().splitlines(), 1):
            if not line.strip():
                continue
            record = json.loads(line)
            episode = {k: v for k, v in record.items() if k != "traces"} if "traces" in record else {"id": record["id"]}
            for trace in record.get("traces", [record]):
                doc = prime_trajectory(trace, episode)
                doc["extra"]["source"].update(path=str(file.relative_to(run)), line=line_number)
                session = trace.get("info", {}).get("harness_session", {})
                harvest = session.get("harvested_to")
                is_codex = trace.get("agent", {}).get("config", {}).get("harness", {}).get("id") in ("codex-cli", "codex_cli")
                if harvest or is_codex:
                    directory = resolve_harvest(harvest, run) if harvest else run / "raw" / "codex" / trace.get("info", {}).get("seat_user", "")
                    native = codex_forest(directory) if directory.is_dir() else []
                    if not native:
                        seat = trace.get("info", {}).get("seat_user")
                        candidates = []
                        for base in [run / "raw" / "codex", *(codex_dirs or [])]:
                            if not base.is_dir() or not seat:
                                continue
                            if base.name == seat or base.name.startswith(seat + "-"):
                                candidates.append(base)
                            candidates.extend(p for p in base.rglob("*") if p.is_dir() and (p.name == seat or p.name.startswith(seat + "-")))
                        if len(candidates) == 1:
                            directory = candidates[0]
                            native = codex_forest(directory)
                        if not native:
                            raise ValueError(f"Missing/ambiguous Codex sessions for {trace['id']}: {directory}; supply --codex-dir with seat directories")
                    consumed_dirs.add(directory.resolve())
                    if len(native) == 1:
                        native[0]["extra"].update(benchmark=doc["extra"]["benchmark"], episode=episode,
                                                  prime_source=doc["extra"]["source"])
                        doc = native[0]
                    else:
                        doc["subagent_trajectories"] = native
                        doc["notes"] = "Multiple native root sessions for this seat; retained independently."
                documents.append(doc)
    for directory in codex_dirs or []:
        if not any(directory.resolve() == p or directory.resolve() in p.parents for p in consumed_dirs):
            documents.extend(codex_forest(directory))
    if not documents:
        raise ValueError(f"No trajectories found in {run}")
    dest = run / "trajectories"
    dest.mkdir(parents=True, exist_ok=True)
    entries = []
    for doc in documents:
        # Content-addressed files + manifest-last publication: readers see a complete generation.
        data = json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n"
        digest = hashlib.sha256(data.encode()).hexdigest()
        filename = f"{digest}.atif.json"
        atomic_write(dest / filename, data)
        ep = doc.get("extra", {}).get("episode", {"id": doc["session_id"]})
        entries.append({"file": filename, "trajectory_id": doc["trajectory_id"], "episode_id": ep["id"],
                        "sha256": digest, "size": len(data.encode())})
    manifest = {"format": "openttd-atif-index-v1", "run": run.name, "trajectories": entries}
    atomic_write(dest / "index.json", json.dumps(manifest, indent=2) + "\n")
    return manifest


def atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(data)
    tmp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="run directory; writes RUN/trajectories")
    parser.add_argument("--codex-dir", type=Path, action="append", default=[], help="additional standalone Codex session tree (repeatable)")
    args = parser.parse_args()
    manifest = convert_run(args.run, args.codex_dir)
    print(f"{args.run}/trajectories: {len(manifest['trajectories'])} root trajectories")


if __name__ == "__main__":
    main()
