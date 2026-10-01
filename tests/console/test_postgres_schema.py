import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, select

from spark_console.db import create_schema, session_scope
from spark_console.models import DouyinAccount, SparkTask, User, UserSubscription
from spark_console.scheduler import claim_next_due_task


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="PostgreSQL service is configured only in CI",
)


def test_postgres_schema_and_scheduler_claim_are_real_database_operations():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_pre_ping=True)
    create_schema(engine)
    inspector = inspect(engine)
    indexes = {
        index["name"]
        for table in inspector.get_table_names()
        for index in inspector.get_indexes(table)
    }
    assert "uq_douyin_login_inputs_scan_active" in indexes
    assert "ix_douyin_accounts_dispatch_key" in indexes
    assert "ix_task_runs_status_finished" in indexes
    assert "ix_mail_deliveries_status_updated" in indexes

    now = datetime.now(timezone.utc).replace(microsecond=0)
    with session_scope(engine) as db:
        user = User(username="pg-schema-user", password_hash="hash", role="user")
        db.add(user)
        db.flush()
        db.add(UserSubscription(
            user_id=user.id,
            valid_from=now - timedelta(days=1),
            valid_until=now + timedelta(days=30),
            max_accounts=1,
            max_tasks=5,
        ))
        account = DouyinAccount(
            owner_user_id=user.id,
            display_name="PG account",
            encrypted_cookies=b"cipher",
            cookie_nonce=b"nonce",
            validation_state="valid",
        )
        db.add(account)
        db.flush()
        task = SparkTask(
            owner_user_id=user.id,
            douyin_account_id=account.id,
            target_name="PG friend",
            send_time="09:00",
            message_template="hello",
            enabled=True,
            next_run_at=now,
        )
        db.add(task)
        db.flush()
        run = claim_next_due_task(db, now, "pg-worker")
        assert run is not None
        assert run.task_id == task.id
    with engine.begin() as connection:
        connection.execute(select(User).limit(1))
    engine.dispose()
