from __future__ import annotations

import tempfile
import unittest
import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from spark_console.config import Settings
from spark_console.db import create_schema
from spark_console.web.app import create_app
from spark_console.web.request_limits import RequestBodyLimitMiddleware


class RequestBodyLimitTests(unittest.TestCase):
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

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def test_content_length_is_rejected_before_route_processing(self):
        with TestClient(create_app(self.settings, self.engine)) as client:
            response = client.post("/login", data={"username": "u" * 100, "password": "x"})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"], "request body too large")
        self.assertRegex(response.json()["request_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(response.json()["error_code"], "request_too_large")
        self.assertEqual(response.headers["x-request-id"], response.json()["request_id"])
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_streamed_body_is_counted_when_content_length_is_missing(self):
        received = []
        sent = []
        messages = iter(
            [
                {"type": "http.request", "body": b"123", "more_body": True},
                {"type": "http.request", "body": b"456", "more_body": False},
            ]
        )

        async def receive():
            return next(messages)

        async def send(message):
            sent.append(message)

        async def endpoint(_scope, inner_receive, inner_send):
            while True:
                message = await inner_receive()
                received.append(message)
                if message["type"] == "http.disconnect":
                    break
            await inner_send({"type": "http.response.start", "status": 200, "headers": []})
            await inner_send({"type": "http.response.body", "body": b"ok"})

        middleware = RequestBodyLimitMiddleware(endpoint, max_bytes=4)
        asyncio.run(
            middleware(
                {"type": "http", "headers": []},
                receive,
                send,
            )
        )
        self.assertEqual(received[-1]["type"], "http.disconnect")
        self.assertEqual(sent[0]["status"], 413)
