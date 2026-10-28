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
    script = REMOTE.replace("'/home/agent'", repr(str(tmp_path / 'agent')))
    output = subprocess.check_output([sys.executable, '-c', script, '{"1":0}'], text=True)
    result = json.loads(output)['1']
    assert result['offset'] == len(complete.encode())
    assert result['completed_tools_since_scan'] == 1
    assert len(result['errors']) == 1 and result['errors'][0]['message'].startswith('429')
    assert 'PRIVATE' not in output
