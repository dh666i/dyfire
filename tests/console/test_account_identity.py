import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.account_profile import authenticated_user_id, profile_from_response, read_self_profile
from spark_console.crypto import CookieCipher
from spark_console.db import create_schema
from spark_console.models import User, DouyinAccount, DouyinAccountIdentity, SparkTask
from spark_console.services.accounts import (
    AccountService, AccountIdentityAlreadyBound, AccountIdentityMismatch, AccountIdentityUnavailable,
)
from spark_console.services.account_identity import enroll_legacy_identities
from spark_console.services.audits import AuditService
from tests.console.test_services import _storage_state


@pytest.fixture
def case(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'identity.db'}")
    create_schema(engine)
    cipher = CookieCipher(b"k" * 32)
    with Session(engine) as db:
        users = [User(username=f"identity-owner-{i}", password_hash="fixture", role="user") for i in range(2)]
        db.add_all(users); db.commit()
        owner_ids = [u.id for u in users]
    yield engine, cipher, owner_ids
    engine.dispose()


def service(db, cipher):
    return AccountService(db, cipher, AuditService(db))


def test_live_passport_shape_without_error_code_retains_only_safe_profile_fields():
    body = {"message": "success", "data": {
        "user_id": 1234567890123456789, "user_id_str": "1234567890123456789",
        "name": "昵称", "is_visitor_account": False, "session_key": "never-return-this", "mobile": "never-return-this",
    }}
    assert authenticated_user_id(body) == "1234567890123456789"
    profile = profile_from_response(body)
    assert profile["platform_user_id"] == "1234567890123456789"
    assert profile["nickname"] == "昵称"
    assert "never-return-this" not in str(profile)


@pytest.mark.parametrize("data", [
    {"user_id": "123", "error_code": 13}, {"user_id": "123", "error_code": False},
    {"user_id": "123", "user_id_str": "456"}, {"user_id": True},
    {"user_id": True, "user_id_str": "123"},
    {"user_id": "0"}, {"user_id": "123", "is_visitor_account": True}, {},
])
def test_invalid_visitor_and_conflicting_identity_fail_closed(data):
    assert authenticated_user_id({"message": "success", "data": data}) is None
    assert profile_from_response({"message": "success", "data": data}) == {}


def test_profile_rejects_unauthorized_response_even_if_body_looks_successful():
    context = AsyncMock()
    context.request.get.return_value.status = 401
    context.request.get.return_value.json.return_value = {"message": "success", "data": {"user_id": "123"}}
    assert asyncio.run(read_self_profile(context)) == {}


def test_top_level_error_is_not_hidden_by_a_successful_looking_user_object():
    assert authenticated_user_id({"message": "success", "error_code": 13, "data": {"user_id": "123"}}) is None


def test_uid_is_global_identity_and_display_id_changes_do_not_change_account(case):
    engine, cipher, owners = case
    with Session(engine) as db:
        accounts = service(db, cipher)
        account = accounts.create_from_storage_state(owners[0], "备注", _storage_state("old"), "old-public-id", platform_user_id="123")
        with pytest.raises(AccountIdentityAlreadyBound):
            accounts.create_from_storage_state(owners[1], "重复", _storage_state("duplicate"), "different-public-id", platform_user_id="123")
        accounts.reauthenticate_owned(owners[0], account.id, _storage_state("refreshed"), "new-public-id", platform_user_id="123")
        assert db.get(DouyinAccountIdentity, account.id).douyin_unique_id == "new-public-id"
        assert account.display_name == "备注"
        with pytest.raises(AccountIdentityMismatch):
            accounts.reauthenticate_owned(owners[0], account.id, _storage_state("wrong"), "new-public-id", platform_user_id="456")
        assert b"refreshed" in accounts.decrypt_for_worker(account.id)


def test_database_unique_constraint_protects_against_racing_identity_claims(case):
    engine, cipher, owners = case
    with Session(engine) as db:
        accounts = service(db, cipher)
        a = accounts.create_from_storage_state(owners[0], "a", _storage_state("a"), platform_user_id="123")
        b = accounts.create_from_storage_state(owners[1], "b", _storage_state("b"))
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.get(DouyinAccountIdentity, b.id).douyin_user_id = "123"
                db.flush()
        assert db.get(DouyinAccountIdentity, a.id).douyin_user_id == "123"


def test_legacy_enrollment_is_dry_run_then_preserves_credentials_and_tasks(case):
    engine, cipher, owners = case
    with Session(engine) as db:
        account = service(db, cipher).create_from_storage_state(owners[0], "原备注", _storage_state("original"))
        task = SparkTask(owner_user_id=owners[0], douyin_account_id=account.id, target_name="好友", send_time="09:30", message_template="test", enabled=False)
        db.add(task); db.commit()
        account_id, task_id = account.id, task.id
        expected = (account.encrypted_cookies, account.cookie_nonce, account.updated_at)
    reader = AsyncMock(return_value={"platform_user_id": "123"})
    preview = asyncio.run(enroll_legacy_identities(engine, cipher, reader=reader))
    assert preview["accounts"][0]["result"] == "verified" and preview["pending_accounts"] == 1
    with Session(engine) as db:
        assert db.get(DouyinAccountIdentity, account_id).douyin_user_id is None
    applied = asyncio.run(enroll_legacy_identities(engine, cipher, apply=True, reader=reader))
    assert applied["ready_for_new_bindings"]
    assert applied["accounts"][0]["result"] == "enrolled"
    assert "123" not in str(applied)
    with Session(engine) as db:
        account = db.get(DouyinAccount, account_id)
        assert (account.encrypted_cookies, account.cookie_nonce, account.updated_at) == expected
        assert account.display_name == "原备注"
        task = db.get(SparkTask, task_id)
        assert task.douyin_account_id == account_id and not task.enabled


def test_unverifiable_and_duplicate_legacy_states_are_never_guessed(case):
    engine, cipher, owners = case
    with Session(engine) as db:
        for owner in owners:
            account = service(db, cipher).create_from_storage_state(owner, "旧账号", _storage_state("original"))
            db.add(SparkTask(owner_user_id=owner, douyin_account_id=account.id, target_name="fixture", send_time="09:30", message_template="test", enabled=True))
        db.commit()
    reader = AsyncMock(return_value={"platform_user_id": "123"})
    result = asyncio.run(enroll_legacy_identities(engine, cipher, apply=True, reader=reader))
    assert all(row["result"] == "identity_conflict" for row in result["accounts"])
    with Session(engine) as db:
        assert not list(db.scalars(select(DouyinAccountIdentity).where(DouyinAccountIdentity.douyin_user_id.is_not(None))))
        with pytest.raises(AccountIdentityUnavailable):
            service(db, cipher).require_legacy_identities_ready()
    result = asyncio.run(enroll_legacy_identities(engine, cipher, apply=True, reader=AsyncMock(return_value={})))
    assert all(row["result"] == "unavailable" for row in result["accounts"])


def test_enrollment_compare_and_swap_rejects_newer_credentials(case):
    engine, cipher, owners = case
    with Session(engine) as db:
        accounts = service(db, cipher)
        account = accounts.create_from_storage_state(owners[0], "旧", _storage_state("old"))
        expected = (account.encrypted_cookies, account.cookie_nonce, account.cookie_version)
        account.encrypted_cookies = b"newer-state"; db.flush()
        from spark_console.services import Conflict
        with pytest.raises(Conflict):
            accounts.record_identity_if_current(account.id, expected, "123")
        assert db.get(DouyinAccountIdentity, account.id).douyin_user_id is None


def test_active_cutover_preserves_unavailable_dormant_accounts(case):
    engine, cipher, owners = case
    with Session(engine) as db:
        active = service(db, cipher).create_from_storage_state(owners[0], "active", _storage_state("active"))
        dormant = service(db, cipher).create_from_storage_state(owners[1], "dormant", _storage_state("dormant"))
        db.add(SparkTask(owner_user_id=owners[0], douyin_account_id=active.id, target_name="fixture", send_time="09:30", message_template="test", enabled=True))
        db.commit(); dormant_id = dormant.id
    reader = AsyncMock(return_value={"platform_user_id": "123"})
    result = asyncio.run(enroll_legacy_identities(engine, cipher, apply=True, active_only=True, reader=reader))
    assert result["ready_for_new_bindings"] and result["pending_accounts"] == 1
    reader.assert_awaited_once()
    with Session(engine) as db:
        assert db.get(DouyinAccountIdentity, dormant_id).douyin_user_id is None
        assert db.get(DouyinAccount, dormant_id) is not None


def test_corrupt_legacy_credentials_are_reported_without_partial_identity_write(case):
    engine, cipher, owners = case
    with Session(engine) as db:
        account = service(db, cipher).create_from_storage_state(owners[0], "legacy", _storage_state("old"))
        account.encrypted_cookies = b"not-decryptable";db.commit()
    reader = AsyncMock()
    result = asyncio.run(enroll_legacy_identities(engine, cipher, apply=True, reader=reader))
    assert result["accounts"][0]["result"] == "credentials_unreadable"
    reader.assert_not_awaited()


def test_sqlite_v19_upgrade_only_adds_nullable_identity_and_unique_index(case):
    engine, cipher, owners = case
    # Recreate the legacy identity table without the newly introduced column.
    with engine.begin() as db:
        db.execute(text("DROP TABLE douyin_account_identities"))
        db.execute(text("CREATE TABLE douyin_account_identities (account_id VARCHAR(36) PRIMARY KEY, douyin_unique_id VARCHAR(64), created_at DATETIME)"))
        db.execute(text("DELETE FROM schema_migrations WHERE version=20"))
        db.execute(text("INSERT INTO douyin_account_identities VALUES ('legacy-id', NULL, '2026-01-01')"))
    create_schema(engine)
    with engine.connect() as db:
        row = db.execute(text("SELECT account_id,douyin_unique_id,douyin_user_id FROM douyin_account_identities")).one()
        assert tuple(row) == ("legacy-id", None, None)
        assert db.scalar(text("SELECT count(*) FROM schema_migrations WHERE version=20")) == 1
        assert "uq_douyin_account_user_id" in [r[1] for r in db.exec_driver_sql("PRAGMA index_list(douyin_account_identities)")]
