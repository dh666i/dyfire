"""Recovery must accept a live chat without mistaking an empty shell for login."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.web_chat import CONVERSATION_ITEM_SELECTOR
from spark_console.auth_scanner import DouyinQrScanner, LoginTimedOut, ScanCancelled
from tests.console.test_credentials import _FakeLoginPage, _FakeTextMatches


PNG = b"\x89PNG\r\n\x1a\nrecovery-frame"


def make_recovery_scanner(*, chat=True, verification=False, login=False, api="unknown", timeout=.15):
    page = _FakeLoginPage(login_text="扫码登录" if login else None)
    page.chat = chat
    page.verification = verification
    page.locator = lambda selector: _FakeTextMatches(
        (selector == CONVERSATION_ITEM_SELECTOR and page.chat)
        or ('captcha_container' in selector and page.verification)
    )
    page.goto = AsyncMock()
    page.screenshot = AsyncMock(return_value=PNG)
    page.on = Mock()
    page.remove_listener = Mock()
    response = AsyncMock()
    response.status = 401 if api == "logged_out" else 200
    response.json.return_value = (
        {"message": "success", "data": {"error_code": 0, "user_id": "self"}}
        if api == "valid" else
        {"message": "success", "data": {"error_code": "0"}}
    )
    context = Mock()
    context.request.get = AsyncMock(return_value=response)
    context.new_page = AsyncMock(return_value=page)
    context.storage_state = AsyncMock(return_value={"cookies": [{
        "name": "sid", "value": "refreshed-recovery-marker", "domain": ".douyin.com",
        "path": "/", "expires": -1, "httpOnly": True, "secure": True, "sameSite": "Lax",
    }], "origins": []})
    context.close = AsyncMock()
    browser = Mock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()
    manager = AsyncMock()
    manager.__aenter__.return_value = SimpleNamespace(
        chromium=SimpleNamespace(launch=AsyncMock(return_value=browser))
    )
    scanner = DouyinQrScanner(
        playwright_factory=lambda: manager,
        login_timeout_seconds=timeout,
        poll_interval_seconds=.005,
    )
    scanner._open_chat_login = AsyncMock()
    return scanner, browser, context, page


def run_recovery(scanner, **callbacks):
    return scanner.run(
        callbacks.get("on_qr", lambda png: None),
        callbacks.get("on_confirming", lambda value: None),
        callbacks.get("cancelled", lambda: False),
        recovery_storage_state={"cookies": [{"name": "original-marker"}], "origins": []},
        on_needs_verification=callbacks.get("on_needs_verification"),
        on_view=callbacks.get("on_view"),
        next_interaction=callbacks.get("next_interaction"),
    )


def test_live_chat_completes_recovery_when_optional_api_changes_shape():
    scanner, browser, context, _ = make_recovery_scanner()
    frames, confirmed = [], []
    result = asyncio.run(run_recovery(scanner, on_qr=frames.append, on_confirming=confirmed.append))
    assert result.storage_state["cookies"][0]["value"] == "refreshed-recovery-marker"
    assert frames == [PNG]
    assert confirmed == [True]
    assert browser.new_context.await_args.kwargs["storage_state"]["cookies"][0]["name"] == "original-marker"
    scanner._open_chat_login.assert_not_awaited()
    assert context.request.get.await_count == 2  # Health evidence plus final identity read.
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()


@pytest.mark.parametrize("options", [
    {"chat": False},  # The send path permits hydration; recovery must not.
    {"verification": True, "api": "valid"},
    {"login": True, "api": "valid"},
    {"api": "logged_out"},
])
def test_incomplete_or_conflicting_page_never_clears_account_verification(options):
    scanner, browser, context, _ = make_recovery_scanner(**options)
    confirmed, challenges = [], []
    with pytest.raises(LoginTimedOut):
        asyncio.run(run_recovery(scanner, on_confirming=confirmed.append, on_needs_verification=challenges.append))
    assert confirmed == []
    context.storage_state.assert_not_awaited()
    # Repeated UI checks must not repeatedly call the account API.
    assert context.request.get.await_count <= 1
    if options.get("verification"):
        assert len(challenges) == 1
        context.request.get.assert_not_awaited()
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()


def test_cleared_challenge_automatically_recovers_without_rescanning():
    scanner, _, context, page = make_recovery_scanner(verification=True)
    challenges = []

    def complete_challenge(details):
        challenges.append(details)
        page.verification = False

    asyncio.run(run_recovery(scanner, on_needs_verification=complete_challenge))
    assert len(challenges) == 1
    context.storage_state.assert_awaited_once()
    scanner._open_chat_login.assert_not_awaited()


def test_cancellation_closes_recovery_browser_without_saving_state():
    scanner, browser, context, _ = make_recovery_scanner(chat=False)
    cancelled = False

    def cancel_after_first_frame(_png):
        nonlocal cancelled
        cancelled = True

    with pytest.raises(ScanCancelled):
        asyncio.run(run_recovery(scanner, on_qr=cancel_after_first_frame, cancelled=lambda: cancelled))
    context.storage_state.assert_not_awaited()
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()
