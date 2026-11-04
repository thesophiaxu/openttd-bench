import json
import subprocess
import sys

from scripts.collect_codex_live_errors import REMOTE


def test_native_error_scanner_only_returns_errors_and_activity(tmp_path):
    home = tmp_path / 'agent1'
    home.mkdir()
    private = home / '.codex'
    private.mkdir()
    (private / 'auth.json').write_text('PRIVATE-AUTH-MUST-NOT-BE-READ')
    lines = [
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'PRIVATE-MESSAGE'}},
        {'type': 'item.completed', 'item': {'type': 'command_execution', 'aggregated_output': 'PRIVATE-TOOL'}},
        {'type': 'error', 'message': '429 Too Many Requests, request id: abc'},
    ]
    log = home / 'codex-exec.jsonl'
    complete = ''.join(json.dumps(line) + '\n' for line in lines)
    log.write_text(complete + '{"type":')
    script = REMOTE.replace("'/root/codex-exec.jsonl'", repr(str(log)))
    output = subprocess.check_output([sys.executable, '-c', script, '{"1":0}'], text=True)
    result = json.loads(output)['1']
    assert result['offset'] == len(complete.encode())
    assert result['completed_tools_since_scan'] == 1
    assert len(result['errors']) == 1 and result['errors'][0]['message'].startswith('429')
    assert 'PRIVATE' not in output


def write_inventory(root, *, shared=False):
    directory = root / 'live/runtimes'
    directory.mkdir(parents=True, exist_ok=True)
    inventory = {'server': {'sandbox_id': 'server', 'status': 'running'},
                 'spec': 'game', 'task_idx': 0, 'agents': {
        role: {'company': company, 'sandbox_id': 'server' if shared else vm,
               'trace_id': f'trace-{role}', 'home': '/root', 'workdir': '/work', 'status': status}
        for role, company, vm, status in [('p2', 0, 'vm-first', 'running'),
                                          ('p1', 1, 'vm-second', 'running'),
                                          ('p3', 2, 'vm-stopped', 'stopped')]}}
    (directory / 'server.json').write_text(json.dumps(inventory))
    return inventory


def test_scan_uses_inventory_not_last_log_and_keeps_attribution(tmp_path):
    import asyncio
    import shlex
    from types import SimpleNamespace
    from scripts.collect_codex_live_errors import scan_run
    write_inventory(tmp_path)
    (tmp_path / 'eval.log').write_text('prime: sandbox wrongbox up\n')
    calls = []

    class Client:
        async def execute_command(self, sandbox, command, **kwargs):
            calls.append(sandbox)
            assert '/root/codex-exec.jsonl' in command
            positions = json.loads(shlex.split(command)[-1])
            seat, = positions
            assert positions[seat] == (42 if len(calls) > 2 else 0)
            return SimpleNamespace(exit_code=0, stdout=json.dumps({seat: {
                'offset': 42, 'errors': [{'type': 'error', 'message': 'HTTP 429'}],
                'completed_tools_since_scan': 2, 'native_log_mtime': 1}}))

    state = {}
    asyncio.run(scan_run(Client(), tmp_path, state))
    asyncio.run(scan_run(Client(), tmp_path, state))
    assert calls == ['vm-first', 'vm-second'] * 2
    assert set(state) == {'vm-first', 'vm-second'}
    activity = json.loads((tmp_path / 'live/codex/activity.json').read_text())['seats']
    assert set(activity) == {'1', '2'}
    assert activity['1']['company'] == 0 and activity['1']['role'] == 'p2'
    assert activity['2']['completed_tools_observed'] == 4
    error = json.loads((tmp_path / 'live/codex/agent1-errors.jsonl').read_text().splitlines()[0])
    assert error['sandbox_id'] == 'vm-first' and error['trace_id'] == 'trace-p2'


def test_inventory_absent_never_falls_back_to_logs_and_shared_vm_fails(tmp_path):
    import pytest
    from scripts.runtime_inventory import active_agents
    (tmp_path / 'eval.log').write_text('prime: sandbox wrongbox up\n')
    assert active_agents(tmp_path) == []
    write_inventory(tmp_path, shared=True)
    with pytest.raises(ValueError, match='distinct Prime'):
        active_agents(tmp_path)
