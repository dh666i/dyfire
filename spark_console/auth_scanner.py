from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable
from urllib.parse import urlparse

from core.web_chat import (
    CONVERSATION_ITEM_SELECTOR,
    CONVERSATION_TITLE_SELECTOR,
    DouyinUserIdentity,
    UserInfoCollector,
)


CHAT_LOGIN_URL = "https://www.douyin.com/chat"
ACCOUNT_INFO_URL = "https://www.douyin.com/passport/account/info/v2/?aid=6383"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

LOGIN_PANEL_SELECTORS = (
    '[class*="login"]',
    '[class*="Login"]',
    '[class*="passport"]',
)
QR_SELECTORS = (
    'img[alt*="二维码"]',
    '[class*="qrcode"] img',
    '[class*="qr-code"] img',
    '[class*="qrcode"] canvas',
    '[class*="qr-code"] canvas',
)
AUTHENTICATED_SELECTOR = (
    'xpath=//*[contains(@id,"garfish_app_for_douyin_creator_pc_home")]'
)
CHAT_AUTHENTICATED_SELECTOR = ".conversationConversationItemwrapper"
AUTHENTICATED_PATH_PREFIX = "/creator-micro/"
QRCONNECT_PATH = "/passport/web/check_qrconnect/"
QR_SCANNED_SELECTOR = "#douyin_login_comp_scan_code_avatar_desc_id"
QRCONNECT_STATUS_FIELDS = ("status", "qr_status", "qrcode_status")
QRCONNECT_PAYLOAD_FIELDS = ("data", "result", "payload")
QRCONNECT_KNOWN_STATUSES = frozenset(
    {
        "1",
        "new",
        "2",
        "scanned",
        "3",
        "confirmed",
        "4",
        "5",
        "refused",
        "expired",
    }
)
QRCONNECT_CONFIRMING_STATUSES = frozenset(
    {"2", "scanned", "3", "confirmed"}
)
MAX_BROWSER_TEXT_LENGTH = 256
DISPLAY_NAME_SELECTOR = (
    'xpath=//*[contains(@id,"garfish_app_for_douyin_creator_pc_home")]'
    "/div/div[2]/div/div[2]/div[1]/div[2]/div[1]/div[1]/div[1]"
)
UNIQUE_ID_SELECTOR = (
    'xpath=//*[contains(@id,"garfish_app_for_douyin_creator_pc_home")]'
    "/div/div[2]/div/div[2]/div[1]/div[2]/div[1]/div[3]"
)
CONFIRMING_TEXT = ("扫码成功", "请在手机上确认", "已扫码")
VERIFICATION_TEXT = (
    "安全验证",
    "请完成验证",
    "人脸验证",
    "人脸识别",
    "身份验证",
    "请前往抖音完成验证",
)
SMS_VERIFICATION_TEXT = (
    "接收短信验证码",
    "发送短信验证",
    "短信已发送",
    "验证码已发送",
    "短信验证码已发送",
)

logger = logging.getLogger(__name__)


class QrLoadFailed(Exception):
    pass


class LoginTimedOut(Exception):
    pass


class VerificationRequired(Exception):
    pass


class ScanCancelled(Exception):
    pass


class BrowserTextDelivery(str, Enum):
    """Outcome of one cloud-browser text delivery attempt.

    ``not_ready`` is the only retryable result: no editable focused field was
    found and no text-entry side effect was started. ``failed`` is emitted only
    after that retry window expires without starting a side effect. Once
    ``fill`` (or the compatibility keyboard fallback) is invoked, the result
    is terminal for that interaction: either ``delivered`` or ``uncertain``.
    Treating driver exceptions as uncertain is deliberately conservative
    because the browser may have accepted the text before reporting the error.
    """

    NOT_READY = "not_ready"
    DELIVERED = "delivered"
    UNCERTAIN = "uncertain"
    FAILED = "failed"


@dataclass(frozen=True)
class ScannedAccount:
    display_name: str
    unique_id: str | None
    storage_state: dict[str, object]
    conversation_names: tuple[str, ...] = ()
    contact_identities: tuple[DouyinUserIdentity, ...] = ()
    profile: dict | None = None


def _default_playwright_factory():
    from playwright.async_api import async_playwright

    return async_playwright()


async def _invoke(callback: Callable, *args) -> None:
    result = callback(*args)
    if inspect.isawaitable(result):
        await result


class DouyinQrScanner:
    def __init__(
        self,
        playwright_factory=None,
        *,
        qr_timeout_seconds: float = 45,
        login_timeout_seconds: float = 300,
        poll_interval_seconds: float = 0.2,
        text_delivery_timeout_seconds: float = 15,
    ):
        self.playwright_factory = playwright_factory or _default_playwright_factory
        self.qr_timeout_seconds = qr_timeout_seconds
        self.login_timeout_seconds = login_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.text_delivery_timeout_seconds = text_delivery_timeout_seconds

    async def run(
        self,
        on_qr,
        on_confirming,
        cancelled,
        *,
        expires_at: datetime | None = None,
        on_view=None,
        next_interaction=None,
        on_needs_verification=None,
        on_interaction_result=None,
        recovery_storage_state: dict[str, object] | None = None,
    ) -> ScannedAccount:
        deadline = self._deadline(expires_at)
        self._checkpoint(cancelled, deadline)
        browser = None
        context = None
        page = None
        qrconnect_responses: asyncio.Queue = asyncio.Queue()

        def capture_qrconnect(response) -> None:
            try:
                if urlparse(response.url).path == QRCONNECT_PATH:
                    qrconnect_responses.put_nowait(response)
            except Exception:
                return

        async with self.playwright_factory() as playwright:
            try:
                browser = await self._await_stage(
                    playwright.chromium.launch(headless=True),
                    cancelled,
                    deadline,
                )
                context = await self._await_stage(
                    browser.new_context(storage_state=recovery_storage_state)
                    if recovery_storage_state is not None
                    else browser.new_context(), cancelled, deadline
                )
                page = await self._await_stage(
                    context.new_page(), cancelled, deadline
                )
                user_info = UserInfoCollector()
                page.on("response", user_info.capture)
                # Buffer QR status responses before navigation and before the QR
                # publication callback. A fast scan must not fall into a listener
                # gap while the image is being handed to the web process.
                page.on("response", capture_qrconnect)
                if recovery_storage_state is not None:
                    await self._await_stage(
                        page.goto(CHAT_LOGIN_URL, wait_until="domcontentloaded", timeout=120_000),
                        cancelled, deadline,
                    )
                    await self._await_stage(
                        self._wait_for_recovery(
                            page, context, cancelled,
                            on_view=on_view,
                            next_interaction=next_interaction,
                            on_needs_verification=on_needs_verification,
                            on_interaction_result=on_interaction_result,
                        ), cancelled, deadline,
                    )
                    from spark_console.directory_browser import read_self_profile
                    profile = await self._await_stage(read_self_profile(context), cancelled, deadline)
                    storage_state = await self._await_stage(context.storage_state(), cancelled, deadline)
                    return ScannedAccount(profile.get("nickname") or "抖音账号", None, storage_state, profile=profile)
                await self._await_stage(
                    page.goto(
                        CHAT_LOGIN_URL,
                        wait_until="domcontentloaded",
                        timeout=120_000,
                    ),
                    cancelled,
                    deadline,
                )
                await self._await_stage(
                    self._open_chat_login(page, cancelled), cancelled, deadline
                )
                qr = await self._await_stage(
                    self._find_qr(page, cancelled), cancelled, deadline
                )
                png = await self._await_stage(
                    page.screenshot(type="png"), cancelled, deadline
                )
                if not isinstance(png, bytes) or not png.startswith(PNG_SIGNATURE):
                    raise QrLoadFailed()
                await self._await_stage(
                    _invoke(on_qr, png), cancelled, deadline
                )
                baseline_auth_cookies = self._auth_cookie_fingerprint(
                    await self._await_stage(context.cookies(), cancelled, deadline)
                )

                await self._await_stage(
                    self._wait_for_login(
                        page,
                        on_confirming,
                        cancelled,
                        context=context,
                        qr=qr,
                        baseline_auth_cookies=baseline_auth_cookies,
                        on_view=on_view or on_qr,
                        next_interaction=next_interaction,
                        on_needs_verification=on_needs_verification,
                        on_interaction_result=on_interaction_result,
                        qrconnect_responses=qrconnect_responses,
                        initial_view=png,
                    ),
                    cancelled,
                    deadline,
                )
                from spark_console.directory_browser import read_self_profile
                profile = await self._await_stage(read_self_profile(context), cancelled, deadline)
                display_name = profile.get("nickname") or await self._await_stage(
                    self._optional_text(page, DISPLAY_NAME_SELECTOR),
                    cancelled,
                    deadline,
                ) or "抖音账号"
                unique_id = await self._await_stage(
                    self._optional_text(page, UNIQUE_ID_SELECTOR),
                    cancelled,
                    deadline,
                )
                storage_state = await self._await_stage(
                    context.storage_state(), cancelled, deadline
                )
                conversation_names = await self._await_stage(
                    self._visible_conversation_names(page), cancelled, deadline
                )
                contact_identities = await self._await_stage(
                    user_info.drain(), cancelled, deadline
                )
                return ScannedAccount(
                    display_name,
                    unique_id,
                    storage_state,
                    conversation_names,
                    contact_identities,
                    profile,
                )
            finally:
                try:
                    if page is not None:
                        try:
                            page.remove_listener("response", capture_qrconnect)
                        except Exception:
                            pass
                    if context is not None:
                        await context.close()
                finally:
                    if browser is not None:
                        await browser.close()

    async def _wait_for_recovery(self, page, context, cancelled, *, on_view=None,
                                 next_interaction=None, on_needs_verification=None,
                                 on_interaction_result=None):
        live_view = asyncio.create_task(self._stream_browser_view(page, on_view)) if on_view else None
        interaction = asyncio.create_task(self._process_interactions(
            page, next_interaction, on_verification_state=on_needs_verification,
            on_interaction_result=on_interaction_result,
        )) if next_interaction else None
        from core.session_health import inspect_session
        last_verification_reason = None
        try:
            while True:
                if cancelled():
                    raise ScanCancelled()
                evidence = await inspect_session(page, context)
                if evidence.state == "verification":
                    if evidence.reason != last_verification_reason and on_needs_verification:
                        await _invoke(on_needs_verification, {"required": True, "verification_kind": "app", "reason": evidence.reason})
                    last_verification_reason = evidence.reason
                if evidence.state != "verification" and await self._account_session_is_authenticated(context):
                    return
                await asyncio.sleep(self.poll_interval_seconds)
        finally:
            for task in (live_view, interaction):
                if task:
                    task.cancel()
            await asyncio.gather(*(task for task in (live_view, interaction) if task), return_exceptions=True)

    async def _open_chat_login(self, page, cancelled) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.qr_timeout_seconds
        while loop.time() < deadline:
            if cancelled():
                raise ScanCancelled()
            for label in ("扫码登录", "登录"):
                for button in await page.get_by_text(label, exact=True).all():
                    if not await button.is_visible():
                        continue
                    await button.click(timeout=10_000)
                    return
            await asyncio.sleep(self.poll_interval_seconds)
        raise QrLoadFailed()

    async def _find_qr(self, page, cancelled):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.qr_timeout_seconds
        while loop.time() < deadline:
            if cancelled():
                raise ScanCancelled()
            try:
                for panel_selector in LOGIN_PANEL_SELECTORS:
                    panel = page.locator(panel_selector).first
                    if not await panel.is_visible():
                        continue
                    for qr_selector in QR_SELECTORS:
                        qr = panel.locator(qr_selector).first
                        if await self._qr_candidate_is_ready(qr):
                            return qr
                    for qr in await panel.locator("img, canvas").all():
                        if await self._qr_candidate_is_ready(
                            qr, require_semantic=True
                        ):
                            return qr
            except ScanCancelled:
                raise
            except Exception:
                pass
            await asyncio.sleep(self.poll_interval_seconds)
        raise QrLoadFailed()

    @staticmethod
    async def _qr_candidate_is_ready(qr, *, require_semantic: bool = False) -> bool:
        if not await qr.is_visible():
            return False
        box = await qr.bounding_box()
        if box is None:
            return False
        width, height = box["width"], box["height"]
        if not (120 <= width <= 320 and 120 <= height <= 320):
            return False
        if abs(width - height) > 10:
            return False
        if await qr.evaluate(
            'element => Boolean(element.closest("[class*=feature]"))'
        ):
            return False
        if not require_semantic:
            return True
        return bool(
            await qr.evaluate(
                """element => {
                    const nodes = [element];
                    let parent = element.parentElement;
                    for (let depth = 0; parent && depth < 3; depth += 1) {
                        nodes.push(parent);
                        parent = parent.parentElement;
                    }
                    const signal = nodes.map(node => [
                        node.tagName,
                        node.id,
                        node.className,
                        node.getAttribute && node.getAttribute('alt'),
                        node.getAttribute && node.getAttribute('aria-label'),
                        node.getAttribute && node.getAttribute('title'),
                    ].filter(Boolean).join(' ')).join(' ');
                    return element.tagName === 'CANVAS' || /二维码|qrcode|qr-code|qr_code/i.test(signal);
                }"""
            )
        )

    async def _wait_for_login(
        self,
        page,
        on_confirming,
        cancelled,
        *,
        context=None,
        qr=None,
        baseline_auth_cookies=frozenset(),
        on_view=None,
        next_interaction=None,
        on_needs_verification=None,
        on_interaction_result=None,
        qrconnect_responses: asyncio.Queue | None = None,
        initial_view: bytes | None = None,
    ) -> None:
        confirmed = False
        confirmed_event = asyncio.Event()
        # ``confirmed_event`` is deliberately broader than a QR scan marker:
        # it is also used to gate the account-info probe after a page-level
        # hint (for example the QR disappearing).  Extra-verification
        # reporting must use a separate event so that a hidden QR by itself
        # can never be interpreted as an SMS/face challenge.
        scan_confirmed_event = asyncio.Event()
        # The chat DOM is only an authenticated-session fallback signal. It
        # never completes a real browser login by itself; the credential
        # watcher also requires scan confirmation/QR consumption and a changed
        # HttpOnly Douyin cookie fingerprint.
        chat_authenticated_event = asyncio.Event()

        async def confirm_once(_value=True):
            nonlocal confirmed
            if confirmed:
                return
            confirmed = True
            confirmed_event.set()
            await _invoke(on_confirming, True)

        async def confirm_scan(_value=True):
            scan_confirmed_event.set()
            await confirm_once(True)

        # A response can arrive while the QR image is being published to the
        # web process. Consume anything already buffered before creating the
        # long-running watcher task. This removes a short-deadline race where
        # the outer login timeout could cancel the watcher before it got its
        # first event-loop turn, even though the response had been captured.
        if qrconnect_responses is not None:
            while True:
                try:
                    buffered_response = qrconnect_responses.get_nowait()
                except asyncio.QueueEmpty:
                    break
                await self._handle_qrconnect_response(
                    buffered_response,
                    confirm_scan,
                )

        authenticated = asyncio.create_task(
            page.wait_for_selector(
                AUTHENTICATED_SELECTOR, state="visible", timeout=0
            )
        )
        chat_authenticated = asyncio.create_task(
            page.wait_for_selector(
                CHAT_AUTHENTICATED_SELECTOR, state="visible", timeout=0
            )
        )
        authenticated_url = asyncio.create_task(
            self._wait_for_authenticated_url(page)
        )
        confirming = asyncio.create_task(
            self._wait_for_scan_confirmation(page)
        )
        verification = asyncio.create_task(
            self._wait_for_verification(
                page,
                scan_confirmed_event,
            )
        )
        cancellation = asyncio.create_task(self._wait_until_cancelled(cancelled))
        qrconnect = asyncio.create_task(
            self._wait_for_qrconnect_status(
                page,
                confirm_scan,
                responses=qrconnect_responses,
            )
        )
        pending = {
            authenticated,
            chat_authenticated,
            authenticated_url,
            confirming,
            verification,
            cancellation,
            qrconnect,
        }
        credentials = None
        if context is not None and qr is not None:
            credentials = asyncio.create_task(
                self._wait_for_authenticated_credentials(
                    page,
                    context,
                    qr,
                    baseline_auth_cookies,
                    confirm_once,
                    confirmed_event,
                    chat_authenticated_event,
                    scan_confirmed_event,
                )
            )
            pending.add(credentials)
        interaction_processor = None
        if next_interaction is not None:
            interaction_processor = asyncio.create_task(
                self._process_interactions(
                    page,
                    next_interaction,
                    on_verification_state=on_needs_verification,
                    on_interaction_result=on_interaction_result,
                )
            )
            pending.add(interaction_processor)
        live_view = None
        if on_view is not None:
            live_view = asyncio.create_task(
                self._stream_browser_view(page, on_view, initial_view=initial_view)
            )
            pending.add(live_view)
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                if cancellation in done:
                    cancellation.result()
                    raise ScanCancelled()
                # The account-info probe is the only authoritative success
                # signal when a real browser context is present. Give it
                # priority if a stale verification marker disappears in the
                # same event-loop turn.
                if credentials is not None and credentials in done:
                    credentials.result()
                    return
                if verification in done:
                    verification_state = verification.result()
                    # Extra verification is observed only.  The browser page
                    # remains live so the customer can choose SMS, complete
                    # face/security checks, and click every control directly
                    # in the cloud-rendered Douyin page.  It must never be
                    # treated as a terminal scanner result.
                    if on_needs_verification is None:
                        raise VerificationRequired()
                    await _invoke(on_needs_verification, verification_state)
                if confirming in done:
                    confirming.result()
                    await confirm_scan()
                if chat_authenticated in done:
                    chat_authenticated.result()
                    chat_authenticated_event.set()
                    logger.warning(
                        "auth scan chat list visible path=%s",
                        urlparse(page.url).path,
                    )
                    await confirm_once()
                    # With a real browser context, DOM visibility is only a
                    # hint.  Account-info validation below is authoritative;
                    # returning here could accept a session that is still on
                    # an SMS/face-verification page.
                    if credentials is None:
                        await asyncio.sleep(0.1)
                        return
                    # Probe the account API inline before yielding back to the
                    # watcher task.  Besides being the authoritative success
                    # signal, this guarantees that a chat-DOM hint cannot
                    # starve the first credential check when a scan expires
                    # immediately after confirmation.
                    if await self._account_session_is_authenticated(context):
                        return
                    # Let the credential watcher run immediately after the
                    # chat DOM confirmation.  This keeps the account-session
                    # probe deterministic even when the overall login
                    # deadline is intentionally very short (for example in
                    # a timeout test or a quickly-expiring scan session).
                    await asyncio.sleep(0)
                if authenticated in done:
                    authenticated.result()
                    await confirm_once()
                    if credentials is None:
                        return
                if authenticated_url in done:
                    authenticated_url.result()
                    await confirm_once()
                    if credentials is None:
                        return
                if qrconnect in done:
                    qrconnect.result()
                    raise RuntimeError("qrcode status monitor stopped")
                if (
                    interaction_processor is not None
                    and interaction_processor in done
                ):
                    interaction_processor.result()
                if live_view is not None and live_view in done:
                    live_view.result()
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def _wait_for_qrconnect_status(
        self,
        page,
        on_confirming,
        *,
        responses: asyncio.Queue | None = None,
    ) -> None:
        owns_listener = responses is None
        if responses is None:
            responses = asyncio.Queue()
        last_status = None

        def capture(response) -> None:
            if urlparse(response.url).path == QRCONNECT_PATH:
                responses.put_nowait(response)

        if owns_listener:
            page.on("response", capture)
        try:
            while True:
                response = await responses.get()
                last_status = await self._handle_qrconnect_response(
                    response,
                    on_confirming,
                    last_status=last_status,
                )
        finally:
            if owns_listener:
                page.remove_listener("response", capture)

    async def _handle_qrconnect_response(
        self,
        response,
        on_confirming,
        *,
        last_status=None,
    ):
        """Consume one buffered QR status response and return its log state."""
        try:
            body = await response.json()
        except Exception:
            return last_status
        status = self._qrconnect_status(body)
        public_status = (
            status if status in QRCONNECT_KNOWN_STATUSES else "unknown"
        )
        if public_status != last_status:
            log = logger.info if public_status == "new" else logger.warning
            if public_status == "unknown":
                log(
                    "auth scan qrcode status=unknown shape=%s",
                    self._qrconnect_shape(body),
                )
            else:
                log("auth scan qrcode status=%s", public_status)
        if status in QRCONNECT_CONFIRMING_STATUSES:
            await on_confirming(True)
        return public_status

    @staticmethod
    def _qrconnect_status(body) -> str:
        if not isinstance(body, dict):
            return ""
        candidates = [body]
        fallback = ""
        for _depth in range(4):
            nested = []
            for payload in candidates:
                for field in QRCONNECT_STATUS_FIELDS:
                    value = payload.get(field)
                    if (
                        isinstance(value, (str, int, float))
                        and not isinstance(value, bool)
                    ):
                        status = str(value).strip().lower()
                        if status in QRCONNECT_KNOWN_STATUSES:
                            return status
                        if status and not fallback:
                            fallback = status
                for field in QRCONNECT_PAYLOAD_FIELDS:
                    value = payload.get(field)
                    if isinstance(value, dict):
                        nested.append(value)
            if not nested:
                break
            candidates = nested
        return fallback

    @staticmethod
    def _qrconnect_shape(body) -> str:
        def describe(value, depth: int) -> str:
            if not isinstance(value, dict):
                return type(value).__name__
            if depth >= 2:
                return "dict"
            fields = []
            for key in sorted(value, key=lambda item: str(item))[:16]:
                name = str(key)
                if not name.isprintable() or len(name) > 48:
                    name = "field"
                fields.append(f"{name}:{describe(value[key], depth + 1)}")
            return "{" + ",".join(fields) + "}"

        return describe(body, 0)

    async def _wait_for_authenticated_credentials(
        self,
        page,
        context,
        qr,
        baseline,
        on_confirming,
        confirmed_event,
        chat_authenticated_event: asyncio.Event | None = None,
        scan_confirmed_event: asyncio.Event | None = None,
    ) -> None:
        chat_authenticated_event = (
            chat_authenticated_event or asyncio.Event()
        )
        scan_confirmed_event = scan_confirmed_event or asyncio.Event()
        qr_hidden = False
        credentials_changed = False
        while True:
            if not qr_hidden:
                try:
                    qr_hidden = not await qr.is_visible()
                except Exception:
                    qr_hidden = True
                if qr_hidden:
                    logger.warning(
                        "auth scan qr consumed path=%s", urlparse(page.url).path
                    )
                    await on_confirming(True)
            if confirmed_event.is_set():
                current = self._auth_cookie_fingerprint(await context.cookies())
                if current and current != baseline and not credentials_changed:
                    credentials_changed = True
                    logger.warning(
                        "auth scan credentials changed path=%s",
                        urlparse(page.url).path,
                    )
                if await self._account_session_is_authenticated(context):
                    logger.warning(
                        "auth scan authenticated by account api path=%s",
                        urlparse(page.url).path,
                    )
                    return
                confirmation_observed = (
                    scan_confirmed_event.is_set() or qr_hidden
                )
                if (
                    confirmation_observed
                    and credentials_changed
                    and chat_authenticated_event.is_set()
                ):
                    logger.warning(
                        "auth scan authenticated by chat fallback "
                        "path=%s scan_confirmed=%s qr_consumed=%s "
                        "cookies_changed=true chat_visible=true",
                        urlparse(page.url).path,
                        scan_confirmed_event.is_set(),
                        qr_hidden,
                    )
                    return
            await asyncio.sleep(self.poll_interval_seconds)

    async def _process_interactions(
        self,
        page,
        next_interaction,
        *legacy_args,
        on_verification_state=None,
        on_interaction_result=None,
    ) -> None:
        # ``verification_ready`` was part of the short-lived structured-SMS
        # API.  Accept and ignore it positionally so existing worker/test
        # callers can roll forward without breaking, while real page clicks
        # remain unrestricted.
        if legacy_args:
            if len(legacy_args) == 1 and on_verification_state is None:
                candidate = legacy_args[0]
                if callable(candidate) and not isinstance(candidate, asyncio.Event):
                    on_verification_state = candidate
            elif len(legacy_args) >= 2 and on_verification_state is None:
                on_verification_state = legacy_args[1]
        claim_failures = 0
        pending_texts = deque()
        active_text_ids = set()
        completed_text_ids: dict[tuple[str, object], BrowserTextDelivery] = {}
        loop = asyncio.get_running_loop()
        while True:
            action = None
            try:
                action = next_interaction()
                if inspect.isawaitable(action):
                    action = await action
                claim_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                claim_failures += 1
                logger.warning(
                    "auth scan interaction claim failed attempt=%s",
                    claim_failures,
                    exc_info=True,
                )
                if claim_failures >= 4:
                    raise
                await asyncio.sleep(max(0.25, self.poll_interval_seconds))
                continue

            kind = action.get("kind") if isinstance(action, dict) else None
            try:
                if kind == "click":
                    delivered = False
                    for attempt in range(4):
                        delivered = await self._forward_browser_click(page, action)
                        if delivered:
                            break
                        if attempt < 3:
                            await asyncio.sleep(
                                max(0.25, self.poll_interval_seconds)
                            )
                    if not delivered:
                        logger.warning("auth scan browser click was not delivered")
                elif kind == "text":
                    value = action.get("text")
                    if self._is_safe_browser_text(value):
                        interaction_id = self._text_interaction_id(action)
                        interaction_key = self._text_interaction_key(
                            interaction_id
                        )
                        if interaction_key in completed_text_ids:
                            logger.info(
                                "auth scan ignored completed text interaction"
                            )
                        elif interaction_key in active_text_ids:
                            logger.info(
                                "auth scan ignored duplicate pending text "
                                "interaction"
                            )
                        else:
                            # Keep claimed text briefly in memory if focus is
                            # not ready yet. Distinct interaction IDs are
                            # intentionally not deduplicated by text content:
                            # after a rejected password the owner must be able
                            # to submit another explicit attempt.
                            pending_texts.append(
                                {
                                    "value": value,
                                    "interaction_id": interaction_id,
                                    "interaction_key": interaction_key,
                                    "deadline": loop.time()
                                    + self.text_delivery_timeout_seconds,
                                    "last_result": None,
                                }
                            )
                            active_text_ids.add(interaction_key)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "auth scan browser interaction failed",
                    exc_info=True,
                )

            if pending_texts:
                pending_text = pending_texts[0]
                try:
                    delivery = await self._deliver_browser_text(
                        page, pending_text["value"]
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A driver failure may happen after the browser accepted
                    # the value. Never retry the same interaction after an
                    # ambiguous exception.
                    delivery = BrowserTextDelivery.UNCERTAIN
                    # Do not attach exception details here. A browser-driver
                    # error may include the fill argument, which can be a
                    # user's Douyin password.
                    logger.warning("auth scan browser text delivery failed")

                if delivery is BrowserTextDelivery.NOT_READY:
                    if pending_text["last_result"] is not delivery:
                        pending_text["last_result"] = delivery
                        await self._report_text_delivery(
                            on_interaction_result,
                            pending_text["interaction_id"],
                            delivery,
                        )
                    if loop.time() >= pending_text["deadline"]:
                        pending_texts.popleft()
                        active_text_ids.discard(
                            pending_text["interaction_key"]
                        )
                        delivery = BrowserTextDelivery.FAILED
                        completed_text_ids[
                            pending_text["interaction_key"]
                        ] = delivery
                        pending_text["value"] = None
                        await self._report_text_delivery(
                            on_interaction_result,
                            pending_text["interaction_id"],
                            delivery,
                        )
                        logger.warning("auth scan browser text delivery expired")
                else:
                    # ``delivered`` and ``uncertain`` are terminal. Remove the
                    # plaintext and finalize the ID before publishing the
                    # result so callback failures cannot cause another fill.
                    pending_texts.popleft()
                    active_text_ids.discard(pending_text["interaction_key"])
                    completed_text_ids[
                        pending_text["interaction_key"]
                    ] = delivery
                    pending_text["value"] = None
                    await self._report_text_delivery(
                        on_interaction_result,
                        pending_text["interaction_id"],
                        delivery,
                    )
                    if delivery is BrowserTextDelivery.UNCERTAIN:
                        logger.warning(
                            "auth scan browser text delivery outcome uncertain"
                        )
            await asyncio.sleep(max(0.25, self.poll_interval_seconds))

    @staticmethod
    def _text_interaction_id(action: dict[str, object]) -> object | None:
        for field in ("interaction_id", "id"):
            value = action.get(field)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return value
        return None

    @staticmethod
    def _text_interaction_key(
        interaction_id: object | None,
    ) -> tuple[str, object]:
        if interaction_id is None:
            # Legacy action sources did not expose an ID. Their claim operation
            # is destructive, so each claimed mapping is a distinct explicit
            # action and receives a process-local key.
            return ("legacy", object())
        return (type(interaction_id).__name__, interaction_id)

    @staticmethod
    async def _report_text_delivery(
        callback,
        interaction_id: object | None,
        delivery: BrowserTextDelivery,
    ) -> None:
        if callback is None:
            return
        result = {
            "kind": "text",
            "delivery": delivery.value,
        }
        if interaction_id is not None:
            result["interaction_id"] = interaction_id
        try:
            await _invoke(callback, result)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Delivery has already been finalized. Reporting must never make a
            # sensitive interaction eligible for another browser-side attempt.
            logger.warning("auth scan text delivery result publication failed")

    async def _stream_browser_view(
        self,
        page,
        on_view,
        next_interaction=None,
        *,
        initial_view: bytes | None = None,
    ) -> None:
        # ``next_interaction`` is retained as an ignored positional argument
        # for callers compiled against the earlier live-view API. Interaction
        # delivery now has its own worker so screenshots cannot starve clicks.
        del next_interaction
        # The first frame was already captured and published before the login
        # watchers started.  Seed the digest so an unchanged page is not sent
        # twice, while every subsequent visual change is still streamed.
        last_digest = (
            hashlib.sha256(initial_view).digest()
            if isinstance(initial_view, bytes)
            and initial_view.startswith(PNG_SIGNATURE)
            else None
        )
        transient_failures = 0
        while True:
            try:
                png = await page.screenshot(type="png")
                if not isinstance(png, bytes) or not png.startswith(PNG_SIGNATURE):
                    raise ValueError("invalid browser view")
            except asyncio.CancelledError:
                raise
            except Exception:
                transient_failures += 1
                logger.warning(
                    "auth scan browser screenshot failed attempt=%s",
                    transient_failures,
                    exc_info=True,
                )
                if transient_failures >= 4:
                    raise
                await asyncio.sleep(max(0.75, self.poll_interval_seconds))
                continue

            digest = hashlib.sha256(png).digest()
            if digest != last_digest:
                try:
                    await _invoke(on_view, png)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    transient_failures += 1
                    logger.warning(
                        "auth scan browser view publication failed attempt=%s",
                        transient_failures,
                        exc_info=True,
                    )
                    if transient_failures >= 4:
                        raise
                    await asyncio.sleep(max(0.75, self.poll_interval_seconds))
                    continue
                last_digest = digest
            if transient_failures:
                logger.info("auth scan browser view recovered")
            transient_failures = 0
            await asyncio.sleep(max(0.75, self.poll_interval_seconds))

    @classmethod
    async def _forward_browser_click(cls, page, action: dict[str, object]) -> bool:
        if not cls._is_douyin_page(page):
            return False
        try:
            x = float(action.get("x", -1))
            y = float(action.get("y", -1))
        except (TypeError, ValueError):
            return False
        if not (0 <= x <= 1 and 0 <= y <= 1):
            return False
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        width = float(viewport.get("width", 0))
        height = float(viewport.get("height", 0))
        if width <= 0 or height <= 0:
            return False
        await page.mouse.click(x * width, y * height)
        return True

    @classmethod
    async def _deliver_browser_text(
        cls, page, value: str
    ) -> BrowserTextDelivery:
        """Deliver one text interaction with a strict at-most-once boundary.

        Locator discovery and editability checks are side-effect free and may
        move on to a child frame. Once a text-entry method is invoked, however,
        no other frame is inspected and no retry is attempted. A raised driver
        exception is therefore ``uncertain``, not ``not_ready``.
        """

        if (
            not cls._is_douyin_page(page)
            or not cls._is_safe_browser_text(value)
        ):
            return BrowserTextDelivery.NOT_READY

        targets = [page]
        main_frame = getattr(page, "main_frame", None)
        if callable(main_frame):
            main_frame = main_frame()
        frames = getattr(page, "frames", ())
        if callable(frames):
            frames = frames()
        for frame in frames or ():
            # ``Page.locator`` already addresses the main frame. Playwright's
            # ``page.frames`` includes that same frame, so processing both can
            # turn one interaction into two fill attempts.
            if cls._same_browser_target(frame, main_frame):
                continue
            if not any(
                cls._same_browser_target(frame, target) for target in targets
            ):
                targets.append(frame)

        locator_supported = False
        for target in targets:
            locator = getattr(target, "locator", None)
            if not callable(locator):
                continue
            locator_supported = True
            try:
                field = locator(":focus")
                first = getattr(field, "first", field)
                field = first() if callable(first) else first
                is_visible = getattr(field, "is_visible", None)
                if callable(is_visible) and not await is_visible():
                    continue
                is_editable = getattr(field, "is_editable", None)
                if callable(is_editable) and not await is_editable():
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                # No fill has started, so another distinct child frame may be
                # checked safely.
                continue

            try:
                await field.fill(value, timeout=2_000)
                return BrowserTextDelivery.DELIVERED
            except asyncio.CancelledError:
                raise
            except Exception:
                # The browser may have applied the value before the transport
                # reported an error. This is terminal for the interaction.
                return BrowserTextDelivery.UNCERTAIN

        # Retain compatibility with lightweight page doubles used by callers;
        # real Playwright pages always take the focused-field path above.
        if not locator_supported:
            try:
                await page.keyboard.type(value)
                return BrowserTextDelivery.DELIVERED
            except asyncio.CancelledError:
                raise
            except Exception:
                return BrowserTextDelivery.UNCERTAIN
        return BrowserTextDelivery.NOT_READY

    @classmethod
    async def _forward_browser_text(cls, page, value: str) -> bool:
        """Compatibility wrapper returning the legacy boolean projection."""

        return (
            await cls._deliver_browser_text(page, value)
            is BrowserTextDelivery.DELIVERED
        )

    @staticmethod
    def _same_browser_target(left, right) -> bool:
        if left is None or right is None:
            return False
        if left is right:
            return True
        try:
            return bool(left == right)
        except Exception:
            return False

    @classmethod
    async def _forward_numeric_text(cls, page, value: str) -> bool:
        """Compatibility wrapper for callers that only submit an OTP."""

        if (
            not isinstance(value, str)
            or not value.isascii()
            or not value.isdigit()
            or not 4 <= len(value) <= 8
        ):
            return False
        return await cls._forward_browser_text(page, value)

    @staticmethod
    def _is_safe_browser_text(value: object) -> bool:
        return bool(
            isinstance(value, str)
            and 1 <= len(value) <= MAX_BROWSER_TEXT_LENGTH
            and value.isprintable()
        )

    @staticmethod
    def _is_douyin_page(page) -> bool:
        hostname = (urlparse(str(page.url)).hostname or "").lower()
        return hostname == "douyin.com" or hostname.endswith(".douyin.com")

    async def _wait_for_verification(
        self,
        page,
        confirmed_event: asyncio.Event,
        *,
        allow_sms_selection: bool | None = None,
    ) -> dict[str, object]:
        """Observe an extra-verification page without taking it over.

        The cloud-rendered Douyin page is the source of truth for SMS, face,
        and security challenges.  This watcher only emits a non-terminal
        signal for the web UI; it never selects an option, sends a code, or
        ends the browser session.
        """
        # ``allow_sms_selection`` belonged to the removed structured-SMS
        # state machine. Keep accepting it for source compatibility, but do
        # not let it enable any page automation.
        del allow_sms_selection
        await confirmed_event.wait()
        sms_text = asyncio.create_task(
            self._wait_for_any_text(page, SMS_VERIFICATION_TEXT)
        )
        extra_text = asyncio.create_task(
            self._wait_for_any_text(page, VERIFICATION_TEXT)
        )
        tasks = {sms_text, extra_text}
        try:
            done, _pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            sms_detected = sms_text in done
            if sms_detected:
                sms_text.result()
            if extra_text in done:
                extra_text.result()
            return {
                "required": True,
                # Keep the public projection compatible with the existing
                # session model.  The value is only informational; the page
                # itself determines whether the challenge is SMS, face, or
                # another security check.
                "verification_kind": "sms" if sms_detected else "app",
            }
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @classmethod
    async def _wait_for_scan_confirmation(cls, page) -> None:
        text = asyncio.create_task(cls._wait_for_any_text(page, CONFIRMING_TEXT))
        marker = asyncio.create_task(
            page.wait_for_selector(
                QR_SCANNED_SELECTOR,
                state="visible",
                timeout=0,
            )
        )
        tasks = {text, marker}
        try:
            done, _pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            first = next(iter(done))
            first.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _account_session_is_authenticated(context) -> bool:
        try:
            response = await context.request.get(ACCOUNT_INFO_URL, timeout=10_000)
            body = await response.json()
        except Exception:
            return False
        data = body.get("data") if isinstance(body, dict) else None
        return bool(
            isinstance(data, dict)
            and body.get("message") == "success"
            and data.get("error_code") == 0
            and data.get("user_id")
        )

    @staticmethod
    def _auth_cookie_fingerprint(cookies) -> frozenset[tuple[str, str, str, str]]:
        return frozenset(
            (
                str(cookie.get("name", "")),
                str(cookie.get("domain", "")),
                str(cookie.get("path", "")),
                str(cookie.get("value", "")),
            )
            for cookie in cookies
            if isinstance(cookie, dict)
            and cookie.get("httpOnly") is True
            and str(cookie.get("domain", "")).lower().endswith("douyin.com")
        )

    async def _wait_for_authenticated_url(self, page) -> None:
        while True:
            path = urlparse(page.url).path
            if path.startswith(AUTHENTICATED_PATH_PREFIX):
                return
            await asyncio.sleep(self.poll_interval_seconds)

    @staticmethod
    async def _wait_for_any_text(page, values: tuple[str, ...]) -> str:
        tasks = {
            asyncio.create_task(
                page.wait_for_selector(
                    f"text={value}", state="visible", timeout=0
                )
            ): value
            for value in values
        }
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            first = next(iter(done))
            first.result()
            return tasks[first]
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_until_cancelled(self, cancelled) -> None:
        while True:
            if cancelled():
                return
            await asyncio.sleep(self.poll_interval_seconds)

    def _deadline(self, expires_at: datetime | None) -> float:
        if expires_at is None:
            remaining = self.login_timeout_seconds
        else:
            value = expires_at
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            else:
                value = value.astimezone(timezone.utc)
            remaining = (value - datetime.now(timezone.utc)).total_seconds()
        return asyncio.get_running_loop().time() + max(0, remaining)

    @staticmethod
    def _checkpoint(cancelled, deadline: float) -> None:
        if cancelled():
            raise ScanCancelled()
        if asyncio.get_running_loop().time() >= deadline:
            raise LoginTimedOut()

    async def _await_stage(self, awaitable, cancelled, deadline: float):
        try:
            self._checkpoint(cancelled, deadline)
        except BaseException:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            elif isinstance(awaitable, asyncio.Future):
                awaitable.cancel()
            raise
        operation = asyncio.create_task(awaitable)
        cancellation = asyncio.create_task(self._wait_until_cancelled(cancelled))
        timeout = asyncio.create_task(
            asyncio.sleep(
                max(0, deadline - asyncio.get_running_loop().time())
            )
        )
        watchers = {cancellation, timeout}
        try:
            done, _pending = await asyncio.wait(
                {operation, *watchers}, return_when=asyncio.FIRST_COMPLETED
            )
            if operation in done or operation.done():
                return operation.result()
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            if cancellation in done:
                cancellation.result()
                raise ScanCancelled()
            timeout.result()
            raise LoginTimedOut()
        finally:
            if not operation.done():
                operation.cancel()
            for task in watchers:
                task.cancel()
            await asyncio.gather(operation, *watchers, return_exceptions=True)

    @staticmethod
    async def _visible_conversation_names(page) -> tuple[str, ...]:
        names = []
        seen = set()
        try:
            items = await page.locator(CONVERSATION_ITEM_SELECTOR).all()
            for item in items:
                if hasattr(item, "is_visible") and not await item.is_visible():
                    continue
                name = (
                    await item.locator(CONVERSATION_TITLE_SELECTOR).inner_text()
                ).strip()
                if name and name not in seen:
                    seen.add(name)
                    names.append(name)
        except Exception:
            logger.warning("auth scan could not snapshot visible conversations")
        return tuple(names)

    @staticmethod
    async def _required_text(page, selector: str) -> str:
        text = (await page.locator(selector).first.inner_text(timeout=5_000)).strip()
        if not text:
            raise RuntimeError("account identity unavailable")
        return text

    @staticmethod
    async def _optional_text(page, selector: str) -> str | None:
        locator = page.locator(selector).first
        try:
            if not await locator.is_visible():
                return None
            text = (await locator.inner_text(timeout=5_000)).strip()
        except Exception:
            return None
        for prefix in ("抖音号：", "抖音号:"):
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
                break
        return text or None
