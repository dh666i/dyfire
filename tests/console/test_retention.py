from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from spark_console.config import Settings
from spark_console.db import create_engine_for, create_schema, session_scope
from spark_console.models import (
    AuditEvent,
    DouyinLoginAction,
    DouyinLoginChallenge,
    DouyinLoginInput,
    DouyinLoginSession,
    MailDelivery,
    SparkTask,
    TaskHealthRetry,
    TaskRun,
    TaskRunAttempt,
    TaskRunEvent,
    User,
    WebSession,
)
from spark_console.retention import RetentionPolicy, retain
from spark_console.cli import build_parser, command_retention
from sqlalchemy import func, select


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "cookie.key").write_bytes(b"c" * 32)
        (root / "session.key").write_bytes(b"s" * 32)
        self.settings = Settings.from_env(
            {
                "SPARK_DATA_DIR": str(root),
                "SPARK_COOKIE_KEY_FILE": str(root / "cookie.key"),
                "SPARK_SESSION_KEY_FILE": str(root / "session.key"),
            }
        )
        self.engine = create_engine_for(self.settings)
        create_schema(self.engine)
        self.now = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def _seed(self):
        old = self.now - timedelta(days=100)
        fresh = self.now - timedelta(days=2)
        with session_scope(self.engine) as db:
            user = User(username="retention", password_hash="hash", role="user")
            db.add(user)
            db.flush()
            task = SparkTask(
                owner_user_id=user.id,
                target_name="好友",
                send_time="09:00",
                message_template="早安",
            )
            db.add(task)
            db.flush()

            terminal = TaskRun(
                task_id=task.id,
                scheduled_for=old,
                status="success",
                stage="submitted",
                finished_at=old,
            )
            active = TaskRun(
                task_id=task.id,
                scheduled_for=old + timedelta(minutes=1),
                status="running",
                stage="sending",
                started_at=old,
                lease_until=self.now + timedelta(minutes=5),
            )
            unconfirmed = TaskRun(
                task_id=task.id,
                scheduled_for=old + timedelta(minutes=2),
                status="submitted_unconfirmed",
                stage="submitted",
                submitted_at=old,
                finished_at=old,
            )
            recent = TaskRun(
                task_id=task.id,
                scheduled_for=fresh,
                status="failed",
                stage="finished",
                finished_at=fresh,
            )
            db.add_all([terminal, active, unconfirmed, recent])
            db.flush()
            db.add_all(
                [
                    TaskRunEvent(run_id=terminal.id, sequence=1, occurred_at=old, event="done", stage="finished"),
                    TaskRunAttempt(run_id=terminal.id, number=1, started_at=old, finished_at=old, status="success", stage="finished"),
                    TaskHealthRetry(run_id=terminal.id, task_revision=old, expires_at=old, last_reason="old"),
                ]
            )

            done_login = DouyinLoginSession(
                owner_user_id=user.id,
                status="succeeded",
                expires_at=old,
                created_at=old,
                finished_at=old,
            )
            active_login = DouyinLoginSession(
                owner_user_id=user.id,
                status="awaiting_scan",
                expires_at=old,
                created_at=old,
            )
            db.add_all([done_login, active_login])
            db.flush()
            db.add_all(
                [
                    DouyinLoginChallenge(scan_id=done_login.id, masked_phone="***"),
                    DouyinLoginAction(
                        scan_id=done_login.id,
                        kind="click",
                        x_million=100000,
                        y_million=200000,
                    ),
                    DouyinLoginInput(
                        scan_id=done_login.id,
                        ciphertext=b"encrypted",
                        nonce=b"n" * 12,
                    ),
                ]
            )
            db.add_all(
                [
                    WebSession(user_id=user.id, token_hash="old-token", csrf_token="c", expires_at=old, created_at=old),
                    WebSession(user_id=user.id, token_hash="live-token", csrf_token="c", expires_at=self.now + timedelta(days=1), created_at=old),
                    MailDelivery(event_key="old-mail", user_id=user.id, recipient="a@example.com", event="expiry", subject="old", body="old", status="accepted", created_at=old, updated_at=old),
                    MailDelivery(event_key="queued-mail", user_id=user.id, recipient="a@example.com", event="expiry", subject="queued", body="queued", status="queued", created_at=old, updated_at=old),
                    AuditEvent(actor_user_id=user.id, action="old", resource_type="test", created_at=old),
                ]
            )
            db.flush()
            return {
                "terminal": terminal.id,
                "active": active.id,
                "unconfirmed": unconfirmed.id,
                "recent": recent.id,
                "done_login": done_login.id,
                "active_login": active_login.id,
            }

    def test_default_is_dry_run_and_reports_protected_records(self):
        ids = self._seed()
        with session_scope(self.engine) as db:
            report = retain(
                db,
                RetentionPolicy(runs_days=90, sessions_days=30, mail_days=30, audit_days=90, batch_size=1),
                now=self.now,
            )
            self.assertTrue(report.dry_run)
            self.assertEqual(1, report.categories["runs"].candidates)
            self.assertEqual(2, report.categories["runs"].protected)
            self.assertEqual(1, report.categories["login_sessions"].candidates)
            self.assertEqual(1, report.categories["login_sessions"].protected)
            self.assertEqual(1, report.categories["mail"].candidates)
            self.assertEqual(1, report.categories["mail"].protected)
            self.assertEqual(1, report.categories["web_sessions"].candidates)
            self.assertEqual(0, report.deleted_count)
            self.assertIsNotNone(db.get(TaskRun, ids["terminal"]))

    def test_apply_deletes_only_expired_terminal_rows_in_batches(self):
        ids = self._seed()
        with session_scope(self.engine) as db:
            report = retain(
                db,
                RetentionPolicy(runs_days=90, sessions_days=30, mail_days=30, audit_days=90, batch_size=1),
                dry_run=False,
                now=self.now,
            )
            self.assertEqual(5, report.deleted_count)
            self.assertEqual(1, report.categories["runs"].deleted)
            self.assertEqual(1, report.categories["login_sessions"].deleted)
            self.assertEqual(1, report.categories["web_sessions"].deleted)
            self.assertEqual(1, report.categories["mail"].deleted)
            self.assertEqual(1, report.categories["audits"].deleted)
            self.assertIsNone(db.get(TaskRun, ids["terminal"]))
            self.assertIsNotNone(db.get(TaskRun, ids["active"]))
            self.assertIsNotNone(db.get(TaskRun, ids["unconfirmed"]))
            self.assertIsNotNone(db.get(TaskRun, ids["recent"]))
            self.assertIsNone(db.scalar(select(TaskRunEvent).where(TaskRunEvent.run_id == ids["terminal"])))
            self.assertIsNone(db.scalar(select(TaskRunAttempt).where(TaskRunAttempt.run_id == ids["terminal"])))
            self.assertIsNone(db.scalar(select(TaskHealthRetry).where(TaskHealthRetry.run_id == ids["terminal"])))
            self.assertIsNone(db.get(DouyinLoginChallenge, ids["done_login"]))
            self.assertIsNone(
                db.scalar(
                    select(DouyinLoginAction).where(
                        DouyinLoginAction.scan_id == ids["done_login"]
                    )
                )
            )
            self.assertIsNone(
                db.scalar(
                    select(DouyinLoginInput).where(
                        DouyinLoginInput.scan_id == ids["done_login"]
                    )
                )
            )
            self.assertIsNotNone(db.get(DouyinLoginSession, ids["active_login"]))
            self.assertIsNotNone(
                db.scalar(select(WebSession).where(WebSession.token_hash == "live-token"))
            )
            self.assertIsNotNone(
                db.scalar(select(MailDelivery).where(MailDelivery.event_key == "queued-mail"))
            )

    def test_dry_run_does_not_write_maintenance_audit(self):
        self._seed()
        with session_scope(self.engine) as db:
            retain(db, RetentionPolicy(), now=self.now)
            self.assertEqual(
                0,
                db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.action == "retention.applied")
                ),
            )

    def test_apply_records_maintenance_audit_after_deletion(self):
        self._seed()
        with session_scope(self.engine) as db:
            report = retain(
                db,
                RetentionPolicy(runs_days=90, sessions_days=30, mail_days=30, audit_days=90),
                dry_run=False,
                now=self.now,
            )
            self.assertGreater(report.deleted_count, 0)
            event = db.scalar(
                select(AuditEvent)
                .where(AuditEvent.action == "retention.applied")
                .order_by(AuditEvent.created_at.desc())
            )
            self.assertIsNotNone(event)
            self.assertIn("runs=1", event.detail)

    def test_policy_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            RetentionPolicy(runs_days=0)
        with self.assertRaises(ValueError):
            RetentionPolicy(batch_size=10001)

    def test_cli_defaults_to_dry_run_and_reports_pending_batch(self):
        self._seed()
        args = build_parser().parse_args(["retention", "--as-of", self.now.isoformat()])
        self.assertFalse(args.apply)
        output = []
        with patch("spark_console.cli.runtime", return_value=(self.settings, self.engine)), patch(
            "builtins.print", side_effect=lambda value: output.append(value)
        ):
            self.assertEqual(0, command_retention(args))
        report = json.loads(output[0])
        self.assertTrue(report["dry_run"])
        self.assertEqual(1, report["categories"]["runs"]["remaining"])

    def test_cli_apply_deletes_at_most_one_configured_batch_per_category(self):
        self._seed()
        with session_scope(self.engine) as db:
            user = User(username="second", password_hash="hash", role="user")
            db.add(user)
            db.flush()
            task = SparkTask(owner_user_id=user.id, target_name="另一个好友", send_time="10:00", message_template="早安")
            db.add(task)
            db.flush()
            for index in range(3):
                created = self.now - timedelta(days=120 + index)
                db.add(TaskRun(task_id=task.id, scheduled_for=created, status="success", stage="finished", finished_at=created))

        args = build_parser().parse_args([
            "retention", "--apply", "--batch-size", "2", "--as-of", self.now.isoformat()
        ])
        output = []
        with patch("spark_console.cli.runtime", return_value=(self.settings, self.engine)), patch(
            "builtins.print", side_effect=lambda value: output.append(value)
        ):
            self.assertEqual(0, command_retention(args))
        report = json.loads(output[0])
        self.assertFalse(report["dry_run"])
        self.assertEqual(2, report["categories"]["runs"]["deleted"])
        self.assertEqual(2, report["categories"]["runs"]["remaining"])

    def test_cli_rejects_naive_as_of_before_opening_database(self):
        args = build_parser().parse_args(["retention", "--as-of", "2026-01-01T00:00:00"])
        with patch("spark_console.cli.runtime") as runtime:
            with self.assertRaisesRegex(SystemExit, "必须包含时区"):
                command_retention(args)
            runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
