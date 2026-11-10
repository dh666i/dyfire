"""Chart contracts in Chromium, using isolated data and no production services.

Touch coverage uses Chromium emulation; it does not certify native iOS or
Android devices. Screenshots are review artifacts, not golden-image assertions.
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from tests.console.test_browser_acceptance import browser_site


END = int(datetime(2026, 11, 10, 4, tzinfo=timezone.utc).timestamp())
TODAY = date(2026, 11, 10)


def _context(browser_site, width=1440, theme='dark'):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={'width': width, 'height': 1000},
        has_touch=width < 700, is_mobile=width < 700,
        reduced_motion='reduce', bypass_csp=True)
    context.add_init_script(f"localStorage.setItem('dyfire-theme','{theme}')")
    context.add_cookies([{'name': 'spark_session', 'value': tokens['admin'], 'url': base}])
    return context, base


def _history():
    return {'hours': 24, 'state': 'fresh', 'start': END - 86400, 'end': END,
        'bucket_seconds': 120, 'sample_seconds': 5, 'latest_at': END,
        'points': [
            {'at': END - 7200, 'cpu': 80, 'memory': 40, 'disk': 30},
            {'at': END - 3600, 'cpu': 40, 'memory': 41, 'disk': 30},
            {'at': END - 240, 'cpu': 0, 'memory': 42, 'disk': 30},
            {'at': END - 120, 'cpu': None, 'memory': 43, 'disk': 30},
            {'at': END, 'cpu': 0, 'memory': 44, 'disk': 30},
        ]}


def _trend(days=7, *, zero=False, gap=True):
    rows = []
    for index in range(days):
        if gap and index == days // 2:
            continue
        day = TODAY - timedelta(days=days - index - 1)
        if zero or index == 0:
            counts = {'total': 0, 'success': 0, 'failed': 0, 'skipped': 0}
        elif index == days - 1:
            # failed includes an ordinary failure and an unconfirmed result.
            # total also contains a running record, shown as "other".
            counts = {'total': 7, 'success': 3, 'failed': 2, 'skipped': 1}
        else:
            counts = {'total': 3, 'success': 2, 'failed': 1, 'skipped': 0}
        rows.append({'date': day.isoformat(), **counts})
    return {'days': days, 'trend': rows,
        'alerts': {'accounts': [], 'failed_runs': [], 'expiring': []}}


def _capture(chart, name):
    output = Path('.artifacts/ui-chart-quality/screenshots')
    output.mkdir(parents=True, exist_ok=True)
    chart.screenshot(path=str(output / f'{name}.png'))


def _assert_chart_accessibility(page, selector):
    source = Path('frontend/node_modules/axe-core/axe.min.js')
    assert source.is_file(), 'Install frontend dependencies before browser validation'
    page.evaluate(source.read_text(encoding='utf-8'))
    failures = page.evaluate("""async selector => {
      const result = await axe.run(document.querySelector(selector), {
        runOnly: {type:'rule', values:['color-contrast','aria-required-attr','aria-valid-attr-value']}
      });
      return result.violations.map(item => ({id:item.id, targets:item.nodes.map(node=>node.target)}));
    }""", selector)
    assert failures == [], failures


@pytest.mark.parametrize('width', [360, 1440])
@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_resource_chart_preserves_zero_gaps_range_and_keyboard_touch_selection(browser_site, width, theme):
    context, base = _context(browser_site, width, theme)
    try:
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/admin/operations/history?*', lambda route: route.fulfill(json=_history()))
        page.goto(base + '/admin/operations', wait_until='networkidle')
        chart = page.locator('.resource-history-chart')
        slider = chart.get_by_role('slider', name='CPU采样时间')
        expect(slider).to_be_visible()
        cpu = chart.locator('.resource-series button').first
        expect(cpu.locator('b')).to_have_text('0%')
        expect(cpu.locator('small')).to_contain_text('80%')
        expect(chart.locator('.resource-state')).to_have_text('采集中')
        expect(chart.locator('.resource-chart-description')).to_contain_text('每 2 分钟')
        chart.get_by_role('button', name='近 1 小时', exact=True).click()
        expect(cpu.locator('small')).to_contain_text('40%')
        expect(chart.get_by_role('button', name='近 1 小时', exact=True)).to_have_attribute('aria-pressed', 'true')
        path = chart.locator('.resource-plot path').get_attribute('d')
        assert path.count('M') == 3 and 'L' not in path, path
        assert ',100.00' in path, 'A real 0% sample must stay on the zero baseline'

        slider.focus()
        slider.press('End')
        expect(slider).to_have_attribute('aria-valuetext', '11月10日 12:00，CPU区间峰值 0%')
        slider.press('ArrowLeft')
        expect(slider).to_have_attribute('aria-valuetext', '11月10日 11:58，CPU未采集')
        slider.press('ArrowLeft')
        expect(slider).to_have_attribute('aria-valuetext', '11月10日 11:56，CPU区间峰值 0%')
        slider.press('Home')
        expect(slider).to_have_attribute('aria-valuetext', '11月10日 11:00，CPU区间峰值 40%')
        slider.press('Escape')
        expect(chart.locator('.resource-tooltip')).to_have_count(0)

        slider.scroll_into_view_if_needed()
        box = slider.bounding_box()
        before = chart.bounding_box()
        if width >= 700:
            # The 11:30 gap must not snap to 11:00 or 11:56.
            slider.hover(position={'x': box['width'] * .5, 'y': box['height'] * .6})
            expect(chart.locator('.resource-tooltip')).to_contain_text('未采集')
            expect(chart.locator('.resource-tooltip')).to_contain_text('11月10日 11:30')
            assert chart.bounding_box()['height'] == before['height']
            assert chart.locator('[title]').count() == 0
            _capture(chart, f'resource-gap-{theme}-{width}')
            page.mouse.move(4, 4)
            expect(chart.locator('.resource-tooltip')).to_have_count(0)
        else:
            # Point into the genuine 11:56 bucket, then leave the chart.
            slider.tap(position={'x': box['width'] * (3390 / 3600), 'y': box['height'] * .6})
            expect(chart.locator('.resource-tooltip')).to_contain_text('0%')
            expect(chart.get_by_role('button', name='取消锁定')).to_be_visible()
            page.touchscreen.tap(4, 4)
            expect(chart.locator('.resource-tooltip')).to_contain_text('0%')
            _capture(chart, f'resource-touch-pinned-{theme}-{width}')
            chart.get_by_role('button', name='取消锁定').tap()
            expect(chart.locator('.resource-tooltip')).to_have_count(0)

        chart.get_by_role('button', name='近 6 小时', exact=True).click()
        expect(cpu.locator('small')).to_contain_text('80%')
        expect(chart.locator('.resource-chart-description')).to_contain_text('近 6 小时')
        expect(chart.locator('.resource-chart-description')).to_contain_text('每 2 分钟')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        _assert_chart_accessibility(page, '.resource-history-chart')
        _capture(chart, f'resource-range-{theme}-{width}')
        assert errors == [], errors
    finally:
        context.close()


@pytest.mark.parametrize('state,title', [
    ('empty', '所选时段暂无采样'),
    ('unavailable', '暂时无法读取监控数据'),
    ('partial', '此指标暂无有效采样'),
])
def test_resource_empty_failure_and_missing_are_not_zero(browser_site, state, title):
    context, base = _context(browser_site)
    try:
        value = {**_history(), 'state': state, 'points': [], 'latest_at': None}
        if state == 'partial':
            value['points'] = [{'at': END, 'cpu': None, 'memory': 50, 'disk': 30}]
        page = context.new_page()
        page.route('**/admin/operations/history?*', lambda route: route.fulfill(json=value))
        page.goto(base + '/admin/operations', wait_until='networkidle')
        chart = page.locator('.resource-history-chart')
        expect(chart.locator('.resource-empty > b')).to_have_text(title)
        expect(chart.locator('.resource-series button').first.locator('b')).to_have_text('—')
        expect(chart.get_by_role('slider')).to_have_count(0)
        _capture(chart, f'resource-state-{state}')
    finally:
        context.close()


def test_resource_loading_is_replaced_by_real_history(browser_site):
    context, base = _context(browser_site)
    pending = []
    try:
        page = context.new_page()
        page.route('**/admin/operations/history?*', lambda route: pending.append(route))
        page.goto(base + '/admin/operations', wait_until='domcontentloaded')
        chart = page.locator('.resource-history-chart')
        expect(chart.locator('.resource-empty > b')).to_have_text('正在读取监控数据')
        expect(chart.locator('.resource-series button').first.locator('small')).to_contain_text('正在读取采样')
        assert pending
        pending.pop(0).fulfill(json=_history())
        expect(chart.get_by_role('slider', name='CPU采样时间')).to_be_visible()
        expect(chart.locator('.resource-empty')).to_have_count(0)
    finally:
        context.close()


@pytest.mark.parametrize('width', [360, 1440])
@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_delivery_chart_7_and_30_days_gap_tooltip_theme_and_touch(browser_site, width, theme):
    context, base = _context(browser_site, width, theme)
    try:
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def insights(route):
            days = int(parse_qs(urlparse(route.request.url).query).get('days', ['7'])[0])
            route.fulfill(json=_trend(days))

        page.route('**/api/admin/insights?*', insights)
        page.goto(base + '/admin/overview', wait_until='networkidle')
        chart = page.locator('.delivery-trend')
        slider = chart.get_by_role('slider', name='执行统计日期')
        expect(slider).to_be_visible()
        expect(chart.locator('.delivery-day')).to_have_count(7)
        expect(chart.locator('.delivery-day.is-missing')).to_have_count(1)
        expect(chart.locator('.delivery-chart-caption')).to_contain_text('11月4日 — 11月10日')
        expect(chart.locator('.delivery-legend')).to_contain_text('失败 / 待核实')
        expect(chart.locator('.delivery-legend')).to_contain_text('其他状态')
        slider.focus()
        slider.press('Home')
        expect(slider).to_have_attribute('aria-valuetext', '11月4日，共 0 次，已发出 0 次，失败或待核实 0 次，已跳过 0 次')
        slider.press('End')
        expect(slider).to_have_attribute('aria-valuetext', '11月10日，共 7 次，已发出 3 次，失败或待核实 2 次，已跳过 1 次，其他状态 1 次')
        slider.press('Escape')
        expect(chart.locator('.delivery-tooltip')).to_have_count(0)
        slider.scroll_into_view_if_needed()
        box = slider.bounding_box()
        before = chart.bounding_box()
        if width >= 700:
            slider.hover(position={'x': box['width'] * .5, 'y': box['height'] * .6})
            expect(chart.locator('.delivery-tooltip')).to_contain_text('统计未采集')
            expect(chart.locator('.delivery-tooltip')).to_contain_text('11月7日')
            assert chart.bounding_box()['height'] == before['height']
            assert chart.locator('[title]').count() == 0
            _capture(chart, f'delivery-7-gap-{theme}-{width}')
            page.mouse.move(4, 4)
            expect(chart.locator('.delivery-tooltip')).to_have_count(0)
        else:
            slider.tap(position={'x': box['width'] * (6.5 / 7), 'y': box['height'] * .6})
            expect(chart.locator('.delivery-tooltip')).to_contain_text('共 7 次执行')
            page.touchscreen.tap(4, 4)
            expect(chart.locator('.delivery-tooltip')).to_be_visible()
            _capture(chart, f'delivery-7-touch-{theme}-{width}')
            chart.get_by_role('button', name='取消锁定').tap()
            expect(chart.locator('.delivery-tooltip')).to_have_count(0)

        page.get_by_label('发送趋势时间范围', exact=True).select_option('30')
        expect(chart.locator('.delivery-day')).to_have_count(30)
        expect(chart.locator('.delivery-day.is-missing')).to_have_count(1)
        expect(chart.locator('.delivery-chart-caption')).to_contain_text('10月12日 — 11月10日')
        slider.focus()
        slider.press('End')
        expect(chart.locator('.delivery-tooltip')).to_contain_text('共 7 次执行')
        expect(chart.locator('.delivery-tooltip')).to_contain_text('失败 / 待核实')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        _assert_chart_accessibility(page, '.delivery-trend')
        _capture(chart, f'delivery-30-{theme}-{width}')
        assert errors == [], errors
    finally:
        context.close()


def test_delivery_all_zero_is_a_valid_chart_not_an_empty_or_error_state(browser_site):
    context, base = _context(browser_site, 360, 'light')
    try:
        page = context.new_page()
        page.route('**/api/admin/insights?*', lambda route: route.fulfill(json=_trend(zero=True, gap=False)))
        page.goto(base + '/admin/overview', wait_until='networkidle')
        chart = page.locator('.delivery-trend')
        expect(chart.get_by_role('slider')).to_be_visible()
        expect(chart.locator('.delivery-zero')).to_have_count(7)
        expect(chart.locator('.delivery-zero-note')).to_have_text('此时段暂无执行，已返回的 7 天统计均为 0 次。')
        expect(chart.locator('.delivery-empty')).to_have_count(0)
        _capture(chart, 'delivery-valid-zero-light-360')
    finally:
        context.close()


@pytest.mark.parametrize('outcome,title', [
    ('empty', '暂无执行统计'),
    ('error', '发送趋势读取失败'),
])
def test_delivery_missing_and_failed_reads_are_not_zero(browser_site, outcome, title):
    context, base = _context(browser_site)
    try:
        page = context.new_page()

        def response(route):
            if outcome == 'error':
                route.fulfill(status=503, json={'detail': 'isolated history read failure'})
            else:
                route.fulfill(json={'days': 7, 'trend': [], 'alerts': {}})

        page.route('**/api/admin/insights?*', response)
        page.goto(base + '/admin/overview', wait_until='networkidle')
        chart = page.locator('.delivery-trend')
        expect(chart.locator('.delivery-empty > b')).to_have_text(title)
        expect(chart.get_by_role('slider')).to_have_count(0)
        expect(chart.locator('.delivery-zero')).to_have_count(0)
        _capture(chart, f'delivery-state-{outcome}')
    finally:
        context.close()


def test_delivery_loading_resolves_to_the_server_statistics(browser_site):
    context, base = _context(browser_site)
    pending = []
    try:
        page = context.new_page()
        page.route('**/api/admin/insights?*', lambda route: pending.append(route))
        page.goto(base + '/admin/overview', wait_until='domcontentloaded')
        chart = page.locator('.delivery-trend')
        expect(chart.locator('.delivery-empty > b')).to_have_text('正在读取发送趋势')
        assert pending
        pending.pop(0).fulfill(json=_trend())
        expect(chart.locator('.delivery-day')).to_have_count(7)
        expect(chart.locator('.delivery-empty')).to_have_count(0)
        expect(chart).to_have_attribute('aria-busy', 'false')
    finally:
        context.close()
