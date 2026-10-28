#!/usr/bin/env python3
"""Best-effort periodic native-log backups; never reads the Codex auth store.

Read only with respect to the game/agent. Archives only native session logs and
codex-exec.jsonl, not agent home/config files. Failure never restarts a game.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import time

from prime_sandboxes import AsyncSandboxClient

REMOTE = r'''
import base64,hashlib,io,json,tarfile
from pathlib import Path
home=Path('/home/agent1')
buf=io.BytesIO()
with tarfile.open(fileobj=buf,mode='w:gz') as tar:
 for name in ('codex-exec.jsonl','.codex/sessions','.codex/archived_sessions'):
  p=home/name
  if not p.exists() or p.is_symlink():continue
  files=[p] if p.is_file() else sorted(p.rglob('*.jsonl'))
  for f in files:
   if f.is_symlink() or not f.is_file():continue
   data=f.read_bytes()
   # Do not preserve an incomplete record being written concurrently.
   data=data[:data.rfind(b'\n')+1]
   info=tarfile.TarInfo(str(f.relative_to(home)))
   info.size=len(data);info.mode=0o600
   tar.addfile(info,io.BytesIO(data))
data=buf.getvalue()
print(json.dumps({'sha256':hashlib.sha256(data).hexdigest(),'gzip_b64':base64.b64encode(data).decode()}))
'''
TERMINAL = {'complete', 'complete_with_issues', 'failed', 'cancelled', 'cancelled_before_launch'}


async def main(series, interval):
    client = AsyncSandboxClient(api_key=json.loads((Path.home() / '.prime/config.json').read_text())['api_key'])

    async def capture(item):
        root = Path(item['out'])
        try:
            manifest = json.loads((root / 'manifest.json').read_text())
            if manifest['status'] not in ('launching', 'running'):
                return
            log = root / 'eval.log'
            matches = re.findall(r'prime: sandbox ([a-z0-9]+) up', log.read_text()) if log.exists() else []
            if not matches:
                return
            result = await client.execute_command(matches[-1], 'python3 -c ' + shlex.quote(REMOTE), timeout=45)
            if result.exit_code:
                raise RuntimeError(f'snapshot command exit {result.exit_code}')
            payload = json.loads(result.stdout)
            data = base64.b64decode(payload['gzip_b64'], validate=True)
            assert hashlib.sha256(data).hexdigest() == payload['sha256']
            dest = root / 'live/native-backups'
            dest.mkdir(parents=True, exist_ok=True)
            timestamp = time.time_ns()
            target = dest / f'{timestamp}.tgz'
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as file:
                file.write(data)
            record = {'captured_unix': time.time(), 'sandbox_id': matches[-1], 'bytes': len(data),
                      'sha256': payload['sha256'], 'file': str(target)}
            target.with_suffix('.json').write_text(json.dumps(record, indent=2) + '\n')
            print(json.dumps({'event': 'native_backup', 'run': str(root), **record}), flush=True)
        except Exception as exc:
            print(json.dumps({'event': 'native_backup_failed', 'run': str(root), 'error_type': type(exc).__name__}), flush=True)

    while True:
        plan = json.loads(series.read_text())
        if plan['status'] in TERMINAL:
            print('Series ended; native backups stopped.', flush=True)
            return
        await asyncio.gather(*(capture(item) for item in plan['runs'].values()))
        await asyncio.sleep(interval)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('series', type=Path)
    parser.add_argument('--interval', type=float, default=300)
    args = parser.parse_args()
    asyncio.run(main(args.series, args.interval))
