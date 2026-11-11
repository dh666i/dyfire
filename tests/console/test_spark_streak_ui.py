"""Platform streak displays against isolated HTTP/browser fixtures, no Douyin calls."""
from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from tests.console.test_browser_acceptance import browser_site


def _data(context, base):
    data = context.request.get(base + '/api/app/bootstrap').json()
    account = data['accounts'][0]
    observed = datetime.now(timezone.utc).isoformat()
    active = {'days': 128, 'state': 'active', 'observed_at': observed, 'source': 'douyin_dom', 'stale': False, 'platform_text': '连续聊天 128 天', 'milestones': []}
    broken = {**active, 'days': 18, 'state': 'broken', 'platform_text': '火花已熄灭'}
    recovering = {**active, 'days': 67, 'state': 'recovering', 'recover_current': 1, 'recover_total': 3, 'platform_text': '重燃中'}
    first = {**data['tasks'][0], 'name': '小琪', 'sec_uid': 'test-friend-one', 'spark': active}
    second = {**first, 'id': 'unread-task', 'name': '还未同步', 'sec_uid': 'test-friend-two', 'spark': None, 'streak': 999}
    third = {**first, 'id': 'broken-task', 'name': '旧友', 'sec_uid': 'test-friend-three', 'spark': broken}
    data['tasks'] = [first, second, third]
    data['runs'] = [{**data['runs'][0], 'taskId': first['id'], 'status': 'success', 'label': '已发出'}]
    data['membership'].update(max_tasks=10, can_add_task=True)
    data['resource_limits'].update(tasks_max=10, tasks_enabled_total=3)
    contacts = [
        {'name': '小琪', 'sec_uid': first['sec_uid'], 'spark': active},
        {'name': '还未同步', 'sec_uid': second['sec_uid'], 'spark': None},
        {'name': '同名新好友', 'sec_uid': 'new-friend-one', 'spark': {**active, 'days': 200}},
        {'name': '同名新好友', 'sec_uid': 'new-friend-two', 'spark': recovering},
        {'name': '没有天数的好友', 'sec_uid': 'new-unknown', 'spark': None},
    ]
    history = {'current': active, 'milestones': [{'days': 100, 'kind': 'first_observed', 'observed_at': observed}], 'items': [{'id': 'h1', **active}, {'id': 'h2', **active, 'days': 127}], 'has_more': False}
    return data, account, contacts, history


def _capture(page, name, width, theme):
    page.evaluate('document.fonts.ready')
    output = Path('.artifacts/spark-streak-ui'); output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / f'{name}-{width}-{theme}.png'), full_page=name=='dashboard')


@pytest.mark.parametrize('width,theme', [(360, 'light'), (390, 'dark'), (1440, 'light'), (1440, 'dark')])
def test_streak_cards_details_and_filters_are_readable_without_changing_delivery(browser_site, width, theme):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={'width': width, 'height': 900}, has_touch=width<700, reduced_motion='reduce')
    context.add_init_script(f"localStorage.setItem('dyfire-theme','{theme}')")
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        data, account, contacts, history = _data(context, base)
        page = context.new_page(); errors = []; reads = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/app/bootstrap', lambda route: route.fulfill(json=data))
        page.route('**/accounts/*/conversations?*', lambda route: route.fulfill(json={'items': contacts, 'total': len(contacts), 'has_more':False, 'can_sync_more':False}))
        def detail(route):
            reads.append(parse_qs(urlparse(route.request.url).query))
            route.fulfill(json=history)
        page.route('**/api/streaks/history?*', detail)
        page.goto(base + '/dashboard', wait_until='networkidle')
        card = page.locator('.fire-friend-with-streak').filter(has=page.get_by_role('button', name='编辑 小琪 的任务', exact=True))
        expect(card.locator('.badge')).to_have_text('已完成')
        expect(card.locator('.streak-summary')).to_contain_text('128')
        expect(card.locator('.streak-summary')).to_contain_text('已点亮')
        assert card.locator('.streak-summary').bounding_box()['y'] >= card.locator('.fire-friend-main').bounding_box()['y'] + card.locator('.fire-friend-main').bounding_box()['height']
        unknown = page.get_by_role('button', name='查看 还未同步 的火花详情', exact=True)
        expect(unknown).to_contain_text('天数待同步')
        expect(unknown).not_to_contain_text('999')
        assert reads == []
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        _capture(page, 'dashboard', width, theme)
        trigger = page.get_by_role('button', name='查看 小琪 的火花详情', exact=True)
        trigger.focus(); page.keyboard.press('Enter')
        panel = page.get_by_role('dialog', name='小琪 · 火花', exact=True)
        expect(panel).to_be_visible()
        expect(panel.locator('.streak-history li')).to_have_count(2)
        expect(panel).to_contain_text('首次读取已达到')
        assert reads[-1]['account_id'] == [account['id']]
        assert reads[-1]['sec_uid'] == ['test-friend-one']
        assert panel.evaluate("node=>node.closest('.route-view')===null")
        assert panel.bounding_box()['width'] <= width
        _capture(page, 'detail', width, theme)
        page.keyboard.press('Escape'); expect(panel).to_have_count(0)
        expect(trigger).to_be_focused()
        assert page.evaluate('document.body.style.overflow') != 'hidden'
        page.goto(base + '/studio', wait_until='networkidle')
        expect(page.get_by_role('button', name='查看 小琪 的火花详情')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.locator('.friend-trigger').click()
        picker = page.get_by_role('dialog', name='选择目标好友', exact=True)
        expect(picker.locator('.contact-row')).to_have_count(5)
        expect(picker.locator('.contact-row').filter(has=page.get_by_text('小琪', exact=True)).get_by_role('checkbox')).to_be_disabled()
        picker.get_by_role('combobox', name='好友排序').click()
        expect(page.get_by_role('option', name='火花天数从高到低', exact=True)).to_be_visible()
        page.keyboard.press('Escape')
        expect(picker).to_be_visible()
        expect(page.get_by_role('option', name='火花天数从高到低', exact=True)).to_have_count(0)
        picker.get_by_role('combobox', name='好友排序').click()
        page.get_by_role('option', name='火花天数从高到低', exact=True).click()
        expect(picker.locator('.contact-row').first).to_contain_text('200 天')
        picker.get_by_role('combobox', name='筛选好友火花与任务').click()
        page.get_by_role('option', name='尚未创建任务', exact=True).click()
        expect(picker.locator('.contact-row')).to_have_count(3)
        expect(picker.locator('.contact-row').first).to_contain_text('200 天')
        checkbox = picker.get_by_role('checkbox').first
        checkbox.focus(); page.keyboard.press('Space'); expect(checkbox).to_be_checked()
        assert picker.get_by_role('button', name='完成选择').bounding_box()['y'] + picker.get_by_role('button', name='完成选择').bounding_box()['height'] <= 900
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        _capture(page, 'picker', width, theme)
        picker.get_by_role('button', name='完成选择').click()
        expect(page.locator('.friend-trigger')).to_contain_text('同名新好友')
        assert errors == []
        page.goto(base + f'/studio?account={account["id"]}&spark=test-friend-one', wait_until='networkidle')
        deep_link=page.get_by_role('dialog', name='小琪 · 火花', exact=True)
        expect(deep_link).to_be_visible()
        expect(deep_link.locator('.streak-detail-hero')).to_contain_text('128')
    finally:
        context.close()


def test_streak_detail_retry_and_pagination_do_not_infer_missing_days(browser_site):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        data, _account, _contacts, history = _data(context, base)
        page = context.new_page(); calls = []
        page.route('**/api/app/bootstrap', lambda route: route.fulfill(json=data))
        def detail(route):
            query = parse_qs(urlparse(route.request.url).query); calls.append(query)
            if len(calls) == 1:
                route.fulfill(status=503, json={'detail':'记录暂时无法读取'}); return
            if query['sec_uid'] == ['test-friend-two']:
                route.fulfill(json={'current':{'days':None,'state':'unknown'},'milestones':[],'items':[],'has_more':False}); return
            if query['offset'] == ['0']:
                route.fulfill(json={**history,'items':history['items'][:1],'has_more':True,'next_offset':1}); return
            route.fulfill(json={**history,'items':history['items'][1:],'has_more':False})
        page.route('**/api/streaks/history?*', detail)
        page.goto(base+'/dashboard',wait_until='networkidle')
        page.get_by_role('button', name='查看 小琪 的火花详情').click()
        panel=page.get_by_role('dialog',name='小琪 · 火花')
        expect(panel.get_by_role('alert')).to_contain_text('记录暂时无法读取')
        expect(panel.locator('.streak-detail-hero')).to_contain_text('128')
        panel.get_by_role('button',name='重试',exact=True).click()
        expect(panel.locator('.streak-history li')).to_have_count(1)
        panel.get_by_role('button',name='加载更多记录').click()
        expect(panel.locator('.streak-history li')).to_have_count(2)
        assert calls[-1]['offset']==['1']
        page.keyboard.press('Escape');expect(panel).to_have_count(0)
        page.get_by_role('button',name='查看 还未同步 的火花详情').click()
        unknown=page.get_by_role('dialog',name='还未同步 · 火花')
        expect(unknown).to_contain_text('暂未获取天数')
        expect(unknown.locator('.streak-history')).to_contain_text('不会用发送次数推算天数')
        expect(unknown.locator('.streak-history li')).to_have_count(0)
        expect(unknown).not_to_contain_text(re.compile(r'128|999|0 天'))
    finally:
        context.close()
