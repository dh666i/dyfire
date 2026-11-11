"""Current account surfaces against an isolated real HTTP server and browser."""
from pathlib import Path
import json
import re
from uuid import uuid4

import pytest
from playwright.sync_api import expect

from tests.console.test_browser_acceptance import browser_site


@pytest.mark.parametrize('width,theme', [(360, 'light'), (390, 'dark'), (1440, 'light'), (1440, 'dark')])
def test_account_surfaces_share_readable_touch_sizing_and_no_overflow(browser_site, width, theme):
    browser, base, tokens = browser_site
    context = browser.new_context(viewport={'width': width, 'height': 900}, reduced_motion='reduce')
    context.add_init_script(f"localStorage.setItem('dyfire-theme','{theme}')")
    errors = []
    try:
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        for route, title in [('/login', '欢迎回来'), ('/register', '创建账号'), ('/forgot-password', '找回网站密码'), ('/forgot-password?sent=1&username=friend', '设置新密码')]:
            page.goto(base + route, wait_until='networkidle')
            expect(page.get_by_role('heading', name=title, exact=True)).to_be_visible()
            page.evaluate('document.fonts.ready')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            assert not errors
            for field in page.locator('.auth-field input').all():
                assert field.bounding_box()['height'] >= 44
            for button in page.locator('.auth-password-toggle').all():
                assert button.bounding_box()['height'] >= 44
                assert button.bounding_box()['width'] >= 44
            if width < 600:
                assert page.locator('.auth-field input').first.evaluate('(node)=>parseFloat(getComputedStyle(node).fontSize)') >= 16
            assert page.locator('.fire-auth-card').evaluate("node=>getComputedStyle(node).borderRadius") == '24px'
            assert page.locator('.fire-auth-card').evaluate('node=>node.getAnimations().length') == 0
            output = Path('.artifacts/auth-ui'); output.mkdir(parents=True, exist_ok=True)
            if route == '/login':
                typography = page.locator('#auth-title').evaluate('''node=>({
                    family:getComputedStyle(node).fontFamily,
                    weight:getComputedStyle(node).fontWeight,
                    fontsReady:document.fonts.status,
                    chineseLoaded:document.fonts.check('500 32px "Noto Sans SC"','欢迎回来'),
                    uiFont:getComputedStyle(node).getPropertyValue('--ui-font')
                })''')
                assert typography['fontsReady'] == 'loaded' and typography['chineseLoaded']
                assert 'Noto Sans SC' in typography['family'] and typography['weight'] == '500'
                (output / f'fonts-{theme}-{width}.json').write_text(json.dumps(typography, ensure_ascii=False), encoding='utf-8')
            page.screenshot(path=str(output / f'{route.split("?")[0].strip("/")}{"-reset" if "sent=" in route else ""}-{theme}-{width}.png'), full_page=True)
        context.add_cookies([{'name': 'spark_session', 'value': tokens['user'], 'url': base}])
        page.goto(base + '/change-password', wait_until='networkidle')
        page.evaluate('document.fonts.ready')
        expect(page.get_by_role('heading', name='设置登录密码', exact=True)).to_be_visible()
        assert page.locator('input[name=csrf_token]').input_value()
        expect(page.locator('.fire-auth-tabs')).to_have_count(0)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.screenshot(path=str(output / f'change-password-{theme}-{width}.png'), full_page=True)
    finally:
        context.close()


def test_password_visibility_and_tab_navigation_keep_form_contract(browser_site):
    browser, base, _tokens = browser_site
    context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
    try:
        page = context.new_page(); page.goto(base + '/login', wait_until='networkidle')
        page.get_by_label('密码', exact=True).fill('Local-Test-Pass42')
        page.get_by_role('button', name='显示密码', exact=True).click()
        expect(page.get_by_label('密码', exact=True)).to_have_attribute('type', 'text')
        expect(page.get_by_role('button', name='隐藏密码', exact=True)).to_have_attribute('aria-pressed', 'true')
        page.get_by_role('button', name='隐藏密码', exact=True).click()
        expect(page.get_by_label('密码', exact=True)).to_have_attribute('type', 'password')
        page.locator('.fire-auth-tabs').get_by_role('link', name='注册', exact=True).click()
        expect(page.get_by_role('heading', name='创建账号', exact=True)).to_be_visible()
        assert page.evaluate('document.activeElement.tagName') != 'INPUT'
        expect(page.locator('.auth-form')).to_have_attribute('action', '/register')
        assert page.locator('input[name=password_confirmation]').count() == 1
        assert page.locator('.auth-field small').count() == 2
        page.go_back()
        expect(page.get_by_role('heading', name='欢迎回来', exact=True)).to_be_visible()
        expect(page.locator('.auth-form')).to_have_attribute('action', '/login')
    finally:
        context.close()


def test_registration_and_password_change_reach_real_backend(browser_site):
    browser, base, _tokens = browser_site
    context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
    username = 'authui-' + uuid4().hex[:10]
    original, replacement = 'Sepia-Grove-704!', 'Cedar-Cloud-938!'
    try:
        page = context.new_page(); page.goto(base + '/register', wait_until='networkidle')
        page.locator('input[name=username]').fill(username)
        page.locator('input[name=password]').fill(original)
        page.locator('input[name=password_confirmation]').fill(original)
        page.get_by_role('button', name='创建账号', exact=True).click()
        page.wait_for_url('**/login?**')
        expect(page.get_by_text('注册成功，请登录后开始绑定抖音账号。', exact=True)).to_be_visible()
        page.locator('input[name=username]').fill(username)
        page.locator('input[name=password]').fill(original)
        page.get_by_role('button', name='登录', exact=True).click()
        page.wait_for_url('**/dashboard')
        page.goto(base + '/change-password', wait_until='networkidle')
        assert page.locator('input[name=csrf_token]').input_value()
        page.get_by_label('当前密码', exact=True).fill(original)
        page.get_by_label('新密码', exact=True).fill(replacement)
        page.get_by_label('确认新密码', exact=True).fill(replacement)
        page.get_by_role('button', name='保存新密码', exact=True).click()
        page.wait_for_url('**/dashboard?notice=password-updated')
        fresh = browser.new_context()
        try:
            verify = fresh.new_page(); verify.goto(base + '/login', wait_until='networkidle')
            verify.get_by_label('登录账号', exact=True).fill(username)
            verify.get_by_label('密码', exact=True).fill(replacement)
            verify.get_by_role('button', name='登录', exact=True).click()
            verify.wait_for_url('**/dashboard')
        finally:
            fresh.close()
    finally:
        context.close()


def test_recovery_unavailable_and_invalid_reset_stay_in_account_surface(browser_site):
    browser, base, _tokens = browser_site
    context = browser.new_context(viewport={'width': 360, 'height': 800}, reduced_motion='reduce')
    try:
        page = context.new_page(); page.goto(base + '/forgot-password', wait_until='networkidle')
        expect(page.get_by_text('邮箱找回暂未开启，请联系管理员处理网站密码。', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='发送重置邮件', exact=True)).to_be_disabled()
        page.goto(base + '/forgot-password?sent=1&username=friend', wait_until='networkidle')
        page.get_by_label('重置代码', exact=True).fill('BADCODE01234')
        page.get_by_label('新密码', exact=True).fill('Cedar-Cloud-938!')
        page.get_by_label('再次输入新密码', exact=True).fill('Cedar-Cloud-938!')
        page.get_by_role('button', name='重置密码', exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('重置未完成')
        expect(page.get_by_role('heading', name='设置新密码', exact=True)).to_be_visible()
        expect(page.locator('.auth-form')).to_have_attribute('action', '/forgot-password/reset')
        assert re.search(r'/forgot-password\?', page.url)
        expect(page.get_by_role('button', name='重置密码', exact=True)).to_be_enabled()
    finally:
        context.close()


def test_reduced_motion_and_small_keyboard_height_keep_controls_reachable(browser_site):
    browser, base, _tokens = browser_site
    context = browser.new_context(viewport={'width': 360, 'height': 500}, reduced_motion='reduce', is_mobile=True, has_touch=True)
    try:
        page = context.new_page(); page.goto(base + '/login', wait_until='networkidle')
        page.locator('.fire-auth-tabs').get_by_role('link', name='注册', exact=True).click()
        expect(page.get_by_role('heading', name='创建账号', exact=True)).to_be_visible()
        assert page.locator('.auth-form-motion').evaluate('node=>node.getAnimations().length') == 0
        field = page.get_by_label('确认密码', exact=True)
        field.focus(); field.scroll_into_view_if_needed()
        expect(field).to_be_in_viewport()
        button = page.get_by_role('button', name='创建账号', exact=True)
        button.scroll_into_view_if_needed()
        expect(button).to_be_in_viewport()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    finally:
        context.close()
