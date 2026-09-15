import asyncio
from unittest.mock import AsyncMock, Mock, patch
import pytest
from core.session_health import SessionEvidence, open_chat_session, require_session, SessionUnavailable

@pytest.mark.parametrize("state", ["valid", "verification", "logged_out"])
def test_terminal_evidence_never_reloads(state):
    page = Mock()
    page.goto = AsyncMock(return_value=Mock(status=200))
    page.reload = AsyncMock()
    with patch("core.session_health.wait_for_session", AsyncMock(return_value=SessionEvidence(state, "evidence"))):
        result = asyncio.run(open_chat_session(page, Mock()))
    assert result.state == state
    page.reload.assert_not_called()
    page.remove_listener.assert_called_once()

def test_temporary_check_reloads_once_and_recovers():
    page = Mock()
    page.goto = AsyncMock(return_value=Mock(status=200))
    page.reload = AsyncMock(return_value=Mock(status=200))
    with patch("core.session_health.wait_for_session", AsyncMock(side_effect=[
        SessionEvidence("temporary", "loading"), SessionEvidence("valid", "ready")])):
        result = asyncio.run(open_chat_session(page, Mock()))
    assert result.state == "valid"
    assert "页面加载 2 次" in result.reason
    page.reload.assert_awaited_once()

def test_failed_navigation_verification_does_not_reload():
    page = Mock()
    page.goto = AsyncMock(side_effect=TimeoutError("sensitive request"))
    page.reload = AsyncMock()
    with patch("core.session_health.inspect_session", AsyncMock(return_value=SessionEvidence("verification", "需要验证"))):
        result = asyncio.run(open_chat_session(page, Mock()))
    assert result.state == "verification"
    assert "sensitive" not in result.reason
    page.reload.assert_not_called()

def test_shared_entry_refuses_send_after_two_temporary_checks():
    page = Mock()
    page.goto = AsyncMock(return_value=Mock(status=200))
    page.reload = AsyncMock(return_value=Mock(status=200))
    with patch("core.session_health.wait_for_session", AsyncMock(return_value=SessionEvidence("temporary", "not ready"))):
        with pytest.raises(SessionUnavailable):
            asyncio.run(require_session(page, Mock(), navigate=True))
    page.goto.assert_awaited_once()
    page.reload.assert_awaited_once()
