"""Task menus use real Chromium and isolated API responses, never Douyin."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.console.test_browser_acceptance import browser_site


@pytest.mark.parametrize('width', [390, 1440])
@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_task_menu_escapes_animated_rows_and_keeps_probe_feedback(browser_site, width, theme):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={'width': width, 'height': 900})
    context.add_cookies([{'name': 'spark_session', 'value': tokens['user'], 'url': base}])
    context.add_init_script(f"localStorage.setItem('dyfire-theme', '{theme}')")
    try:
        value = context.request.get(base + '/api/app/bootstrap').json()
        template = value['tasks'][0]
        value['tasks'] = [{**template, 'id': f'overlay-task-{index}', 'name': f'菜单好友 {index}'} for index in range(6)]
        value['tasks_pagination'].update(total=6, has_next=False)
        value['resource_limits'].update(tasks_total=6, tasks_enabled_total=6, tasks_paused_total=0)
        page = context.new_page()
        reads, starts, errors, deletes = [], [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: deletes.append(request.url) if request.method == 'DELETE' else None)
        page.route('**/api/app/bootstrap', lambda route: route.fulfill(json=value))
        def checks(route):
            reads.append(route.request.url)
            route.fulfill(json={'items': []})
        page.route('**/api/tasks/*/checks', checks)
        probe = {'id': 'overlay-probe', 'status': 'queued', 'status_url': '/api/isolated-menu-probe'}
        def start(route):
            starts.append(route.request.url)
            route.fulfill(json=probe)
        page.route('**/api/tasks/*/self-check', start)
        page.route('**/api/isolated-menu-probe', lambda route: route.fulfill(json={**probe, 'status': 'completed', 'result': {'summary': '好友窗口可用，未发送消息'}}))
        page.goto(base + '/studio', wait_until='networkidle')
        assert reads == [], 'Closed task menus must not create a per-row checks request'
        trigger = page.get_by_role('button', name='菜单好友 0 更多操作', exact=True)
        trigger.scroll_into_view_if_needed()
        trigger.click()
        menu = page.locator('.row-menu-popup[data-open]')
        expect(menu).to_be_visible()
        expect(menu).to_have_css('opacity', '1')
        output = Path('.artifacts/task-menu-overlay'); output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / f'menu-{theme}-{width}.png'))
        assert menu.evaluate("el=>!el.closest('.task-row,.admin-row,.product-row')"), 'Menu is trapped inside an animated row'
        for item in menu.get_by_role('menuitem').all():
            assert item.evaluate("el=>{const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}"), 'Next-row controls cover a menu item'
        bounds = menu.bounding_box()
        assert 8 <= bounds['x'] and bounds['x'] + bounds['width'] <= width - 8
        dock = page.locator('.mobile-dock')
        if dock.is_visible():
            assert bounds['y'] + bounds['height'] <= dock.bounding_box()['y'] - 8
        selfcheck = menu.get_by_role('menuitem', name='运行前自检', exact=True)
        selfcheck.click()
        expect(menu.get_by_role('status')).to_contain_text('等待账号空闲')
        page.keyboard.press('Escape')
        expect(menu).to_be_hidden()
        expect(trigger).to_be_focused()
        page.wait_for_timeout(700)
        trigger.click()
        expect(menu.get_by_role('status')).to_contain_text('好友窗口可用，未发送消息')
        assert len(reads) == 1 and len(starts) == 1, 'Closing a menu must not restart its probe or lose its result'
        page.keyboard.press('Escape')
        trigger.press('ArrowDown')
        expect(menu.get_by_role('menuitem', name='运行前自检', exact=True)).to_be_focused()
        page.keyboard.press('ArrowDown')
        expect(menu.get_by_role('menuitem', name='删除任务', exact=True)).to_be_focused()
        page.keyboard.press('Escape')
        expect(trigger).to_be_focused()
        trigger.click()
        menu.get_by_role('menuitem', name='删除任务', exact=True).click()
        confirmation = page.get_by_role('dialog', name='删除续火任务', exact=True)
        expect(confirmation).to_be_visible()
        expect(confirmation).to_contain_text('菜单好友 0')
        expect(page.get_by_role('dialog')).to_have_count(1)
        expect(menu).to_be_hidden()
        confirmation.get_by_role('button', name='取消', exact=True).click()
        expect(confirmation).to_be_hidden()
        assert deletes == [] and page.locator('.task-row').count() == 6
        trigger.click()
        page.get_by_role('heading', name='续火任务', exact=True).click()
        expect(menu).to_be_hidden()
        page.set_viewport_size({'width': width, 'height': 700})
        trigger.click()
        page.mouse.move(40, 300)
        page.mouse.wheel(0, 100)
        expect(menu).to_be_hidden()
        page.set_viewport_size({'width': width, 'height': 900})
        if width < 700:
            # Put an anchor just above the mobile dock; the popup must flip
            # upward instead of covering navigation or escaping the viewport.
            edge = page.get_by_role('button', name='菜单好友 4 更多操作', exact=True)
            edge.evaluate("el=>{const dock=document.querySelector('.mobile-dock').getBoundingClientRect();const r=el.getBoundingClientRect();let parent=el.parentElement;while(parent&&!(parent.scrollHeight>parent.clientHeight+1&&/(auto|scroll)/.test(getComputedStyle(parent).overflowY)))parent=parent.parentElement;const delta=r.top-(dock.top-r.height-12);if(parent)parent.scrollBy({top:delta,behavior:'instant'});else window.scrollBy({top:delta,behavior:'instant'})}")
            before = edge.bounding_box()
            page.mouse.click(before['x'] + before['width'] / 2, before['y'] + before['height'] / 2)
            expect(menu).to_be_visible()
            expect(menu).to_have_css('opacity', '1')
            bounds = menu.bounding_box(); anchor = edge.bounding_box()
            assert bounds['y'] + bounds['height'] <= anchor['y'] + 1, (bounds, anchor, before, dock.bounding_box())
            assert bounds['y'] >= 10
            page.screenshot(path=str(output / f'menu-dock-{theme}-{width}.png'))
            page.keyboard.press('Escape')
        assert errors == []
    finally:
        context.close()
