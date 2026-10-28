from scripts.watch_429 import RATE_LIMIT, scan_file


def test_only_rate_limit_context_matches():
    for text in ['upstream 429: busy', 'last status: 429 Too Many Requests', 'HTTP 429',
                 '"status_code": 429', '"type":"rate_limit_exceeded"']:
        assert RATE_LIMIT.search(text)
    for text in ['profit $429', '429 vehicles', 'upstream 500', 'year 1950']:
        assert not RATE_LIMIT.search(text)


def test_incremental_dedup_and_partial_records(tmp_path):
    file = tmp_path / 'eval.log'
    state = {'offsets': {}, 'seen': []}
    line = 'WARNING upstream 429: {"request_id":"request-A"}\n'
    file.write_text(line + 'WARNING upstream 4')
    first = scan_file(file, state, 'Astra')
    assert len(first) == 1 and first[0]['request_ids'] == ['request-A']
    assert scan_file(file, state, 'Astra') == []
    with file.open('a') as stream:
        stream.write('29: {"request_id":"request-B"}\n' + line)
    second = scan_file(file, state, 'Astra')
    assert len(second) == 1 and second[0]['request_ids'] == ['request-B']
    assert scan_file(file, state, 'Astra') == []


def test_same_request_in_other_file_is_deduplicated(tmp_path):
    state = {'offsets': {}, 'seen': []}
    a, b = tmp_path / 'eval.log', tmp_path / 'native.log'
    a.write_text('upstream 429: {"request_id":"r-123"}\n')
    b.write_text('exceeded retry limit, last status: 429 Too Many Requests, request id: r-123\n')
    assert len(scan_file(a, state, 'Sol')) == 1
    assert scan_file(b, state, 'Sol') == []
