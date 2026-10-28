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
    script = REMOTE.replace("Path('/home/agent1')", f'Path({str(home)!r})')
    result = json.loads(subprocess.check_output([sys.executable, '-c', script], text=True))
    data = base64.b64decode(result['gzip_b64'])
    assert hashlib.sha256(data).hexdigest() == result['sha256']
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        assert set(archive.getnames()) == {'codex-exec.jsonl', '.codex/sessions/session.jsonl'}
        assert archive.extractfile('.codex/sessions/session.jsonl').read() == b'{"type":"event"}\n'
