import asyncio
import gc
import unittest
import warnings
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from spark_console.auth_scanner import (
    AUTHENTICATED_SELECTOR,
    CHAT_AUTHENTICATED_SELECTOR,
    CONFIRMING_TEXT,
    DISPLAY_NAME_SELECTOR,
    LOGIN_PANEL_SELECTORS,
    QR_SELECTORS,
    QR_SCANNED_SELECTOR,
    SMS_VERIFICATION_TEXT,
    UNIQUE_ID_SELECTOR,
    VERIFICATION_TEXT,
    BrowserTextDelivery,
    DouyinQrScanner,
    LoginTimedOut,
    QrLoadFailed,
    ScanCancelled,
    VerificationRequired,
)
from core.web_chat import CONVERSATION_ITEM_SELECTOR, CONVERSATION_TITLE_SELECTOR


PNG = b"\x89PNG\r\n\x1a\nscanner-fixture"
PAGE_PNG = b"\x89PNG\r\n\x1a\nfull-browser-view"


class _Locator:
    def __init__(
        self,
        *,
        visible=False,
        visible_sequence=None,
        editable=True,
        editable_sequence=None,
        enabled=True,
        png=None,
        text="",
        children=None,
        items=None,
        box=None,
        decorative=False,
        qr_semantic=False,
        on_click=None,
    ):
        self.visible = visible
        self.visible_sequence = list(visible_sequence or [])
        self.editable = editable
        self.editable_sequence = list(editable_sequence or [])
        self.enabled = enabled
        self.png = png
        self.text = text
        self.children = children or {}
        self.items = items or []
        self.box = box
        self.decorative = decorative
        self.qr_semantic = qr_semantic
        self.on_click = on_click
        self.clicked = False
        self.filled_values = []
        self.screenshot_calls = []

    @property
    def first(self):
        return self

    def locator(self, selector):
        return self.children.get(selector, _Locator())

    async def is_visible(self, **_kwargs):
        if self.visible_sequence:
            self.visible = self.visible_sequence.pop(0)
        return self.visible

    async def is_editable(self, **_kwargs):
        if self.editable_sequence:
            self.editable = self.editable_sequence.pop(0)
        return self.editable

    async def is_enabled(self, **_kwargs):
        return self.enabled

    async def screenshot(self, **_kwargs):
        self.screenshot_calls.append(_kwargs)
        return self.png

    async def inner_text(self, **_kwargs):
        return self.text

    async def all(self):
        return self.items

    async def bounding_box(self):
        return self.box

    async def evaluate(self, _expression):
        if "element.tagName === 'CANVAS'" in _expression:
            return self.qr_semantic
        return self.decorative

    async def click(self, **_kwargs):
        self.clicked = True
        if self.on_click is not None:
            self.on_click()

    async def fill(self, value, **_kwargs):
        self.filled_values.append(value)


class _Response:
    def __init__(self, status=None, *, body=None):
        self.url = "https://creator.douyin.com/passport/web/check_qrconnect/"
        self._status = status
        self._body = body

    async def json(self):
        if self._body is not None:
            return self._body
        return {"data": {"status": self._status, "error_code": 0}}


class _Mouse:
    def __init__(self):
        self.clicks = []

    async def click(self, x, y):
        self.clicks.append((x, y))


class _Page:
    def __init__(
        self,
        mode="success",
        *,
        qr_visible=True,
        semantic_qr=False,
        normal_verification_tab=False,
        profile_visible=True,
    ):
        self.mode = mode
        self.url = "https://creator.douyin.com/"
        self.authenticated = asyncio.Event()
        self.chat_authenticated = asyncio.Event()
        self.never = asyncio.Event()
        self.navigation_started = asyncio.Event()
        self.qr = _Locator(
            visible=False if mode in {"chat_entry", "delayed_chat_entry"} else qr_visible,
            visible_sequence=[True, True, False]
            if mode in {"credential_success", "cookie_only", "qr_hidden_sms_challenge"}
            else None,
            png=PNG,
            box={"width": 180, "height": 180},
        )
        self.semantic_candidates = _Locator(
            items=[
                _Locator(visible=True, png=PNG, box={"width": 180, "height": 180}, decorative=True),
                _Locator(
                    visible=True,
                    png=PNG,
                    box={"width": 178, "height": 178},
                    qr_semantic=True,
                ),
            ]
            if semantic_qr
            else [self.qr]
        )
        panel_children = (
            {} if semantic_qr else {selector: self.qr for selector in QR_SELECTORS}
        )
        panel_children["img, canvas"] = self.semantic_candidates
        self.panel = _Locator(
            visible=True,
            children=panel_children,
        )
        self.normal_verification_tab = normal_verification_tab
        self.profile_visible = profile_visible
        self.listeners = {}
        self.goto_url = None
        self.login_button = _Locator(
            visible=True,
            visible_sequence=[False, False, True]
            if mode == "delayed_chat_entry"
            else None,
            on_click=lambda: setattr(self.qr, "visible", bool(qr_visible)),
        )
        self.scan_login_button = _Locator(visible=mode == "scan_login_available")
        self.body_text = {
            "sms_option": "接收短信验证码",
            "sms_verification": "接收短信验证码\n短信已发送至 176******50\n42s后重新发送",
            "sms_not_editable": "短信已发送至 176******50\n42s后重新发送",
            "sms_prompt": "请输入验证码",
            "sms_sent": "短信已发送至 176******50\n42s后重新发送",
            "sms_then_chat": "接收短信验证码\n短信已发送至 176******50\n42s后重新发送",
            "sms_input_only": "请输入验证码",
            "sms_resend_ready": "短信已发送至 176******50\n重新发送",
            "sms_resend_cooldown": "短信已发送至 176******50\n42s后重新发送",
            "qr_hidden_sms_challenge": "接收短信验证码\n短信已发送至 176******50",
        }.get(mode, "")
        sms_input_visible = mode in {
            "sms_verification",
            "sms_input_only",
            "sms_not_editable",
            "sms_prompt",
            "sms_then_chat",
            "sms_resend_ready",
            "sms_resend_cooldown",
            "qr_hidden_sms_challenge",
        }
        self.verification_input = _Locator(
            visible=sms_input_visible,
            editable=mode != "sms_not_editable",
        )
        self.sms_button = _Locator(
            visible=mode == "sms_option",
        )
        self.initial_send_button = _Locator(visible=False)
        self.verify_button = _Locator(
            visible=mode
            in {
                "sms_option",
                "sms_verification",
                "sms_input_only",
                "sms_not_editable",
                "sms_prompt",
                "sms_sent",
                "sms_then_chat",
                "sms_resend_ready",
                "sms_resend_cooldown",
                "qr_hidden_sms_challenge",
            }
        )
        self.focused_input = _Locator(visible=True, editable=True)
        self.viewport_size = {"width": 1280, "height": 720}
        self.mouse = _Mouse()
        self.keyboard = _Keyboard()
        self.screenshot_calls = []

    async def screenshot(self, **kwargs):
        self.screenshot_calls.append(kwargs)
        return PAGE_PNG

    async def goto(self, *_args, **_kwargs):
        self.goto_url = _args[0]
        self.navigation_started.set()
        if self.mode == "navigation":
            await self.never.wait()
        return None

    def get_by_text(self, text, exact=False):
        if exact and text == "登录":
            return _Locator(items=[self.login_button])
        if exact and text == "扫码登录":
            return _Locator(items=[self.scan_login_button])
        return _Locator(items=[])

    def get_by_role(self, role, *, name, exact=False):
        del role, name, exact
        return _Locator(items=[])

    def locator(self, selector):
        if selector == ":focus":
            return self.focused_input
        if selector == "img, canvas":
            return self.semantic_candidates
        if selector == "body":
            return _Locator(visible=True, text=self.body_text)
        if selector == LOGIN_PANEL_SELECTORS[0]:
            return self.panel
        if selector == DISPLAY_NAME_SELECTOR:
            return _Locator(visible=self.profile_visible, text=" 测试昵称 " if self.profile_visible else "")
        if selector == UNIQUE_ID_SELECTOR:
            return _Locator(visible=True, text=" 抖音号：douyin-123 ")
        if selector == CONVERSATION_ITEM_SELECTOR and self.mode in {
            "chat_dom_success",
            "chat_cookie_fallback",
        }:
            return _Locator(
                items=[
                    _Locator(
                        visible=True,
                        children={CONVERSATION_TITLE_SELECTOR: _Locator(text=" wzlovegsy ")},
                    ),
                    _Locator(
                        visible=True,
                        children={CONVERSATION_TITLE_SELECTOR: _Locator(text="gsy")},
                    ),
                ]
            )
        return _Locator()

    def on(self, event, listener):
        self.listeners[event] = listener
        if event == "response" and self.mode == "network_success":
            loop = asyncio.get_running_loop()
            loop.call_soon(listener, _Response("scanned"))
            loop.call_soon(listener, _Response("confirmed"))

    def remove_listener(self, event, listener):
        if self.listeners.get(event) is listener:
            self.listeners.pop(event)

    async def wait_for_selector(self, selector, **_kwargs):
        if selector == "text=验证码" and self.normal_verification_tab:
            return _Locator(visible=True, text="验证码登录")
        if selector == QR_SCANNED_SELECTOR:
            if self.mode == "dom_confirmation":
                await asyncio.sleep(0)
                return _Locator(visible=True)
            await self.never.wait()
        if selector == AUTHENTICATED_SELECTOR:
            await self.authenticated.wait()
            return _Locator(visible=True)
        if selector == CHAT_AUTHENTICATED_SELECTOR:
            if self.mode in {"chat_dom_success", "chat_cookie_fallback"}:
                await asyncio.sleep(0)
                return _Locator(visible=True)
            if self.mode == "sms_then_chat":
                await self.chat_authenticated.wait()
                return _Locator(visible=True)
            await self.never.wait()
        if any(text in selector for text in CONFIRMING_TEXT):
            if self.mode in {
                "success",
                "url_success",
                "chat_entry",
                "delayed_chat_entry",
                "chat_account_success",
                "chat_cookie_fallback",
                "verification",
                "sms_option",
                "sms_verification",
                "sms_not_editable",
                "sms_prompt",
                "sms_sent",
                "sms_then_chat",
                "sms_input_only",
                "sms_resend_ready",
                "sms_resend_cooldown",
            }:
                await asyncio.sleep(0)
                if self.mode == "success":
                    asyncio.get_running_loop().call_soon(self.authenticated.set)
                elif self.mode in {
                    "url_success",
                    "chat_entry",
                    "delayed_chat_entry",
                }:
                    self.url = "https://creator.douyin.com/creator-micro/home"
                return _Locator(visible=True)
            await self.never.wait()
        if any(text in selector for text in SMS_VERIFICATION_TEXT):
            expected = {
                "sms_option": "接收短信验证码",
                "sms_verification": "接收短信验证码",
                "sms_not_editable": "短信已发送",
                "sms_sent": "短信已发送",
                "sms_then_chat": "接收短信验证码",
                "sms_resend_ready": "短信已发送",
                "sms_resend_cooldown": "短信已发送",
                "qr_hidden_sms_challenge": "接收短信验证码",
            }.get(self.mode)
            if expected is not None and expected in selector:
                await asyncio.sleep(0)
                return _Locator(visible=True, text=expected)
            await self.never.wait()
        if any(text in selector for text in VERIFICATION_TEXT):
            if self.mode == "verification":
                await asyncio.sleep(0)
                return _Locator(visible=True)
            await self.never.wait()
        await self.never.wait()


class _Frame:
    def __init__(self, focused_input):
        self.focused_input = focused_input

    def locator(self, selector):
        if selector == ":focus":
            return self.focused_input
        return _Locator()


class _Context:
    def __init__(self, page, *, fail_storage_state=False, credential_success=False):
        self.page = page
        self.fail_storage_state = fail_storage_state
        self.closed = False
        self.credential_success = credential_success
        self.cookie_reads = 0
        self.request = _ContextRequest(
            authenticated=page.mode
            in {
                "success",
                "url_success",
                "chat_entry",
                "delayed_chat_entry",
                "chat_account_success",
                "credential_success",
                "qr_hidden_sms_challenge",
            }
        )

    async def new_page(self):
        return self.page

    async def storage_state(self):
        if self.fail_storage_state:
            raise RuntimeError("fixed storage failure")
        cookies = [
                {
                    "name": "sid",
                    "value": "scanner-cookie-marker",
                    "domain": ".douyin.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
            ]
        if self.credential_success:
            cookies.append(
                {
                    "name": "auth-session",
                    "value": "authenticated-marker",
                    "domain": ".douyin.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
            )
        return {
            "cookies": cookies,
            "origins": [],
        }

    async def cookies(self):
        self.cookie_reads += 1
        state = await self.storage_state()
        if self.credential_success and self.cookie_reads == 1:
            return state["cookies"][:1]
        return state["cookies"]

    async def close(self):
        self.closed = True


class _ContextResponse:
    def __init__(self, authenticated):
        self.authenticated = authenticated

    async def json(self):
        return {
            "message": "success" if self.authenticated else "error",
            "data": {
                "error_code": 0 if self.authenticated else 13,
                "user_id": "user-fixture" if self.authenticated else "",
            },
        }


class _ContextRequest:
    def __init__(self, authenticated=False):
        self.authenticated = authenticated
        self.calls = 0

    async def get(self, _url, **_kwargs):
        self.calls += 1
        return _ContextResponse(self.authenticated)


class _Keyboard:
    def __init__(self):
        self.values = []

    async def type(self, value):
        self.values.append(value)


class _Browser:
    def __init__(self, context, *, fail_new_context=False):
        self.context = context
        self.fail_new_context = fail_new_context
        self.closed = False

    async def new_context(self):
        if self.fail_new_context:
            raise RuntimeError("fixed context failure")
        return self.context

    async def close(self):
        self.closed = True


class _Chromium:
    def __init__(self, browser):
        self.browser = browser

    async def launch(self, **_kwargs):
        return self.browser


class _PlaywrightManager:
    def __init__(self, browser):
        self.playwright = type(
            "FakePlaywright", (), {"chromium": _Chromium(browser)}
        )()

    async def __aenter__(self):
        return self.playwright

    async def __aexit__(self, *_args):
        return None


class DouyinQrScannerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1.0

    def _scanner(
        self,
        mode="success",
        *,
        qr_visible=True,
        semantic_qr=False,
        normal_verification_tab=False,
        profile_visible=True,
        fail_storage_state=False,
        qr_timeout_seconds=0.01,
        login_timeout_seconds=0.2,
    ):
        page = _Page(
            mode,
            qr_visible=qr_visible,
            semantic_qr=semantic_qr,
            normal_verification_tab=normal_verification_tab,
            profile_visible=profile_visible,
        )
        context = _Context(
            page,
            fail_storage_state=fail_storage_state,
            credential_success=mode
            in {
                "credential_success",
                "cookie_only",
                "qr_hidden_sms_challenge",
                "chat_cookie_fallback",
            },
        )
        browser = _Browser(context)
        scanner = DouyinQrScanner(
            playwright_factory=lambda: _PlaywrightManager(browser),
            qr_timeout_seconds=qr_timeout_seconds,
            login_timeout_seconds=login_timeout_seconds,
            poll_interval_seconds=0,
        )
        return scanner, browser, context

    async def test_randomized_square_qr_is_found_without_qrcode_class(self):
        scanner, browser, context = self._scanner(semantic_qr=True)
        qr_images = []

        try:
            await scanner.run(qr_images.append, lambda _value: None, lambda: False)
        except QrLoadFailed:
            self.fail("randomized square QR should be discovered")

        self.assertEqual([PAGE_PNG], qr_images)
        self.assertEqual(
            [], context.page.semantic_candidates.items[1].screenshot_calls
        )
        self.assertGreaterEqual(len(context.page.screenshot_calls), 1)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_normal_verification_login_tab_is_not_extra_verification(self):
        scanner, _browser, context = self._scanner(
            mode="timeout", normal_verification_tab=True
        )
        checks = 0

        def cancelled():
            nonlocal checks
            checks += 1
            return checks > 10

        try:
            with self.assertRaises(ScanCancelled):
                await scanner._wait_for_login(
                    context.page, lambda _value: None, cancelled
                )
        except VerificationRequired:
            self.fail("normal verification-code login tab must not block QR login")

    async def test_scanner_returns_account_after_qr_and_mobile_confirmation(self):
        scanner, browser, context = self._scanner()
        qr_images = []
        confirmations = []

        result = await scanner.run(
            qr_images.append,
            confirmations.append,
            lambda: False,
        )

        self.assertEqual("测试昵称", result.display_name)
        self.assertEqual("douyin-123", result.unique_id)
        self.assertEqual(1, len(result.storage_state["cookies"]))
        self.assertEqual([PAGE_PNG], qr_images)
        self.assertEqual([True], confirmations)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_chat_login_entry_is_opened_before_qr_capture(self):
        scanner, browser, context = self._scanner(mode="chat_entry")

        result = await scanner.run(
            lambda _png: None,
            lambda _confirmed: None,
            lambda: False,
        )

        self.assertEqual("https://www.douyin.com/chat", context.page.goto_url)
        self.assertTrue(context.page.login_button.clicked)
        self.assertEqual("测试昵称", result.display_name)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_initial_image_is_the_complete_cloud_browser_view(self):
        scanner, browser, context = self._scanner(mode="chat_entry")
        views = []

        await scanner.run(views.append, lambda _confirmed: None, lambda: False)

        self.assertEqual([PAGE_PNG], views)
        self.assertEqual([], context.page.qr.screenshot_calls)
        self.assertGreaterEqual(len(context.page.screenshot_calls), 1)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_unrelated_square_images_are_not_published_as_qr(self):
        scanner, _browser, context = self._scanner(
            mode="timeout", semantic_qr=True, qr_timeout_seconds=0.01
        )
        context.page.semantic_candidates.items[1].qr_semantic = False

        with self.assertRaises(QrLoadFailed):
            await scanner._find_qr(context.page, lambda: False)

        self.assertEqual(
            [], context.page.semantic_candidates.items[1].screenshot_calls
        )

    async def test_chat_login_prefers_the_explicit_qr_login_control(self):
        scanner, _browser, context = self._scanner(mode="scan_login_available")

        await scanner._open_chat_login(context.page, lambda: False)

        self.assertTrue(context.page.scan_login_button.clicked)
        self.assertFalse(context.page.login_button.clicked)

    async def test_confirmed_chat_login_completes_from_account_api_without_url_change(self):
        scanner, browser, context = self._scanner(
            mode="chat_account_success", profile_visible=False
        )

        result = await scanner.run(
            lambda _png: None,
            lambda _confirmed: None,
            lambda: False,
        )

        self.assertEqual("https://creator.douyin.com/", context.page.url)
        self.assertEqual("抖音账号", result.display_name)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_loaded_chat_dom_does_not_complete_without_account_api(self):
        scanner, browser, context = self._scanner(
            mode="chat_dom_success",
            profile_visible=False,
            # Keep enough scheduling headroom for the authoritative account
            # API probe when the whole suite has already created many event
            # loops. The production timeout is five minutes; this remains a
            # deliberately short integration timeout.
            login_timeout_seconds=0.2,
        )

        with self.assertRaises(LoginTimedOut):
            await scanner.run(
                lambda _png: None,
                lambda _confirmed: None,
                lambda: False,
            )

        self.assertGreaterEqual(context.request.calls, 1)
        self.assertGreaterEqual(context.cookie_reads, 2)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_confirmed_chat_dom_and_changed_http_only_cookie_use_fallback(self):
        scanner, browser, context = self._scanner(
            mode="chat_cookie_fallback",
            profile_visible=False,
            login_timeout_seconds=0.2,
        )
        confirmations = []

        result = await scanner.run(
            lambda _png: None,
            confirmations.append,
            lambda: False,
        )

        self.assertFalse(context.request.authenticated)
        self.assertGreaterEqual(context.request.calls, 1)
        self.assertGreaterEqual(context.cookie_reads, 2)
        self.assertEqual([True], confirmations)
        self.assertEqual(("wzlovegsy", "gsy"), result.conversation_names)
        self.assertTrue(
            any(
                cookie["httpOnly"] is True
                and cookie["domain"].endswith("douyin.com")
                and cookie["name"] == "auth-session"
                for cookie in result.storage_state["cookies"]
            )
        )
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_interaction_processor_forwards_click_and_focused_numeric_text(self):
        scanner, _browser, context = self._scanner(mode="sms_verification")
        verification_ready = asyncio.Event()
        delivered = asyncio.Event()
        actions = [
            {"kind": "click", "x": 0.25, "y": 0.75},
            {"kind": "text", "text": "123456"},
        ]

        def next_interaction():
            return actions.pop(0) if actions else None

        original_fill = context.page.focused_input.fill

        async def fill_and_signal(value, **kwargs):
            await original_fill(value, **kwargs)
            delivered.set()

        context.page.focused_input.fill = fill_and_signal
        task = asyncio.create_task(
            scanner._process_interactions(
                context.page,
                next_interaction,
                verification_ready,
            )
        )
        await asyncio.wait_for(delivered.wait(), timeout=1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        self.assertEqual([(320.0, 540.0)], context.page.mouse.clicks)
        self.assertEqual(["123456"], context.page.focused_input.filled_values)
        self.assertEqual([], context.page.verification_input.filled_values)
        self.assertFalse(context.page.verify_button.clicked)
        self.assertFalse(context.page.sms_button.clicked)
        self.assertFalse(context.page.initial_send_button.clicked)
        self.assertEqual([], context.page.keyboard.values)

    async def test_interaction_processor_claims_actions_without_structured_verification_gate(self):
        scanner, _browser, context = self._scanner(mode="sms_verification")
        verification_ready = asyncio.Event()
        claimed = asyncio.Event()

        def next_interaction():
            claimed.set()
            return None

        task = asyncio.create_task(
            scanner._process_interactions(
                context.page,
                next_interaction,
                verification_ready,
            )
        )
        await asyncio.wait_for(claimed.wait(), timeout=0.2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        self.assertTrue(claimed.is_set())

    async def test_numeric_text_waits_for_focus_and_is_not_lost(self):
        scanner, _browser, context = self._scanner(mode="sms_verification")
        context.page.focused_input.visible = False
        delivered = asyncio.Event()
        actions = [
            {"kind": "text", "text": "654321"},
            {"kind": "click", "x": 0.5, "y": 0.5},
        ]

        def next_interaction():
            return actions.pop(0) if actions else None

        original_click = context.page.mouse.click

        async def click_and_focus(x, y):
            await original_click(x, y)
            context.page.focused_input.visible = True

        original_fill = context.page.focused_input.fill

        async def fill_and_signal(value, **kwargs):
            await original_fill(value, **kwargs)
            delivered.set()

        context.page.mouse.click = click_and_focus
        context.page.focused_input.fill = fill_and_signal
        task = asyncio.create_task(
            scanner._process_interactions(context.page, next_interaction)
        )
        await asyncio.wait_for(delivered.wait(), timeout=1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        self.assertEqual([(640.0, 360.0)], context.page.mouse.clicks)
        self.assertEqual(["654321"], context.page.focused_input.filled_values)
        self.assertFalse(context.page.verify_button.clicked)

    async def test_interaction_processor_forwards_focused_unicode_password(self):
        scanner, _browser, context = self._scanner(mode="sms_verification")
        delivered = asyncio.Event()
        password = " 密码-P@ss w0rd![]{} "
        actions = [{"kind": "text", "text": password}]

        def next_interaction():
            return actions.pop(0) if actions else None

        original_fill = context.page.focused_input.fill

        async def fill_and_signal(value, **kwargs):
            await original_fill(value, **kwargs)
            delivered.set()

        context.page.focused_input.fill = fill_and_signal
        task = asyncio.create_task(
            scanner._process_interactions(context.page, next_interaction)
        )
        await asyncio.wait_for(delivered.wait(), timeout=1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        self.assertEqual([password], context.page.focused_input.filled_values)
        self.assertEqual([], context.page.verification_input.filled_values)
        self.assertEqual([], context.page.keyboard.values)

    async def test_text_fill_side_effect_then_error_is_never_retried(self):
        scanner, _browser, context = self._scanner(mode="sms_verification")
        interaction = {
            "kind": "text",
            "interaction_id": "password-attempt-1",
            "text": "correct horse battery staple",
        }
        claim_count = 0
        repeated_claims = asyncio.Event()
        results = []
        child_input = _Locator(visible=True, editable=True)
        context.page.frames = [_Frame(child_input)]

        async def fill_then_fail(value, **_kwargs):
            # Model a Playwright/transport exception after Chromium has already
            # applied the value to the real field.
            context.page.focused_input.filled_values.append(value)
            raise RuntimeError("fixed post-fill driver failure")

        context.page.focused_input.fill = fill_then_fail

        def next_interaction():
            nonlocal claim_count
            claim_count += 1
            if claim_count >= 3:
                repeated_claims.set()
            # A defensive scanner must remain at-most-once even if an upstream
            # claimer accidentally returns the same durable ID repeatedly.
            return interaction

        task = asyncio.create_task(
            scanner._process_interactions(
                context.page,
                next_interaction,
                on_interaction_result=results.append,
            )
        )
        await asyncio.wait_for(repeated_claims.wait(), timeout=1.5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(
            ["correct horse battery staple"],
            context.page.focused_input.filled_values,
        )
        self.assertEqual([], child_input.filled_values)
        self.assertEqual(
            [
                {
                    "kind": "text",
                    "delivery": "uncertain",
                    "interaction_id": "password-attempt-1",
                }
            ],
            results,
        )

    async def test_pending_text_waits_for_focus_then_fills_only_once(self):
        scanner, _browser, context = self._scanner(mode="sms_verification")
        context.page.focused_input.visible = False
        interaction = {
            "kind": "text",
            "interaction_id": 42,
            "text": "654321",
        }
        claim_count = 0
        observed_after_delivery = asyncio.Event()
        results = []

        def next_interaction():
            nonlocal claim_count
            claim_count += 1
            if claim_count == 2:
                context.page.focused_input.visible = True
            if claim_count >= 4:
                observed_after_delivery.set()
            return interaction

        task = asyncio.create_task(
            scanner._process_interactions(
                context.page,
                next_interaction,
                on_interaction_result=results.append,
            )
        )
        await asyncio.wait_for(observed_after_delivery.wait(), timeout=1.5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(["654321"], context.page.focused_input.filled_values)
        self.assertEqual(
            [
                {
                    "kind": "text",
                    "delivery": "not_ready",
                    "interaction_id": 42,
                },
                {
                    "kind": "text",
                    "delivery": "delivered",
                    "interaction_id": 42,
                },
            ],
            results,
        )

    async def test_text_that_never_becomes_ready_finishes_as_failed(self):
        scanner, _browser, context = self._scanner(mode="sms_verification")
        scanner.text_delivery_timeout_seconds = 0
        context.page.focused_input.visible = False
        interaction = {
            "kind": "text",
            "interaction_id": 43,
            "text": "not-delivered",
        }
        claim_count = 0
        observed_after_timeout = asyncio.Event()
        results = []

        def next_interaction():
            nonlocal claim_count
            claim_count += 1
            if claim_count >= 2:
                observed_after_timeout.set()
            return interaction

        task = asyncio.create_task(
            scanner._process_interactions(
                context.page,
                next_interaction,
                on_interaction_result=results.append,
            )
        )
        await asyncio.wait_for(observed_after_timeout.wait(), timeout=1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        self.assertEqual([], context.page.focused_input.filled_values)
        self.assertEqual(
            [
                {
                    "kind": "text",
                    "delivery": "not_ready",
                    "interaction_id": 43,
                },
                {
                    "kind": "text",
                    "delivery": "failed",
                    "interaction_id": 43,
                },
            ],
            results,
        )

    async def test_distinct_text_interaction_ids_are_not_content_deduplicated(self):
        scanner, _browser, context = self._scanner(mode="sms_verification")
        second_fill = asyncio.Event()
        actions = [
            {"kind": "text", "interaction_id": "first", "text": "same"},
            {"kind": "text", "interaction_id": "second", "text": "same"},
        ]
        results = []
        original_fill = context.page.focused_input.fill

        async def fill_and_signal(value, **kwargs):
            await original_fill(value, **kwargs)
            if len(context.page.focused_input.filled_values) == 1:
                raise RuntimeError("fixed first-attempt uncertainty")
            if len(context.page.focused_input.filled_values) == 2:
                second_fill.set()

        context.page.focused_input.fill = fill_and_signal

        def next_interaction():
            return actions.pop(0) if actions else None

        task = asyncio.create_task(
            scanner._process_interactions(
                context.page,
                next_interaction,
                on_interaction_result=results.append,
            )
        )
        await asyncio.wait_for(second_fill.wait(), timeout=1.5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(["same", "same"], context.page.focused_input.filled_values)
        self.assertEqual(
            [
                {
                    "kind": "text",
                    "delivery": "uncertain",
                    "interaction_id": "first",
                },
                {
                    "kind": "text",
                    "delivery": "delivered",
                    "interaction_id": "second",
                },
            ],
            results,
        )

    async def test_main_frame_is_not_reprocessed_after_page_locator(self):
        scanner, _browser, context = self._scanner(mode="sms_verification")
        context.page.focused_input.visible_sequence = [False, True]
        main_frame = _Frame(context.page.focused_input)
        context.page.main_frame = main_frame
        context.page.frames = [main_frame]

        first = await scanner._deliver_browser_text(context.page, "password")
        second = await scanner._deliver_browser_text(context.page, "password")

        self.assertIs(BrowserTextDelivery.NOT_READY, first)
        self.assertIs(BrowserTextDelivery.DELIVERED, second)
        self.assertEqual(
            ["password"], context.page.focused_input.filled_values
        )

    async def test_sms_verification_is_observed_without_automatic_page_actions(self):
        scanner, _browser, context = self._scanner(mode="sms_option")
        confirmed = asyncio.Event()
        confirmed.set()

        state = await scanner._wait_for_verification(
            context.page,
            confirmed,
            allow_sms_selection=True,
        )

        self.assertEqual(
            {"required": True, "verification_kind": "sms"},
            state,
        )
        self.assertFalse(context.page.sms_button.clicked)
        self.assertFalse(context.page.initial_send_button.clicked)
        self.assertFalse(context.page.verify_button.clicked)
        self.assertEqual([], context.page.verification_input.filled_values)

    async def test_forwarding_rejects_external_pages_and_invalid_numeric_text(self):
        scanner, _browser, context = self._scanner(mode="sms_verification")
        context.page.url = "https://example.invalid/login"

        self.assertFalse(
            await scanner._forward_browser_click(
                context.page, {"kind": "click", "x": 0.25, "y": 0.75}
            )
        )
        self.assertFalse(
            await scanner._forward_numeric_text(context.page, "123456")
        )

        context.page.url = "https://creator.douyin.com/"
        for value in ("123", "123456789", "12a456", "１２３４５６"):
            with self.subTest(value=value):
                self.assertFalse(
                    await scanner._forward_numeric_text(context.page, value)
                )

        self.assertEqual([], context.page.mouse.clicks)
        self.assertEqual([], context.page.focused_input.filled_values)
        self.assertFalse(context.page.verify_button.clicked)

    async def test_forward_browser_text_accepts_unicode_and_rejects_invalid_values(self):
        scanner, _browser, context = self._scanner(mode="sms_verification")

        self.assertTrue(
            await scanner._forward_browser_text(context.page, "密码123")
        )
        for value in ("", "a" * 257, "password\nwith-control"):
            with self.subTest(value=value):
                self.assertFalse(
                    await scanner._forward_browser_text(context.page, value)
                )

        self.assertEqual(["密码123"], context.page.focused_input.filled_values)

    async def test_chat_login_entry_waits_for_delayed_login_button(self):
        scanner, browser, context = self._scanner(
            mode="delayed_chat_entry", qr_timeout_seconds=0.05
        )

        result = await scanner.run(
            lambda _png: None,
            lambda _confirmed: None,
            lambda: False,
        )

        self.assertTrue(context.page.login_button.clicked)
        self.assertEqual("测试昵称", result.display_name)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_authenticated_creator_url_completes_when_legacy_home_dom_is_absent(self):
        scanner, browser, context = self._scanner(
            mode="url_success", profile_visible=False
        )

        result = await scanner.run(
            lambda _png: None,
            lambda _confirmed: None,
            lambda: False,
        )

        self.assertEqual("抖音账号", result.display_name)
        self.assertTrue(result.storage_state["cookies"])
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_hidden_qr_and_new_http_only_cookie_complete_without_legacy_dom(self):
        scanner, browser, context = self._scanner(
            mode="credential_success", profile_visible=False
        )
        confirmations = []

        result = await scanner.run(
            lambda _png: None,
            confirmations.append,
            lambda: False,
        )

        self.assertEqual("抖音账号", result.display_name)
        self.assertEqual([True], confirmations)
        self.assertEqual(2, len(result.storage_state["cookies"]))
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_qr_hiding_alone_does_not_authorize_sms_verification(self):
        scanner, browser, context = self._scanner(
            mode="qr_hidden_sms_challenge", profile_visible=False
        )
        needs = []

        result = await scanner.run(
            lambda _png: None,
            lambda _confirmed: None,
            lambda: False,
            on_needs_verification=needs.append,
        )

        self.assertEqual([], needs)
        self.assertEqual("抖音账号", result.display_name)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_cookie_change_alone_does_not_save_unauthenticated_state(self):
        scanner, browser, context = self._scanner(
            mode="cookie_only",
            profile_visible=False,
            login_timeout_seconds=0.2,
        )

        with self.assertRaises(LoginTimedOut):
            await scanner.run(
                lambda _png: None,
                lambda _confirmed: None,
                lambda: False,
            )

        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_qrconnect_confirmation_does_not_save_unauthenticated_state(self):
        scanner, browser, context = self._scanner(
            mode="network_success",
            profile_visible=False,
            login_timeout_seconds=0.2,
        )
        confirmations = []

        with self.assertLogs("spark_console.auth_scanner", level="INFO") as logs:
            with self.assertRaises(LoginTimedOut):
                await scanner.run(
                    lambda _png: None,
                    confirmations.append,
                    lambda: False,
                )

        self.assertEqual([True], confirmations)
        self.assertTrue(any("status=scanned" in line for line in logs.output))
        self.assertTrue(any("status=confirmed" in line for line in logs.output))
        self.assertNotIn("response", context.page.listeners)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_stable_scanned_dom_marker_enters_confirming_state(self):
        scanner, browser, context = self._scanner(
            mode="dom_confirmation",
            profile_visible=False,
            login_timeout_seconds=0.2,
        )
        confirmations = []

        with self.assertRaises(LoginTimedOut):
            await scanner.run(
                lambda _png: None,
                confirmations.append,
                lambda: False,
            )

        self.assertEqual([True], confirmations)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_qrconnect_response_during_qr_publication_is_buffered(self):
        scanner, browser, context = self._scanner(
            mode="timeout",
            profile_visible=False,
            login_timeout_seconds=0.2,
        )
        confirmations = []

        async def publish_and_scan(_png):
            context.page.listeners["response"](_Response("scanned"))

        with self.assertRaises(LoginTimedOut):
            await scanner.run(
                publish_and_scan,
                confirmations.append,
                lambda: False,
            )

        self.assertEqual([True], confirmations)
        self.assertNotIn("response", context.page.listeners)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    def test_qrconnect_status_accepts_current_and_wrapped_payload_shapes(self):
        cases = (
            ({"status": "new"}, "new"),
            ({"data": {"status": 2}}, "2"),
            ({"status": "success", "data": {"status": "2"}}, "2"),
            ({"data": {"result": {"qr_status": "CONFIRMED"}}}, "confirmed"),
            ({"payload": {"data": {"qrcode_status": " scanned "}}}, "scanned"),
        )

        for payload, expected in cases:
            with self.subTest(payload=payload):
                self.assertEqual(
                    expected,
                    DouyinQrScanner._qrconnect_status(payload),
                )

        self.assertEqual(
            "",
            DouyinQrScanner._qrconnect_status(
                {"data": {"scan_user_info": {"status": "2"}}}
            ),
        )

    async def test_qrconnect_monitor_completion_never_counts_as_login(self):
        scanner, _browser, context = self._scanner(mode="timeout")

        with patch.object(
            scanner,
            "_wait_for_qrconnect_status",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "qrcode status monitor stopped"
            ):
                await scanner._wait_for_login(
                    context.page,
                    lambda _value: None,
                    lambda: False,
                )

    async def test_expired_qrconnect_status_does_not_bypass_live_page(self):
        scanner, _browser, context = self._scanner(mode="timeout")
        responses = asyncio.Queue()
        responses.put_nowait(_Response("expired"))

        monitor = asyncio.create_task(
            scanner._wait_for_qrconnect_status(
                context.page,
                lambda _value: None,
                responses=responses,
            )
        )
        await asyncio.sleep(0)
        self.assertFalse(monitor.done())
        monitor.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await monitor

    async def test_missing_qr_maps_to_qr_load_failed_and_closes_resources(self):
        scanner, browser, context = self._scanner(qr_visible=False)

        with self.assertRaises(QrLoadFailed):
            await scanner.run(lambda _png: None, lambda: None, lambda: False)

        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_login_deadline_maps_to_login_timed_out(self):
        scanner, browser, context = self._scanner(
            mode="timeout", login_timeout_seconds=0.03
        )

        with self.assertRaises(LoginTimedOut):
            await scanner.run(lambda _png: None, lambda: None, lambda: False)

        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_persisted_deadline_interrupts_navigation(self):
        scanner, browser, context = self._scanner(
            mode="navigation", login_timeout_seconds=1
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=0.03)

        with self.assertRaises(LoginTimedOut):
            await asyncio.wait_for(
                scanner.run(
                    lambda _png: None,
                    lambda _confirmed: None,
                    lambda: False,
                    expires_at=expires_at,
                ),
                timeout=0.2,
            )

        self.assertTrue(context.page.navigation_started.is_set())
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_persisted_deadline_caps_qr_loading(self):
        scanner, browser, context = self._scanner(
            mode="timeout",
            qr_visible=False,
            qr_timeout_seconds=1,
            login_timeout_seconds=1,
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=0.03)

        with self.assertRaises(LoginTimedOut):
            await asyncio.wait_for(
                scanner.run(
                    lambda _png: None,
                    lambda _confirmed: None,
                    lambda: False,
                    expires_at=expires_at,
                ),
                timeout=0.2,
            )

        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_persisted_deadline_caps_login_wait(self):
        scanner, browser, context = self._scanner(
            mode="timeout", login_timeout_seconds=1
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=0.03)

        with self.assertRaises(LoginTimedOut):
            await asyncio.wait_for(
                scanner.run(
                    lambda _png: None,
                    lambda _confirmed: None,
                    lambda: False,
                    expires_at=expires_at,
                ),
                timeout=0.2,
            )

        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_expired_checkpoint_closes_unstarted_stage_awaitable(self):
        scanner, _browser, _context = self._scanner()
        awaitable = asyncio.sleep(0)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", RuntimeWarning)
            with self.assertRaises(LoginTimedOut):
                await scanner._await_stage(
                    awaitable,
                    lambda: False,
                    asyncio.get_running_loop().time() - 1,
                )
            del awaitable
            gc.collect()

        self.assertFalse(
            any(issubclass(item.category, RuntimeWarning) for item in caught)
        )

    async def test_app_verification_is_reported_and_keeps_session_alive(self):
        scanner, browser, context = self._scanner(mode="verification")
        needs = []
        stopping = asyncio.Event()

        def report_verification(state):
            needs.append(state)
            stopping.set()

        with self.assertRaises(ScanCancelled):
            await scanner.run(
                lambda _png: None,
                lambda _confirmed: None,
                stopping.is_set,
                on_needs_verification=report_verification,
            )

        self.assertEqual(
            [{"required": True, "verification_kind": "app"}],
            needs,
        )
        self.assertFalse(context.page.sms_button.clicked)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_extra_verification_without_callback_still_fails_closed(self):
        scanner, browser, context = self._scanner(mode="verification")

        with self.assertRaises(VerificationRequired):
            await scanner.run(
                lambda _png: None,
                lambda _confirmed: None,
                lambda: False,
            )

        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_cancellation_before_launch_stops_scan(self):
        scanner, browser, context = self._scanner(mode="timeout")

        with self.assertRaises(ScanCancelled):
            await scanner.run(lambda _png: None, lambda: None, lambda: True)

    async def test_late_cancellation_interrupts_navigation_and_closes_resources(self):
        scanner, browser, context = self._scanner(
            mode="navigation", login_timeout_seconds=1
        )
        stopping = asyncio.Event()
        task = asyncio.create_task(
            scanner.run(
                lambda _png: None,
                lambda _confirmed: None,
                stopping.is_set,
            )
        )
        await asyncio.wait_for(context.page.navigation_started.wait(), timeout=0.1)

        stopping.set()

        with self.assertRaises(ScanCancelled):
            await asyncio.wait_for(task, timeout=0.2)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_unexpected_storage_failure_still_closes_context_and_browser(self):
        scanner, browser, context = self._scanner(fail_storage_state=True)

        with self.assertRaises(RuntimeError):
            await scanner.run(
                lambda _png: None, lambda _confirmed: None, lambda: False
            )

        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_browser_closes_when_context_creation_fails(self):
        page = _Page()
        context = _Context(page)
        browser = _Browser(context, fail_new_context=True)
        scanner = DouyinQrScanner(
            playwright_factory=lambda: _PlaywrightManager(browser),
            qr_timeout_seconds=0.01,
            login_timeout_seconds=0.03,
            poll_interval_seconds=0,
        )

        with self.assertRaises(RuntimeError):
            await scanner.run(lambda _png: None, lambda: None, lambda: False)

        self.assertTrue(browser.closed)


if __name__ == "__main__":
    unittest.main()
