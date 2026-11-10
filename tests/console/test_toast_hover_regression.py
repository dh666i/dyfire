"""Real notification behavior, with isolated mocked writes and full animation."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.console.test_browser_acceptance import browser_site, _context_navigation_payload


_EVENT_RECORDING = """(() => {
  window.toastEvents = [];
  for (const type of ['pointerover','pointerout','pointermove','focusin','focusout']) {
    document.addEventListener(type, event => {
      const toast = document.querySelector('[data-sonner-toast]');
      if (!toast) return;
      const target = event.target;
      const box = toast.getBoundingClientRect();
      window.toastEvents.push({type, at:performance.now(), x:event.clientX,
        y:event.clientY, target:target.tagName + '.' + String(target.className),
        inside:toast.contains(target), expanded:toast.dataset.expanded,
        removed:toast.dataset.removed, focused:toast.contains(document.activeElement),
        rect:{x:box.x,y:box.y,width:box.width,height:box.height}});
    }, true);
  }
})()"""


def _open_saved_notification(browser_site, *, theme='dark'):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={'width':1440, 'height':900})
    context.add_init_script(f"localStorage.setItem('dyfire-theme','{theme}')")
    context.add_init_script(_EVENT_RECORDING)
    context.add_cookies([{'name':'spark_session', 'value':tokens['user'], 'url':base}])
    value = _context_navigation_payload(context, base)
    task = value['tasks'][0]
    writes = []
    page = context.new_page()
    page.route('**/api/app/bootstrap', lambda route:route.fulfill(json=value))
    def save(route):
        writes.append(route.request.url)
        task['time'] = '13:15'
        route.fulfill(json={'ok':True})
    page.route(f'**/tasks/{task["id"]}/edit', save)
    page.goto(base+'/studio', wait_until='networkidle')
    trigger = page.get_by_role('button', name=f'编辑 {task["name"]}', exact=True)
    trigger.click()
    editor = page.get_by_role('dialog', name=f'编辑 {task["name"]}', exact=True)
    editor.locator('input[type=time]').fill('13:15')
    editor.get_by_role('button', name='保存任务', exact=True).click()
    toast = page.locator('[data-sonner-toast]').filter(has_text='任务已保存')
    expect(toast).to_be_visible()
    return context, page, toast, trigger, writes


def _save_evidence(page, name):
    output = Path('.artifacts/ui-toast-hover')
    output.mkdir(parents=True, exist_ok=True)
    (output/f'{name}.json').write_text(json.dumps(page.evaluate('toastEvents'),
        ensure_ascii=False, indent=2), encoding='utf-8')
    page.screenshot(path=str(output/f'{name}.png'))


@pytest.mark.parametrize('position', ['center', 'left', 'right', 'top', 'bottom'])
def test_stationary_pointer_pauses_single_notification_at_each_edge(browser_site, position):
    context, page, toast, trigger, writes = _open_saved_notification(browser_site,
        theme='light' if position in ('top','right') else 'dark')
    try:
        box = toast.bounding_box()
        locations = {
            'center':{'x':box['width']/2, 'y':box['height']/2},
            'left':{'x':8, 'y':box['height']/2},
            'right':{'x':box['width']-8, 'y':box['height']/2},
            'top':{'x':box['width']/2, 'y':8},
            'bottom':{'x':box['width']/2, 'y':box['height']-8},
        }
        toast.hover(position=locations[position])
        assert toast.evaluate('(el) => el.matches(":hover")')
        # A closing drawer restores focus while a single toast is under the
        # pointer. Focus moving elsewhere must not cancel pointer-based pause.
        trigger.evaluate('(el) => el.focus({preventScroll:true})')
        page.wait_for_timeout(4400)
        expect(toast).to_be_visible()
        assert toast.evaluate('(el) => el.matches(":hover")')
        expect(toast.get_by_role('button', name='关闭提示', exact=True)).to_be_visible()
        assert len(writes) == 1
        toast.get_by_role('button', name='关闭提示', exact=True).click()
        expect(toast).to_have_count(0)
    finally:
        _save_evidence(page, 'stationary-'+position)
        context.close()


def test_keyboard_focus_pauses_notification_until_the_user_dismisses_it(browser_site):
    context, page, toast, trigger, writes = _open_saved_notification(browser_site)
    try:
        expect(page.get_by_role('dialog')).to_have_count(0)
        page.mouse.move(20, 100)
        trigger.focus()
        close = toast.get_by_role('button', name='关闭提示', exact=True)
        close.focus()
        expect(close).to_be_focused()
        page.wait_for_timeout(4400)
        expect(toast).to_be_visible()
        expect(close).to_be_focused()
        close.press('Enter')
        expect(toast).to_have_count(0)
        expect(trigger).to_be_focused()
        assert len(writes) == 1
    finally:
        _save_evidence(page, 'keyboard-focus')
        context.close()


def test_notification_resumes_auto_close_after_pointer_leaves(browser_site):
    context, page, toast, _trigger, writes = _open_saved_notification(browser_site)
    try:
        toast.hover()
        page.wait_for_timeout(4400)
        expect(toast).to_be_visible()
        page.mouse.move(20, 100)
        # Pausing must not accidentally turn a timed notification permanent.
        expect(toast).to_have_count(0, timeout=6000)
        expect(page.locator('[data-sonner-toaster]')).to_have_count(0)
        assert len(writes) == 1
    finally:
        _save_evidence(page, 'resume-auto-close')
        context.close()
