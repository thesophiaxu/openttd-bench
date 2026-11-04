"""Read the env-owned, credential-free Prime VM inventory. Never infer IDs from logs."""
import json
from pathlib import Path


def active_agents(root: Path) -> list[dict]:
    agents = []
    seen = set()
    for path in sorted((root / 'live/runtimes').glob('*.json')):
        inventory = json.loads(path.read_text())
        server = inventory['server']
        if server['status'] not in ('booting', 'running'):
            continue
        ids = {server['sandbox_id']} if server.get('sandbox_id') else set()
        for role, agent in inventory['agents'].items():
            sandbox = agent.get('sandbox_id')
            if sandbox:
                if sandbox in ids:
                    raise ValueError('Expected distinct Prime server and agent VMs; shared-box/Docker is unsupported')
                ids.add(sandbox)
            if agent['status'] != 'running' or not sandbox:
                continue
            if agent['home'] != '/root':
                raise ValueError('Live collectors require isolated root VMs')
            if sandbox in seen:
                raise ValueError('Duplicate active VM in runtime inventory')
            seen.add(sandbox)
            agents.append({**agent, 'role': role, 'spec': inventory['spec'],
                           'task_idx': inventory['task_idx']})
    return agents
