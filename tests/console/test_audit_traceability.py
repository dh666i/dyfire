from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
import tempfile

from sqlalchemy import create_engine, select

from spark_console.config import Settings
from spark_console.db import _upgrade_sqlite_audit_request_context, create_schema
from spark_console.models import AuditEvent
from spark_console.services.audits import AuditService
from spark_console.web.proxy_headers import TrustedProxyMiddleware
from spark_console.web.request_context import RequestCorrelationMiddleware


class AuditTraceabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.engine = create_engine(
            f"sqlite:///{root / 'audit.db'}",
            connect_args={"check_same_thread": False},
        )
        create_schema(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def test_audit_write_inherits_request_metadata_and_result(self):
        async def endpoint(scope, _receive, send):
            from sqlalchemy.orm import Session

            with Session(self.engine) as db:
                event = AuditService(db).write(
                    "user-1",
                    "account.bound",
                    "douyin_account",
                    "account-1",
                )
                db.commit()
                self.assertEqual("request-1", event.request_id)
                self.assertEqual("203.0.113.4", event.client_ip)
                self.assertEqual("success", event.outcome)
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

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

        app = RequestCorrelationMiddleware(
            TrustedProxyMiddleware(endpoint, trusted_proxy_cidrs=("10.0.0.0/8",))
        )
        asyncio.run(
            app(
                {
                    "type": "http",
                    "headers": [
                        (b"x-request-id", b"request-1"),
                        (b"x-forwarded-for", b"203.0.113.4, 10.0.0.3"),
                    ],
                    "client": ("10.0.0.2", 40000),
                    "state": {},
                },
                receive,
                send,
            )
        )
        from sqlalchemy.orm import Session
        with Session(self.engine) as db:
            event = db.scalars(select(AuditEvent)).one()
            self.assertEqual("request-1", event.request_id)

    def test_explicit_failure_fields_are_bounded_and_preserved(self):
        from sqlalchemy.orm import Session

        with Session(self.engine) as db:
            event = AuditService(db).write(
                None,
                "auth.step_up_failed",
                "web_session",
                "session-1",
                error_code="password_invalid",
                outcome="failure",
                request_id="manual-request",
                client_ip="2001:db8::1",
            )
            db.commit()
            self.assertEqual("failure", event.outcome)
            self.assertEqual("password_invalid", event.error_code)
            self.assertEqual("2001:db8::1", event.client_ip)

    def test_legacy_failure_action_gets_searchable_error_code(self):
        from sqlalchemy.orm import Session

        with Session(self.engine) as db:
            event = AuditService(db).write(
                None, "account.directory.unavailable", "douyin_account", "account-1"
            )
            outcome, error_code = event.outcome, event.error_code
            db.commit()
        self.assertEqual("failure", outcome)
        self.assertEqual("account.directory.unavailable", error_code)

    def test_sqlite_additive_migration_preserves_existing_events(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE audit_events")
            connection.exec_driver_sql(
                "CREATE TABLE audit_events ("
                "id VARCHAR(36) PRIMARY KEY, actor_user_id VARCHAR(36), "
                "action VARCHAR(64) NOT NULL, resource_type VARCHAR(32) NOT NULL, "
                "resource_id VARCHAR(36), detail VARCHAR(240), created_at DATETIME)"
            )
            connection.exec_driver_sql(
                "INSERT INTO audit_events VALUES "
                "('legacy-1', NULL, 'legacy.action', 'user', NULL, NULL, CURRENT_TIMESTAMP)"
            )
            _upgrade_sqlite_audit_request_context(connection)
            row = connection.exec_driver_sql(
                "SELECT action, request_id, client_ip, outcome, error_code "
                "FROM audit_events WHERE id = 'legacy-1'"
            ).one()
            indexes = {
                value[1]
                for value in connection.exec_driver_sql(
                    "PRAGMA index_list(audit_events)"
                ).fetchall()
            }
        self.assertEqual(("legacy.action", None, None, None, None), tuple(row))
        self.assertIn("ix_audit_events_request_id", indexes)
