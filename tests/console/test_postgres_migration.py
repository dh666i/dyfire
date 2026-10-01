"""Real PostgreSQL cutover checks using disposable, isolated schemas only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest
from sqlalchemy import create_engine, event, inspect, select, text

from spark_console import data_migration
from spark_console.credentials import CredentialPayload
from spark_console.crypto import CookieCipher
from spark_console.db import create_schema
from spark_console.models import Base


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="requires an explicitly configured PostgreSQL test service",
)

COOKIE_KEY = b"migration-cookie-key".ljust(32, b"!")
STAMP = datetime(2024, 2, 29, 16, 42, 31, 654321)
ACCOUNT_IDS = ("account-Z", "account-a")


@pytest.fixture
def postgres_target():
    admin = create_engine(os.environ["TEST_POSTGRES_URL"], pool_pre_ping=True)
    schema = "migration_test_" + uuid.uuid4().hex
    quoted = admin.dialect.identifier_preparer.quote(schema)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {quoted}"))
    target = create_engine(
        os.environ["TEST_POSTGRES_URL"],
        pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={schema} -ctimezone=Asia/Shanghai"},
    )
    try:
        with target.connect() as connection:
            assert connection.scalar(text("SELECT current_schema()")) == schema
            assert connection.scalar(text("SHOW TIME ZONE")) == "Asia/Shanghai"
        yield target
    finally:
        target.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {quoted} CASCADE"))
        admin.dispose()


def _source(tmp_path: Path, *, populated: bool = True):
    path = tmp_path / "migration-source.db"
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    create_schema(engine)
    plaintext = {}
    if populated:
        cookie = {
            "name": "sessionid", "value": "preserve-active-login",
            "domain": ".douyin.com", "path": "/", "expires": -1,
            "httpOnly": True, "secure": True, "sameSite": "Lax",
        }
        formats = (
            [cookie],
            {
                "version": 2,
                "storage_state": {
                    "cookies": [cookie],
                    "origins": [{
                        "origin": "https://www.douyin.com",
                        "localStorage": [{"name": "session", "value": "preserve-local-storage"}],
                    }],
                },
            },
        )
        cipher = CookieCipher(COOKIE_KEY)
        accounts = []
        for version, (account_id, credential) in enumerate(zip(ACCOUNT_IDS, formats), 1):
            raw = json.dumps(credential, ensure_ascii=True).encode("ascii")
            sealed = cipher.encrypt(raw)
            plaintext[account_id] = (raw, sealed, version)
            accounts.append({
                "id": account_id, "owner_user_id": "owner", "display_name": f"Existing {version}",
                "encrypted_cookies": sealed.ciphertext, "cookie_nonce": sealed.nonce,
                "cookie_version": version, "validation_state": "valid",
                "last_verified_at": STAMP, "dispatch_key": 2147483648 + version,
            })

        records = {
            "users": [{
                "id": "owner", "username": "migration-owner", "password_hash": "preserve-password-hash",
                "role": "admin", "status": "active", "admin_note": "认识的用户",
                "must_change_password": False, "failed_login_count": 2, "locked_until": None,
            }],
            "mail_settings": [{"id": 7, "ciphertext": b"\x00mail-cipher\xff", "nonce": b"n" * 12}],
            "douyin_accounts": accounts,
            "account_health": [{
                "account_id": ACCOUNT_IDS[0], "reason": "preserved", "checked_at": STAMP,
                "next_check_at": STAMP + timedelta(days=1), "attempts": 3, "negative_checks": 1,
            }],
            "account_operations": [{
                "account_id": ACCOUNT_IDS[0], "token": "expired-operation", "kind": "send", "expires_at": STAMP,
            }],
            "contact_directory_jobs": [{
                "id": "directory", "account_id": ACCOUNT_IDS[0], "owner_user_id": "owner",
                "query": "朋友", "limit": 10, "status": "completed", "result_json": '[{"name":"朋友"}]',
                "started_at": STAMP, "finished_at": STAMP + timedelta(seconds=2),
            }],
            "douyin_account_identities": [{"account_id": ACCOUNT_IDS[0], "douyin_unique_id": "keep-account-uid"}],
            "douyin_account_profiles": [{
                "account_id": ACCOUNT_IDS[0], "nickname": "昵称", "unique_id": "public-id",
                "avatar_url": "https://example.com/avatar.png", "custom_remark": True, "synced_at": STAMP,
            }],
            "douyin_contact_identities": [{
                "account_id": ACCOUNT_IDS[0], "sec_uid": "keep-friend-sec-uid", "short_id": "12345",
                "unique_id": "friend-id", "nickname": "好友", "remark_name": "同学",
            }],
            "douyin_contact_avatars": [{
                "account_id": ACCOUNT_IDS[0], "sec_uid": "keep-friend-sec-uid", "avatar_url": "https://example.com/friend.png",
            }],
            "douyin_conversations": [
                {"account_id": ACCOUNT_IDS[0], "display_name": name} for name in ("朋友", "A-friend", "z-friend")
            ],
            "douyin_login_sessions": [{
                "id": "finished-scan", "owner_user_id": "owner", "status": "succeeded", "slot": None,
                "mode": "reauth", "qr_png": b"\x89PNG\x00", "account_id": ACCOUNT_IDS[0],
                "expires_at": STAMP + timedelta(minutes=5), "finished_at": STAMP,
            }],
            "douyin_login_actions": [{
                "id": 9001, "scan_id": "finished-scan", "kind": "click", "x_million": 500000,
                "y_million": 250000, "consumed_at": STAMP,
            }],
            "douyin_login_inputs": [{
                "id": 4101, "scan_id": "finished-scan", "request_id": "original-request",
                "ciphertext": b"input-cipher", "nonce": b"i" * 12, "delivery_status": "delivered",
                "consumed_at": STAMP, "attempted_at": STAMP, "finished_at": STAMP,
            }],
            "douyin_login_challenges": [{
                "scan_id": "finished-scan", "masked_phone": "138****1234", "verification_sent": True,
                "resend_pending": False, "can_resend": False, "resend_available_at": None,
            }],
            "mail_preferences": [{"user_id": "owner", "email": "owner@example.com", "enabled": True, "verified": True}],
            "notification_policies": [{"user_id": "owner", "events": "account_alert,expiry", "quiet_enabled": True}],
            "password_resets": [{"user_id": "owner", "digest": "d" * 64, "expires_at": STAMP}],
            "mail_account_states": [{"account_id": ACCOUNT_IDS[0], "state": "valid", "revision": 7}],
            "mail_deliveries": [{
                "id": "mail", "event_key": "preserved-mail", "user_id": "owner", "recipient": "owner@example.com",
                "event": "account_recovered", "subject": "恢复", "body": "邮件内容", "status": "sent",
            }],
            "invite_codes": [{
                "id": "invite", "code_hash": "i" * 64, "created_by_user_id": "owner",
                "expires_at": STAMP + timedelta(days=30), "used_by_user_id": "owner", "used_at": STAMP,
            }],
            "invite_code_secrets": [{"invite_id": "invite", "ciphertext": b"invite-cipher", "nonce": b"v" * 12}],
            "redemption_codes": [{
                "id": "code", "code": "KEEP-FULL-CODE", "code_hash": "c" * 64, "duration_days": 30,
                "max_accounts": 1, "max_tasks": 5, "expires_at": datetime(9999, 12, 31, 23, 59, 59, 999999),
                "created_by_user_id": "owner", "redeemed_by_user_id": None, "redeemed_at": None,
            }],
            "user_subscriptions": [{
                "user_id": "owner", "valid_from": STAMP, "valid_until": STAMP + timedelta(days=30),
                "max_accounts": 3, "max_tasks": 10, "version": 6,
            }],
            "subscription_grants": [{
                "id": "grant", "user_id": "owner", "source": "admin", "actor_user_id": "owner",
                "duration_days": 30, "valid_from": STAMP, "valid_until": STAMP + timedelta(days=30),
                "max_accounts": 3, "max_tasks": 10,
            }],
            "web_sessions": [{
                "id": "web-session", "user_id": "owner", "token_hash": "t" * 64, "csrf_token": "s" * 64,
                "expires_at": STAMP + timedelta(days=60), "elevated_until": None,
            }],
            "spark_tasks": [
                {
                    "id": task_id, "owner_user_id": "owner", "douyin_account_id": account_id,
                    "target_name": "原有好友", "target_key": "uid:keep-friend-sec-uid", "send_time": send_time,
                    "message_template": "原有消息内容", "message_kind": "text", "enabled": enabled,
                    "next_run_at": next_run_at,
                }
                for task_id, account_id, send_time, enabled, next_run_at in (
                    ("task-enabled", ACCOUNT_IDS[0], "00:05", True, STAMP + timedelta(days=1)),
                    ("task-disabled", ACCOUNT_IDS[1], "23:59", False, None),
                )
            ],
            "spark_task_target_identities": [{"task_id": "task-enabled", "sec_uid": "keep-friend-sec-uid"}],
            "task_runs": [{
                "id": "finished-run", "task_id": "task-enabled", "scheduled_for": STAMP,
                "status": "succeeded", "stage": "done", "worker_id": "old-worker", "attempt": 2,
                "submitted_at": STAMP, "started_at": STAMP, "finished_at": STAMP + timedelta(seconds=5),
                "message_digest": "m" * 64,
            }],
            "task_health_retries": [{
                "run_id": "finished-run", "task_revision": STAMP, "expires_at": STAMP, "last_reason": "history",
            }],
            "task_run_attempts": [{
                "run_id": "finished-run", "number": 2, "started_at": STAMP, "finished_at": STAMP,
                "submitted_at": STAMP, "status": "succeeded", "stage": "done", "reason": None,
            }],
            "task_run_events": [{
                "id": "run-event", "run_id": "finished-run", "sequence": 2, "occurred_at": STAMP,
                "event": "confirmed", "stage": "done", "detail": "已确认",
            }],
            "audit_events": [{
                "id": "audit", "actor_user_id": "owner", "action": "account.bound",
                "resource_type": "account", "resource_id": ACCOUNT_IDS[0], "detail": "历史记录",
            }],
            # Optional administrator MFA enrollment has its own table. The
            # migration fixture keeps it empty while still requiring the
            # destination schema to include it.
            "admin_totp_credentials": [],
            "admin_totp_recovery_codes": [],
        }
        assert set(records) | {"worker_lock"} == set(Base.metadata.tables)
        with engine.begin() as connection:
            for table in Base.metadata.sorted_tables:
                for record in records.get(table.name, []):
                    for column in table.columns:
                        if column.name not in record and column.name in ("created_at", "updated_at", "discovered_at"):
                            record[column.name] = STAMP
                    connection.execute(table.insert(), record)
            connection.execute(text("UPDATE worker_lock SET worker_id='retired-worker', lease_until=:stamp"), {"stamp": STAMP})
            connection.execute(text("UPDATE schema_migrations SET applied_at=:stamp"), {"stamp": STAMP})
    engine.dispose()
    return path, plaintext


def _snapshot(engine):
    result = {}
    with engine.connect() as connection:
        for table in [*Base.metadata.sorted_tables, data_migration.SCHEMA_HISTORY]:
            records = []
            for row in connection.execute(select(table)):
                record = data_migration._normalize_record(table, dict(row._mapping))
                values = []
                for column in table.columns:
                    value = record[column.name]
                    if isinstance(value, datetime):
                        value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
                    elif isinstance(value, (bytearray, memoryview)):
                        value = bytes(value)
                    values.append(value)
                records.append(tuple(values))
            indices = [list(table.columns).index(column) for column in table.primary_key.columns]
            result[table.name] = sorted(records, key=lambda row: tuple(row[index] for index in indices))
    return result


def test_postgres_migration_preserves_every_row_cookie_and_schedule(tmp_path, postgres_target):
    source, credentials = _source(tmp_path)
    original_file = hashlib.sha256(source.read_bytes()).digest()
    source_engine = data_migration.readonly_sqlite_engine(source)
    try:
        before = _snapshot(source_engine)
        report = data_migration.migrate_sqlite_to_postgres(
            source, postgres_target, apply=True, batch_size=1, cookie_key=COOKIE_KEY
        )
        assert report.applied is True
        assert report.table_rows["douyin_accounts"] == 2
        assert report.table_rows["spark_tasks"] == 2
        assert _snapshot(postgres_target) == before
        assert hashlib.sha256(source.read_bytes()).digest() == original_file

        accounts = Base.metadata.tables["douyin_accounts"]
        with postgres_target.connect() as connection:
            for account_id, (raw, sealed, version) in credentials.items():
                account = connection.execute(select(accounts).where(accounts.c.id == account_id)).one()._mapping
                assert account["encrypted_cookies"] == sealed.ciphertext
                assert account["cookie_nonce"] == sealed.nonce
                assert account["cookie_version"] == version
                restored = CookieCipher(COOKIE_KEY).decrypt(account["encrypted_cookies"], account["cookie_nonce"])
                assert restored == raw
                assert CredentialPayload.parse(restored, version) == CredentialPayload.parse(raw, version)
            assert connection.scalar(text("SHOW TIME ZONE")) == "Asia/Shanghai"
    finally:
        source_engine.dispose()


@pytest.mark.parametrize("populated", [False, True])
def test_postgres_migration_synchronizes_only_real_sequences(tmp_path, postgres_target, populated):
    source, _ = _source(tmp_path, populated=populated)
    data_migration.migrate_sqlite_to_postgres(source, postgres_target, apply=True, cookie_key=COOKIE_KEY)
    with postgres_target.begin() as connection:
        assert connection.scalar(text("SELECT pg_get_serial_sequence('worker_lock','id')")) is None
        assert connection.scalar(text("SELECT pg_get_serial_sequence('mail_settings','id')")) is None
        for table, expected in (("douyin_login_actions", 9002), ("douyin_login_inputs", 4102)):
            sequence = connection.scalar(text("SELECT pg_get_serial_sequence(:table,'id')"), {"table": table})
            assert sequence is not None
            assert connection.scalar(text("SELECT nextval(CAST(:sequence AS regclass))"), {"sequence": sequence}) == (expected if populated else 1)


def test_postgres_migration_dry_run_never_creates_tables(tmp_path, postgres_target):
    source, _ = _source(tmp_path)
    report = data_migration.migrate_sqlite_to_postgres(source, postgres_target, cookie_key=COOKIE_KEY)
    assert report.applied is False
    assert report.total_rows > 0
    assert inspect(postgres_target).get_table_names() == []


@pytest.mark.parametrize("existing", ["application", "unexpected", "history"])
@pytest.mark.parametrize("apply", [False, True])
def test_postgres_migration_rejects_existing_target_without_changing_it(tmp_path, postgres_target, existing, apply):
    source, _ = _source(tmp_path)
    with postgres_target.begin() as connection:
        if existing == "application":
            Base.metadata.tables["users"].create(connection)
        elif existing == "unexpected":
            connection.execute(text("CREATE TABLE existing_customer_data (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO existing_customer_data VALUES (42)"))
        else:
            data_migration.SCHEMA_HISTORY.create(connection)
            connection.execute(data_migration.SCHEMA_HISTORY.insert(), {"version": 1, "name": "keep", "applied_at": STAMP})
    tables = inspect(postgres_target).get_table_names()
    with pytest.raises(ValueError, match="not empty|application tables"):
        data_migration.migrate_sqlite_to_postgres(source, postgres_target, apply=apply, cookie_key=COOKIE_KEY)
    assert inspect(postgres_target).get_table_names() == tables
    with postgres_target.connect() as connection:
        if existing == "unexpected":
            assert connection.scalar(text("SELECT id FROM existing_customer_data")) == 42
        elif existing == "history":
            assert connection.scalar(text("SELECT name FROM schema_migrations")) == "keep"


def test_postgres_migration_failure_rolls_back_rows_and_schema(tmp_path, postgres_target, monkeypatch):
    source, _ = _source(tmp_path)
    original_file = hashlib.sha256(source.read_bytes()).digest()
    copy_table = data_migration._copy_table

    def fail_after_user_copy(source_connection, destination, table, *, batch_size):
        count = copy_table(source_connection, destination, table, batch_size=batch_size)
        if table.name == "users":
            assert destination.scalar(select(Base.metadata.tables["users"].c.id)) == "owner"
            raise RuntimeError("injected copy failure")
        return count

    monkeypatch.setattr(data_migration, "_copy_table", fail_after_user_copy)
    with pytest.raises(RuntimeError, match="injected copy failure"):
        data_migration.migrate_sqlite_to_postgres(source, postgres_target, apply=True, cookie_key=COOKIE_KEY)
    assert inspect(postgres_target).get_table_names() == []
    assert hashlib.sha256(source.read_bytes()).digest() == original_file


def test_postgres_migration_rejects_same_count_cookie_corruption(tmp_path, postgres_target, monkeypatch):
    source, _ = _source(tmp_path)
    synchronize = data_migration._synchronize_postgres_sequences

    def corrupt_cookie(destination):
        synchronize(destination)
        account = Base.metadata.tables["douyin_accounts"]
        destination.execute(account.update().where(account.c.id == ACCOUNT_IDS[0]).values(encrypted_cookies=b"corrupted"))

    monkeypatch.setattr(data_migration, "_synchronize_postgres_sequences", corrupt_cookie)
    with pytest.raises(RuntimeError, match="content mismatch for douyin_accounts"):
        data_migration.migrate_sqlite_to_postgres(source, postgres_target, apply=True, cookie_key=COOKIE_KEY)
    assert inspect(postgres_target).get_table_names() == []


def test_postgres_migration_wrong_cookie_key_leaves_target_empty(tmp_path, postgres_target):
    source, _ = _source(tmp_path)
    with pytest.raises(ValueError, match="Cookie"):
        data_migration.migrate_sqlite_to_postgres(source, postgres_target, apply=True, cookie_key=b"x" * 32)
    assert inspect(postgres_target).get_table_names() == []
