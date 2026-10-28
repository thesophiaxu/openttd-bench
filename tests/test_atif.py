"""Conversion invariants: source recovery, graph ownership, metrics, publication."""
import json
import subprocess
import sys

import pytest

from openttd_bench.atif import codex_forest, codex_trajectory, convert_run, prime_trajectory


def node(role, content="", parent=None, sampled=False, **message):
    return {"parent": parent, "sampled": sampled, "timestamp": 1_700_000_000,
            "message": {"role": role, "content": content, **message}, "unknown": [1, 2]}


def call(cid, name="exec", arguments="{}"):
    return {"id": cid, "name": name, "arguments": arguments}


def prime():
    return {"id": "seat", "agent": {"name": "p1", "config": {"harness": {"id": "claude"}, "model": "m"}},
            "nodes": [
                node("system", "system"),
                node("assistant", parent=0, sampled=True, tool_calls=[call("spawn", "Agent", '{"prompt":"child task"}')]),
                node("system", "child system"),
                node("user", "child task", parent=2),
                node("assistant", parent=3, sampled=True, tool_calls=[call("exec", arguments="not json")]),
                node("tool", "spawned", parent=1, tool_call_id="spawn"),
                node("tool", "child result", parent=4, tool_call_id="exec"),
                node("assistant", parent=5, sampled=True, tool_calls=[call("exec")]),
                node("tool", "parent result", parent=7, tool_call_id="exec"),
                node("assistant", "replayed", parent=8),
                node("system", "compaction root"),
                node("assistant", "continued", parent=10, sampled=True),
            ], "calls": [{"node": 4, "usage": {"prompt_tokens": 12, "completion_tokens": 5, "reasoning_tokens": 3}, "model": "child-model"}],
            "info": {"openttd_company": 0}, "unknown_trace_field": {"important": True}}


def record(kind, payload, **extra):
    return {"timestamp": "2026-01-01T00:00:00Z", "type": kind, "payload": payload, **extra}


def native(tid="root", parent=None):
    return [record("session_meta", {"id": tid, "session_id": "root", "parent_thread_id": parent,
                                     "agent_path": "/root/child" if parent else "/root", "cli_version": "1"}),
            record("turn_context", {"model": "test-model"}),
            record("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "task"}]}),
            record("response_item", {"type": "custom_tool_call", "call_id": "exec", "name": "exec", "input": "a non-JSON program"}),
            record("response_item", {"type": "custom_tool_call_output", "call_id": "exec", "output": "result"}),
            record("response_item", {"type": "reasoning", "encrypted_content": "opaque", "summary": []}),
            record("token_usage_record", {"response_id": "one", "usage": {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 8}}),
            record("event_msg", {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 10, "output_tokens": 2}}}),
            record("unknown_future_event", {"retain": "everything"})]


def dump(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_prime_lossless_subagents_and_context():
    original = prime()
    doc = prime_trajectory(original, {"id": "episode", "future": "field"})
    assert json.loads(json.dumps(doc))["extra"]["source"]["trace"] == original
    assert doc["extra"]["episode"]["future"] == "field"
    child, = doc["subagent_trajectories"]
    assert child["steps"][-1]["observation"]["results"][0]["content"] == "child result"
    assert child["steps"][-1]["tool_calls"][0]["arguments"] == {"raw": "not json"}
    assert child["final_metrics"]["total_prompt_tokens"] == 12
    parent_result = next(s for s in doc["steps"] if s["extra"]["prime_node"] == 7)
    assert parent_result["observation"]["results"][0]["content"] == "parent result"
    spawn = doc["steps"][1]["observation"]["results"][0]
    assert spawn["subagent_trajectory_ref"] == [{"trajectory_id": child["trajectory_id"]}]
    assert any(s.get("is_copied_context") for s in doc["steps"])
    assert doc["steps"][-1]["message"] == "continued"
    all_steps = doc["steps"] + child["steps"]
    assert {s["extra"]["prime_node"] for s in all_steps} == {0, 1, 2, 3, 4, 7, 9, 10, 11}


def test_prime_results_on_replayed_call_remain_visible_on_sampled_call():
    trace = {"id": "replay", "nodes": [
        node("assistant", sampled=True, tool_calls=[call("x")]),
        node("system", "new context"),
        node("assistant", parent=1, tool_calls=[call("x")]),
        node("tool", "result from replay", parent=2, tool_call_id="x"),
    ]}
    doc = prime_trajectory(trace)
    assert doc["steps"][0]["observation"]["results"][0]["content"] == "result from replay"
    assert doc["extra"]["source"]["trace"] == trace


def test_prime_failed_request_without_node_is_preserved():
    failed = {"model": "m", "error": {"type": "ReadError", "message": ""},
              "time": {"start": 1700000001, "end": 1700000002},
              "usage": {"prompt_tokens": 3}}
    trace = {"id": "retry", "nodes": [node("assistant", "recovered", sampled=True)],
             "calls": [failed, {"node": 0, "usage": {"prompt_tokens": 5}}]}
    doc = prime_trajectory(trace)
    assert doc["extra"]["source"]["trace"] == trace
    assert doc["steps"][-1]["extra"]["unattached_failed_call"] == failed
    assert doc["steps"][-1]["extra"]["prime_call_index"] == 0
    assert doc["final_metrics"]["total_prompt_tokens"] == 8
    assert doc["steps"][0]["message"] == "recovered"
    with pytest.raises(ValueError, match="neither a node nor an error"):
        prime_trajectory({"id": "bad", "calls": [{"model": "m"}]})


def test_codex_lossless_usage_and_custom_tools():
    records = native()
    doc = codex_trajectory(records)
    assert doc["extra"]["source"]["records"] == records
    assert doc["final_metrics"]["total_prompt_tokens"] == 10  # event mirror not counted
    assert doc["steps"][1]["tool_calls"][0]["arguments"] == {"raw": "a non-JSON program"}
    assert doc["steps"][1]["observation"]["results"][0]["content"] == "result"
    assert doc["steps"][-1]["extra"]["encrypted_reasoning"]


def test_codex_tree_snapshots_refs_and_artifacts(tmp_path):
    root = native()
    root.extend([record("response_item", {"type": "function_call", "call_id": "spawn", "name": "spawn_agent", "arguments": "{}"}),
                 record("response_item", {"type": "function_call_output", "call_id": "spawn", "output": '{"task_name":"/root/child"}'})])
    dump(tmp_path / "root.jsonl", root)
    dump(tmp_path / "snapshot" / "root.jsonl", root[:3])
    dump(tmp_path / "child.jsonl", native("child", "root"))
    (tmp_path / "codex-exec.jsonl").write_text("non JSON stderr\n")
    doc, = codex_forest(tmp_path)
    child, = doc["subagent_trajectories"]
    assert child["trajectory_id"] == "child"
    assert doc["steps"][-1]["observation"]["results"][0]["subagent_trajectory_ref"] == [{"trajectory_id": "child"}]
    assert doc["extra"]["source"]["artifacts"][0]["content"] == "non JSON stderr\n"
    dump(tmp_path / "snapshot" / "root.jsonl", [root[0], record("different", {})])
    with pytest.raises(ValueError, match="Conflicting"):
        codex_forest(tmp_path)


def test_invalid_graph_and_orphan_tool():
    with pytest.raises(ValueError, match="invalid parent"):
        prime_trajectory({"id": "bad", "nodes": [node("user", parent=99)]})
    doc = prime_trajectory({"id": "orphan", "nodes": [node("tool", "unmatched", tool_call_id="missing")]})
    result = doc["steps"][0]["observation"]["results"][0]
    assert "source_call_id" not in result
    assert result["extra"]["original_call_id"] == "missing"


def test_manifest_idempotence_and_failure_preserves_previous(tmp_path):
    source = tmp_path / "eval" / "traces.jsonl"
    dump(source, [{"id": "e", "traces": [prime()]}])
    first = convert_run(tmp_path)
    assert first == convert_run(tmp_path)
    assert len(list((tmp_path / "trajectories").glob("*.atif.json"))) == 1
    index = (tmp_path / "trajectories" / "index.json").read_bytes()
    source.write_text('{"partial":')
    with pytest.raises(ValueError):
        convert_run(tmp_path)
    assert (tmp_path / "trajectories" / "index.json").read_bytes() == index


def test_wrapper_converts_after_failed_eval(tmp_path):
    command = "import os,json,pathlib,sys; p=pathlib.Path(os.environ['OPENTTD_RUN_DIR']); (p/'traces.jsonl').write_text(json.dumps({'id':'failed','nodes':[]})+'\\n'); sys.exit(7)"
    result = subprocess.run([sys.executable, "-m", "openttd_bench.run_with_atif", str(tmp_path), "--", sys.executable, "-c", command], capture_output=True, text=True)
    assert result.returncode == 7, result.stderr
    assert (tmp_path / "trajectories" / "index.json").exists()


def test_missing_codex_harvest_is_not_silently_empty(tmp_path):
    trace = {"id": "codex", "agent": {"config": {"harness": {"id": "codex-cli"}}}, "nodes": []}
    dump(tmp_path / "traces.jsonl", [trace])
    with pytest.raises(ValueError, match="Missing/ambiguous"):
        convert_run(tmp_path)
