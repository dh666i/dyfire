from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from inspect import isawaitable

from core.stickers import (
    DOUYIN_SPARK_STICKER,
    NativeStickerPreparationError,
    NativeStickerSubmissionError,
    send_douyin_sticker,
)
from core.web_chat import (
    CHAT_EDITOR_SELECTOR,
    WEB_CHAT_URL,
    TargetNotFoundError,
    UserInfoCollector,
    WebChatLoginRequiredError,
    ensure_web_chat_authenticated,
    select_web_chat_target,
)
from spark_console.credentials import CredentialError, CredentialPayload
from core.session_health import inspect_session, require_session, wait_for_session, SessionUnavailable


logger = logging.getLogger(__name__)


class ExecutionStage(StrEnum):
    STARTING = "starting"
    AUTHENTICATING = "authenticating"
    SELECTING_TARGET = "selecting_target"
    SENDING = "sending"
    CONFIRMING = "confirming"
    SUBMITTED = "submitted"
    COMPLETE = "complete"


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    stage: str
    error_code: str | None = None
    error_summary: str | None = None
    retryable: bool = False
    refreshed_storage_state: dict[str, object] | None = field(
        default=None, repr=False, compare=False
    )
    contact_avatars: dict[str, str] = field(default_factory=dict, repr=False, compare=False)
    health_state: str | None = None
    health_reason: str | None = None


class DouyinExecutor:
    SESSION_IDLE_SECONDS = 180

    def __init__(self):
        self._live: dict[str, object] | None = None

    async def close_session(self) -> None:
        live = self._live
        self._live = None
        if not live:
            return
        for key in ("context", "browser"):
            closer = getattr(live.get(key), "close", None)
            if closer is None:
                continue
            try:
                await closer()
            except Exception as error:
                logger.warning(
                    "browser %s cleanup failed exception=%s",
                    key,
                    type(error).__name__,
                )
        stop = getattr(live.get("playwright"), "stop", None)
        if stop is not None:
            try:
                await stop()
            except Exception as error:
                logger.warning("playwright stop failed exception=%s", type(error).__name__)

    async def _open_browser(self, payload: CredentialPayload):
        from playwright.async_api import async_playwright

        manager = async_playwright()
        start = getattr(manager, "start", None)
        playwright = await start() if start is not None else await manager.__aenter__()
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(**payload.context_options())
        legacy_cookies = payload.cookies_to_add()
        if legacy_cookies:
            await context.add_cookies(legacy_cookies)
        page = await context.new_page()
        return {
            "playwright": playwright,
            "browser": browser,
            "context": context,
            "page": page,
            "manager": manager,
            "used_at": time.monotonic(),
        }

    def _live_matches(
        self,
        session_key: str,
        credential_version: int,
        credential_fingerprint: str | None = None,
    ) -> bool:
        live = self._live
        if not live:
            return False
        if live.get("key") != session_key or live.get("version") != credential_version:
            return False
        if credential_fingerprint is not None and live.get("fingerprint") != credential_fingerprint:
            return False
        used_at = live.get("used_at")
        if not isinstance(used_at, (int, float)):
            return False
        return time.monotonic() - used_at < self.SESSION_IDLE_SECONDS

    async def _reset_chat_surface(self, page) -> None:
        """Return a reused browser session to the conversation list.

        A persistent account session keeps cookies warm, but the previous run
        leaves the page inside the last friend's thread. The next run must not
        search that stale DOM: on busy schedules this was the main source of
        mixed success/failure batches. A fresh navigation is cheap compared
        with a failed send and does not touch credentials or submit anything.
        """
        try:
            await page.goto(WEB_CHAT_URL, wait_until="domcontentloaded", timeout=120_000)
        except Exception as error:
            # The subsequent session probe has better evidence (login page,
            # verification prompt, or a still-hydrating chat surface).
            logger.info("chat surface refresh deferred exception=%s", type(error).__name__)

    async def execute(
        self,
        cookie_payload: bytes | bytearray,
        target: str,
        message: str,
        credential_version: int = 1,
        target_sec_uid: str | None = None,
        on_submitted: Callable[[], object] | None = None,
        message_kind: str = "text",
        session_key: str | None = None,
    ) -> ExecutionResult:
        from playwright.async_api import async_playwright
        from core.tasks import (
            MessageNetworkAckMonitor,
            capture_message_confirmation_baseline,
            confirm_message_sent,
            dispose_message_confirmation_probe,
        )

        stage = ExecutionStage.AUTHENTICATING
        message_kind = str(message_kind or "text").strip().lower()
        message_submitted = False
        delivery_confirmed = False
        refreshed_storage_state = None
        delivery_monitor = None
        user_info = UserInfoCollector()
        playwright_cm = None
        context = None
        browser = None
        page = None
        reused = False
        user_info_listener_pages: list[object] = []
        try:
            raw_cookie_payload = bytes(cookie_payload)
            credential_fingerprint = hashlib.sha256(raw_cookie_payload).hexdigest()
            payload = CredentialPayload.parse(raw_cookie_payload, credential_version)
            if session_key:
                if not self._live_matches(
                    session_key, credential_version, credential_fingerprint
                ):
                    await self.close_session()
                    live = await self._open_browser(payload)
                    live["key"] = session_key
                    live["version"] = credential_version
                    live["fingerprint"] = credential_fingerprint
                    self._live = live
                else:
                    live = self._live
                    reused = True
                context = live["context"]
                page = live["page"]
                browser = live["browser"]
            else:
                playwright_cm = async_playwright()
                playwright = await playwright_cm.__aenter__()
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(**payload.context_options())
                legacy_cookies = payload.cookies_to_add()
                if legacy_cookies:
                    await context.add_cookies(legacy_cookies)
                page = await context.new_page()
            try:
                if message_kind == "text":
                    delivery_monitor = MessageNetworkAckMonitor(message)
                    delivery_monitor.attach(page)
                page.on("response", user_info.capture)
                user_info_listener_pages.append(page)
                if reused:
                    await self._reset_chat_surface(page)
                    evidence = await wait_for_session(page, context, timeout=45)
                    if evidence.state != "valid":
                        await self.close_session()
                        live = await self._open_browser(payload)
                        live["key"] = session_key
                        live["version"] = credential_version
                        live["fingerprint"] = credential_fingerprint
                        self._live = live
                        context = live["context"]
                        page = live["page"]
                        browser = live["browser"]
                        if message_kind == "text":
                            delivery_monitor = MessageNetworkAckMonitor(message)
                            delivery_monitor.attach(page)
                        page.on("response", user_info.capture)
                        user_info_listener_pages.append(page)
                        reused = False
                if not reused:
                    await require_session(page, context, navigate=True)
                stage = ExecutionStage.SELECTING_TARGET
                try:
                    identity = (
                        await user_info.wait_for(target_sec_uid)
                        if target_sec_uid
                        else None
                    )
                    aliases = identity.aliases if identity else ()
                    try:
                        await select_web_chat_target(
                            page, target, timeout=45000, aliases=aliases
                        )
                        await page.wait_for_selector(CHAT_EDITOR_SELECTOR, timeout=30000)
                    except Exception as first_error:
                        # Playwright's TimeoutError is a separate exception
                        # class from the built-in one. Match it by name too so
                        # a selector timeout enters the same bounded page
                        # reset/retry path instead of falling straight through
                        # to a generic conversation failure.
                        is_timeout = isinstance(first_error, TimeoutError) or (
                            type(first_error).__name__ == "TimeoutError"
                        )
                        if not isinstance(first_error, TargetNotFoundError) and not is_timeout:
                            raise
                        # The web chat list is virtualized and can still be
                        # settling after navigation. Rebuild the list once and
                        # retry selection; this is deliberately bounded and is
                        # never used after the send checkpoint.
                        logger.warning(
                            "target surface not ready target=%s retry=1 exception=%s",
                            target,
                            type(first_error).__name__,
                        )
                        await self._reset_chat_surface(page)
                        # A plain session probe can still observe the previous
                        # thread while the navigation is settling. Re-run the
                        # bounded navigation/session check so the retry starts
                        # from a hydrated conversation list.
                        await require_session(page, context, navigate=True)
                        await select_web_chat_target(
                            page, target, timeout=45000, aliases=aliases
                        )
                        await page.wait_for_selector(CHAT_EDITOR_SELECTOR, timeout=30000)
                except Exception:
                    # Login surfaces can render after domcontentloaded or while
                    # target selection is waiting. Reclassify before the page is
                    # closed so an expired session is never reported as a target
                    # or automation failure.
                    await require_session(page, context)
                    raise
                stage = ExecutionStage.SENDING
                if message_kind == "douyin_sticker":
                    try:
                        sticker_result = await send_douyin_sticker(
                            page,
                            message or DOUYIN_SPARK_STICKER,
                            on_submitted=on_submitted,
                            timeout=45000,
                        )
                    except NativeStickerPreparationError as error:
                        return ExecutionResult(
                            False,
                            ExecutionStage.SENDING,
                            "native_sticker_unavailable",
                            str(error),
                            retryable=True,
                        )
                    except NativeStickerSubmissionError as error:
                        message_submitted = True
                        try:
                            refreshed_storage_state = await context.storage_state()
                        except Exception:
                            logger.warning(
                                "native sticker submitted but credential refresh failed"
                            )
                        return ExecutionResult(
                            True,
                            ExecutionStage.SUBMITTED,
                            "delivery_confirmation_unavailable",
                            str(error),
                            refreshed_storage_state=refreshed_storage_state,
                            contact_avatars=user_info.avatar_urls,
                        )
                    message_submitted = sticker_result.submitted
                    try:
                        refreshed_storage_state = await context.storage_state()
                    except Exception:
                        logger.warning(
                            "native sticker sent but credential refresh failed"
                        )
                    delivery_confirmed = sticker_result.confirmed
                    if delivery_confirmed:
                        return ExecutionResult(
                            True,
                            ExecutionStage.COMPLETE,
                            refreshed_storage_state=refreshed_storage_state,
                            contact_avatars=user_info.avatar_urls,
                        )
                    return ExecutionResult(
                        True,
                        ExecutionStage.SUBMITTED,
                        "delivery_confirmation_unavailable",
                        "原生续火花已提交，页面未能二次确认",
                        refreshed_storage_state=refreshed_storage_state,
                        contact_avatars=user_info.avatar_urls,
                    )
                if message_kind != "text":
                    return ExecutionResult(
                        False,
                        ExecutionStage.SENDING,
                        "unsupported_message_kind",
                        "不支持的消息类型",
                        retryable=False,
                    )
                editor = page.locator(CHAT_EDITOR_SELECTOR).first
                lines = message.splitlines() or [message]
                for index, line in enumerate(lines):
                    await editor.type(line)
                    if index < len(lines) - 1:
                        await editor.press("Shift+Enter")
                confirmation_baseline = None
                try:
                    confirmation_baseline = (
                        await capture_message_confirmation_baseline(
                            page,
                            message,
                            network_monitor=delivery_monitor,
                        )
                    )
                except Exception as error:
                    logger.warning(
                        "message confirmation baseline unavailable exception=%s",
                        type(error).__name__,
                    )
                if on_submitted is not None:
                    try:
                        callback_result = on_submitted()
                        if isawaitable(callback_result):
                            await callback_result
                    except Exception:
                        logger.warning(
                            "submission checkpoint failed before message send"
                        )
                        await dispose_message_confirmation_probe(
                            page, confirmation_baseline
                        )
                        return ExecutionResult(
                            False,
                            ExecutionStage.SENDING,
                            "submission_checkpoint_failed",
                            "发送前安全记录失败，本次未执行发送",
                            retryable=True,
                        )
                # Once the durable checkpoint exists, delivery becomes
                # intentionally at-most-once. If Enter or the process fails
                # after this point, the run must stay unconfirmed instead of
                # being retried and potentially sending a duplicate message.
                message_submitted = True
                try:
                    await editor.press("Enter")
                except Exception:
                    await dispose_message_confirmation_probe(
                        page, confirmation_baseline
                    )
                    return ExecutionResult(
                        True,
                        ExecutionStage.SUBMITTED,
                        "delivery_confirmation_unavailable",
                        "消息已进入发送步骤，但页面未能确认是否送达",
                    )
                try:
                    refreshed_storage_state = await context.storage_state()
                except Exception:
                    logger.warning("message submitted but credential refresh failed")
                stage = ExecutionStage.CONFIRMING
                try:
                    if confirmation_baseline is None:
                        raise RuntimeError("message confirmation baseline unavailable")
                    confirmation = await confirm_message_sent(
                        page,
                        editor,
                        message,
                        timeout=45000,
                        baseline_count=confirmation_baseline,
                    )
                    if confirmation is None:
                        # The original confirmation helper returned None
                        # on success. Keep accepting that result for
                        # compatibility with legacy probes and adapters;
                        # the helper itself has already waited for its
                        # confirmation signal before returning.
                        logger.info(
                            "message delivery confirmed source=legacy reason=helper_returned_none"
                        )
                    else:
                        logger.info(
                            "message delivery confirmed source=%s reason=%s",
                            confirmation.source,
                            confirmation.reason,
                        )
                except Exception as error:
                    logger.warning(
                        "message submitted but confirmation was unavailable exception=%s",
                        type(error).__name__,
                    )
                    return ExecutionResult(
                        True,
                        ExecutionStage.SUBMITTED,
                        "delivery_confirmation_unavailable",
                        "消息已提交，页面未能二次确认",
                        refreshed_storage_state=refreshed_storage_state,
                        contact_avatars=user_info.avatar_urls,
                    )
                delivery_confirmed = True
                return ExecutionResult(
                    True,
                    ExecutionStage.COMPLETE,
                    refreshed_storage_state=refreshed_storage_state,
                    contact_avatars=user_info.avatar_urls,
                )
            finally:
                # A reused page survives across scheduled tasks. Remove the
                # per-run user-info listener or every task would keep parsing
                # future responses, gradually slowing the session and mixing
                # stale identity data into later runs.
                for listener_page in reversed(user_info_listener_pages):
                    remove_listener = getattr(listener_page, "remove_listener", None)
                    if not callable(remove_listener):
                        continue
                    try:
                        remove_listener("response", user_info.capture)
                    except Exception:
                        logger.debug("user-info response listener cleanup unavailable")
                if delivery_monitor is not None:
                    try:
                        delivery_monitor.detach()
                    except Exception:
                        pass
                if session_key:
                    if stage == ExecutionStage.AUTHENTICATING and not message_submitted:
                        await self.close_session()
                    elif self._live is not None:
                        self._live["used_at"] = time.monotonic()
                else:
                    try:
                        if context is not None:
                            await context.close()
                    except Exception as error:
                        logger.warning(
                            "browser context cleanup failed exception=%s",
                            type(error).__name__,
                        )
                    try:
                        if browser is not None:
                            await browser.close()
                    except Exception as error:
                        logger.warning(
                            "browser cleanup failed exception=%s",
                            type(error).__name__,
                        )
                    if playwright_cm is not None:
                        try:
                            await playwright_cm.__aexit__(None, None, None)
                        except Exception as error:
                            logger.warning(
                                "playwright cleanup failed exception=%s",
                                type(error).__name__,
                            )
        except SessionUnavailable as error:
            await self.close_session()
            return ExecutionResult(False, ExecutionStage.AUTHENTICATING,
                'account_verification_required' if error.evidence.state == 'verification' else 'account_health_pending',
                error.evidence.reason, health_state=error.evidence.state, health_reason=error.evidence.reason)
        except WebChatLoginRequiredError:
            await self.close_session()
            return ExecutionResult(
                False,
                ExecutionStage.AUTHENTICATING,
                "account_health_pending",
                "登录信号异常，等待复核",
                retryable=False,
                health_state='temporary', health_reason='旧版页面检测报告登录异常，等待复核',
            )
        except TargetNotFoundError:
            return ExecutionResult(
                False,
                ExecutionStage.SELECTING_TARGET,
                "target_not_found",
                "聊天列表在本次执行窗口内未完成目标好友同步，已安排有限重试",
                # A virtualized list can temporarily omit a real conversation.
                # The scheduler bounds this to two delayed retries, so a real
                # typo still becomes a terminal failure without spamming Douyin.
                retryable=True,
            )
        except CredentialError:
            return ExecutionResult(False, ExecutionStage.AUTHENTICATING, "cookie_invalid", "账号凭据格式无效", health_state='invalid', health_reason='保存的登录资料格式不可用，需要重新登录')
        except Exception as error:
            logger.warning(
                "douyin execution failed stage=%s exception=%s",
                stage,
                type(error).__name__,
            )
            if delivery_confirmed:
                return ExecutionResult(
                    True,
                    ExecutionStage.COMPLETE,
                    refreshed_storage_state=refreshed_storage_state,
                    contact_avatars=user_info.avatar_urls,
                )
            if message_submitted:
                return ExecutionResult(
                    True,
                    ExecutionStage.SUBMITTED,
                    "delivery_confirmation_unavailable",
                    "消息已进入发送步骤，但页面未能确认是否送达",
                    refreshed_storage_state=refreshed_storage_state,
                    contact_avatars=user_info.avatar_urls,
                )
            if stage == ExecutionStage.SELECTING_TARGET:
                return ExecutionResult(
                    False,
                    ExecutionStage.SELECTING_TARGET,
                    "conversation_not_opened",
                    f"已找到好友，但聊天窗口没有打开（{type(error).__name__}）",
                    retryable=True,
                )
            if stage == ExecutionStage.AUTHENTICATING:
                return ExecutionResult(False, stage, 'account_health_pending', '登录页面暂时无法检查，等待后台复核',
                    health_state='temporary', health_reason=f'登录检查异常：{type(error).__name__}')
            return ExecutionResult(
                False,
                stage,
                "automation_failed",
                f"页面操作或发送确认失败（{type(error).__name__}）",
                retryable=True,
            )
