import hashlib
import json
import tomllib

import pytest
from verifiers.v1 import ModelContext
from verifiers.v1.clients import EvalClientConfig

from codex_cli import CodexCliHarness, CodexCliHarnessConfig
from codex_prime import CodexPrimeHarness, CodexPrimeHarnessConfig
from openttd_bench.activity import has_model_activity
from openttd_seat import seat_paths
from scripts.finalize_ledger_episode import verify_native_sources


def test_oauth_config_has_no_proxy_or_api_key_and_keeps_exit_status(tmp_path):
    auth = tmp_path / 'auth.json'
    auth.write_text(json.dumps({'auth_mode': 'chatgpt', 'tokens': {'access_token': 'test-oauth-only'}}))
    h = CodexCliHarness(CodexCliHarnessConfig(id='codex-cli', auth_file=str(auth)))
    ctx = ModelContext(model='openai/gpt-6-astra', client=EvalClientConfig())
    paths = seat_paths('agent2')
    files = h.seat_files(ctx, paths, 'http://interception/v1', 'proxy-secret')
    config = tomllib.loads(files[paths.home + '/.codex/config.toml'].decode())
    assert config['model_provider'] == 'openai' and config['model'] == 'gpt-6-astra'
    assert not any(b'interception' in content or b'proxy-secret' in content for content in files.values())
    script = h.seat_script(paths)
    assert 'unset OPENAI_API_KEY OPENAI_BASE_URL CODEX_API_KEY' in script
    assert 'pipefail' in script
    prime = CodexPrimeHarness(CodexPrimeHarnessConfig(id='codex-prime'))
    assert 'unset OPENAI_API_KEY' not in prime.seat_script(paths)
    auth.write_text(json.dumps({'auth_mode': 'apikey', 'OPENAI_API_KEY': 'fake'}))
    with pytest.raises(ValueError, match='ChatGPT OAuth'):
        h.seat_files(ctx, paths, 'http://interception/v1', 'proxy-secret')


def test_native_activity_is_required_when_calls_are_not_intercepted():
    assert has_model_activity([{}], {})
    assert not has_model_activity([], {})
    native = {'store': 'codex_cli', 'sessions': 1, 'output_tokens': 123, 'tool_calls': 2}
    assert has_model_activity([], {'harness_session': native})
    for change in [{'store': 'other'}, {'sessions': 0}, {'output_tokens': 0}, {'tool_calls': 0}]:
        assert not has_model_activity([], {'harness_session': {**native, **change}})


def test_native_export_verified_against_original_file(tmp_path):
    source = tmp_path / 'session.jsonl'
    source.write_text('{"type":"session_meta"}\n')
    doc = {'extra': {'source': {'format': 'codex', 'path': str(source),
        'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'records': [{'type': 'session_meta'}]}}}
    verify_native_sources(doc, tmp_path)
    source.write_text('{"type":"changed"}\n')
    with pytest.raises(AssertionError):
        verify_native_sources(doc, tmp_path)
