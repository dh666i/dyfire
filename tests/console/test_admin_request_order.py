"""Out-of-order administrator reads cannot replace the selected customer.

Only the isolated browser fixture is used. The fetch harness deliberately lets
an aborted read resolve, exercising request identity as well as cancellation.
No management writes, Douyin workers, or external services are involved.
"""
import re

from playwright.sync_api import expect

from tests.console.test_browser_acceptance import browser_site


_DEFER_ADMIN_READS = """(() => {
  const original = window.fetch.bind(window);
  const harness = window.__adminReadHarness = {
    requests: [],
    release(index, payload) {
      const entry = this.requests[index];
      if (!entry || entry.released) throw new Error('Read was not pending');
      entry.released = true;
      entry.resolve(new Response(JSON.stringify(payload), {
        status: 200, headers: {'Content-Type': 'application/json'}
      }));
    }
  };
  window.fetch = (input, options = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (/^\\/api\\/admin\\/(users\\/[^/]+\\/workspace|audit\\/[^/]+)$/.test(url.pathname)) {
      return new Promise(resolve => harness.requests.push({
        path: url.pathname, signal: options.signal, resolve, released: false
      }));
    }
    return original(input, options);
  };
})()"""


def _admin_context(browser, base, tokens):
    context = browser.new_context(
        viewport={'width': 1440, 'height': 900}, reduced_motion='reduce'
    )
    context.add_cookies([{'name': 'spark_session', 'value': tokens['admin'], 'url': base}])
    context.add_init_script(_DEFER_ADMIN_READS)
    return context


def _workspace_payloads(context, base):
    response = context.request.get(base + '/api/admin/bootstrap')
    assert response.ok
    users = {user['username']: user for user in response.json()['users']}
    payloads = {}
    for name in ('friend', 'workspace-check'):
        response = context.request.get(base + f'/api/admin/users/{users[name]["id"]}/workspace')
        assert response.ok
        payloads[name] = response.json()
    return payloads


def _open_user(page, name):
    page.locator('.user-workbench-row').filter(has_text=name).get_by_role(
        'button', name='编辑', exact=True
    ).click()


def _wait_reads(page, count):
    page.wait_for_function('count => window.__adminReadHarness.requests.length === count', arg=count)


def _release(page, index, payload):
    page.evaluate(
        'args => window.__adminReadHarness.release(args.index, args.payload)',
        {'index': index, 'payload': payload},
    )
    # Flush the resolved fetch, React update and next paint without a sleep.
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')


def test_last_selected_customer_wins_when_previous_read_arrives_late(browser_site):
    browser, base, tokens = browser_site
    context = _admin_context(browser, base, tokens)
    try:
        payloads = _workspace_payloads(context, base)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base + '/admin/users', wait_until='networkidle')
        _open_user(page, 'friend')
        _wait_reads(page, 1)
        _open_user(page, 'workspace-check')
        _wait_reads(page, 2)
        _release(page, 1, payloads['workspace-check'])
        workspace = page.get_by_role('dialog', name=re.compile('workspace-check'))
        expect(workspace).to_be_visible()
        expect(workspace.get_by_label('管理员备注')).to_be_visible()
        _release(page, 0, payloads['friend'])
        expect(workspace).to_be_visible()
        expect(page.get_by_role('dialog', name=re.compile('friend'))).to_have_count(0)
        assert not errors, errors
    finally:
        context.close()


def test_closed_customer_workspace_stays_closed_after_an_older_read(browser_site):
    browser, base, tokens = browser_site
    context = _admin_context(browser, base, tokens)
    try:
        payloads = _workspace_payloads(context, base)
        page = context.new_page()
        page.goto(base + '/admin/users', wait_until='networkidle')
        _open_user(page, 'friend')
        _wait_reads(page, 1)
        _open_user(page, 'workspace-check')
        _wait_reads(page, 2)
        _release(page, 1, payloads['workspace-check'])
        workspace = page.get_by_role('dialog', name=re.compile('workspace-check'))
        expect(workspace).to_be_visible()
        workspace.get_by_role('button', name='关闭', exact=True).click()
        expect(page.get_by_role('dialog')).to_have_count(0)
        _release(page, 0, payloads['friend'])
        expect(page.get_by_role('dialog')).to_have_count(0)
    finally:
        context.close()


def test_audit_details_ignore_a_late_response_for_the_previous_event(browser_site):
    browser, base, tokens = browser_site
    context = _admin_context(browser, base, tokens)
    try:
        page = context.new_page()
        events = [
            {'id': 'audit-first', 'event': '审计事件甲', 'actor': '甲操作者',
             'subject_type': 'user', 'subject_id': '甲用户', 'outcome': 'success'},
            {'id': 'audit-second', 'event': '审计事件乙', 'actor': '乙操作者',
             'subject_type': 'user', 'subject_id': '乙用户', 'outcome': 'success'},
        ]
        page.route('**/api/admin/audit?*', lambda route: route.fulfill(json={'items': events}))
        page.goto(base + '/admin/operations?view=audit', wait_until='networkidle')
        page.locator('.audit-row').filter(has_text='审计事件甲').click()
        _wait_reads(page, 1)
        drawer = page.get_by_role('dialog', name='审计详情', exact=True)
        expect(drawer).to_be_visible()
        drawer.get_by_role('button', name='关闭', exact=True).click()
        expect(drawer).to_have_count(0)
        page.locator('.audit-row').filter(has_text='审计事件乙').click()
        _wait_reads(page, 2)
        _release(page, 1, {**events[1], 'detail': '乙事件的完整详情'})
        expect(drawer).to_contain_text('乙事件的完整详情')
        _release(page, 0, {**events[0], 'detail': '甲事件的迟到详情'})
        expect(drawer).to_contain_text('乙事件的完整详情')
        expect(drawer).not_to_contain_text('甲事件的迟到详情')
    finally:
        context.close()
