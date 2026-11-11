"""Streak observations must come from platform evidence tied to stable IDs."""
from datetime import datetime, timezone
import os
from pathlib import Path
from unittest.mock import AsyncMock
import asyncio

import pytest

from core.streak_observation import STREAK_ROWS_SCRIPT, parse_streak_rows, read_streak_observations


NOW = datetime(2026, 11, 11, 4, 0, tzinfo=timezone.utc)


def row(text, *, kind='badge', sec_uid='sec-a', uid=None, conversation=None):
    return {
        'identity': {'sec_uids': [sec_uid] if sec_uid else [], 'uids': [uid] if uid else [],
                     'conversation_ids': [conversation] if conversation else []},
        'signals': [{'kind': kind, 'text': text}],
    }


def parse(rows, **kwargs):
    return parse_streak_rows(rows, observed_at=NOW, **kwargs)


@pytest.mark.parametrize('text,kind,days', [
    ('392', 'badge', 392), ('1', 'badge', 1), ('1000', 'badge', 1000),
    ('🔥 10 天', 'badge', 10), ('火花392天', 'aria', 392),
    ('火花天数：1000天', 'title', 1000), ('连续聊天 20 天', 'aria', 20),
])
def test_platform_days_are_observed_without_inferring_current_fire_state(text, kind, days):
    result = parse([row(text, kind=kind)])
    assert result == [{
        'sec_uid': 'sec-a', 'days': days, 'state': 'unknown',
        'recover_current': None, 'recover_total': None, 'platform_text': text,
        'source': 'douyin_dom', 'observed_at': NOW.isoformat(),
    }]


@pytest.mark.parametrize('text,kind', [
    ('392', 'aria'), ('09:30', 'badge'), ('2026-11-11', 'badge'),
    ('小王100', 'badge'), ('今天发100条消息', 'badge'), ('消息：火花392天真好', 'aria'),
    ('100000天', 'badge'), ('火花100000天', 'aria'), ('火花-1天', 'aria'),
    ('3.5天', 'badge'), ('5条未读', 'badge'), ('火花1/3', 'badge'),
])
def test_unrelated_or_malformed_digits_never_become_streak_days(text, kind):
    assert parse([row(text, kind=kind)]) == []


def test_recovery_progress_is_not_confused_with_streak_days():
    result = parse([row('火花84天，重燃中 1/3', kind='aria')])[0]
    assert (result['days'], result['state'], result['recover_current'], result['recover_total']) == (84, 'recovering', 1, 3)
    progress_only = parse([row('重燃中 2/3')])[0]
    assert progress_only['days'] is None
    assert progress_only['recover_current'] == 2
    assert progress_only['recover_total'] == 3


@pytest.mark.parametrize('text,state', [('火花已点亮', 'active'), ('火花已熄灭', 'broken'), ('火花已中断', 'broken')])
def test_explicit_status_does_not_invent_a_zero_day_value(text, state):
    result = parse([row(text, kind='aria')])[0]
    assert result['state'] == state
    assert result['days'] is None


@pytest.mark.parametrize('text', ['重燃中 4/3', '重燃中 1/0', '重燃中 -1/3', '重燃中 1/3000'])
def test_invalid_recovery_progress_is_not_saved(text):
    assert parse([row(text)]) == []


def test_missing_identity_is_never_matched_by_nickname():
    value = row('392', sec_uid=None)
    value.update(name='同名好友', nickname='同名好友', sec_uid='untrusted-top-level')
    assert parse([value], uid_to_sec_uid={'同名好友': 'sec-a'}) == []


def test_uid_and_conversation_mapping_must_be_explicit_and_account_local():
    value = row('392', sec_uid=None, uid='12345', conversation='0:1:12345:98765')
    assert parse([value]) == []
    assert parse([value], uid_to_sec_uid={'12345': 'sec-a'})[0]['sec_uid'] == 'sec-a'
    assert parse([value], conversation_to_sec_uid={'0:1:12345:98765': 'sec-a'})[0]['sec_uid'] == 'sec-a'
    assert parse([value], uid_to_sec_uid={'12345': 'sec-a'},
                 conversation_to_sec_uid={'0:1:12345:98765': 'sec-b'}) == []
    assert parse([row('392', uid='12345')], uid_to_sec_uid={'12345': 'sec-b'}) == []


def test_same_name_different_ids_stay_separate_and_ambiguous_identity_is_rejected():
    first, second = row('10'), row('20', sec_uid='sec-b')
    first['name'] = second['name'] = '同名好友'
    assert [(value['sec_uid'], value['days']) for value in parse([first, second])] == [('sec-a', 10), ('sec-b', 20)]
    first['identity']['sec_uids'].append('sec-b')
    assert parse([first]) == []


def test_repeated_windows_merge_evidence_but_conflicting_counts_never_choose_maximum():
    assert len(parse([row('392'), row('火花392天', kind='aria')])) == 1
    assert parse([row('392'), row('393')]) == []
    result = parse([row('392'), row('火花已熄灭', kind='aria'), row('火花已点亮', kind='aria')])[0]
    assert result['days'] == 392
    assert result['state'] == 'unknown'


def test_absence_is_not_zero_and_next_day_never_increments_without_new_evidence():
    assert parse([]) == []
    assert parse([{'identity': {'sec_uids': ['sec-a']}, 'signals': []}]) == []
    tomorrow = datetime(2026, 11, 12, 4, tzinfo=timezone.utc)
    assert parse_streak_rows([row('392')], observed_at=tomorrow)[0]['days'] == 392
    assert parse([row('0')])[0]['days'] == 0  # zero only when explicitly displayed


def test_invalid_shapes_are_ignored_and_naive_observation_time_is_rejected():
    assert parse([None, 'row', {'identity': []}, {'identity': {'sec_uids': 'sec-a'}, 'signals': []}]) == []
    assert parse([row('392', sec_uid='bad\x00identity')]) == []
    with pytest.raises(ValueError, match='timezone'):
        parse_streak_rows([], observed_at=datetime(2026, 11, 11))


def test_async_reader_uses_only_a_dom_evaluation():
    page = AsyncMock()
    page.evaluate.return_value = [row('392')]
    result = asyncio.run(read_streak_observations(page, observed_at=NOW))
    assert result[0]['days'] == 392
    page.evaluate.assert_awaited_once_with(STREAK_ROWS_SCRIPT)
    assert [call[0] for call in page.mock_calls] == ['evaluate']


def test_real_dom_ignores_nicknames_unread_counts_hidden_rows_and_message_text():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            if os.environ.get('DYFIRE_REQUIRE_BROWSER') == '1':
                pytest.fail('Chromium is required in CI')
            pytest.skip('Chromium is not installed')
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('''<style>.conversationConversationItemwrapper{height:64px}</style>
              <div class="conversationConversationItemwrapper" data-sec-uid="sec-a">
                <span class="conversationConversationItemtitle">小王2026</span>
                <span>09:30</span><span class="unread">99</span><span>消息火花888天</span>
                <span class="commonStreaknormalText">1000</span>
              </div>
              <div class="conversationConversationItemwrapper" data-uid="777" aria-label="小李，火花84天，重燃中 1/3">
                <span class="conversationConversationItemtitle">小李</span>
              </div>
              <div class="conversationConversationItemwrapper" data-sec-uid="nickname-only" aria-label="火花123天">
                <span class="conversationConversationItemtitle">火花123天</span><span>22:30</span>
              </div>
              <div class="conversationConversationItemwrapper" data-sec-uid="no-badge"><span>火花777天</span></div>
              <div class="conversationConversationItemwrapper" data-sec-uid="hidden" style="display:none"><span class="commonStreaknormalText">99</span></div>
              <div class="conversationConversationItemwrapper" data-sec-uid="hidden-badge"><span class="commonStreaknormalText" style="visibility:hidden">99</span></div>
              <div class="conversationConversationItemwrapper"><span class="conversationConversationItemtitle">同名好友</span><span class="commonStreaknormalText">88</span></div>
              <div class="conversationConversationItemwrapper"><a href="https://evil.invalid/user/foreign">假链接</a><span class="commonStreaknormalText">88</span></div>
              <div class="conversationConversationItemwrapper"><a href="https://www.douyin.com/user/sec-profile">好友</a><span class="commonStreaknormalText">1</span></div>
            ''')
            raw = page.evaluate(STREAK_ROWS_SCRIPT)
            found = {item['sec_uid']: item for item in parse(raw, uid_to_sec_uid={'777': 'sec-b'})}
            assert set(found) == {'sec-a', 'sec-b', 'sec-profile'}
            assert found['sec-a']['days'] == 1000
            assert found['sec-a']['state'] == 'unknown'
            assert found['sec-b']['days'] == 84
            assert found['sec-b']['state'] == 'recovering'
            assert found['sec-b']['recover_current'] == 1
            assert found['sec-profile']['days'] == 1
        finally:
            browser.close()
