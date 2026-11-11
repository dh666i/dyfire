"""Updater UI contracts against an isolated browser; no host update requests."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.console.test_browser_acceptance import browser_site


def update_status(**changes):
    return {
        'online': True, 'current': '0.4.6', 'latest': '0.4.6', 'available': '0.4.6',
        'component_versions': {'web': '0.4.6', 'browser': '0.4.5'},
        'phase': 'checked', 'message': '版本检查完成', 'operation_id': 'previous',
        'update_available': True, 'web_update_available': False, 'web_update_allowed': True,
        **changes,
    }


def open_panel(browser_site, state, *, width=1280, theme='light'):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={'width': width, 'height': 1000}, reduced_motion='reduce')
    context.add_init_script(f"localStorage.setItem('dyfire-theme','{theme}')")
    context.add_cookies([{'name': 'spark_session', 'value': tokens['admin'], 'url': base}])
    page = context.new_page()
    page.route('**/api/admin/updates', lambda route: route.fulfill(json=state))
    # Every mutation is intercepted, including unexpected action names.
    page.route('**/api/admin/updates/**', lambda route: route.fulfill(json={'accepted': True, 'operation_id': 'new-operation'}))
    page.goto(base + '/admin/settings', wait_until='domcontentloaded')
    panel = page.locator('.update-panel')
    expect(panel).to_be_visible()
    expect(panel.get_by_text('更新服务在线', exact=True)).to_be_visible()
    return context, page, panel


def test_full_update_available_when_only_browser_is_outdated(browser_site):
    context, page, panel = open_panel(browser_site, update_status())
    try:
        expect(panel.locator('.update-version-grid').get_by_text('0.4.6', exact=True)).to_have_count(2)
        expect(panel.locator('.update-version-grid').get_by_text('0.4.5', exact=True)).to_be_visible()
        expect(panel.get_by_role('button', name='仅更新网页', exact=True)).to_be_disabled()
        expect(panel.get_by_role('button', name='完整更新', exact=True)).to_be_enabled()
        expect(panel.get_by_text('网页已是最新版本，登录与发送服务可通过完整更新升级。')).to_be_visible()
    finally:
        context.close()


def test_web_update_explains_compatibility_block(browser_site):
    state = update_status(web_update_available=True, web_update_allowed=False,
                          web_update_reason='数据库结构已变更，请选择完整更新。')
    context, page, panel = open_panel(browser_site, state)
    try:
        button = panel.get_by_role('button', name='仅更新网页', exact=True)
        expect(button).to_be_disabled()
        expect(button).to_have_attribute('aria-describedby', 'web-update-compatibility')
        expect(panel.get_by_text('数据库结构已变更，请选择完整更新。')).to_be_visible()
        expect(panel.get_by_role('button', name='完整更新', exact=True)).to_be_enabled()
    finally:
        context.close()


@pytest.mark.parametrize('width,theme', [(360, 'light'), (1440, 'dark')])
def test_transfer_progress_distinguishes_unknown_download_and_reuse(browser_site, width, theme):
    state = update_status(phase='web_pulling', message='正在下载网页版本', pull_progress={
        'percent': None, 'downloaded_bytes': 2 * 1024 ** 2, 'reused_bytes': 40 * 1024 ** 2,
        'bytes_per_second': 512 * 1024, 'idle_seconds': 65, 'attempt': 2,
        'layers_ready': 3, 'layers_total': 5, 'label': '正在下载镜像层',
    }, web_download_plan={'known': True, 'compressed_image_bytes': 100 * 1024 ** 2,
                         'download_upper_bound_bytes': 2 * 1024 ** 2, 'reused_layers': 7},
       download_plan={'known': True, 'compressed_image_bytes': 900 * 1024 ** 2,
                      'download_upper_bound_bytes': 20 * 1024 ** 2, 'reused_layers': 15})
    context, page, panel = open_panel(browser_site, state, width=width, theme=theme)
    try:
        assert panel.get_by_role('progressbar').get_attribute('value') is None
        expect(panel.get_by_text('2.0 MiB', exact=True)).to_be_visible()
        expect(panel.get_by_text('40.0 MiB', exact=True)).to_be_visible()
        expect(panel.get_by_text('512.0 KiB/s', exact=True)).to_be_visible()
        expect(panel.get_by_text('已有 65 秒没有新进展，正在等待镜像仓库响应。')).to_be_visible()
        expect(panel.get_by_role('button', name='检查更新', exact=True)).to_be_disabled()
        assert 'web_pulling' not in panel.inner_text()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        output = Path('.artifacts/update-panel'); output.mkdir(parents=True, exist_ok=True)
        panel.screenshot(path=str(output / f'{theme}-{width}.png'))
    finally:
        context.close()


def test_accepted_request_stays_locked_until_matching_status_arrives(browser_site):
    state = update_status(web_update_available=True)
    context, page, panel = open_panel(browser_site, state)
    try:
        requests = []
        def accept(route):
            requests.append(route.request.post_data_json)
            route.fulfill(json={'accepted': True, 'operation_id': 'new-operation'})
        page.route('**/api/admin/updates/install-web', accept)
        page.clock.install()
        panel.get_by_role('button', name='仅更新网页', exact=True).click()
        expect(panel.get_by_text('请求已提交，等待更新服务接收。')).to_be_visible()
        expect(panel.get_by_role('button', name='完整更新', exact=True)).to_be_disabled()
        page.clock.fast_forward(5100)
        # The status still describes an older operation; keep the action locked.
        expect(panel.get_by_role('button', name='检查更新', exact=True)).to_be_disabled()
        state.update(operation_id='new-operation', phase='web_candidate', message='正在验证新网页')
        page.clock.fast_forward(5100)
        expect(panel.get_by_text('验证新网页', exact=True)).to_be_visible()
        expect(panel.get_by_role('button', name='完整更新', exact=True)).to_be_disabled()
        state.update(phase='web_complete', message='网页更新完成', web_update_available=False)
        page.clock.fast_forward(5100)
        expect(panel.get_by_role('button', name='检查更新', exact=True)).to_be_enabled()
        assert requests == [{'version': '0.4.6'}]
    finally:
        context.close()


def test_rollback_submits_visible_default_version(browser_site):
    state = update_status(rollback_versions=[{'version': '0.4.4', 'published_at': '2026-11-09T00:00:00Z'}])
    context, page, panel = open_panel(browser_site, state)
    try:
        requests = []
        def accept(route):
            requests.append(route.request.post_data_json)
            route.fulfill(json={'accepted': True, 'operation_id': 'new-operation'})
        page.route('**/api/admin/updates/rollback', accept)
        panel.get_by_role('button', name='回滚', exact=True).click()
        dialog = page.get_by_role('dialog')
        expect(dialog.get_by_text('将恢复到 0.4.4，登录和发送服务也会重启。')).to_be_visible()
        dialog.get_by_role('button', name='确认回滚', exact=True).click()
        expect(panel.get_by_text('请求已提交，等待更新服务接收。')).to_be_visible()
        assert requests == [{'version': '0.4.4'}]
    finally:
        context.close()
