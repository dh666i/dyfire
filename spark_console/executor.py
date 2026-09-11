from __future__ import annotations

import logging
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
from core.session_health import require_session, SessionUnavailable


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
    async def execute(
        self,
        cookie_payload: bytes | bytearray,
        target: str,
        message: str,
        credential_version: int = 1,
        target_sec_uid: str | None = None,
        on_submitted: Callable[[], object] | None = None,
        message_kind: str = "text",
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
        try:
            payload = CredentialPayload.parse(bytes(cookie_payload), credential_version)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = None
                try:
                    context = await browser.new_context(**payload.context_options())
                    legacy_cookies = payload.cookies_to_add()
                    if legacy_cookies:
                        await context.add_cookies(legacy_cookies)
                    page = await context.new_page()
                    if message_kind == "text":
                        delivery_monitor = MessageNetworkAckMonitor(message)
                        delivery_monitor.attach(page)
                    page.on("response", user_info.capture)
                    await page.goto(WEB_CHAT_URL, wait_until="domcontentloaded", timeout=120000)
                    await require_session(page, context)
                    stage = ExecutionStage.SELECTING_TARGET
                    try:
                        identity = (
                            await user_info.wait_for(target_sec_uid)
                            if target_sec_uid
                            else None
                        )
                        await select_web_chat_target(
                            page,
                            target,
                            timeout=45000,
                            aliases=identity.aliases if identity else (),
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
                    try:
                        if delivery_monitor is not None:
                            delivery_monitor.detach()
                        if context is not None:
                            try:
                                await context.close()
                            except Exception as error:
                                logger.warning(
                                    "browser context cleanup failed exception=%s",
                                    type(error).__name__,
                                )
                    finally:
                        try:
                            await browser.close()
                        except Exception as error:
                            logger.warning(
                                "browser cleanup failed exception=%s",
                                type(error).__name__,
                            )
        except SessionUnavailable as error:
            return ExecutionResult(False, ExecutionStage.AUTHENTICATING,
                'account_verification_required' if error.evidence.state == 'verification' else 'account_health_pending',
                error.evidence.reason, health_state=error.evidence.state, health_reason=error.evidence.reason)
        except WebChatLoginRequiredError:
            return ExecutionResult(
                False,
                ExecutionStage.AUTHENTICATING,
                "account_health_pending",
                "登录信号异常，等待复核",
                retryable=False,
                health_state='temporary', health_reason='旧版页面检测报告登录异常，等待复核',
            )
        except TargetNotFoundError:
            return ExecutionResult(False, ExecutionStage.SELECTING_TARGET, "target_not_found", "未找到完全匹配的目标好友")
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
                    "已找到好友，但聊天窗口没有打开",
                    retryable=True,
                )
            if stage == ExecutionStage.AUTHENTICATING:
                return ExecutionResult(False, stage, 'account_health_pending', '登录页面暂时无法检查，等待后台复核',
                    health_state='temporary', health_reason=f'登录检查异常：{type(error).__name__}')
            return ExecutionResult(
                False,
                stage,
                "automation_failed",
                "页面操作或发送确认失败",
                retryable=True,
            )
