"""Offline native capture, adversarial archives, and failure-path cleanup."""

import asyncio
import io
import json
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from verifiers.v1 import ProgramResult

from codex_cli.harness import CodexCliHarness, CodexCliHarnessConfig
from codex_cli import harvest as capture
from openttd_seat import seat_paths
from openttd_seat.store import codex_session_summary


class LocalRecorder:
    home = None
    config = SimpleNamespace(workdir="/work")

    def __init__(self):
        self.calls = []
        self.read_limits = []

    async def run(self, argv, env):
        self.calls.append(argv)
        if argv[0] == "python3":
            argv = [sys.executable, *argv[1:]]
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        return ProgramResult(process.returncode, stdout.decode(), stderr.decode())

    async def read(self, path, max_bytes=None):
        self.read_limits.append(max_bytes)
        with open(path, "rb") as source:
            data = source.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("read limit")
        return data


def archive(*members):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        for name, value in members:
            member = tarfile.TarInfo(name)
            if value not in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE):
                member.size = len(value)
                tar.addfile(member, io.BytesIO(value))
            else:
                member.type = value
                member.linkname = "/etc/passwd"
                tar.addfile(member)
    return output.getvalue()


def test_capture_native_only_trims_incomplete_tail_and_keeps_vm_evidence(tmp_path, monkeypatch):
    home = tmp_path / "home"
    session = home / ".codex/sessions/2026/01/01/rollout.jsonl"
    archived = home / ".codex/archived_sessions/old.jsonl"
    original = b'{"type":"session_meta"}\n{"type":"response_item"}\n{"incomplete":'
    for path, data in (
        (session, original), (archived, b'{"archived":true}\n'),
        (home / ".codex/auth.json", b'{"secret":"NEVER_EXPORT"}'),
        (home / ".codex/config.toml", b'api_key = "NEVER_EXPORT"'),
        (home / ".codex/sessions/not-json.txt", b"NEVER_EXPORT"),
        (home / capture.EXEC_LOG, b'{"type":"thread.started"}\n'),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    monkeypatch.setenv("OPENTTD_RUN_DIR", str(tmp_path / "run"))
    harness = CodexCliHarness(CodexCliHarnessConfig(id="codex-cli"))
    runtime = LocalRecorder()
    dest = Path(asyncio.run(harness._harvest(runtime, seat_paths(str(home)), "trace-one")))
    assert dest == tmp_path / "run/raw/codex/trace-one"
    assert (dest / session.relative_to(home)).read_bytes() == original[:original.rfind(b"\n") + 1]
    assert session.read_bytes() == original  # VM originals are never truncated or deleted
    assert (dest / archived.relative_to(home)).read_bytes() == archived.read_bytes()
    assert len([p for p in dest.rglob("*") if p.is_file()]) == 3
    assert all(b"NEVER_EXPORT" not in p.read_bytes() for p in dest.rglob("*") if p.is_file())
    assert runtime.read_limits == [capture.MAX_ARCHIVE_BYTES]
    assert runtime.calls[-1][0:2] == ["rm", "-f"]
    assert not list(dest.parent.glob(".capture-*"))


def test_missing_stores_are_harvestable_and_valid_unterminated_record_is_preserved(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    payload = b'{"type":"thread.started"}'
    (home / capture.EXEC_LOG).write_bytes(payload)
    dest = Path(asyncio.run(capture.harvest(LocalRecorder(), str(home), "empty", tmp_path / "raw")))
    assert (dest / capture.EXEC_LOG).read_bytes() == payload


@pytest.mark.parametrize("name,kind", [
    ("../escape.jsonl", b"{}\n"),
    ("/tmp/escape.jsonl", b"{}\n"),
    (".codex/sessions/../auth.jsonl", b"{}\n"),
    (".codex/auth.json", b"{}\n"),
    (".codex/config.toml", b"{}\n"),
    (".codex/sessions/link.jsonl", tarfile.SYMTYPE),
    (".codex/sessions/hard.jsonl", tarfile.LNKTYPE),
    (".codex/sessions/pipe.jsonl", tarfile.FIFOTYPE),
])
def test_unsafe_archives_never_publish(tmp_path, name, kind):
    data = archive((".codex/sessions/good.jsonl", b"{}\n"), (name, kind))
    with pytest.raises(ValueError):
        capture.publish(data, tmp_path, "trace")
    assert list(tmp_path.iterdir()) == []


def test_duplicate_and_oversize_archives_never_publish(tmp_path, monkeypatch):
    member = ("codex-exec.jsonl", b"{}\n")
    with pytest.raises(ValueError):
        capture.publish(archive(member, member), tmp_path, "duplicate")
    monkeypatch.setattr(capture, "MAX_FILE_BYTES", 2)
    with pytest.raises(ValueError):
        capture.publish(archive(member), tmp_path, "oversize")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("limit,value", [("MAX_FILES", 1), ("MAX_TOTAL_BYTES", 5), ("MAX_ARCHIVE_BYTES", 100)])
def test_archive_limits_are_enforced(tmp_path, monkeypatch, limit, value):
    data = archive(("codex-exec.jsonl", b"{}\n"), (".codex/sessions/one.jsonl", b"{}\n"))
    monkeypatch.setattr(capture, limit, value)
    with pytest.raises(ValueError):
        capture.publish(data, tmp_path, "bounded")
    assert list(tmp_path.iterdir()) == []


def test_trace_identity_and_existing_capture_are_not_overwritten(tmp_path):
    data = archive(("codex-exec.jsonl", b"{}\n"))
    with pytest.raises(ValueError):
        capture.publish(data, tmp_path, "../other")
    dest = Path(capture.publish(data, tmp_path, "trace"))
    with pytest.raises(FileExistsError):
        capture.publish(archive(("codex-exec.jsonl", b"changed")), tmp_path, "trace")
    assert (dest / "codex-exec.jsonl").read_bytes() == b"{}\n"


def test_remote_symlinks_and_size_limit_fail_without_publication(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / capture.EXEC_LOG).symlink_to("/etc/passwd")
    with pytest.raises(RuntimeError, match="native capture failed"):
        asyncio.run(capture.harvest(LocalRecorder(), str(home), "link", tmp_path / "raw"))
    (home / capture.EXEC_LOG).unlink()
    (home / capture.EXEC_LOG).write_bytes(b"{}\n")
    monkeypatch.setattr(capture, "MAX_FILE_BYTES", 2)
    with pytest.raises(RuntimeError, match="native capture failed"):
        asyncio.run(capture.harvest(LocalRecorder(), str(home), "large", tmp_path / "raw"))
    assert not (tmp_path / "raw").exists()


def test_cleanup_harvest_runs_even_when_summary_fails_and_is_idempotent():
    calls = []

    class Harness(CodexCliHarness):
        async def session_summary(self, runtime, paths):
            raise RuntimeError("credential MUST_NOT_LOG")

        async def _harvest(self, runtime, paths, trace_id):
            calls.append(trace_id)
            return "/run/raw/codex/trace"

    trace = SimpleNamespace(id="trace", info={})
    harness = Harness(CodexCliHarnessConfig(id="codex-cli"))
    asyncio.run(harness.cleanup(trace, LocalRecorder()))
    asyncio.run(harness.cleanup(trace, LocalRecorder()))
    assert calls == ["trace"]
    assert trace.info["harness_session"]["harvested_to"] == "/run/raw/codex/trace"
    assert "RuntimeError" in trace.info["harness_session"]["error"]
    assert "MUST_NOT_LOG" not in str(trace.info)


def test_harvest_timeout_keeps_summary(monkeypatch):
    from codex_cli import harness as module

    class Harness(CodexCliHarness):
        async def session_summary(self, runtime, paths):
            return {"store": "codex_cli", "sessions": 2}

        async def _harvest(self, runtime, paths, trace_id):
            await asyncio.Event().wait()

    monkeypatch.setattr(module, "CAPTURE_TIMEOUT", 0.01)
    trace = SimpleNamespace(id="trace", info={})
    asyncio.run(Harness(CodexCliHarnessConfig(id="codex-cli")).cleanup(trace, LocalRecorder()))
    assert trace.info["harness_session"]["sessions"] == 2
    assert "TimeoutError" in trace.info["harness_session"]["harvest_error"]


def test_summary_output_is_bounded_and_error_text_is_not_exported(tmp_path):
    from openttd_seat.store import _run_summary

    for script in ("print('x' * 65537)", "raise RuntimeError('DO_NOT_LOG')"):
        summary = asyncio.run(_run_summary(LocalRecorder(), script, str(tmp_path)))
        assert summary == {"error": "session summary process failed"}
        assert "DO_NOT_LOG" not in str(summary)


def test_real_native_summary_includes_archived_sessions(tmp_path):
    for store in ("sessions", "archived_sessions"):
        path = tmp_path / ".codex" / store / "session.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"type": "response_item", "payload": {"type": "function_call", "name": "shell"}}) + "\n")
    summary = asyncio.run(codex_session_summary(LocalRecorder(), str(tmp_path)))
    assert summary["sessions"] == 2
    assert summary["tools"] == {"shell": 2}
    assert summary["tool_calls"] == 2
