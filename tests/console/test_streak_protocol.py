"""Observed IM schema, bounded messages, exact identities and current segments."""
import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.protobuf_codec import ProtobufDecodeError, fields
from core.streak_protocol import (
    MAX_RESPONSE_BYTES, StreakCollector, conversation_messages, extract_init_streaks,
    parse_ext_map, parse_flame_segment, parse_streak_ext,
)

NOW = datetime(2026, 11, 11, 4, 0, tzinfo=timezone.utc)
TS = int(NOW.timestamp())


def vi(value):
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128); value >>= 7
    out.append(value)
    return bytes(out)


def pb(number, value):
    if isinstance(value, int):
        return vi(number << 3) + vi(value)
    if isinstance(value, str):
        value = value.encode()
    return vi(number << 3 | 2) + vi(len(value)) + value


def segment(**changes):
    return {'start': TS - 100, 'end': TS + 100, 'days': 374, 'real_days': 374,
            'level': 'normal|normal', 'state': 1, 'text': '374', **changes}


def ext_data(**changes):
    return {'a:consecutive_chat': '374:1794381081:1794499200:1:1',
            'a:consecutive_chat_data': json.dumps({'flame_infos': [segment(**changes)]})}


def core(conversation_id, ext):
    return pb(1, conversation_id) + b''.join(pb(11, pb(1, key) + pb(2, value)) for key, value in ext.items())


def conversation(conversation_id='0:1:111:222', ext=None, *, core_id=None, kind=1, unrelated=b''):
    info = pb(1, conversation_id) + pb(3, kind) + pb(50, core(core_id or conversation_id, ext if ext is not None else ext_data()))
    # field 2 contains messages, including convincing lookalike metadata.
    return pb(1, pb(1, info) + pb(2, unrelated))


def response(*conversations):
    return pb(6, pb(2043, b''.join(conversations)))


def test_real_shaped_envelope_yields_per_conversation_metadata_only():
    raw = response(conversation(), conversation('0:1:111:333', ext_data(days=12, real_days=12, text='12') | {'a:consecutive_chat': '12:1:2:1:1'}))
    found = extract_init_streaks(raw, observed_at=NOW)
    assert [(item['participants'], item['days']) for item in found] == [(('111', '222'), 374), (('111', '333'), 12)]
    assert all(item['source'] == 'douyin_im' and item['state'] == 'active' for item in found)
    assert set(found[0]) == {'participants', 'conversation_id', 'days', 'state', 'recover_current', 'recover_total', 'platform_text', 'source', 'observed_at'}


def test_message_body_cannot_lend_neighbor_identity_or_streak():
    deceptive = conversation('0:1:111:444') + pb(11, pb(1, 'a:consecutive_chat') + pb(2, '999:1:2:1:1'))
    raw = response(conversation('0:1:111:222', {}, unrelated=deceptive), conversation('0:1:111:333'))
    found = extract_init_streaks(raw, observed_at=NOW)
    assert len(found) == 1 and found[0]['participants'] == ('111', '333') and found[0]['days'] == 374


def test_field42_key_metadata_is_never_parsed_as_field11_values():
    info = pb(1, '0:1:111:222') + pb(42, pb(1, 'a:consecutive_chat') + pb(2, '999:1:2:1:1'))
    assert parse_ext_map(info) == {}
    actual = info + pb(11, pb(1, 'a:consecutive_chat') + pb(2, '12:1:2:1:1'))
    assert parse_ext_map(actual) == {'a:consecutive_chat': '12:1:2:1:1'}


@pytest.mark.parametrize('conversation_id,core_id,kind', [
    ('0:1:111:222', '0:1:111:333', 1), ('0:2:111:222', None, 1),
    ('0:1:111:222', None, 2), ('0:1:111:111', None, 1),
    ('0:1:111:222:333', None, 1), ('0:1:hello:222', None, 1),
])
def test_unverified_or_ambiguous_conversation_identity_is_discarded(conversation_id, core_id, kind):
    assert extract_init_streaks(response(conversation(conversation_id, core_id=core_id, kind=kind)), observed_at=NOW) == []


@pytest.mark.parametrize('payload', [b'\x00', b'\x0a\x08abc', b'\x08\x80', b'\x09abc', b'\x0dabc', b'\x0b', b'\x08' + b'\xff'*10, b'\x80'*11])
def test_malformed_wire_fields_fail_closed(payload):
    with pytest.raises(ProtobufDecodeError):
        list(fields(payload))


def test_bounded_envelope_and_duplicate_singular_fields_fail_closed():
    with pytest.raises(ProtobufDecodeError):
        conversation_messages(b'a' * (MAX_RESPONSE_BYTES + 1))
    with pytest.raises(ProtobufDecodeError):
        conversation_messages(response(conversation()) + response(conversation()))
    with pytest.raises(ProtobufDecodeError):
        conversation_messages(pb(6, pb(2043, pb(1, pb(1, b'') + pb(1, b'')))))
    with pytest.raises(ProtobufDecodeError):
        parse_ext_map(pb(11, pb(1, 'a:consecutive_chat')))
    with pytest.raises(ProtobufDecodeError):
        parse_ext_map(core('0:1:111:222', {'a:consecutive_chat': '1:1:2:1:1'}) + pb(11, pb(1, 'a:consecutive_chat') + pb(2, '2:1:2:1:1')))


def test_current_segment_uses_half_open_time_bounds_not_maximum_days_or_future_state():
    data = {'flame_infos': [segment(start=TS-200, end=TS, days=999, real_days=999, text='999'),
                            segment(start=TS, end=TS+100, days=12, real_days=12, text='12')]}
    found = parse_flame_segment(data, observed_at=TS)
    assert found['days'] == 12
    assert parse_flame_segment(data, observed_at=TS+100) is None
    data['flame_infos'].append(segment(start=TS-1, end=TS+1))
    assert parse_flame_segment(data, observed_at=TS) is None


def test_recovery_uses_real_visible_text_not_conflicting_machine_progress():
    data = {'flame_infos': [segment(days=671, real_days=671, level='gray|mate', state=3,
                                    text='重燃中 1/3', recover_days=2, recover_need_days=2)]}
    result = parse_flame_segment(data, observed_at=TS)
    assert result == {'days': 671, 'state': 'recovering', 'recover_current': 1, 'recover_total': 3, 'platform_text': '重燃中 1/3'}


def test_gray_is_unknown_not_broken_and_countdown_is_not_a_day_count():
    gray = parse_flame_segment({'flame_infos': [segment(level='gray|normal', state=2)]}, observed_at=TS)
    assert gray['days'] == 374 and gray['state'] == 'unknown'
    waiting = parse_flame_segment({'flame_infos': [segment(level='to_recover|mate', state=3,
                                  text='6 天后消失', simplify_text='待重燃')]}, observed_at=TS)
    assert waiting['days'] == 374 and waiting['state'] == 'broken'
    assert waiting['recover_current'] is None


def test_conflicting_sources_never_silently_choose_one_and_missing_never_becomes_zero():
    assert parse_streak_ext(ext_data(days=12, real_days=12, text='12'), observed_at=TS) is None
    assert parse_streak_ext({}, observed_at=TS) is None
    assert parse_streak_ext({'a:consecutive_chat_data': '{}'}, observed_at=TS) is None
    assert parse_flame_segment({'flame_infos': [segment(real_days=999)]}, observed_at=TS) is None
    assert parse_flame_segment({'flame_infos': [segment(text='100000')]}, observed_at=TS)['days'] == 374
    # Invalid/unavailable current segment preserves only explicit marker days,
    # without claiming a state or incrementing across dates.
    result = parse_streak_ext({'a:consecutive_chat': '374:1:2:1:1'}, observed_at=TS+86400)
    assert result['days'] == 374 and result['state'] == 'unknown'


def fake_response(url, *, raw=b'', body=None, status=200):
    return SimpleNamespace(url=url, status=status, headers={}, body=AsyncMock(return_value=raw), json=AsyncMock(return_value=body))


def test_collector_requires_trusted_self_and_account_local_peer_mapping():
    async def run():
        collector = StreakCollector()
        init = fake_response('https://imapi.douyin.com/v1/message/get_message_by_init', raw=response(conversation()))
        users = fake_response('https://www.douyin.com/aweme/v1/web/im/user/info/', body={'data': [{'uid': '222', 'sec_uid': 'sec-peer', 'nickname': '敏感昵称不保留'}]})
        collector.capture(init); collector.capture(users)
        result = await collector.finalize('111')
        assert len(result) == 1 and result[0]['sec_uid'] == 'sec-peer' and result[0]['days'] == 374
        assert collector.observations(None) == []
        assert collector.observations('999') == []
        assert StreakCollector().observations('111') == []
        assert all(not isinstance(value, bytes) for value in collector.__dict__.values())
        assert '敏感昵称' not in str(collector.__dict__)
        await collector.handle_response(fake_response(users.url, body={'data': [{'uid': '222', 'sec_uid': 'different'}]}))
        assert collector.observations('111') == []
    asyncio.run(run())


def test_collector_merges_streaks_across_init_response_batches():
    async def run():
        collector = StreakCollector()
        requests = [
            SimpleNamespace(url='https://imapi.douyin.com/v1/message/get_message_by_init'),
            SimpleNamespace(url='https://imapi.douyin.com/v1/message/get_message_by_init'),
        ]
        for request, peer in zip(requests, ('222', '333')):
            collector.capture_request(request)
            init = fake_response(request.url, raw=response(conversation(f'0:1:111:{peer}')))
            init.request = request
            collector.capture(init)
        users = fake_response(
            'https://www.douyin.com/aweme/v1/web/im/user/info/',
            body={'data': [
                {'uid': '222', 'sec_uid': 'sec-a'},
                {'uid': '333', 'sec_uid': 'sec-b'},
            ]},
        )
        collector.capture(users)
        result = await collector.finalize('111')
        assert {item['sec_uid'] for item in result} == {'sec-a', 'sec-b'}
        assert len(result) == 2
    asyncio.run(run())


def test_response_concurrency_is_bounded_without_dropping_small_bursts():
    async def run():
        collector = StreakCollector()
        for _ in range(8):
            collector.capture(fake_response(
                'https://imapi.douyin.com/v1/message/get_message_by_init',
                raw=response(conversation()),
            ))
        await collector.finalize('111')
        diagnostic = collector.diagnostics()
        assert diagnostic['init_responses'] == 8
        assert diagnostic['init_conversations'] == 8
        assert diagnostic['dropped_responses'] == 0
    asyncio.run(run())


def test_collector_diagnostics_report_only_counts_for_each_matching_stage():
    async def run():
        collector = StreakCollector()
        request = SimpleNamespace(url='https://imapi.douyin.com/v1/message/get_message_by_init')
        collector.capture_request(request)
        init = fake_response(request.url, raw=response(conversation()))
        init.request = request
        users = fake_response(
            'https://www.douyin.com/aweme/v1/web/im/user/info/',
            body={'data': [{'uid': '222', 'sec_uid': 'sec-private', 'nickname': '不应出现在诊断中'}]},
        )
        collector.capture(init)
        collector.capture(users)
        await collector.finalize('111')

        diagnostic = collector.diagnostics()
        assert diagnostic == {
            'init_requests': 1,
            'init_responses': 1,
            'init_ok_responses': 1,
            'init_conversations': 1,
            'init_observations': 1,
            'user_info_responses': 1,
            'uid_mappings': 1,
            'decode_errors': 0,
            'dropped_responses': 0,
            'self_participant_matches': 1,
            'peer_uid_matches': 1,
        }
        assert 'sec-private' not in str(diagnostic)
        assert '不应出现在诊断中' not in str(diagnostic)

    asyncio.run(run())


@pytest.mark.parametrize('url', [
    'https://evil.invalid/v1/message/get_message_by_init',
    'http://imapi.douyin.com/v1/message/get_message_by_init',
    'https://imapi.douyin.com/v1/message/get_message_by_init_more',
    'https://imapi.douyin.com/v1/message/send',
    'https://www.douyin.com/aweme/v1/web/im/user/info/other',
    'https://imapi.douyin.com:444/v1/message/get_message_by_init',
])
def test_collector_reads_only_verified_read_response_endpoints(url):
    async def run():
        collector = StreakCollector()
        candidate = fake_response(url, raw=response(conversation()))
        collector.capture(candidate)
        await collector.finalize('111')
        candidate.body.assert_not_awaited(); candidate.json.assert_not_awaited()
    asyncio.run(run())


def test_invalid_response_does_not_stop_other_valid_observations():
    async def run():
        collector = StreakCollector()
        await collector.handle_response(fake_response('https://imapi.douyin.com/v1/message/get_message_by_init', raw=b'\x00'))
        assert collector.decode_errors == 1
        assert collector.observations('111') == []
    asyncio.run(run())


def test_oversized_declared_body_is_rejected_before_reading():
    async def run():
        collector = StreakCollector()
        candidate = fake_response('https://imapi.douyin.com/v1/message/get_message_by_init', raw=response(conversation()))
        candidate.headers = {'content-length': str(MAX_RESPONSE_BYTES + 1)}
        collector.capture(candidate)
        await collector.finalize('111')
        candidate.body.assert_not_awaited()
        assert collector.decode_errors == 1
    asyncio.run(run())


def test_json_boolean_is_not_accepted_as_numeric_flame_state():
    result = parse_flame_segment({'flame_infos': [segment(state=True)]}, observed_at=TS)
    assert result['days'] == 374 and result['state'] == 'unknown'
