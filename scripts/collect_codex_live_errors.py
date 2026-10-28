#!/usr/bin/env python3
"""Mirror native Codex error events from active Prime VMs for OAuth 429 alerts.

Sandbox control-plane calls only: no Prime inference. Never reads auth/config
files or assistant content. Watches future series stages automatically.
"""
import argparse
import asyncio
import json
import re
import shlex
import time
from pathlib import Path

from prime_sandboxes import AsyncSandboxClient

REMOTE = r'''
import json,sys
from pathlib import Path
positions=json.loads(sys.argv[1]); result={}
for seat,offset in positions.items():
 p=Path('/home/agent'+seat+'/codex-exec.jsonl')
 if not p.exists(): continue
 if p.stat().st_size < offset: offset=0
 errors=[]; completed_tools=0
 with p.open('rb') as f:
  f.seek(offset)
  while True:
   start=f.tell(); line=f.readline()
   if not line or not line.endswith(b'\n'):break
   offset=f.tell()
   try:e=json.loads(line)
   except (ValueError,UnicodeDecodeError):continue
   if e.get('type') in ('error','turn.failed'):
    errors.append({'type':e['type'],'message':e.get('message') or (e.get('error') or {}).get('message'),'native_log_offset':start})
   if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='command_execution':completed_tools+=1
 result[seat]={'offset':offset,'errors':errors,'completed_tools_since_scan':completed_tools,'native_log_mtime':p.stat().st_mtime}
print(json.dumps(result))
'''


async def main(series: Path):
    state_path = series.with_name(series.stem + '-native-error-cursors.json')
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    config = json.loads((Path.home() / '.prime/config.json').read_text())
    client = AsyncSandboxClient(api_key=config['api_key'])
    while True:
        plan = json.loads(series.read_text())
        for item in plan['runs'].values():
            root = Path(item['out'])
            try:
                manifest = json.loads((root / 'manifest.json').read_text())
            except json.JSONDecodeError:
                continue  # A startup verifier may be replacing this snapshot.
            if manifest['status'] not in ('launching', 'running') or not (root / 'eval.log').exists():
                continue
            matches = re.findall(r'prime: sandbox ([a-z0-9]+) up', (root / 'eval.log').read_text())
            if not matches:
                continue
            sandbox = matches[-1]
            positions = state.setdefault(sandbox, {str(i): 0 for i in range(1, manifest['llm_seats'] + 1)})
            try:
                result = await client.execute_command(sandbox, 'python3 -c ' + shlex.quote(REMOTE) + ' ' + shlex.quote(json.dumps(positions)), timeout=20)
                if result.exit_code != 0:
                    raise RuntimeError('Native error scan command failed')
                data = json.loads(result.stdout.strip())
                destination = root / 'live/codex'
                destination.mkdir(parents=True, exist_ok=True)
                activity_path = destination / 'activity.json'
                previous = json.loads(activity_path.read_text()).get('seats', {}) if activity_path.exists() else {}
                for seat, record in data.items():
                    record['completed_tools_observed'] = previous.get(seat, {}).get('completed_tools_observed', 0) + record['completed_tools_since_scan']
                    with (destination / f'agent{seat}-errors.jsonl').open('a') as file:
                        for event in record['errors']:
                            file.write(json.dumps({**event, 'observed_unix': time.time()}) + '\n')
                    positions[seat] = record['offset']
                temporary_activity = destination / 'activity.tmp'
                temporary_activity.write_text(json.dumps({'checked_unix': time.time(), 'seats': data}, indent=2) + '\n')
                temporary_activity.replace(activity_path)
            except Exception as exc:
                print(json.dumps({'event': 'native_scan_error', 'run': str(root), 'error_type': type(exc).__name__}), flush=True)
        temporary = state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, indent=2) + '\n')
        temporary.replace(state_path)
        if plan['status'] in ('complete', 'complete_with_issues', 'cancelled', 'failed'):
            print('Series ended; native error collector stopped.', flush=True)
            return
        await asyncio.sleep(10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('series', type=Path)
    asyncio.run(main(parser.parse_args().series))
