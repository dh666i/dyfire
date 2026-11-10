"""Mobile geometry regressions against the isolated browser fixture.

The viewport event test simulates keyboard/zoom geometry in Chromium. It is
not evidence of native iOS software-keyboard or Android device validation.
"""
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from tests.console.test_browser_acceptance import (
    browser_site,
    _context_navigation_payload,
)


_VIEWPORT_SIMULATION = """(() => {
  const viewport = new EventTarget();
  Object.assign(viewport, {width:innerWidth, height:innerHeight, offsetTop:0,
    offsetLeft:0, pageTop:0, pageLeft:0, scale:1});
  Object.defineProperty(window, 'visualViewport', {configurable:true, value:viewport});
  window.setTestVisualViewport = value => {
    Object.assign(viewport, value);
    viewport.dispatchEvent(new Event('resize'));
    viewport.dispatchEvent(new Event('scroll'));
  };
})()"""


def _capture(page, name):
    output = Path('.artifacts/ui-mobile-quality/screenshots')
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / f'{name}.png'))


@pytest.mark.parametrize('width', [360, 430])
def test_editor_reveals_focused_field_without_scrolling_page_or_fighting_zoom(browser_site, width):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={'width':width, 'height':900},
        is_mobile=True, has_touch=True, reduced_motion='reduce')
    context.add_init_script(_VIEWPORT_SIMULATION)
    context.add_cookies([{'name':'spark_session', 'value':tokens['user'], 'url':base}])
    try:
        value = _context_navigation_payload(context, base)
        value['tasks'][0].update(kind='文字消息', message='保持原有消息，不提交修改')
        name = value['tasks'][0]['name']
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error:errors.append(str(error)))
        page.route('**/api/app/bootstrap', lambda route:route.fulfill(json=value))
        page.goto(base+'/studio', wait_until='networkidle')
        trigger = page.get_by_role('button', name=f'编辑 {name}', exact=True)
        trigger.click()
        dialog = page.get_by_role('dialog', name=f'编辑 {name}', exact=True)
        expect(dialog).to_be_visible()
        expect(page.get_by_role('navigation', name='移动端主导航')).to_be_hidden()
        field = dialog.get_by_placeholder('写一句日常问候')
        field.focus()
        document_scroll = page.evaluate('scrollY')
        page.evaluate('() => setTestVisualViewport({height:410, offsetTop:70, pageTop:70})')
        page.wait_for_function("""() => {
          const panel = document.querySelector('.drawer');
          const field = panel?.querySelector('textarea');
          if (!field || panel.parentElement.style.height !== '410px') return false;
          const r = field.getBoundingClientRect();
          return r.top >= 70 && r.bottom <= 480;
        }""")
        panel_box = dialog.bounding_box()
        close_box = dialog.get_by_role('button', name='关闭', exact=True).bounding_box()
        assert panel_box['y'] >= 69 and panel_box['y']+panel_box['height'] <= 481, panel_box
        assert close_box['y'] >= 70 and close_box['y']+close_box['height'] <= 480, close_box
        assert close_box['width'] >= 44 and close_box['height'] >= 44
        assert page.evaluate('scrollY') == document_scroll
        assert dialog.locator('.drawer-content').evaluate('(el) => el.scrollTop') > 0
        _capture(page, f'editor-keyboard-geometry-{width}')

        # Pinching changes viewport geometry too; do not shrink/recenter the
        # modal a second time or scroll away from the user's focused field.
        page.evaluate('() => setTestVisualViewport({scale:2, height:205, offsetTop:120})')
        page.wait_for_timeout(100)
        assert dialog.evaluate('(el) => el.parentElement.style.height') == '410px'
        assert dialog.evaluate('(el) => el.parentElement.style.top') == '70px'
        assert page.evaluate('scrollY') == document_scroll

        page.evaluate('() => setTestVisualViewport({scale:1, height:900, offsetTop:0, pageTop:0})')
        dialog.get_by_role('button', name='关闭', exact=True).click()
        expect(dialog).to_have_count(0)
        expect(trigger).to_be_focused()
        expect(page.get_by_role('navigation', name='移动端主导航')).to_be_visible()
        assert page.evaluate('scrollY') == document_scroll
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize('width', [360, 390])
def test_touch_filters_are_reachable_and_dock_yields_to_search(browser_site, width):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={'width':width, 'height':844},
        is_mobile=True, has_touch=True, reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session', 'value':tokens['user'], 'url':base}])
    try:
        page = context.new_page()
        reads = []
        def runs(route):
            status = parse_qs(urlparse(route.request.url).query).get('status', [''])[0]
            reads.append(status)
            route.fulfill(json={'items':[], 'total':0, 'page':1, 'pages':1})
        page.route('**/api/studio/runs?*', runs)
        page.goto(base+'/runs', wait_until='networkidle')
        filters = page.get_by_role('radiogroup', name='执行记录筛选', exact=True)
        for label, status in [('全部',''), ('已发出','success'), ('未发出','failed'),
                ('执行中','running'), ('待核实','submitted_unconfirmed')]:
            control = filters.get_by_role('radio', name=label, exact=True)
            box = control.bounding_box()
            assert box['height'] >= 44 and box['width'] >= 44, (label, box)
            assert box['x'] >= 0 and box['x']+box['width'] <= width+1, (label, box)
            control.tap()
            expect(control).to_be_checked()
            page.wait_for_function('(status) => new URL(location.href).searchParams.get("status") === (status || null)', arg=status)
            expect(page.locator('.runs-card')).to_have_attribute('aria-busy', 'false')
            assert reads[-1] == status, reads
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        dock = page.get_by_role('navigation', name='移动端主导航')
        expect(dock).to_be_visible()
        search = page.get_by_placeholder('按好友名搜索')
        search.focus()
        expect(dock).to_be_hidden()
        search.evaluate('(el) => el.blur()')
        expect(dock).to_be_visible()
        clear = page.locator('.runs-card').get_by_role('button', name='清除筛选', exact=True)
        clear.scroll_into_view_if_needed()
        clear_box, dock_box = clear.bounding_box(), dock.bounding_box()
        assert clear_box['y']+clear_box['height'] <= dock_box['y'], (clear_box, dock_box)
        _capture(page, f'touch-filters-{width}')
    finally:
        context.close()


@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_long_names_leave_mobile_editor_and_friend_actions_usable(browser_site, theme):
    browser, base, tokens = browser_site
    width = 360
    context = browser.new_context(viewport={'width':width, 'height':844},
        is_mobile=True, has_touch=True, reduced_motion='reduce')
    context.add_init_script(f"localStorage.setItem('dyfire-theme','{theme}')")
    context.add_cookies([{'name':'spark_session', 'value':tokens['user'], 'url':base}])
    try:
        value = _context_navigation_payload(context, base)
        name = '很长的好友名称用于检查换行与编辑操作ABCDEFG1234567890'*2
        value['tasks'][0]['name'] = name
        value['accounts'][0]['name'] = '账号名称也需要在窄屏正确换行ABCDEFG1234567890'*2
        page = context.new_page()
        page.route('**/api/app/bootstrap', lambda route:route.fulfill(json=value))
        page.route('**/accounts/*/conversations?*', lambda route:route.fulfill(json={
            'items':[{'name':name, 'sec_uid':'mobile-long-contact'}],
            'total':1, 'has_more':False, 'can_sync_more':False}))
        page.goto(base+'/studio', wait_until='networkidle')
        trigger = page.get_by_role('button', name=f'编辑 {name}', exact=True)
        trigger.click()
        dialog = page.get_by_role('dialog', name=f'编辑 {name}', exact=True)
        expect(dialog).to_be_visible()
        assert dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
        title_box = dialog.get_by_role('heading', name=f'编辑 {name}', exact=True).bounding_box()
        close = dialog.get_by_role('button', name='关闭', exact=True)
        close_box = close.bounding_box()
        assert title_box['x']+title_box['width'] <= close_box['x']+1, (title_box, close_box)
        save = dialog.get_by_role('button', name='保存任务', exact=True)
        save.scroll_into_view_if_needed()
        expect(save).to_be_in_viewport()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        close.click()
        expect(dialog).to_have_count(0)

        page.locator('.friend-trigger').click()
        picker = page.get_by_role('dialog', name='选择目标好友', exact=True)
        checkbox = picker.get_by_role('checkbox')
        expect(checkbox).to_be_visible()
        checkbox.check()
        done = picker.get_by_role('button', name='完成选择', exact=True)
        expect(done).to_be_enabled()
        expect(done).to_be_in_viewport()
        assert picker.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        _capture(page, f'long-names-{theme}')
        done.click()
        expect(picker).to_have_count(0)
        expect(page.locator('.friend-trigger')).to_contain_text(name)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    finally:
        context.close()
