import json

import pytest

from scripts.prepare_scheduled_ledger import ensure_codex, main


def test_codex_installer_honors_historical_pin(tmp_path, monkeypatch):
    from scripts import prepare_scheduled_ledger as module
    monkeypatch.setattr(module, 'Path', lambda _: tmp_path)
    binary = tmp_path / 'openttd-codex-0.154.0/node_modules/.bin/codex'
    binary.parent.mkdir(parents=True)
    binary.write_text('fake binary')
    monkeypatch.setattr(module.subprocess, 'check_output', lambda argv, **kw: 'codex-cli 0.154.0\n')
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: pytest.fail('Cached pin must not reinstall'))
    assert ensure_codex('0.154.0', None) == binary
    with pytest.raises(ValueError, match='exact pinned'):
        ensure_codex('latest', None)
    monkeypatch.setattr(module.subprocess, 'check_output', lambda argv, **kw: 'codex-cli 0.162.1\n')
    with pytest.raises(RuntimeError, match='version mismatch'):
        ensure_codex('0.154.0', None)


def test_dependency_blocks_preflight_until_predecessor_finishes(tmp_path, monkeypatch):
    from scripts import prepare_scheduled_ledger as module
    dependency = tmp_path / 'prior'
    dependency.mkdir()
    (dependency / 'manifest.json').write_text('{"status":"running"}')
    root = tmp_path / 'next'
    root.mkdir()
    (root / 'manifest.json').write_text(json.dumps({'status': 'scheduled', 'not_before_unix': 0,
        'launch_after_run': str(dependency), 'codex_version': '0.162.1', 'models': ['test/model']}))
    def finish_dependency(_):
        assert json.loads((root / 'manifest.json').read_text())['status'] == 'waiting_dependency'
        (dependency / 'manifest.json').write_text('{"status":"complete"}')
    def reached_preflight(*_):
        assert json.loads((dependency / 'manifest.json').read_text())['status'] == 'complete'
        raise RuntimeError('test reached preflight after dependency')
    monkeypatch.setattr(module.time, 'sleep', finish_dependency)
    monkeypatch.setattr(module, 'ensure_codex', reached_preflight)
    monkeypatch.setattr(module, 'post', lambda *args: None)
    with pytest.raises(RuntimeError, match='test reached preflight'):
        main(root)


def test_scheduled_run_refuses_early_inference(tmp_path, monkeypatch):
    from scripts import prepare_scheduled_ledger as module
    manifest = {"status": "scheduled", "not_before_unix": 100}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(module.time, "time", lambda: 99)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: pytest.fail("Must not start any subprocess"))
    with pytest.raises(RuntimeError, match="refusing early inference"):
        main(tmp_path)
    assert not (tmp_path / "launch-claimed").exists()
    assert json.loads((tmp_path / "manifest.json").read_text()) == manifest


def test_scheduled_run_refuses_duplicate_launch(tmp_path, monkeypatch):
    from scripts import prepare_scheduled_ledger as module
    (tmp_path / "manifest.json").write_text(json.dumps({"status": "scheduled", "not_before_unix": 0}))
    (tmp_path / "launch-claimed").write_text("already claimed")
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: pytest.fail("Must not start any subprocess"))
    with pytest.raises(FileExistsError):
        main(tmp_path)
