"""Redemption refreshes shared entitlements without repeating acknowledged writes."""
import pytest
from playwright.sync_api import expect

from tests.console.test_browser_acceptance import browser_site


@pytest.mark.parametrize("refresh_fails", [False, True])
def test_redemption_refreshes_account_entitlement_once(browser_site, refresh_fails):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    context.add_cookies([{"name": "spark_session", "value": tokens["user"], "url": base}])
    try:
        value = context.request.get(base + "/api/app/bootstrap").json()
        before = {**value["membership"], "active": False, "free_tier": True,
                  "can_add_account": False, "can_add_task": False, "max_accounts": 1, "max_tasks": 1}
        after = {**before, "active": True, "free_tier": False,
                 "can_add_account": True, "can_add_task": True, "max_accounts": 2, "max_tasks": 10}
        reads, writes = [], []
        page = context.new_page()

        def bootstrap(route):
            reads.append(route.request.url)
            if len(reads) > 1 and refresh_fails:
                route.fulfill(status=503, json={"detail": "隔离验收：额度读取暂时失败"})
            else:
                route.fulfill(json={**value, "membership": after if writes else before})

        def redeem(route):
            writes.append(route.request.post_data_json)
            route.fulfill(json={"ok": True, "message": "兑换成功，订阅额度已更新。"})

        page.route("**/api/app/bootstrap", bootstrap)
        page.route("**/api/subscription", lambda route: route.fulfill(json={
            "membership": after if writes else before, "csrf": value["csrf"], "plans": [], "grants": [],
        }))
        page.route("**/subscription/preview", lambda route: route.fulfill(json={"preview": {
            "duration_days": 30, "max_accounts": 2, "max_tasks": 10,
            "quota_effect": "immediate", "valid_until": "2027-01-01T00:00:00Z",
        }}))
        page.route("**/api/subscription/redeem", redeem)
        page.goto(base + "/subscription", wait_until="networkidle")
        page.get_by_label("兑换码", exact=True).fill("ISOLATED-CODE")
        page.get_by_role("button", name="检查兑换码", exact=True).click()
        page.get_by_role("button", name="确认兑换", exact=True).click()
        expect(page.get_by_label("兑换码", exact=True)).to_have_value("")
        expect(page.get_by_text("兑换成功，订阅额度已更新。", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="检查兑换码", exact=True)).to_be_disabled()
        if refresh_fails:
            expect(page.locator(".refresh-notice")).to_contain_text("额度读取暂时失败")
        else:
            # Client navigation must see the new quota, before the 60-second poll.
            page.get_by_role("radio", name="抖音账号", exact=True).click()
            expect(page.get_by_role("button", name="绑定抖音账号", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="查看订阅额度", exact=True)).to_have_count(0)
        assert len(reads) == 2
        assert len(writes) == 1
    finally:
        context.close()


def test_editing_code_invalidates_late_preview_and_submits_displayed_code(browser_site):
    browser, base, tokens = browser_site
    context = browser.new_context(reduced_motion="reduce")
    context.add_cookies([{"name": "spark_session", "value": tokens["user"], "url": base}])
    try:
        page = context.new_page()
        # Deliver the first reply late even if the transport has already been aborted.
        page.add_init_script("""{
          const realFetch = window.fetch.bind(window);
          window.fetch = (url, opts) => {
            if (String(url).endsWith('/subscription/preview') && opts.body.get('code') === 'FIRST')
              return new Promise(resolve => { window.finishFirstPreview = () => resolve(
                Response.json({preview:{duration_days:180,max_tasks:80,max_accounts:1}})); });
            return realFetch(url, opts);
          };
        }""")
        writes = []
        page.route("**/subscription/preview", lambda route: route.fulfill(json={
            "preview": {"duration_days": 7, "max_tasks": 10, "max_accounts": 1}
        }))

        def redeem(route):
            writes.append(route.request.post_data_json["code"])
            route.fulfill(json={"ok": True, "message": "兑换成功"})

        page.route("**/api/subscription/redeem", redeem)
        page.goto(base + "/subscription")
        field = page.get_by_label("兑换码", exact=True)
        check = page.get_by_role("button", name="检查兑换码")
        field.fill("FIRST")
        check.click()
        page.wait_for_function("typeof window.finishFirstPreview === 'function'")
        field.fill("SECOND")
        check.click()
        preview = page.locator(".subscription-preview")
        expect(preview).to_contain_text("增加 7 天")
        page.evaluate("async () => {window.finishFirstPreview(); await new Promise(requestAnimationFrame)}")
        expect(preview).to_contain_text("增加 7 天")
        page.get_by_role("button", name="确认兑换").click()
        expect(field).to_have_value("")
        assert writes == ["SECOND"]
    finally:
        context.close()


def test_notification_feedback_stays_with_edited_card(browser_site):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    context.add_cookies([{"name": "spark_session", "value": tokens["user"], "url": base}])
    try:
        page = context.new_page()
        reads, policy_writes = [], []
        payload = {"mail_ready": True, "preference": {"email":"qa@example.com","verified":True,"enabled": True},
                   "policy": {"events": [], "quiet_enabled": False}}
        page.route("**/api/notifications", lambda route: reads.append(route))
        page.route("**/notifications/save", lambda route: route.fulfill(json={"message":"接收设置已保存"}))
        page.route("**/notifications/policy", lambda route: policy_writes.append(route))
        page.goto(base + "/notifications", wait_until="domcontentloaded")
        expect(page.get_by_label("正在读取通知设置…")).to_be_visible()
        expect(page.get_by_text("邮件服务尚未启用", exact=False)).to_have_count(0)
        reads.pop().fulfill(json=payload)
        email_button = page.get_by_role("button", name="保存邮箱设置")
        policy_button = page.get_by_role("button", name="保存提醒偏好")
        email_card = page.get_by_role("heading", name="接收邮箱").locator("..")
        policy_card = page.get_by_role("heading", name="提醒偏好").locator("..")
        event = policy_card.get_by_role("checkbox", name="任务执行异常")
        event.check()
        email_button.click()
        policy_button.click()
        assert policy_writes[0].request.post_data_json["events"] == ["task_failed"]
        reads.pop().fulfill(json=payload)
        expect(event).to_be_checked()
        expect(email_button).to_be_enabled()
        policy_writes.pop().fulfill(status=400,json={"message":"开始与结束时间不能相同"})
        expect(policy_card.get_by_role("alert")).to_contain_text("开始与结束时间不能相同")
        expect(email_card.get_by_role("status")).to_contain_text("接收设置已保存")
        policy_button.click()
        policy_writes.pop().fulfill(json={"message":"提醒偏好已保存"})
        expect(policy_card.get_by_role("status")).to_contain_text("提醒偏好已保存")
        expect(policy_card.get_by_role("alert")).to_have_count(0)
    finally:
        context.close()
