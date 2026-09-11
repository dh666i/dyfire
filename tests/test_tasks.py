import asyncio
import json
import unittest

from core.tasks import (
    LoginRequiredError,
    MessageConfirmationEvidence,
    MessageConfirmationProbe,
    MessageNetworkAckMonitor,
    _is_strict_message_send_url,
    _is_candidate_message_send_url,
    _parse_douyin_send_request,
    _parse_douyin_send_response,
    capture_message_confirmation_baseline,
    click_first_match,
    confirm_message_sent,
    page_has_login_prompt,
    wait_for_authenticated_selector,
    wait_for_first_visible,
)


class FakePage:
    def __init__(self, visible_selector):
        self.visible_selector = visible_selector
        self.attempts = []

    async def wait_for_selector(self, selector, *, state, timeout):
        self.attempts.append((selector, state, timeout))
        if selector != self.visible_selector:
            raise TimeoutError(selector)
        return selector


class FakeLocator:
    def __init__(self, count):
        self._count = count

    async def count(self):
        return self._count


class FakeLoginPage:
    def __init__(self, visible_text, url="https://creator.douyin.com/creator-micro/data/following/chat"):
        self.visible_text = visible_text
        self.url = url

    def locator(self, selector):
        return FakeLocator(1 if selector == f"text={self.visible_text}" else 0)


class FakeDelayedLoginPage(FakeLoginPage):
    def __init__(self):
        super().__init__(None)

    async def wait_for_selector(self, selector, *, state, timeout):
        self.visible_text = "扫码登录"
        raise TimeoutError(selector)


class FakeClickableLocator:
    def __init__(self):
        self.first = self
        self.clicked = False

    async def click(self):
        self.clicked = True


class FakeClickablePage:
    def __init__(self):
        self.result = FakeClickableLocator()

    def locator(self, selector):
        return self.result


class FakeChatInput:
    async def element_handle(self):
        return "chat-input-handle"


class FakeDeliveryPage:
    def __init__(self):
        self.evaluate_calls = []
        self.wait_function_calls = []
        self.fail_editor_settle = False
        self.listeners = {}

    def on(self, event, callback):
        self.listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event, callback):
        callbacks = self.listeners.get(event, [])
        if callback in callbacks:
            callbacks.remove(callback)

    async def evaluate(self, expression, arg):
        self.evaluate_calls.append((expression, arg))
        if "MutationObserver" in expression:
            return {"installed": True, "baselineIds": 1, "baselineFingerprints": 1}
        return True

    async def wait_for_function(self, expression, *, arg, timeout):
        self.wait_function_calls.append((expression, arg, timeout))
        if self.fail_editor_settle and arg == "chat-input-handle":
            raise TimeoutError("editor did not settle")
        if "__sparkMessageConfirmationV2" in expression:
            return {
                "source": "dom",
                "reason": "new_outgoing_node",
                "messageId": "dom-message-1",
            }
        return True


def _encode_varint(value):
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _protobuf_varint(field_number, value):
    return _encode_varint(field_number << 3) + _encode_varint(value)


def _protobuf_bytes(field_number, value):
    if isinstance(value, str):
        value = value.encode()
    return _encode_varint((field_number << 3) | 2) + _encode_varint(len(value)) + value


def _send_request(message="今日火花", client_id="client-1"):
    content = json.dumps({"text": message}, ensure_ascii=False, separators=(",", ":"))
    send_content = _protobuf_bytes(4, content) + _protobuf_bytes(8, client_id)
    return _protobuf_bytes(8, _protobuf_bytes(100, send_content))


def _send_response(
    *, client_id="client-1", message_id=123456, error_code=0, status="OK"
):
    message_info = (
        _protobuf_varint(1, message_id)
        + _protobuf_varint(3, 0)
        + _protobuf_bytes(4, client_id)
    )
    message_data = _protobuf_bytes(100, message_info)
    return (
        _protobuf_varint(3, error_code)
        + _protobuf_bytes(4, status)
        + _protobuf_bytes(6, message_data)
    )


class FakeRequest:
    def __init__(self, payload, method="POST"):
        self.method = method
        self.post_data_buffer = payload


class FakeResponse:
    def __init__(
        self,
        request_payload,
        response_payload,
        *,
        url="https://imapi.douyin.com/v1/message/send",
        status=200,
        method="POST",
    ):
        self.url = url
        self.status = status
        self.request = FakeRequest(request_payload, method=method)
        self._body = response_payload

    async def body(self):
        return self._body


class WaitForFirstVisibleTests(unittest.IsolatedAsyncioTestCase):
    async def test_falls_back_to_the_first_visible_selector(self):
        page = FakePage("new-selector")

        selected = await wait_for_first_visible(
            page,
            ["old-selector", "new-selector", "unused-selector"],
            timeout=250,
        )

        self.assertEqual("new-selector", selected)
        self.assertEqual(
            [
                ("old-selector", "visible", 250),
                ("new-selector", "visible", 250),
            ],
            page.attempts,
        )


class LoginPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_detects_a_scan_login_prompt(self):
        page = FakeLoginPage("扫码登录")

        self.assertTrue(await page_has_login_prompt(page))

    async def test_stops_waiting_when_login_prompt_appears_late(self):
        page = FakeDelayedLoginPage()

        with self.assertRaises(LoginRequiredError):
            await wait_for_authenticated_selector(
                page,
                ["friends-tab"],
                timeout=1000,
                poll_timeout=25,
            )


class ClickFirstMatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_clicks_only_the_first_matching_element(self):
        page = FakeClickablePage()

        await click_first_match(page, "many-matches")

        self.assertTrue(page.result.clicked)

    async def test_detects_redirect_back_to_the_login_homepage(self):
        page = FakeLoginPage(None, url="https://creator.douyin.com/")

        self.assertTrue(await page_has_login_prompt(page))


class ConfirmMessageSentTests(unittest.IsolatedAsyncioTestCase):
    async def test_captures_probe_before_send(self):
        page = FakeDeliveryPage()

        probe = await capture_message_confirmation_baseline(
            page,
            "first line\nunique verification line",
        )

        self.assertIsInstance(probe, MessageConfirmationProbe)
        self.assertEqual("first line\nunique verification line", probe.message)
        self.assertTrue(probe.dom_available)
        self.assertIsNotNone(probe.network_monitor)
        self.assertEqual(1, len(page.evaluate_calls))
        expression, arg = page.evaluate_calls[0]
        self.assertIn("MutationObserver", expression)
        self.assertEqual("first line\nunique verification line", arg["message"])
        self.assertTrue(arg["token"])

    async def test_waits_for_a_new_message_probe(self):
        page = FakeDeliveryPage()
        chat_input = FakeChatInput()
        probe = await capture_message_confirmation_baseline(
            page,
            "first line\nunique verification line",
        )

        await confirm_message_sent(
            page,
            chat_input,
            "first line\nunique verification line",
            timeout=1200,
            baseline_count=probe,
        )

        self.assertEqual(2, len(page.wait_function_calls))
        confirmation_expression, confirmation_arg, confirmation_timeout = (
            page.wait_function_calls[0]
        )
        self.assertIn("__sparkMessageConfirmationV2", confirmation_expression)
        self.assertEqual(probe.token, confirmation_arg)
        self.assertEqual(1200, confirmation_timeout)
        self.assertEqual(
            (
                "(element) => !String(element.innerText || element.textContent || '').trim()",
                "chat-input-handle",
                1200,
            ),
            page.wait_function_calls[1],
        )

    async def test_editor_settle_failure_does_not_override_confirmed_message(self):
        page = FakeDeliveryPage()
        page.fail_editor_settle = True
        probe = await capture_message_confirmation_baseline(page, "message")

        await confirm_message_sent(
            page,
            FakeChatInput(),
            "message",
            baseline_count=probe,
        )

        self.assertEqual(2, len(page.wait_function_calls))

    async def test_supports_legacy_numeric_baseline_without_resending(self):
        page = FakeDeliveryPage()

        evidence = await confirm_message_sent(
            page,
            FakeChatInput(),
            "message",
            timeout=1200,
            baseline_count=1,
        )

        self.assertEqual("dom", evidence.source)
        self.assertEqual("legacy_baseline_count", evidence.reason)


class MessageNetworkAckTests(unittest.IsolatedAsyncioTestCase):
    async def test_correlates_known_douyin_protobuf_response(self):
        monitor = MessageNetworkAckMonitor("今日火花")
        monitor.arm()
        await monitor.handle_response(FakeResponse(_send_request(), _send_response()))

        evidence = await asyncio.wait_for(monitor.wait(), timeout=0.1)
        self.assertEqual("network_response", evidence.source)
        self.assertEqual("correlated_protobuf_send_response", evidence.reason)
        self.assertEqual("123456", evidence.message_id)

    async def test_correlates_json_response_on_versioned_web_route(self):
        monitor = MessageNetworkAckMonitor("今日火花")
        monitor.arm()
        request = json.dumps(
            {
                "action": "send_message",
                "content": {"text": "今日火花"},
                "client_message_id": "json-client-1",
            },
            ensure_ascii=False,
        ).encode()
        response = json.dumps(
            {
                "status": "success",
                "message_id": "json-server-1",
                "client_message_id": "json-client-1",
            }
        ).encode()
        await monitor.handle_response(
            FakeResponse(
                request,
                response,
                url="https://www.douyin.com/api/v2/message/send",
            )
        )

        evidence = await asyncio.wait_for(monitor.wait(), timeout=0.1)
        self.assertEqual("network_response", evidence.source)
        self.assertEqual("correlated_json_send_response", evidence.reason)
        self.assertEqual("json-server-1", evidence.message_id)

    async def test_accepts_successful_empty_http_ack_for_exact_request(self):
        monitor = MessageNetworkAckMonitor("今日火花")
        monitor.arm()
        await monitor.handle_response(
            FakeResponse(
                _send_request(),
                b"",
                status=204,
            )
        )

        evidence = await asyncio.wait_for(monitor.wait(), timeout=0.1)
        self.assertEqual("http_send_response_empty_body", evidence.reason)
        self.assertIsNone(evidence.message_id)

    async def test_accepts_known_ack_inside_an_additional_protobuf_envelope(self):
        monitor = MessageNetworkAckMonitor("今日火花")
        monitor.arm()
        wrapped_response = _protobuf_bytes(9, _send_response())
        await monitor.handle_response(
            FakeResponse(_send_request(), wrapped_response)
        )

        evidence = await asyncio.wait_for(monitor.wait(), timeout=0.1)
        self.assertEqual(
            "correlated_protobuf_send_response_fallback", evidence.reason
        )
        self.assertEqual("123456", evidence.message_id)

    async def test_rejects_mismatched_json_ack(self):
        monitor = MessageNetworkAckMonitor("今日火花")
        monitor.arm()
        request = json.dumps(
            {
                "action": "send_message",
                "text": "今日火花",
                "client_message_id": "json-client-1",
            },
            ensure_ascii=False,
        ).encode()
        response = json.dumps(
            {
                "status": "success",
                "message_id": "json-server-1",
                "client_message_id": "another-client",
            }
        ).encode()
        await monitor.handle_response(
            FakeResponse(
                request,
                response,
                url="https://creator.douyin.com/api/message/send",
            )
        )

        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(monitor.wait(), timeout=0.01)

    async def test_correlates_websocket_ack_without_a_client_id(self):
        monitor = MessageNetworkAckMonitor("今日火花")
        monitor.arm()
        monitor.handle_websocket_sent(
            {"action": "send_message", "text": "今日火花"}
        )
        monitor.handle_websocket_received(
            {"status": "success", "message_id": "ws-server-1"}
        )

        evidence = await asyncio.wait_for(monitor.wait(), timeout=0.1)
        self.assertEqual("network_websocket", evidence.source)
        self.assertEqual("ws-server-1", evidence.message_id)

    def test_recognizes_official_route_variants_but_not_unrelated_endpoints(self):
        self.assertTrue(
            _is_candidate_message_send_url(
                "https://www.douyin.com/api/v2/message/send"
            )
        )
        self.assertTrue(
            _is_candidate_message_send_url(
                "https://creator.douyin.com/im/send_message"
            )
        )
        self.assertFalse(
            _is_candidate_message_send_url(
                "https://www.douyin.com/api/v2/message/history"
            )
        )


if __name__ == "__main__":
    unittest.main()
