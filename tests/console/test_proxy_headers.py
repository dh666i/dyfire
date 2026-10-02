import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from fastapi.testclient import TestClient

from spark_console.config import Settings
from spark_console.db import create_schema
from spark_console.web.app import create_app
from spark_console.web.proxy_headers import TrustedProxyMiddleware


class ProxyHeaderMiddlewareTests(unittest.TestCase):
    def test_untrusted_peer_cannot_change_scheme_or_client(self):
        captured = {}

        async def endpoint(scope, receive, send):
            captured.update(scope)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        sent = []

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "scheme": "http",
            "client": ("198.51.100.10", 40000),
            "headers": [
                (b"x-forwarded-for", b"203.0.113.7"),
                (b"x-forwarded-proto", b"https"),
            ],
        }
        asyncio.run(
            TrustedProxyMiddleware(endpoint, ["10.0.0.0/8"])(
                scope, receive, send
            )
        )
        self.assertEqual(captured["scheme"], "http")
        self.assertEqual(captured["client"], ("198.51.100.10", 40000))

    def test_trusted_peer_resolves_nearest_untrusted_client(self):
        captured = {}

        async def endpoint(scope, receive, send):
            captured.update(scope)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message):
            return None

        scope = {
            "type": "http",
            "scheme": "http",
            "client": ("10.2.3.4", 40000),
            "headers": [
                (b"x-forwarded-for", b"203.0.113.7, 10.1.2.3"),
                (b"x-forwarded-proto", b"https"),
            ],
        }
        asyncio.run(
            TrustedProxyMiddleware(endpoint, ["10.0.0.0/8"])(
                scope, receive, send
            )
        )
        self.assertEqual(captured["scheme"], "https")
        self.assertEqual(captured["client"], ("203.0.113.7", 40000))

    def test_duplicate_forwarded_headers_are_processed_as_one_chain(self):
        captured = {}

        async def endpoint(scope, receive, send):
            captured.update(scope)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message):
            return None

        scope = {
            "type": "http",
            "scheme": "http",
            "client": ("10.2.3.4", 40000),
            "headers": [
                (b"x-forwarded-for", b"203.0.113.7"),
                (b"x-forwarded-for", b"10.1.2.3"),
                (b"x-forwarded-proto", b"http"),
                (b"x-forwarded-proto", b"https"),
            ],
        }
        asyncio.run(
            TrustedProxyMiddleware(endpoint, ["10.0.0.0/8"])(
                scope, receive, send
            )
        )
        self.assertEqual(captured["scheme"], "https")
        self.assertEqual(captured["client"], ("203.0.113.7", 40000))

    def test_malformed_forwarded_chain_is_ignored(self):
        captured = {}

        async def endpoint(scope, receive, send):
            captured.update(scope)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message):
            return None

        scope = {
            "type": "http",
            "scheme": "http",
            "client": ("10.2.3.4", 40000),
            "headers": [(b"x-forwarded-for", b"203.0.113.7,unknown,10.1.2.3")],
        }
        asyncio.run(
            TrustedProxyMiddleware(endpoint, ["10.0.0.0/8"])(
                scope, receive, send
            )
        )
        self.assertEqual(captured["client"], ("10.2.3.4", 40000))

    def test_invalid_proxy_configuration_fails_closed_at_startup(self):
        with self.assertRaisesRegex(ValueError, "invalid trusted proxy CIDR"):
            TrustedProxyMiddleware(lambda *_args: None, ["not-an-ip"])


class HstsProxyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(
            data_dir=root,
            database_url=f"sqlite:///{root / 'test.db'}",
            cookie_key_file=root / "cookie.key",
            session_key_file=root / "session.key",
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )
        self.settings.cookie_key_file.write_bytes(b"c" * 32)
        self.settings.session_key_file.write_bytes(b"s" * 32)
        self.engine = create_engine(
            self.settings.database_url,
            connect_args={"check_same_thread": False},
        )
        create_schema(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def _request(self, peer, scheme="http", headers=()):
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": scheme,
            "path": "/health/ready",
            "raw_path": b"/health/ready",
            "query_string": b"",
            "headers": list(headers),
            "client": peer,
            "server": ("testserver", 443),
        }
        asyncio.run(create_app(self.settings, self.engine)(scope, receive, send))
        start = next(message for message in messages if message["type"] == "http.response.start")
        return dict(start["headers"])

    def test_trusted_proxy_https_gets_hsts(self):
        headers = self._request(
            ("10.1.2.3", 443), headers=[(b"x-forwarded-proto", b"https")]
        )
        self.assertEqual(headers[b"strict-transport-security"], b"max-age=31536000")

    def test_untrusted_forwarded_https_does_not_get_hsts(self):
        headers = self._request(
            ("198.51.100.5", 443), headers=[(b"x-forwarded-proto", b"https")]
        )
        self.assertNotIn(b"strict-transport-security", headers)

    def test_direct_https_gets_hsts_without_proxy_headers(self):
        headers = self._request(("198.51.100.5", 443), scheme="https")
        self.assertEqual(headers[b"strict-transport-security"], b"max-age=31536000")

    def test_security_headers_are_present_on_health_response(self):
        headers = self._request(("198.51.100.5", 443))
        self.assertIn(b"default-src 'self'", headers[b"content-security-policy"])
        self.assertEqual(headers[b"cross-origin-opener-policy"], b"same-origin")
        self.assertEqual(headers[b"cross-origin-resource-policy"], b"same-origin")
        self.assertEqual(headers[b"x-permitted-cross-domain-policies"], b"none")


class RedisReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(
            data_dir=root,
            database_url=f"sqlite:///{root / 'test.db'}",
            cookie_key_file=root / "cookie.key",
            session_key_file=root / "session.key",
            redis_enabled=True,
            redis_url="redis://redis.invalid/0",
        )
        self.settings.cookie_key_file.write_bytes(b"c" * 32)
        self.settings.session_key_file.write_bytes(b"s" * 32)
        self.engine = create_engine(
            self.settings.database_url,
            connect_args={"check_same_thread": False},
        )
        create_schema(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def test_enabled_redis_failure_marks_instance_unready(self):
        with patch("spark_console.web.app.RedisBackend.ping", side_effect=RuntimeError("down")):
            with TestClient(create_app(self.settings, self.engine)) as client:
                response = client.get("/health/ready")
        self.assertEqual(503, response.status_code)
        self.assertEqual("Redis 依赖暂时不可用", response.json()["detail"])

    def test_enabled_redis_success_keeps_instance_ready(self):
        with patch("spark_console.web.app.RedisBackend.ping", return_value=True) as ping:
            with TestClient(create_app(self.settings, self.engine)) as client:
                response = client.get("/health/ready")
        self.assertEqual(200, response.status_code)
        self.assertEqual({"status": "ready"}, response.json())
        ping.assert_called_once_with()
