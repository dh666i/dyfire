"""Real touch activation of the shared select, without native picker defaults."""
import os
import json
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.console.test_browser_acceptance import browser_site


@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
def test_touch_and_keyboard_use_only_the_shared_listbox(engine):
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, engine)
        if not Path(browser_type.executable_path).is_file():
            if os.environ.get('DYFIRE_REQUIRE_BROWSER') == '1':
                pytest.fail(f'{engine} is required in CI')
            pytest.skip(f'{engine} is not installed')
        browser = browser_type.launch(headless=True)
        context = browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True,
                                      is_mobile=True, reduced_motion='reduce')
        try:
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_content('''<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body style="padding:24px">
                <label for="status">用户状态筛选</label>
                <select id="status" style="display:block;width:290px;height:44px;margin:12px 0">
                  <option value="">全部状态</option><option value="active">正常</option>
                  <option value="disabled">已停用</option><option disabled>不可选</option>
                </select><button id="outside">其他操作</button>
                <select id="disabled" disabled><option>禁用控件</option></select>
                <select id="multiple" multiple><option>A</option><option>B</option></select>
                </body></html>''')
            source = Path('frontend/src/select-overlay.js').read_text(encoding='utf-8')
            page.add_script_tag(content=source.replace('export function ', 'function ', 1)
                                + '\nwindow.testSelectOverlay=installSelectOverlay(window);')
            page.add_style_tag(content='''.fire-select-menu{position:fixed;overflow:auto;
                background:#fff;border:1px solid #ddd;z-index:999;padding:6px}
                .fire-select-option{display:flex;min-height:44px;width:100%}
                .fire-select-option.is-active{background:#fdd}''')
            page.evaluate('''() => {
                window.events=[]; window.changes=[]; window.selectTrace=[];
                const select=document.querySelector('#status');
                for(const type of ['pointerdown','touchstart','touchend','mousedown','click'])
                  select.addEventListener(type,event=>events.push({type,cancelled:event.defaultPrevented}));
                select.addEventListener('change',()=>changes.push(select.value));
                for(const type of ['pointerdown','pointerenter','pointermove','touchstart','touchend',
                                   'click','keydown','keyup','focus','blur','change'])
                  document.addEventListener(type,event=>{
                    if(event.target===select||event.target.closest?.('.fire-select-menu'))
                      selectTrace.push({type,key:event.key,pointer:event.pointerType,
                        target:event.target.id||event.target.tagName,cancelled:event.defaultPrevented,
                        value:select.value,highlight:select.getAttribute('aria-activedescendant'),
                        focused:document.activeElement?.id});
                  },true);
            }''')
            control = page.get_by_role('combobox', name='用户状态筛选')
            control.tap()
            expect(page.get_by_role('listbox')).to_have_count(2)  # custom list + native multiple
            menu = page.locator('.fire-select-menu')
            expect(menu).to_be_visible()
            assert menu.evaluate('(el)=>getComputedStyle(el).position') == 'fixed'
            assert menu.bounding_box()['y'] >= control.bounding_box()['y'] + control.bounding_box()['height']
            events = page.evaluate('events')
            assert any(event['type'] == 'touchstart' for event in events), events
            assert all(event['cancelled'] for event in events), events
            assert page.evaluate("!CSS.supports('selector(select:open)') || !document.querySelector('#status').matches(':open')")
            normal_option = menu.get_by_role('option', name='正常', exact=True)
            normal_box = normal_option.bounding_box()
            normal_option.tap()
            expect(control).to_have_value('active')
            expect(menu).to_have_count(0)
            assert page.evaluate('changes') == ['active']
            assert control.evaluate('(el)=>document.activeElement===el')
            control.tap(); expect(menu).to_be_visible()
            control.tap(); expect(menu).to_have_count(0)
            # Keyboard and screen-reader click activation still use the same popup.
            # Keep the physical pointer where the old option was: rebuilding
            # the menu must not let a synthetic enter replace ArrowDown's item.
            page.mouse.move(normal_box['x'] + normal_box['width'] / 2,
                            normal_box['y'] + normal_box['height'] / 2)
            control.press('ArrowDown')
            expect(menu).to_be_visible()
            expect(menu.get_by_role('option', name='已停用', exact=True)).to_have_class('fire-select-option is-active')
            control.press('Enter')
            expect(control).to_have_value('disabled')
            assert page.evaluate('changes') == ['active', 'disabled']
            control.evaluate('(el)=>el.click()')
            expect(menu).to_be_visible()
            control.press('Escape'); expect(menu).to_have_count(0)
            # Disabled and multiple controls keep their native semantics.
            assert page.locator('#disabled').evaluate('''el=>{
                const event=new Event('touchstart',{bubbles:true,cancelable:true});
                el.dispatchEvent(event);return !event.defaultPrevented;
            }''')
            assert page.locator('#multiple').evaluate('''el=>{
                const event=new Event('touchstart',{bubbles:true,cancelable:true});
                el.dispatchEvent(event);return !event.defaultPrevented;
            }''')
            assert not errors, errors
        except Exception:
            output = Path('.artifacts/select-touch'); output.mkdir(parents=True, exist_ok=True)
            (output / f'{engine}-failure-events.json').write_text(
                json.dumps(page.evaluate('window.selectTrace || []'), ensure_ascii=False, indent=2), encoding='utf-8')
            page.screenshot(path=str(output / f'{engine}-failure.png'))
            raise
        finally:
            context.close(); browser.close()


def test_mobile_admin_filter_tap_commits_once_without_native_activation(browser_site):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True,
                                  is_mobile=True, reduced_motion='reduce')
    context.add_cookies([{'name': 'spark_session', 'value': tokens['admin'], 'url': base}])
    try:
        page = context.new_page()
        page.goto(base + '/admin/users', wait_until='domcontentloaded')
        control = page.get_by_role('combobox', name='用户状态筛选')
        expect(control).to_be_visible()
        page.evaluate('''() => {
            window.filterEvents=[];
            const select=document.querySelector('select[aria-label="用户状态筛选"]');
            for(const type of ['touchstart','touchend','click'])
              select.addEventListener(type,event=>filterEvents.push({type,cancelled:event.defaultPrevented}));
        }''')
        control.tap()
        menu = page.locator('.fire-select-menu')
        expect(menu).to_be_visible()
        expect(menu).to_have_count(1)
        events = page.evaluate('filterEvents')
        assert events and all(event['cancelled'] for event in events), events
        output = Path('.artifacts/select-touch'); output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / 'admin-users-touch-menu.png'))
        menu.get_by_role('option', name='正常', exact=True).tap()
        expect(control).to_have_value('active')
        expect(menu).to_have_count(0)
        expect(page.locator('.filter-summary')).to_contain_text('正常')
    finally:
        context.close()
