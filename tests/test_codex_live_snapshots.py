import base64
import hashlib
import io
import json
import subprocess
import sys
import tarfile

from scripts.snapshot_codex_live import REMOTE


def test_snapshot_excludes_credentials_symlinks_and_partial_records(tmp_path):
    home = tmp_path / 'agent1'
    sessions = home / '.codex/sessions'
    sessions.mkdir(parents=True)
    auth = home / '.codex/auth.json'
    auth.write_text('PRIVATE CREDENTIAL')
    (home / '.codex/config.toml').write_text('PRIVATE CONFIG')
    (sessions / 'auth-link.jsonl').symlink_to(auth)
    (sessions / 'session.jsonl').write_text('{"type":"event"}\n{"unfinished":')
    (home / 'codex-exec.jsonl').write_text('{"type":"thread.started"}\n')
    script = REMOTE.replace("Path('/root')", f'Path({str(home)!r})')
    result = json.loads(subprocess.check_output([sys.executable, '-c', script], text=True))
    data = base64.b64decode(result['gzip_b64'])
    assert hashlib.sha256(data).hexdigest() == result['sha256']
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        assert set(archive.getnames()) == {'codex-exec.jsonl', '.codex/sessions/session.jsonl'}
        assert archive.extractfile('.codex/sessions/session.jsonl').read() == b'{"type":"event"}\n'


def test_snapshots_capture_each_active_role_from_inventory(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from scripts.snapshot_codex_live import capture_run
    directory = tmp_path / 'live/runtimes'
    directory.mkdir(parents=True)
    (directory / 'server.json').write_text(json.dumps({
        'server': {'sandbox_id': 'server', 'status': 'running'}, 'spec': 'game', 'task_idx': 0,
        'agents': {f'p{i+1}': {'company': i, 'sandbox_id': f'vm-{i}', 'trace_id': f'trace-{i}',
                    'home': '/root', 'workdir': '/work', 'status': 'running' if i < 2 else 'closed'}
                   for i in range(3)}}))
    (tmp_path / 'eval.log').write_text('prime: sandbox wrongbox up\n')
    data = b'native archive fixture'
    calls = []

    class Client:
        async def execute_command(self, sandbox, command, **kwargs):
            calls.append(sandbox)
            assert "/root" in command and "/home/agent" not in command
            return SimpleNamespace(exit_code=0, stdout=json.dumps({
                'gzip_b64': base64.b64encode(data).decode(),
                'sha256': hashlib.sha256(data).hexdigest()}))

    asyncio.run(capture_run(Client(), tmp_path))
    assert calls == ['vm-0', 'vm-1']
    metadata = [json.loads(p.read_text()) for p in (tmp_path / 'live/native-backups').glob('*.json')]
    assert {m['role'] for m in metadata} == {'p1', 'p2'}
    assert {m['company'] for m in metadata} == {0, 1}
    assert {m['trace_id'] for m in metadata} == {'trace-0', 'trace-1'}
    for archive in (tmp_path / 'live/native-backups').glob('*.tgz'):
        assert archive.read_bytes() == data and archive.stat().st_mode & 0o777 == 0o600
