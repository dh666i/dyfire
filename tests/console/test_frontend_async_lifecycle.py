"""Late UI responses must not restore retired sessions or overwrite drafts."""
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from tests.console.test_browser_acceptance import browser_site


@pytest.mark.parametrize("replacement", [False, True])
def test_late_verification_reply_cannot_reopen_or_replace_scan(browser_site, replacement):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    context.add_cookies([{"name": "spark_session", "value": tokens["user"], "url": base}])
    context.add_init_script("""(() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const result = await original(...args);
        if (String(args[0]).endsWith('/accounts/scan/lifecycle-1/interact')) {
          await new Promise(resolve => { window.releaseVerificationReply = resolve; });
          window.verificationReplyReleased = true;
        }
        return result;
      };
    })();""")
    states = {}
    try:
        page = context.new_page()
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        def start(route):
            scan_id = f"lifecycle-{len(states) + 1}"
            states[scan_id] = {"id": scan_id, "mode": "recovery", "status": "confirming",
                               "remaining_seconds": 180, "needs_verification": True,
                               "verification_kind": "password", "frame_revision": "fixture"}
            route.fulfill(json=states[scan_id])

        def scan(route):
            path = route.request.url.split("?", 1)[0]
            scan_id = path.split("/accounts/scan/", 1)[1].split("/", 1)[0]
            if path.endswith("/qr"):
                route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><rect width="1280" height="720" fill="white"/></svg>')
            elif path.endswith("/interact"):
                route.fulfill(json={"status": "delivered"})
            elif path.endswith("/cancel"):
                route.fulfill(json={**states[scan_id], "status": "cancelled"})
            else:
                route.fulfill(json=states[scan_id])

        page.route("**/accounts/recovery", start)
        page.route(re.compile(r"/accounts/scan/lifecycle-\d+(?:[/?].*)?$"), scan)
        page.goto(base + "/accounts", wait_until="networkidle")
        page.get_by_role("button", name="打开验证", exact=True).first.click()
        modal = page.get_by_role("dialog", name="复核原抖音账号", exact=True)
        modal.locator(".verification-field input").fill("isolated-password")
        modal.get_by_role("button", name="提交验证", exact=True).click()
        page.wait_for_function("() => typeof window.releaseVerificationReply === 'function'")
        modal.get_by_role("button", name="结束本次复核", exact=True).click()
        expect(modal).to_have_count(0)
        if replacement:
            page.get_by_role("button", name="打开验证", exact=True).first.click()
            expect(modal.locator('.scan-frame-image[src*="lifecycle-2"]')).to_have_count(1)
        page.evaluate("window.releaseVerificationReply()")
        page.wait_for_function("() => window.verificationReplyReleased === true")
        # Drain the fulfilled response's continuation; no platform request is made.
        page.wait_for_timeout(100)
        if replacement:
            expect(modal.locator('.scan-frame-image[src*="lifecycle-2"]')).to_have_count(1)
            expect(modal.locator('.scan-frame-image[src*="lifecycle-1"]')).to_have_count(0)
        else:
            expect(modal).to_have_count(0)
        assert page_errors == []
    finally:
        context.close()


def test_audit_pagination_exposes_older_records_and_search_resets_page(browser_site):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    context.add_cookies([{"name": "spark_session", "value": tokens["admin"], "url": base}])
    reads = []
    try:
        page = context.new_page()
        def audit(route):
            params = parse_qs(urlparse(route.request.url).query)
            page_number = int(params.get("page", ["1"])[0])
            query = params.get("q", [""])[0]
            reads.append((query, page_number))
            route.fulfill(json={"items": [{"id": f"audit-{page_number}", "event": f"审计页{page_number} {query}", "actor": "fixture", "outcome": "success"}],
                                "pagination": {"page": page_number, "pages": 2, "total": 2,
                                               "has_previous": page_number > 1, "has_next": page_number < 2}})
        page.route("**/api/admin/audit?*", audit)
        page.goto(base + "/admin/operations?view=audit", wait_until="networkidle")
        expect(page.locator(".audit-row")).to_contain_text("审计页1")
        page.get_by_role("button", name="下一页", exact=True).click()
        expect(page.locator(".audit-row")).to_contain_text("审计页2")
        page.get_by_placeholder("搜索操作、操作者、资源、请求 ID 或 IP").fill("登录")
        expect(page.locator(".audit-row")).to_contain_text("审计页1 登录")
        assert ("", 2) in reads and ("登录", 1) in reads
    finally:
        context.close()


@pytest.mark.parametrize("route", ["/admin/operations?view=announcements", "/admin/operations?view=business"])
def test_service_announcements_skip_financial_reads_and_keep_customer_lookup(browser_site, route):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    context.add_cookies([{"name": "spark_session", "value": tokens["admin"], "url": base}])
    calls = {"announcements": 0, "customers": 0, "payments": 0, "costs": 0}
    try:
        page = context.new_page()
        def announcements(request):
            calls["announcements"] += 1
            request.fulfill(json={"items": [{"id": "notice-1", "title": "维护通知", "body": "今晚维护", "user_id": None, "withdrawn": False, "expires_at": "2026-11-20T10:00:00Z"}]})
        def customers(request):
            calls["customers"] += 1
            request.fulfill(json={"items": [{"id": "customer-1", "username": "定向客户"}], "pagination": {"page": 1, "total": 1, "has_previous": False, "has_next": False}})
        page.route("**/api/admin/announcements", announcements)
        page.route("**/api/admin/customer-options**", customers)
        page.route("**/api/admin/payments**", lambda request: (calls.__setitem__("payments", calls["payments"] + 1), request.fulfill(json={"items": [], "pagination": {}})))
        page.route("**/api/admin/costs**", lambda request: (calls.__setitem__("costs", calls["costs"] + 1), request.fulfill(json={})))
        page.goto(base + route, wait_until="networkidle")
        expect(page.locator(".fire-main h1").first).to_have_text("服务公告")
        expect(page.get_by_role("heading", name="发布公告", exact=True)).to_be_visible()
        expect(page.get_by_placeholder("按用户名查找公告接收对象")).to_be_visible()
        with page.expect_response(lambda response: "/api/admin/customer-options" in response.url):
            page.get_by_placeholder("按用户名查找公告接收对象").fill("定向")
        assert calls["customers"] >= 1
        expect(page.get_by_text("维护通知", exact=True)).to_be_visible()
        assert calls["announcements"] == 1
        assert calls["customers"] >= 1
        assert calls["payments"] == calls["costs"] == 0
    finally:
        context.close()


def test_late_user_toggle_refreshes_current_filter_instead_of_captured_filter(browser_site):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    context.add_cookies([{"name": "spark_session", "value": tokens["admin"], "url": base}])
    context.add_init_script("""(() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const result = await original(...args);
        if (String(args[0]).endsWith('/toggle')) await new Promise(resolve => { window.releaseUserToggle = resolve; });
        return result;
      };
    })();""")
    reads = []
    try:
        initial = context.request.get(base + "/api/admin/bootstrap").json()
        template = next(user for user in initial["users"] if user["role"] != "admin")
        page = context.new_page()
        def users(route):
            query = parse_qs(urlparse(route.request.url).query).get("q", [""])[0]
            reads.append(query)
            row = {**template, "id": "other-user" if query else template["id"], "username": "other-match" if query else "original-user", "status": "active"}
            route.fulfill(json={**initial, "users": [row], "usersPagination": {"page": 1, "pages": 1, "total": 1, "has_previous": False, "has_next": False}})
        page.route("**/api/admin/bootstrap?*", users)
        page.route("**/api/admin/users/*/toggle", lambda route: route.fulfill(json={"ok": True}))
        page.goto(base + "/admin/users", wait_until="networkidle")
        page.get_by_role("button", name="original-user 更多操作", exact=True).click()
        page.get_by_role("menuitem", name="停用用户", exact=True).click()
        page.wait_for_function("() => typeof window.releaseUserToggle === 'function'")
        page.get_by_placeholder("搜索用户名或备注").fill("other")
        expect(page.locator(".admin-user-row")).to_contain_text("other-match")
        initial_blank_reads = reads.count("")
        page.evaluate("window.releaseUserToggle()")
        page.wait_for_timeout(150)
        expect(page.locator(".admin-user-row")).to_contain_text("other-match")
        assert reads.count("") == initial_blank_reads
    finally:
        context.close()


@pytest.mark.parametrize("edit_while_loading", [False, True])
def test_mail_settings_hydrate_preserve_draft_and_keep_saved_disabled_state(browser_site, edit_while_loading):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    context.add_cookies([{"name": "spark_session", "value": tokens["admin"], "url": base}])
    context.add_init_script("""(() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const result = await original(...args);
        if (String(args[0]) === '/api/admin/mail') await new Promise(resolve => { window.releaseMailSettings = resolve; });
        return result;
      };
    })();""")
    config = {"host": "smtp.fixture.invalid", "port": 587, "mode": "starttls",
              "username": "fixture-user", "sender": "sender@fixture.invalid",
              "sender_name": "Fixture Sender", "enabled": True,
              "password_configured": True, "events": ["account_alert"], "deliveries": []}
    writes = []
    try:
        page = context.new_page()
        page.route("**/api/admin/mail", lambda route: route.fulfill(json=config))
        def save(route):
            writes.append(json.loads(route.request.post_data))
            route.fulfill(json={"message": "保存成功"})
        page.route("**/admin/settings/mail/save", save)
        page.goto(base + "/admin/settings/mail", wait_until="domcontentloaded")
        host = page.get_by_label("SMTP 主机", exact=True)
        expect(host).to_be_visible()
        page.wait_for_function("() => typeof window.releaseMailSettings === 'function'")
        if edit_while_loading:
            host.fill("draft.fixture.invalid")
        page.evaluate("window.releaseMailSettings()")
        expect(host).to_have_value("draft.fixture.invalid" if edit_while_loading else config["host"])
        expect(page.get_by_label("SMTP 用户名", exact=True)).to_have_value(config["username"])
        expect(page.get_by_label("发件邮箱", exact=True)).to_have_value(config["sender"])
        expect(page.get_by_role("switch", name="启用邮件通知服务")).to_be_checked()
        page.get_by_role("switch", name="启用邮件通知服务").uncheck()
        page.get_by_role("button", name="保存设置", exact=True).click()
        expect(page.get_by_text("保存成功", exact=True)).to_be_visible()
        assert writes[-1]["enabled"] is False
        assert writes[-1]["host"] == ("draft.fixture.invalid" if edit_while_loading else config["host"])
        page.get_by_role("radio", name="业务概览", exact=True).click()
        page.get_by_role("radio", name="系统设置", exact=True).click()
        mail_card = page.locator(".card").filter(has=page.get_by_role("heading", name="邮件服务", exact=True))
        expect(mail_card.get_by_text("未启用", exact=True)).to_be_visible()
    finally:
        context.close()
