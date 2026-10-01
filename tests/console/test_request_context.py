from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from spark_console.config import Settings
from spark_console.db import create_schema
from spark_console.web.app import create_app
from spark_console.web.request_context import RequestCorrelationMiddleware


class RequestCorrelationMiddlewareTests(unittest.TestCase):
    @staticmethod
    def _run(app, headers=()):
        sent = []
        received = False

        async def receive():
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        asyncio.run(
            RequestCorrelationMiddleware(app)(
                {"type": "http", "headers": list(headers), "state": {}},
                receive,
                send,
            )
        )
        return sent

    def test_valid_request_id_is_echoed_and_added_to_json_errors(self):
        async def endpoint(scope, _receive, send):
            self.assertEqual("client-123", scope["state"]["request_id"])
            body = b'{"detail":"invalid input"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 400,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})

        sent = self._run(endpoint, [(b"x-request-id", b"client-123")])
        self.assertEqual("client-123", dict(sent[0]["headers"])[b"x-request-id"].decode())
        self.assertEqual(
            {"detail": "invalid input", "request_id": "client-123", "error_code": "bad_request"},
            json.loads(sent[1]["body"]),
        )
        self.assertEqual(len(sent[1]["body"]), int(dict(sent[0]["headers"])[b"content-length"]))

    def test_invalid_request_id_is_replaced_with_safe_generated_id(self):
        async def endpoint(_scope, _receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": [],
                }
            )
            await send({"type": "http.response.body", "body": b""})

        sent = self._run(endpoint, [(b"x-request-id", b"bad\r\nid")])
        request_id = dict(sent[0]["headers"])[b"x-request-id"].decode("ascii")
        self.assertRegex(request_id, r"^[0-9a-f]{32}$")

    def test_existing_request_id_header_is_replaced_instead_of_duplicated(self):
        async def endpoint(_scope, _receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"x-request-id", b"downstream-value")],
                }
            )
            await send({"type": "http.response.body", "body": b"ok"})

        sent = self._run(endpoint, [(b"x-request-id", b"client-value")])
        values = [value for name, value in sent[0]["headers"] if name == b"x-request-id"]
        self.assertEqual([b"client-value"], values)


class RequestCorrelationAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(
            data_dir=root,
            database_url=f"sqlite:///{root / 'test.db'}",
            cookie_key_file=root / "cookie.key",
            session_key_file=root / "session.key",
            max_request_bytes=64,
        )
        self.settings.cookie_key_file.write_bytes(b"c" * 32)
        self.settings.session_key_file.write_bytes(b"s" * 32)
        self.engine = create_engine(
            self.settings.database_url,
            connect_args={"check_same_thread": False},
        )
        create_schema(self.engine)
        self.client = TestClient(create_app(self.settings, self.engine), base_url="https://testserver")

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.temp.cleanup()

    def test_fastapi_json_error_keeps_detail_and_adds_metadata(self):
        response = self.client.get("/api/route-that-does-not-exist", headers={"X-Request-ID": "api-404"})
        self.assertEqual(404, response.status_code)
        self.assertEqual("api-404", response.headers["x-request-id"])
        self.assertEqual("Not Found", response.json()["detail"])
        self.assertEqual("api-404", response.json()["request_id"])
        self.assertEqual("not_found", response.json()["error_code"])

    def test_body_limit_error_has_correlation_metadata(self):
        response = self.client.post(
            "/login",
            data={"username": "u" * 100, "password": "x"},
            headers={"X-Request-ID": "body-too-large"},
        )
        self.assertEqual(413, response.status_code)
        self.assertEqual("body-too-large", response.headers["x-request-id"])
        self.assertEqual("body-too-large", response.json()["request_id"])
        self.assertEqual("request_too_large", response.json()["error_code"])
        self.assertEqual("request body too large", response.json()["detail"])
