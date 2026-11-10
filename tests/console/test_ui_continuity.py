"""User-visible continuity under delayed/failed reads and writes.

All writes and account checks are intercepted. The shared fixture is an
isolated database/server without Douyin workers or real message delivery.
"""
from datetime import datetime, timedelta, timezone
import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from tests.console.test_browser_acceptance import (
    browser_site,
    _context_navigation_payload,
    _route_execution_pages,
)


_BACKGROUND_REFRESH_CLOCK = """(() => {
  const now = Date.now.bind(Date);
  window.continuityElapsed = 0;
  Date.now = () => now() + window.continuityElapsed;
  window.triggerBackgroundRead = () => {
    window.continuityElapsed += 31000;
    document.dispatchEvent(new Event('visibilitychange'));
  };
})()"""


def _context(browser, base, tokens, role='user'):
    context = browser.new_context(viewport={'width':1280, 'height':900}, reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session', 'value':tokens[role], 'url':base}])
    return context


@pytest.mark.parametrize('outcome', ['success', 'failure'])
def test_task_toggle_waits_for_ack_and_failed_write_keeps_original_state(browser_site, outcome):
    browser, base, tokens = browser_site
    context = _context(browser, base, tokens)
    try:
        value = _context_navigation_payload(context, base)
        task = value['tasks'][0]
        task['enabled'] = True
        page = context.new_page()
        writes, refreshes, reads = [], [], []
        def bootstrap(route):
            reads.append(route.request.url)
            if len(reads) == 1:
                route.fulfill(json=value)
            else:
                refreshes.append(route)
        page.route('**/api/app/bootstrap', bootstrap)
        page.route('**/api/studio/tasks/*/enabled', lambda route:writes.append(route))
        page.goto(base+'/studio', wait_until='networkidle')
        row = page.locator(f'#task-row-{task["id"]}')
        button = row.get_by_role('button', name=f'暂停 {task["name"]}', exact=True)
        button.click()
        expect(button).to_be_disabled()
        expect(row).to_have_attribute('aria-busy', 'true')
        expect(row.locator('.task-status')).to_contain_text('已启用')
        expect(row.locator('.row-feedback')).to_have_text('正在保存…')
        # Repeated activation before acknowledgment cannot submit a second write.
        button.evaluate('(el) => el.click()')
        assert len(writes) == 1
        assert writes[0].request.post_data_json == {'enabled':False}
        assert len(reads) == 1
        if outcome == 'failure':
            writes[0].fulfill(status=503, json={'detail':'隔离验收：本次修改未提交'})
            expect(button).to_be_enabled()
            expect(row.locator('.task-status')).to_contain_text('已启用')
            expect(row.get_by_role('alert')).to_have_text('隔离验收：本次修改未提交')
            expect(row.locator('.row-feedback.is-success')).to_have_count(0)
            expect(row.get_by_role('button', name=f'恢复 {task["name"]}', exact=True)).to_have_count(0)
            assert len(reads) == 1, 'A rejected write must not look successful and refresh the list'
        else:
            task['enabled'] = False
            writes[0].fulfill(json={'ok':True})
            expect(row.locator('.task-status')).to_contain_text('已暂停')
            expect(row.locator('.row-feedback')).to_have_text('任务已暂停')
            expect(row.get_by_role('button', name=f'恢复 {task["name"]}', exact=True)).to_be_enabled()
            expect(page.locator('.workspace-read-status')).to_contain_text('正在更新')
            assert len(refreshes) == 1
            refreshes[0].fulfill(json=value)
            expect(page.locator('.workspace-read-status')).not_to_contain_text('正在更新')
            expect(row.locator('.task-status')).to_contain_text('已暂停')
        assert len(writes) == 1
    finally:
        context.close()


def test_confirmed_edit_stays_saved_when_followup_refresh_fails(browser_site):
    browser, base, tokens = browser_site
    context = _context(browser, base, tokens)
    try:
        value = _context_navigation_payload(context, base)
        task = value['tasks'][0]
        page = context.new_page()
        writes, reads, errors = [], [], []
        page.on('pageerror', lambda error:errors.append(str(error)))
        def bootstrap(route):
            reads.append(route.request.url)
            if len(reads) == 1:
                route.fulfill(json=value)
            else:
                route.fulfill(status=503, json={'detail':'隔离验收：列表暂时读取失败'})
        def save(route):
            writes.append(route.request)
            route.fulfill(json={'ok':True})
        page.route('**/api/app/bootstrap', bootstrap)
        page.route(f'**/tasks/{task["id"]}/edit', save)
        page.goto(base+'/studio', wait_until='networkidle')
        trigger = page.get_by_role('button', name=f'编辑 {task["name"]}', exact=True)
        trigger.click()
        editor = page.get_by_role('dialog', name=f'编辑 {task["name"]}', exact=True)
        editor.locator('input[type=time]').fill('13:15')
        editor.get_by_role('button', name='保存任务', exact=True).click()
        expect(editor).to_have_count(0)
        row = page.locator(f'#task-row-{task["id"]}')
        expect(row.locator('time')).to_contain_text('13:15')
        expect(row.locator('.row-feedback')).to_have_text('任务已保存')
        expect(page.locator('.refresh-notice')).to_contain_text('列表暂时读取失败')
        expect(page.locator('.loading-screen')).to_have_count(0)
        assert len(writes) == 1
        # Reading again is safe; it must not replay the already acknowledged save.
        page.get_by_role('button', name='重试读取', exact=True).click()
        expect(page.locator('.refresh-notice')).to_contain_text('列表暂时读取失败')
        assert len(reads) >= 3
        assert len(writes) == 1
        trigger.click()
        expect(editor.locator('input[type=time]')).to_have_value('13:15')
        editor.get_by_role('button', name='关闭', exact=True).click()
        expect(editor).to_have_count(0)
        assert len(writes) == 1
        assert not errors, errors
    finally:
        context.close()


def test_background_refresh_preserves_rows_and_open_execution_details(browser_site):
    browser, base, tokens = browser_site
    context = _context(browser, base, tokens)
    context.add_init_script(_BACKGROUND_REFRESH_CLOCK)
    try:
        value = _context_navigation_payload(context, base)
        record = {'id':'continuity-run', 'name':'持续显示的好友', 'status':'failed',
            'label':'未发出', 'stage':'confirming', 'error':'第一条执行说明',
            'time':'2026-11-10 09:30'}
        value['runs'] = [record]
        page = context.new_page()
        bootstrap_reads, bootstrap_pending, run_reads, run_pending = [], [], [], []
        def bootstrap(route):
            bootstrap_reads.append(route.request.url)
            if len(bootstrap_reads) == 1:
                route.fulfill(json=value)
            else:
                bootstrap_pending.append(route)
        def runs(route):
            run_reads.append(route.request.url)
            if len(run_reads) == 1:
                route.fulfill(json={'items':[record], 'total':1, 'page':1, 'pages':1})
            else:
                run_pending.append(route)
        page.route('**/api/app/bootstrap', bootstrap)
        page.route('**/api/studio/runs?*', runs)
        page.goto(base+'/runs', wait_until='networkidle')
        row = page.locator('.run-row')
        row.click()
        expect(row).to_have_attribute('aria-expanded', 'true')
        expect(row.locator('.run-detail')).to_contain_text('第一条执行说明')
        row.evaluate('(el) => { window.continuityRow = el; }')
        page.evaluate('() => triggerBackgroundRead()')
        expect(page.locator('.workspace-read-status')).to_contain_text('正在更新，保留当前内容')
        expect(row).to_be_visible()
        expect(row).to_have_attribute('aria-expanded', 'true')
        assert row.evaluate('(el) => el === window.continuityRow')
        assert len(bootstrap_pending) == 1
        bootstrap_pending[0].fulfill(json=value)
        expect(page.locator('.runs-card')).to_have_attribute('aria-busy', 'true')
        expect(row).to_have_attribute('aria-expanded', 'true')
        assert len(run_pending) == 1
        record['error'] = '刷新后的执行说明'
        run_pending[0].fulfill(json={'items':[record], 'total':1, 'page':1, 'pages':1})
        expect(page.locator('.runs-card')).to_have_attribute('aria-busy', 'false')
        expect(row.locator('.run-detail')).to_contain_text('刷新后的执行说明')
        expect(row).to_have_attribute('aria-expanded', 'true')
        assert row.evaluate('(el) => el === window.continuityRow')

        page.evaluate('() => triggerBackgroundRead()')
        expect(page.locator('.workspace-read-status')).to_contain_text('正在更新')
        assert len(bootstrap_pending) == 2
        bootstrap_pending[1].fulfill(status=503, json={'detail':'隔离验收：后台读取失败'})
        expect(page.locator('.refresh-notice')).to_contain_text('后台读取失败')
        expect(row).to_have_attribute('aria-expanded', 'true')
        expect(row.locator('.run-detail')).to_contain_text('刷新后的执行说明')
        expect(page.locator('.loading-screen')).to_have_count(0)
    finally:
        context.close()


def test_removing_filter_chips_resets_server_pagination_and_preserves_other_filter(browser_site):
    browser, base, tokens = browser_site
    context = _context(browser, base, tokens)
    try:
        records = [{'id':f'chip-run-{index}', 'name':f'匹配好友{index}',
            'status':'failed' if index < 6 else 'success',
            'label':'未发出' if index < 6 else '已发出', 'stage':'confirming',
            'error':'隔离验收数据', 'time':'2026-11-10 09:30'} for index in range(8)]
        page = context.new_page()
        reads = _route_execution_pages(page, records, page_size=2)
        page.goto(base+'/runs?status=failed&q=好友', wait_until='networkidle')
        filters = page.get_by_role('group', name='当前筛选', exact=True)
        page.get_by_role('button', name='下一页', exact=True).click()
        expect(page.locator('.admin-pagination')).to_contain_text('第 2 / 3 页')
        with page.expect_response(lambda response:'/api/studio/runs?' in response.url
                and parse_qs(urlparse(response.url).query).get('q', [''])[0] == ''
                and parse_qs(urlparse(response.url).query).get('page') == ['1']):
            filters.get_by_role('button', name='移除好友筛选：好友', exact=True).click()
        expect(page.get_by_placeholder('按好友名搜索')).to_have_value('')
        expect(page.locator('.admin-pagination')).to_contain_text('第 1 / 3 页')
        expect(page.locator('.runs-card')).to_have_attribute('aria-busy', 'false')
        assert reads[-1] == ('failed', '', 1), reads
        assert parse_qs(urlparse(page.url).query) == {'status':['failed']}
        expect(filters.get_by_role('button', name='移除结果筛选：未发出', exact=True)).to_be_visible()
        page.get_by_role('button', name='下一页', exact=True).click()
        expect(page.locator('.admin-pagination')).to_contain_text('第 2 / 3 页')
        filters.get_by_role('button', name='移除结果筛选：未发出', exact=True).click()
        expect(page.locator('.admin-pagination')).to_contain_text('第 1 / 4 页')
        assert reads[-1] == ('', '', 1), reads
        expect(filters).to_have_count(0)
        expect(page.get_by_role('radio', name='全部', exact=True)).to_be_checked()
        assert not urlparse(page.url).query
    finally:
        context.close()


def test_administrator_bundle_is_loaded_only_after_entering_management(browser_site):
    browser, base, tokens = browser_site
    admin_asset = re.compile(r'/admin-pages-[^/]+\.js(?:\?|$)')
    for role in ('user', 'admin'):
        context = _context(browser, base, tokens, role)
        try:
            page = context.new_page()
            requests, errors = [], []
            page.on('request', lambda request:requests.append(request.url))
            page.on('pageerror', lambda error:errors.append(str(error)))
            page.goto(base+'/dashboard', wait_until='networkidle')
            expect(page.locator('.fire-main')).to_be_visible()
            navigation = page.get_by_role('radiogroup', name='管理导航' if role == 'admin' else '工作区导航')
            navigation.get_by_role('radio', name='续火任务', exact=True).click()
            page.wait_for_url('**/studio')
            expect(page.get_by_role('heading', name='续火任务', exact=True)).to_be_visible()
            navigation.get_by_role('radio', name='抖音账号', exact=True).click()
            page.wait_for_url('**/accounts')
            expect(page.get_by_role('heading', name='抖音账号', exact=True)).to_be_visible()
            assert not [url for url in requests if admin_asset.search(url)], requests
            if role == 'admin':
                navigation.get_by_role('radio', name='业务概览', exact=True).click()
                page.wait_for_url('**/admin/overview')
                expect(page.get_by_role('heading', name='业务概览', exact=True)).to_be_visible()
                assert any(admin_asset.search(url) for url in requests), requests
                expect(page.locator('.loading-screen')).to_have_count(0)
            assert not errors, errors
        finally:
            context.close()


def test_queued_temporary_account_check_waits_for_a_real_valid_result(browser_site):
    browser, base, tokens = browser_site
    context = _context(browser, base, tokens)
    context.add_init_script(_BACKGROUND_REFRESH_CLOCK)
    try:
        value = _context_navigation_payload(context, base)
        account = value['accounts'][0]
        now = datetime.now(timezone.utc)
        account['health'].update(checked_at=(now-timedelta(hours=1)).isoformat(), checking=False)
        page = context.new_page()
        writes, unexpected_writes = [], []
        page.route('**/api/app/bootstrap', lambda route:route.fulfill(json=value))
        def health_check(route):
            writes.append(route.request)
            account['validation_state'] = 'temporary'
            account['health'].update(state='temporary', blocked=True, checking=True,
                label='等待复核', checked_at=datetime.now(timezone.utc).isoformat(),
                next_check_at=(now+timedelta(minutes=1)).isoformat())
            route.fulfill(status=202, json={'queued':True})
        page.route(f'**/accounts/{account["id"]}/health-check', health_check)
        def block_recovery(route):
            unexpected_writes.append(route.request.url)
            route.fulfill(status=503, json={'detail':'隔离验收：禁止启动真实恢复会话'})
        page.route('**/accounts/recovery', block_recovery)
        page.route('**/accounts/scan', block_recovery)
        page.goto(base+'/accounts', wait_until='networkidle')
        card = page.locator(f'#account-{account["id"]}')
        card.get_by_role('button', name='复核状态', exact=True).click()
        feedback = card.locator('.account-check-feedback')
        expect(feedback).to_have_attribute('data-state', 'pending')
        expect(feedback).to_contain_text('正在等待账号结果')
        expect(feedback).not_to_contain_text('检查完成')
        assert len(writes) == 1 and not unexpected_writes

        account['validation_state'] = 'valid'
        account['health'].update(state='valid', label='账号正常', blocked=False,
            checking=False, next_check_at=None,
            checked_at=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat())
        page.evaluate('() => triggerBackgroundRead()')
        expect(feedback).to_have_attribute('data-state', 'valid')
        expect(feedback).to_contain_text('检查完成：登录有效')
        expect(card.get_by_role('button', name='复核状态', exact=True)).to_be_enabled()
        expect(page.get_by_role('dialog')).to_have_count(0)
        assert len(writes) == 1 and not unexpected_writes
    finally:
        context.close()
