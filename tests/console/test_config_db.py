import multiprocessing
import os
import queue
import sqlite3
import tempfile
import threading
import traceback
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError

from spark_console.cli import command_backup
from spark_console.config import Settings
from spark_console.db import create_engine_for, create_schema, session_scope
from spark_console.models import (
    DouyinAccount,
    DouyinAccountIdentity,
    DouyinLoginInput,
    DouyinLoginSession,
    InviteCode,
    ScanStatus,
    SparkTask,
    User,
    utc_now,
)
from spark_console.task_targets import legacy_task_target_key, task_target_key


def _initialize_schema_process(
    database_url,
    data_dir,
    cookie_key_file,
    session_key_file,
    start_event,
    result_queue,
):
    engine = None
    try:
        if not start_event.wait(timeout=10):
            raise RuntimeError("concurrent schema start timed out")
        settings = Settings(
            data_dir=Path(data_dir),
            database_url=database_url,
            cookie_key_file=Path(cookie_key_file),
            session_key_file=Path(session_key_file),
        )
        engine = create_engine_for(settings)
        create_schema(engine)
    except BaseException as error:
        result_queue.put(
            f"{type(error).__name__}: {error}\n{traceback.format_exc()}"
        )
    else:
        result_queue.put(None)
    finally:
        if engine is not None:
            engine.dispose()


class SettingsTests(unittest.TestCase):
    def test_requires_existing_32_byte_cookie_key_file(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            cookie_key = root_path / "cookie.key"
            session_key = root_path / "session.key"
            cookie_key.write_bytes(b"short")
            session_key.write_bytes(b"s" * 32)

            with self.assertRaisesRegex(ValueError, "exactly 32 bytes"):
                Settings.from_env(
                    {
                        "SPARK_DATA_DIR": root,
                        "SPARK_COOKIE_KEY_FILE": str(cookie_key),
                        "SPARK_SESSION_KEY_FILE": str(session_key),
                    }
                )

    def test_loads_safe_defaults_from_valid_key_files(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            cookie_key = root_path / "cookie.key"
            session_key = root_path / "session.key"
            cookie_key.write_bytes(b"c" * 32)
            session_key.write_bytes(b"s" * 32)

            settings = Settings.from_env(
                {
                    "SPARK_DATA_DIR": root,
                    "SPARK_COOKIE_KEY_FILE": str(cookie_key),
                    "SPARK_SESSION_KEY_FILE": str(session_key),
                }
            )

            self.assertEqual("Asia/Shanghai", settings.timezone)
            self.assertEqual("127.0.0.1", settings.web_bind)
            self.assertEqual(8899, settings.web_port)
            self.assertEqual(1, settings.auth_poll_seconds)
            self.assertEqual(10, settings.worker_poll_seconds)
            self.assertTrue(settings.secure_cookies)
            self.assertTrue(settings.database_url.endswith("spark.db"))
            self.assertEqual(10, settings.database_pool_size)
            self.assertEqual(20, settings.database_max_overflow)

    def test_loads_postgres_pool_settings(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            cookie_key = root_path / "cookie.key"
            session_key = root_path / "session.key"
            cookie_key.write_bytes(b"c" * 32)
            session_key.write_bytes(b"s" * 32)
            settings = Settings.from_env({
                "SPARK_DATA_DIR": root,
                "SPARK_COOKIE_KEY_FILE": str(cookie_key),
                "SPARK_SESSION_KEY_FILE": str(session_key),
                "SPARK_DATABASE_URL": "postgresql+psycopg://user:pass@localhost/db",
                "SPARK_DATABASE_POOL_SIZE": "7",
                "SPARK_DATABASE_MAX_OVERFLOW": "13",
                "SPARK_DATABASE_POOL_TIMEOUT_SECONDS": "19",
                "SPARK_DATABASE_POOL_RECYCLE_SECONDS": "1200",
            })
            self.assertEqual(7, settings.database_pool_size)
            self.assertEqual(13, settings.database_max_overflow)
            self.assertEqual(19, settings.database_pool_timeout_seconds)
            self.assertEqual(1200, settings.database_pool_recycle_seconds)

    def test_postgres_engine_uses_configured_pool(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            cookie_key = root_path / "cookie.key"
            session_key = root_path / "session.key"
            cookie_key.write_bytes(b"c" * 32)
            session_key.write_bytes(b"s" * 32)
            settings = Settings(
                data_dir=root_path,
                database_url="postgresql+psycopg://user:pass@localhost/db",
                cookie_key_file=cookie_key,
                session_key_file=session_key,
                database_pool_size=3,
                database_max_overflow=4,
                database_pool_timeout_seconds=5,
                database_pool_recycle_seconds=6,
            )
            with patch("spark_console.db.create_engine") as factory:
                create_engine_for(settings)
            options = factory.call_args.kwargs
            self.assertEqual(3, options["pool_size"])
            self.assertEqual(4, options["max_overflow"])
            self.assertEqual(5, options["pool_timeout"])
            self.assertEqual(6, options["pool_recycle"])
            self.assertTrue(options["pool_pre_ping"])

    def test_loads_explicit_proxy_and_hsts_settings(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            cookie_key = root_path / "cookie.key"
            session_key = root_path / "session.key"
            cookie_key.write_bytes(b"c" * 32)
            session_key.write_bytes(b"s" * 32)

            settings = Settings.from_env(
                {
                    "SPARK_DATA_DIR": root,
                    "SPARK_COOKIE_KEY_FILE": str(cookie_key),
                    "SPARK_SESSION_KEY_FILE": str(session_key),
                    "SPARK_TRUSTED_PROXY_CIDRS": " 10.0.0.0/8, 2001:db8::/32 ",
                    "SPARK_HSTS_MAX_AGE_SECONDS": "600",
                    "SPARK_HSTS_INCLUDE_SUBDOMAINS": "true",
                }
            )

            self.assertEqual(("10.0.0.0/8", "2001:db8::/32"), settings.trusted_proxy_cidrs)
            self.assertEqual(600, settings.hsts_max_age_seconds)
            self.assertTrue(settings.hsts_include_subdomains)

    def test_loads_and_validates_worker_shard_settings(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            (root_path / "cookie.key").write_bytes(b"c" * 32)
            (root_path / "session.key").write_bytes(b"s" * 32)
            settings = Settings.from_env({
                "SPARK_DATA_DIR": root,
                "SPARK_COOKIE_KEY_FILE": str(root_path / "cookie.key"),
                "SPARK_SESSION_KEY_FILE": str(root_path / "session.key"),
                "SPARK_WORKER_SHARD": "2",
                "SPARK_WORKER_SHARDS": "4",
            })
            self.assertEqual(2, settings.worker_shard)
            self.assertEqual(4, settings.worker_shards)
            with self.assertRaises(ValueError):
                Settings.from_env({
                    "SPARK_DATA_DIR": root,
                    "SPARK_COOKIE_KEY_FILE": str(root_path / "cookie.key"),
                    "SPARK_SESSION_KEY_FILE": str(root_path / "session.key"),
                    "SPARK_WORKER_SHARD": "4",
                    "SPARK_WORKER_SHARDS": "4",
                })

    def test_rejects_unknown_boolean_values(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            cookie_key = root_path / "cookie.key"
            session_key = root_path / "session.key"
            cookie_key.write_bytes(b"c" * 32)
            session_key.write_bytes(b"s" * 32)

            with self.assertRaisesRegex(ValueError, "SPARK_SECURE_COOKIES"):
                Settings.from_env(
                    {
                        "SPARK_DATA_DIR": root,
                        "SPARK_COOKIE_KEY_FILE": str(cookie_key),
                        "SPARK_SESSION_KEY_FILE": str(session_key),
                        "SPARK_SECURE_COOKIES": "maybe",
                    }
                )
    def test_auth_and_task_worker_poll_intervals_are_independent(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            cookie_key = root_path / "cookie.key"
            session_key = root_path / "session.key"
            cookie_key.write_bytes(b"c" * 32)
            session_key.write_bytes(b"s" * 32)

            settings = Settings.from_env(
                {
                    "SPARK_DATA_DIR": root,
                    "SPARK_COOKIE_KEY_FILE": str(cookie_key),
                    "SPARK_SESSION_KEY_FILE": str(session_key),
                    "SPARK_AUTH_POLL_SECONDS": "2",
                    "SPARK_WORKER_POLL_SECONDS": "17",
                }
            )

            self.assertEqual(2, settings.auth_poll_seconds)
            self.assertEqual(17, settings.worker_poll_seconds)

    def test_can_explicitly_allow_http_session_cookie_for_temporary_deployment(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            cookie_key = root_path / "cookie.key"
            session_key = root_path / "session.key"
            cookie_key.write_bytes(b"c" * 32)
            session_key.write_bytes(b"s" * 32)
            settings = Settings.from_env(
                {
                    "SPARK_DATA_DIR": root,
                    "SPARK_COOKIE_KEY_FILE": str(cookie_key),
                    "SPARK_SESSION_KEY_FILE": str(session_key),
                    "SPARK_SECURE_COOKIES": "false",
                }
            )
            self.assertFalse(settings.secure_cookies)


class DatabaseTests(unittest.TestCase):
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

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def test_schema_enforces_unique_enabled_schedule(self):
        with session_scope(self.engine) as session:
            user = User(username="friend", password_hash="hash", role="user")
            session.add(user)
            session.flush()
            account = DouyinAccount(
                owner_user_id=user.id,
                display_name="main",
                encrypted_cookies=b"ciphertext",
                cookie_nonce=b"n" * 12,
            )
            session.add(account)
            session.flush()
            session.add(
                SparkTask(
                    owner_user_id=user.id,
                    douyin_account_id=account.id,
                    target_name="目标",
                    send_time="09:00",
                    message_template="今日火花",
                    enabled=True,
                )
            )
            session.flush()
            session.add(
                SparkTask(
                    owner_user_id=user.id,
                    douyin_account_id=account.id,
                    target_name="目标",
                    send_time="09:00",
                    message_template="今日火花",
                    enabled=True,
                )
            )
            with self.assertRaises(IntegrityError):
                session.flush()
            session.rollback()

    def test_schema_allows_same_name_for_distinct_stable_target_keys(self):
        with session_scope(self.engine) as session:
            user = User(username="same-name-owner", password_hash="hash", role="user")
            session.add(user)
            session.flush()
            account = DouyinAccount(
                owner_user_id=user.id,
                display_name="main",
                encrypted_cookies=b"ciphertext",
                cookie_nonce=b"n" * 12,
            )
            session.add(account)
            session.flush()
            session.add_all(
                [
                    SparkTask(
                        owner_user_id=user.id,
                        douyin_account_id=account.id,
                        target_name="同名好友",
                        target_key=task_target_key("同名好友", "stable-a"),
                        send_time="09:00",
                        message_template="今日火花",
                        enabled=True,
                    ),
                    SparkTask(
                        owner_user_id=user.id,
                        douyin_account_id=account.id,
                        target_name="同名好友",
                        target_key=task_target_key("同名好友", "stable-b"),
                        send_time="09:00",
                        message_template="今日火花",
                        enabled=True,
                    ),
                ]
            )

    def test_concurrent_inserts_enforce_one_schedule_per_stable_identity(self):
        with session_scope(self.engine) as session:
            user = User(username="concurrent-owner", password_hash="hash", role="user")
            session.add(user)
            session.flush()
            account = DouyinAccount(
                owner_user_id=user.id,
                display_name="main",
                encrypted_cookies=b"ciphertext",
                cookie_nonce=b"n" * 12,
            )
            session.add(account)
            session.flush()
            owner_id = user.id
            account_id = account.id

        barrier = threading.Barrier(2)
        stable_key = task_target_key("任意昵称", "same-stable-user")

        def insert_task(task_id: str, target_name: str) -> str:
            barrier.wait(timeout=10)
            try:
                with self.engine.begin() as connection:
                    connection.exec_driver_sql(
                        "INSERT INTO spark_tasks "
                        "(id, owner_user_id, douyin_account_id, target_name, "
                        "target_key, send_time, message_template, message_kind, "
                        "enabled, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, '09:00', '今日火花', 'text', "
                        "1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                        (task_id, owner_id, account_id, target_name, stable_key),
                    )
            except IntegrityError:
                return "conflict"
            return "created"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(
                executor.map(
                    lambda args: insert_task(*args),
                    (("concurrent-a", "旧昵称"), ("concurrent-b", "新昵称")),
                )
            )

        self.assertEqual(["conflict", "created"], sorted(outcomes))
        with self.engine.connect() as connection:
            count = connection.exec_driver_sql(
                "SELECT COUNT(*) FROM spark_tasks "
                "WHERE douyin_account_id = ? AND target_key = ? AND send_time = '09:00'",
                (account_id, stable_key),
            ).scalar_one()
        self.assertEqual(1, count)

    def test_schema_allows_only_one_active_login_text_per_scan(self):
        with session_scope(self.engine) as session:
            user = User(
                username="login-input-owner",
                password_hash="hash",
                role="user",
            )
            session.add(user)
            session.flush()
            scan = DouyinLoginSession(
                owner_user_id=user.id,
                slot="login-input-slot",
                status=ScanStatus.CONFIRMING,
                expires_at=utc_now() + timedelta(minutes=5),
            )
            session.add(scan)
            session.flush()
            session.add(
                DouyinLoginInput(
                    scan_id=scan.id,
                    request_id="00000000-0000-4000-8000-000000000001",
                    ciphertext=b"first",
                    nonce=b"nonce",
                    delivery_status="queued",
                )
            )
            session.flush()
            scan_id = scan.id

        with self.assertRaises(IntegrityError):
            with session_scope(self.engine) as session:
                session.add(
                    DouyinLoginInput(
                        scan_id=scan_id,
                        request_id="00000000-0000-4000-8000-000000000002",
                        ciphertext=b"second",
                        nonce=b"nonce",
                        delivery_status="queued",
                    )
                )
                session.flush()

        with session_scope(self.engine) as session:
            first = session.query(DouyinLoginInput).filter_by(
                scan_id=scan_id
            ).one()
            first.delivery_status = "failed"
            first.finished_at = utc_now()
            session.add(
                DouyinLoginInput(
                    scan_id=scan_id,
                    request_id="00000000-0000-4000-8000-000000000002",
                    ciphertext=b"second",
                    nonce=b"nonce",
                    delivery_status="queued",
                )
            )

        with session_scope(self.engine) as session:
            inputs = (
                session.query(DouyinLoginInput)
                .filter_by(scan_id=scan_id)
                .order_by(DouyinLoginInput.id)
                .all()
            )
            self.assertEqual(
                ["failed", "queued"],
                [row.delivery_status for row in inputs],
            )

    def test_schema_allows_only_one_global_scan_slot(self):
        with session_scope(self.engine) as session:
            owner = User(username="owner", password_hash="hash", role="user")
            session.add(owner)
            session.flush()
            session.add(
                DouyinLoginSession(
                    owner_user_id=owner.id,
                    slot="global",
                    status=ScanStatus.QUEUED,
                    expires_at=utc_now() + timedelta(minutes=5),
                )
            )

        with self.assertRaises(IntegrityError):
            with session_scope(self.engine) as session:
                session.add(
                    DouyinLoginSession(
                        owner_user_id=owner.id,
                        slot="global",
                        status=ScanStatus.QUEUED,
                        expires_at=utc_now() + timedelta(minutes=5),
                    )
                )

        with session_scope(self.engine) as session:
            session.add(
                DouyinLoginSession(
                    owner_user_id=owner.id,
                    slot=None,
                    status=ScanStatus.SUCCEEDED,
                    expires_at=utc_now() + timedelta(minutes=5),
                )
            )
            session.add(
                DouyinLoginSession(
                    owner_user_id=owner.id,
                    slot=None,
                    status=ScanStatus.EXPIRED,
                    expires_at=utc_now() + timedelta(minutes=5),
                )
            )

    def test_invite_model_has_no_plaintext_code_column(self):
        self.assertIn("code_hash", InviteCode.__table__.columns)
        self.assertNotIn("code", InviteCode.__table__.columns)

    def test_invite_plaintext_uses_separate_encrypted_secret_table(self):
        table = InviteCode.metadata.tables.get("invite_code_secrets")

        self.assertIsNotNone(table)
        self.assertEqual(
            {"invite_id", "ciphertext", "nonce"}, set(table.columns.keys())
        )

    def test_invite_persists_creator_and_consumer_foreign_keys(self):
        with session_scope(self.engine) as session:
            creator = User(username="creator", password_hash="hash", role="admin")
            consumer = User(username="consumer", password_hash="hash", role="user")
            session.add_all([creator, consumer])
            session.flush()
            invite = InviteCode(
                code_hash="a" * 64,
                created_by_user_id=creator.id,
                expires_at=utc_now() + timedelta(days=1),
                used_by_user_id=consumer.id,
                used_at=utc_now(),
            )
            session.add(invite)
            session.flush()
            self.assertEqual(creator.id, invite.created_by_user_id)
            self.assertEqual(consumer.id, invite.used_by_user_id)

        foreign_keys = {
            (foreign_key.parent.name, foreign_key.target_fullname)
            for foreign_key in InviteCode.__table__.foreign_keys
        }
        self.assertEqual(
            {
                ("created_by_user_id", "users.id"),
                ("used_by_user_id", "users.id"),
            },
            foreign_keys,
        )

    def test_douyin_identity_metadata_uses_one_to_one_table(self):
        self.assertEqual("douyin_account_identities", DouyinAccountIdentity.__tablename__)
        self.assertEqual(
            {"account_id"},
            {column.name for column in DouyinAccountIdentity.__table__.primary_key.columns},
        )
        self.assertNotIn("douyin_unique_id", DouyinAccount.__table__.columns)
        self.assertIn("douyin_unique_id", DouyinAccountIdentity.__table__.columns)

    def test_repeated_schema_initialization_records_migration_once(self):
        create_schema(self.engine)
        create_schema(self.engine)

        with self.engine.connect() as connection:
            migrations = connection.exec_driver_sql(
                "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
            ).fetchall()

        self.assertGreaterEqual(len(migrations), 10)
        self.assertEqual((1, "task_runs_reliability"), migrations[0][:2])
        self.assertEqual(
            (2, "login_input_delivery_receipts"), migrations[1][:2]
        )
        self.assertEqual((3, "normalize_submitted_runs"), migrations[2][:2])
        self.assertEqual((4, "spark_task_message_kind"), migrations[3][:2])
        self.assertEqual((5, "spark_task_target_key"), migrations[4][:2])
        self.assertEqual(
            (6, "normalize_spark_task_target_keys"), migrations[5][:2]
        )
        self.assertEqual((7, "login_session_mode"), migrations[6][:2])
        self.assertEqual((8, "user_admin_note"), migrations[7][:2])
        self.assertEqual((9, "redemption_code_retrieval"), migrations[8][:2])
        self.assertEqual((10, "scale_query_indexes"), migrations[9][:2])
        self.assertEqual((11, "account_dispatch_shards"), migrations[10][:2])
        self.assertEqual((12, "retention_query_indexes"), migrations[11][:2])
        self.assertEqual((13, "account_queue_query_indexes"), migrations[12][:2])
        self.assertEqual((14, "admin_totp_credentials"), migrations[13][:2])
        self.assertEqual((15, "audit_request_context"), migrations[14][:2])
        self.assertEqual((16, "admin_totp_recovery_codes"), migrations[15][:2])
        self.assertTrue(all(row[2] is not None for row in migrations))

    def test_scale_query_migration_installs_all_expected_indexes(self):
        expected = {
            "ix_user_subscriptions_valid_window",
            "ix_douyin_login_sessions_status_expires",
            "ix_spark_tasks_enabled_next_run",
            "ix_task_runs_status_scheduled",
            "ix_task_runs_task_scheduled_status",
            "ix_audit_events_created_action",
            "ix_audit_events_request_id",
            "ix_douyin_login_sessions_status_finished",
            "ix_task_runs_status_finished",
            "ix_mail_deliveries_status_updated",
        }
        with self.engine.connect() as connection:
            actual = set()
            for table in (
                "user_subscriptions",
                "douyin_login_sessions",
                "spark_tasks",
                "task_runs",
                "audit_events",
                "mail_deliveries",
            ):
                actual.update(
                    row[1]
                    for row in connection.exec_driver_sql(
                        f"PRAGMA index_list({table})"
                    ).fetchall()
                )
        self.assertTrue(expected <= actual)

    def test_target_key_migration_preserves_legacy_duplicates_and_enabled_state(self):
        legacy_path = Path(self.temp.name) / "legacy-task-targets.db"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript(
                """
                CREATE TABLE spark_tasks (
                    id VARCHAR(36) PRIMARY KEY,
                    owner_user_id VARCHAR(36) NOT NULL,
                    douyin_account_id VARCHAR(36),
                    target_name VARCHAR(64) NOT NULL,
                    send_time VARCHAR(5) NOT NULL,
                    message_template VARCHAR(500) NOT NULL,
                    message_kind VARCHAR(24) NOT NULL DEFAULT 'text',
                    enabled BOOLEAN NOT NULL,
                    next_run_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                );
                CREATE UNIQUE INDEX uq_enabled_task_schedule
                ON spark_tasks (douyin_account_id, target_name, send_time)
                WHERE enabled = 1;
                CREATE TABLE spark_task_target_identities (
                    task_id VARCHAR(36) PRIMARY KEY,
                    sec_uid VARCHAR(256) NOT NULL,
                    created_at DATETIME NOT NULL
                );
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(128) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO schema_migrations (version, name) VALUES
                    (1, 'task_runs_reliability'),
                    (2, 'login_input_delivery_receipts'),
                    (3, 'normalize_submitted_runs'),
                    (4, 'spark_task_message_kind');
                INSERT INTO spark_tasks
                    (id, owner_user_id, douyin_account_id, target_name, send_time,
                     message_template, message_kind, enabled, created_at, updated_at)
                VALUES
                    ('stable-old', 'owner', 'account', '旧昵称', '09:00',
                     '消息', 'text', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('stable-new', 'owner', 'account', '新昵称', '09:00',
                     '消息', 'text', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('manual-upper', 'owner', 'account', 'Alice', '10:00',
                     '消息', 'text', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('manual-lower', 'owner', 'account', 'alice', '10:00',
                     '消息', 'text', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                INSERT INTO spark_task_target_identities
                    (task_id, sec_uid, created_at)
                VALUES
                    ('stable-old', 'same-stable-user', CURRENT_TIMESTAMP),
                    ('stable-new', 'same-stable-user', CURRENT_TIMESTAMP);
                """
            )
            connection.commit()
        finally:
            connection.close()

        engine = create_engine(f"sqlite:///{legacy_path}")
        try:
            create_schema(engine)
            create_schema(engine)
            with engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    "SELECT id, target_key, enabled FROM spark_tasks ORDER BY id"
                ).fetchall()
                indexes = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA index_list(spark_tasks)"
                    ).fetchall()
                }
                migrations = connection.exec_driver_sql(
                    "SELECT version, name FROM schema_migrations ORDER BY version"
                ).fetchall()

            self.assertEqual(4, len(rows))
            self.assertEqual(
                {"stable-old", "stable-new", "manual-upper", "manual-lower"},
                {row[0] for row in rows},
            )
            self.assertTrue(all(row[2] == 1 for row in rows))
            keys = {task_id: target_key for task_id, target_key, _enabled in rows}
            stable_key = task_target_key("旧昵称", "same-stable-user")
            manual_key = task_target_key("Alice")
            self.assertEqual(1, sum(value == stable_key for value in keys.values()))
            self.assertEqual(1, sum(value == manual_key for value in keys.values()))
            self.assertEqual(
                2,
                sum(value.startswith("legacy:") for value in keys.values()),
            )
            self.assertEqual(
                1,
                len(
                    {
                        legacy_task_target_key("stable-old"),
                        legacy_task_target_key("stable-new"),
                    }
                    & set(keys.values())
                ),
            )
            self.assertEqual(
                1,
                len(
                    {
                        legacy_task_target_key("manual-upper"),
                        legacy_task_target_key("manual-lower"),
                    }
                    & set(keys.values())
                ),
            )
            self.assertNotIn("uq_enabled_task_schedule", indexes)
            self.assertIn("uq_enabled_task_target_schedule", indexes)
            self.assertEqual(
                (6, "normalize_spark_task_target_keys"), migrations[5]
            )
            self.assertEqual((16, "admin_totp_recovery_codes"), migrations[-1])
        finally:
            engine.dispose()

    def test_target_key_v6_normalizes_old_v5_formats_idempotently(self):
        legacy_path = Path(self.temp.name) / "old-v5-task-targets.db"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript(
                """
                CREATE TABLE spark_tasks (
                    id VARCHAR(36) PRIMARY KEY,
                    owner_user_id VARCHAR(36) NOT NULL,
                    douyin_account_id VARCHAR(36),
                    target_name VARCHAR(256) NOT NULL,
                    target_key VARCHAR(272) NOT NULL,
                    send_time VARCHAR(5) NOT NULL,
                    message_template VARCHAR(500) NOT NULL,
                    message_kind VARCHAR(24) NOT NULL DEFAULT 'text',
                    enabled BOOLEAN NOT NULL,
                    next_run_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                );
                CREATE UNIQUE INDEX uq_enabled_task_target_schedule
                ON spark_tasks (douyin_account_id, target_key, send_time)
                WHERE enabled = 1;
                CREATE TRIGGER require_spark_task_target_key_insert
                BEFORE INSERT ON spark_tasks
                WHEN NEW.target_key IS NULL OR TRIM(NEW.target_key) = ''
                BEGIN SELECT RAISE(ABORT, 'spark task target_key required'); END;
                CREATE TRIGGER require_spark_task_target_key_update
                BEFORE UPDATE OF target_key ON spark_tasks
                WHEN NEW.target_key IS NULL OR TRIM(NEW.target_key) = ''
                BEGIN SELECT RAISE(ABORT, 'spark task target_key required'); END;
                CREATE TABLE spark_task_target_identities (
                    task_id VARCHAR(36) PRIMARY KEY,
                    sec_uid VARCHAR(256) NOT NULL,
                    created_at DATETIME NOT NULL
                );
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(128) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO schema_migrations (version, name) VALUES
                    (1, 'task_runs_reliability'),
                    (2, 'login_input_delivery_receipts'),
                    (3, 'normalize_submitted_runs'),
                    (4, 'spark_task_message_kind'),
                    (5, 'spark_task_target_key');
                INSERT INTO spark_tasks
                    (id, owner_user_id, douyin_account_id, target_name, target_key,
                     send_time, message_template, message_kind, enabled,
                     created_at, updated_at)
                VALUES
                    ('01-stable', 'owner', 'account', '旧昵称',
                     'uid:same-stable-user', '09:00', '消息', 'text', 1,
                     CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('02-stable', 'owner', 'account', '新昵称',
                     'stable:same-stable-user', '09:00', '消息', 'text', 1,
                     CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('03-manual', 'owner', 'account', 'Alice',
                     'name:Alice', '10:00', '消息', 'text', 1,
                     CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('04-manual', 'owner', 'account', 'alice',
                     'nickname:alice', '10:00', '消息', 'text', 1,
                     CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('05-distinct', 'owner', 'account', '同名好友',
                     'uid:first-stable-user', '11:00', '消息', 'text', 1,
                     CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('06-distinct', 'owner', 'account', '同名好友',
                     'uid:second-stable-user', '11:00', '消息', 'text', 1,
                     CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('07-disabled', 'owner', 'account', '又一昵称',
                     'uid:same-stable-user-disabled', '09:00', '消息', 'text', 0,
                     CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                INSERT INTO spark_task_target_identities
                    (task_id, sec_uid, created_at)
                VALUES
                    ('01-stable', 'same-stable-user', CURRENT_TIMESTAMP),
                    ('02-stable', 'same-stable-user', CURRENT_TIMESTAMP),
                    ('05-distinct', 'first-stable-user', CURRENT_TIMESTAMP),
                    ('06-distinct', 'second-stable-user', CURRENT_TIMESTAMP),
                    ('07-disabled', 'same-stable-user', CURRENT_TIMESTAMP);
                """
            )
            connection.commit()
        finally:
            connection.close()

        engine = create_engine(f"sqlite:///{legacy_path}")
        try:
            create_schema(engine)
            with engine.connect() as connection:
                first_rows = connection.exec_driver_sql(
                    "SELECT id, target_key, enabled FROM spark_tasks ORDER BY id"
                ).fetchall()

            # Exercise the migration body a second time, as would happen after
            # a restored migration ledger, and verify deterministic keys.
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "DELETE FROM schema_migrations WHERE version = 6"
                )
            create_schema(engine)
            create_schema(engine)

            with engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    "SELECT id, target_key, enabled FROM spark_tasks ORDER BY id"
                ).fetchall()
                migrations = connection.exec_driver_sql(
                    "SELECT version, name FROM schema_migrations ORDER BY version"
                ).fetchall()
                index_sql = connection.exec_driver_sql(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'index' "
                    "AND name = 'uq_enabled_task_target_schedule'"
                ).scalar_one()

            self.assertEqual(first_rows, rows)
            self.assertEqual(
                {
                    "01-stable",
                    "02-stable",
                    "03-manual",
                    "04-manual",
                    "05-distinct",
                    "06-distinct",
                    "07-disabled",
                },
                {row[0] for row in rows},
            )
            self.assertEqual(
                {
                    "01-stable": 1,
                    "02-stable": 1,
                    "03-manual": 1,
                    "04-manual": 1,
                    "05-distinct": 1,
                    "06-distinct": 1,
                    "07-disabled": 0,
                },
                {task_id: enabled for task_id, _target_key, enabled in rows},
            )
            keys = {task_id: target_key for task_id, target_key, _enabled in rows}
            stable_key = task_target_key("忽略昵称", "same-stable-user")
            self.assertEqual(stable_key, keys["01-stable"])
            self.assertEqual(
                legacy_task_target_key("02-stable"), keys["02-stable"]
            )
            self.assertEqual(stable_key, keys["07-disabled"])
            self.assertEqual(task_target_key("Alice"), keys["03-manual"])
            self.assertEqual(
                legacy_task_target_key("04-manual"), keys["04-manual"]
            )
            self.assertEqual(
                task_target_key("同名好友", "first-stable-user"),
                keys["05-distinct"],
            )
            self.assertEqual(
                task_target_key("同名好友", "second-stable-user"),
                keys["06-distinct"],
            )
            self.assertTrue(
                all(
                    value.startswith(("uid:", "name:", "legacy:"))
                    and len(value.rsplit(":", 1)[-1]) == 64
                    for value in keys.values()
                )
            )
            self.assertIn("WHERE enabled = 1", index_sql)
            self.assertEqual(
                [
                    (1, "task_runs_reliability"),
                    (2, "login_input_delivery_receipts"),
                    (3, "normalize_submitted_runs"),
                    (4, "spark_task_message_kind"),
                    (5, "spark_task_target_key"),
                    (6, "normalize_spark_task_target_keys"),
                ],
                migrations[:6],
            )
        finally:
            engine.dispose()

    def test_create_schema_adds_worker_columns_to_legacy_task_runs_idempotently(self):
        legacy_path = Path(self.temp.name) / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                """
                CREATE TABLE task_runs (
                    id VARCHAR(36) PRIMARY KEY,
                    task_id VARCHAR(36) NOT NULL,
                    scheduled_for DATETIME NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    stage VARCHAR(24) NOT NULL,
                    started_at DATETIME,
                    finished_at DATETIME,
                    error_code VARCHAR(48),
                    error_summary VARCHAR(240),
                    message_digest VARCHAR(64)
                )
                """
            )
            connection.commit()
        finally:
            connection.close()
        engine = create_engine(f"sqlite:///{legacy_path}")
        try:
            create_schema(engine)
            create_schema(engine)
            with engine.connect() as connection:
                columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA table_info(task_runs)"
                    ).fetchall()
                }
                indexes = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA index_list(task_runs)"
                    ).fetchall()
                }
                migrations = connection.exec_driver_sql(
                    "SELECT version, name FROM schema_migrations ORDER BY version"
                ).fetchall()
            self.assertTrue(
                {"worker_id", "attempt", "lease_until", "retry_after", "submitted_at"}
                <= columns
            )
            self.assertTrue(
                {"ix_task_runs_status_lease", "ix_task_runs_task_retry"} <= indexes
            )
            self.assertEqual(
                [
                    (1, "task_runs_reliability"),
                    (2, "login_input_delivery_receipts"),
                    (3, "normalize_submitted_runs"),
                    (4, "spark_task_message_kind"),
                    (5, "spark_task_target_key"),
                    (6, "normalize_spark_task_target_keys"),
                ],
                migrations[:6],
            )
        finally:
            engine.dispose()

    def test_create_schema_upgrades_legacy_login_inputs_and_deduplicates_pending(self):
        legacy_path = Path(self.temp.name) / "legacy-login-inputs.db"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                """
                CREATE TABLE douyin_login_inputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_id VARCHAR(36) NOT NULL,
                    ciphertext BLOB,
                    nonce BLOB,
                    created_at DATETIME NOT NULL,
                    consumed_at DATETIME
                )
                """
            )
            connection.execute(
                "INSERT INTO douyin_login_inputs "
                "(scan_id, ciphertext, nonce, created_at, consumed_at) "
                "VALUES ('consumed', NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO douyin_login_inputs "
                "(scan_id, ciphertext, nonce, created_at, consumed_at) "
                "VALUES ('duplicate', X'01', X'02', CURRENT_TIMESTAMP, NULL)"
            )
            connection.execute(
                "INSERT INTO douyin_login_inputs "
                "(scan_id, ciphertext, nonce, created_at, consumed_at) "
                "VALUES ('duplicate', X'03', X'04', CURRENT_TIMESTAMP, NULL)"
            )
            connection.commit()
        finally:
            connection.close()

        engine = create_engine(f"sqlite:///{legacy_path}")
        try:
            create_schema(engine)
            create_schema(engine)
            with engine.connect() as connection:
                columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA table_info(douyin_login_inputs)"
                    ).fetchall()
                }
                indexes = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA index_list(douyin_login_inputs)"
                    ).fetchall()
                }
                rows = connection.exec_driver_sql(
                    "SELECT id, ciphertext, nonce, consumed_at, "
                    "delivery_status, attempted_at, finished_at "
                    "FROM douyin_login_inputs ORDER BY id"
                ).fetchall()
                migrations = connection.exec_driver_sql(
                    "SELECT version, name FROM schema_migrations ORDER BY version"
                ).fetchall()

            self.assertTrue(
                {
                    "request_id",
                    "delivery_status",
                    "attempted_at",
                    "finished_at",
                }
                <= columns
            )
            self.assertTrue(
                {
                    "uq_douyin_login_inputs_scan_request",
                    "uq_douyin_login_inputs_scan_active",
                }
                <= indexes
            )
            self.assertEqual("uncertain", rows[0][4])
            self.assertIsNotNone(rows[0][5])
            self.assertIsNotNone(rows[0][6])
            self.assertIsNone(rows[1][1])
            self.assertIsNone(rows[1][2])
            self.assertIsNotNone(rows[1][3])
            self.assertEqual("failed", rows[1][4])
            self.assertIsNotNone(rows[1][6])
            self.assertEqual(b"\x03", rows[2][1])
            self.assertEqual(b"\x04", rows[2][2])
            self.assertIsNone(rows[2][3])
            self.assertEqual("queued", rows[2][4])
            self.assertEqual(
                [
                    (1, "task_runs_reliability"),
                    (2, "login_input_delivery_receipts"),
                    (3, "normalize_submitted_runs"),
                    (4, "spark_task_message_kind"),
                    (5, "spark_task_target_key"),
                    (6, "normalize_spark_task_target_keys"),
                ],
                migrations[:6],
            )
            with session_scope(engine) as session:
                self.assertEqual(
                    "queued", session.get(DouyinLoginInput, 3).delivery_status
                )
        finally:
            engine.dispose()

    def test_sqlite_schema_lock_precedes_migration_and_atomic_lock_insert(self):
        legacy_path = Path(self.temp.name) / "ordered-migration.db"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                """
                CREATE TABLE task_runs (
                    id VARCHAR(36) PRIMARY KEY,
                    task_id VARCHAR(36) NOT NULL,
                    scheduled_for DATETIME NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    stage VARCHAR(24) NOT NULL,
                    started_at DATETIME,
                    finished_at DATETIME,
                    error_code VARCHAR(48),
                    error_summary VARCHAR(240),
                    message_digest VARCHAR(64)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(128) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        settings = Settings(
            data_dir=self.settings.data_dir,
            database_url=f"sqlite:///{legacy_path}",
            cookie_key_file=self.settings.cookie_key_file,
            session_key_file=self.settings.session_key_file,
        )
        engine = create_engine_for(settings)
        statements = []

        @event.listens_for(engine, "before_cursor_execute")
        def record_statement(
            _connection, _cursor, statement, _parameters, _context, _executemany
        ):
            statements.append(" ".join(statement.split()))

        try:
            create_schema(engine)
        finally:
            engine.dispose()

        begin = statements.index("BEGIN IMMEDIATE")
        migration_read = next(
            index
            for index, statement in enumerate(statements)
            if statement == "SELECT version FROM schema_migrations"
        )
        first_alter = next(
            index
            for index, statement in enumerate(statements)
            if statement.startswith("ALTER TABLE task_runs ADD COLUMN")
        )
        worker_lock_insert = next(
            index
            for index, statement in enumerate(statements)
            if statement.startswith("INSERT OR IGNORE INTO worker_lock")
        )
        self.assertLess(begin, migration_read)
        self.assertLess(migration_read, first_alter)
        self.assertLess(first_alter, worker_lock_insert)

    def test_three_processes_concurrently_initialize_one_legacy_database(self):
        legacy_path = Path(self.temp.name) / "concurrent-migration.db"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                """
                CREATE TABLE task_runs (
                    id VARCHAR(36) PRIMARY KEY,
                    task_id VARCHAR(36) NOT NULL,
                    scheduled_for DATETIME NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    stage VARCHAR(24) NOT NULL,
                    started_at DATETIME,
                    finished_at DATETIME,
                    error_code VARCHAR(48),
                    error_summary VARCHAR(240),
                    message_digest VARCHAR(64)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(128) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        context = multiprocessing.get_context("spawn")
        start_event = context.Event()
        result_queue = context.Queue()
        database_url = f"sqlite:///{legacy_path}"
        process_args = (
            database_url,
            str(self.settings.data_dir),
            str(self.settings.cookie_key_file),
            str(self.settings.session_key_file),
            start_event,
            result_queue,
        )
        processes = [
            context.Process(target=_initialize_schema_process, args=process_args)
            for _ in range(3)
        ]
        for process in processes:
            process.start()
        start_event.set()
        for process in processes:
            process.join(timeout=20)
        stuck = [process for process in processes if process.is_alive()]
        for process in stuck:
            process.terminate()
            process.join(timeout=5)

        outcomes = []
        for _ in processes:
            try:
                outcomes.append(result_queue.get(timeout=5))
            except queue.Empty:
                outcomes.append("process returned no result")
        result_queue.close()
        result_queue.join_thread()

        self.assertEqual([], stuck)
        self.assertEqual([0, 0, 0], [process.exitcode for process in processes])
        self.assertEqual(
            [None, None, None],
            outcomes,
            msg="\n\n".join(outcome for outcome in outcomes if outcome),
        )

        connection = sqlite3.connect(legacy_path)
        try:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(task_runs)").fetchall()
            }
            migrations = connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
            worker_locks = connection.execute(
                "SELECT id, worker_id, lease_until FROM worker_lock"
            ).fetchall()
        finally:
            connection.close()

        self.assertTrue(
            {"worker_id", "attempt", "lease_until", "retry_after", "submitted_at"}
            <= columns
        )
        self.assertEqual(
            [
                (1, "task_runs_reliability"),
                (2, "login_input_delivery_receipts"),
                (3, "normalize_submitted_runs"),
                (4, "spark_task_message_kind"),
                (5, "spark_task_target_key"),
                (6, "normalize_spark_task_target_keys"),
            ],
            migrations[:6],
        )
        self.assertEqual([(1, None, None)], worker_locks)

    def test_threads_concurrently_initialize_one_legacy_database(self):
        legacy_path = Path(self.temp.name) / "threaded-migration.db"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                """
                CREATE TABLE task_runs (
                    id VARCHAR(36) PRIMARY KEY,
                    task_id VARCHAR(36) NOT NULL,
                    scheduled_for DATETIME NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    stage VARCHAR(24) NOT NULL,
                    started_at DATETIME,
                    finished_at DATETIME,
                    error_code VARCHAR(48),
                    error_summary VARCHAR(240),
                    message_digest VARCHAR(64)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(128) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        settings = Settings(
            data_dir=self.settings.data_dir,
            database_url=f"sqlite:///{legacy_path}",
            cookie_key_file=self.settings.cookie_key_file,
            session_key_file=self.settings.session_key_file,
        )
        engine = create_engine_for(settings)
        barrier = threading.Barrier(8)

        def initialize():
            barrier.wait(timeout=10)
            create_schema(engine)

        try:
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(initialize) for _ in range(8)]
                for future in futures:
                    future.result(timeout=20)
            with engine.connect() as connection:
                migrations = connection.exec_driver_sql(
                    "SELECT version, name FROM schema_migrations ORDER BY version"
                ).fetchall()
                worker_locks = connection.exec_driver_sql(
                    "SELECT id, worker_id, lease_until FROM worker_lock"
                ).fetchall()
        finally:
            engine.dispose()

        self.assertEqual(
            [
                (1, "task_runs_reliability"),
                (2, "login_input_delivery_receipts"),
                (3, "normalize_submitted_runs"),
                (4, "spark_task_message_kind"),
                (5, "spark_task_target_key"),
                (6, "normalize_spark_task_target_keys"),
            ],
            migrations[:6],
        )
        self.assertEqual([(1, None, None)], worker_locks)

    def test_online_backup_contains_wal_commits_and_keeps_seven_files(self):
        backup_dir = self.settings.data_dir / "backups"
        backup_dir.mkdir()
        for index in range(8):
            (backup_dir / f"spark-2026010{index + 1}T000000Z.db").write_bytes(b"old")
        unrelated = backup_dir / "spark-not-a-backup.db"
        unrelated.write_bytes(b"keep")
        stale_temporary = backup_dir / "spark-20260101T000000000000Z.db.tmp"
        stale_temporary.write_bytes(b"stale")
        stale_timestamp = utc_now().timestamp() - 25 * 3600
        stale_temporary.touch()
        os.utime(stale_temporary, (stale_timestamp, stale_timestamp))
        with self.engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA wal_autocheckpoint=0")
            connection.exec_driver_sql("CREATE TABLE backup_probe (value TEXT)")
            connection.exec_driver_sql(
                "INSERT INTO backup_probe (value) VALUES ('wal-visible')"
            )

        with patch(
            "spark_console.cli.runtime", return_value=(self.settings, self.engine)
        ):
            self.assertEqual(0, command_backup(SimpleNamespace(keep=7)))

        backups = sorted(backup_dir.glob("spark-*.db"))
        managed = [path for path in backups if path.name != unrelated.name]
        self.assertEqual(7, len(managed))
        self.assertTrue(unrelated.exists())
        self.assertFalse(stale_temporary.exists())
        newest = max(managed, key=lambda path: path.name)
        connection = sqlite3.connect(newest)
        try:
            self.assertEqual(
                "wal-visible",
                connection.execute("SELECT value FROM backup_probe").fetchone()[0],
            )
            self.assertEqual(
                "ok", connection.execute("PRAGMA integrity_check").fetchone()[0]
            )
        finally:
            connection.close()

    def test_failed_online_backup_removes_partial_temporary_database(self):
        class FailingDriverConnection:
            @staticmethod
            def backup(target):
                target.execute("CREATE TABLE partial_backup (value TEXT)")
                target.commit()
                raise RuntimeError("simulated backup interruption")

        class FailingRawConnection:
            driver_connection = FailingDriverConnection()

            @staticmethod
            def close():
                return None

        fake_engine = SimpleNamespace(
            dialect=SimpleNamespace(name="sqlite"),
            raw_connection=lambda: FailingRawConnection(),
        )
        with patch(
            "spark_console.cli.runtime", return_value=(self.settings, fake_engine)
        ):
            with self.assertRaisesRegex(RuntimeError, "backup interruption"):
                command_backup(SimpleNamespace(keep=7))

        backup_dir = self.settings.data_dir / "backups"
        self.assertEqual([], list(backup_dir.glob("*.db.tmp")))
        self.assertEqual([], list(backup_dir.glob("spark-*.db")))


if __name__ == "__main__":
    unittest.main()
