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
