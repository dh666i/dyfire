"""PostgreSQL scheduler claim concurrency and state-preservation check.

The test uses only the explicitly supplied CI PostgreSQL service.  It creates
an isolated random schema, lets independent engine pools race for a synthetic
set of due tasks, and drops the schema when complete.  It is a scheduler
correctness signal for the first few hundred customers; it is not a
production-capacity claim and never connects to a deployed database.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from spark_console.crypto import CookieCipher
from spark_console.db import create_schema, session_scope
from spark_console.models import (
    DouyinAccount,
    SparkTask,
    SparkTaskTargetIdentity,
    TaskRun,
    User,
    UserSubscription,
)
from spark_console.scheduler import claim_next_due_task


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="requires an explicitly configured isolated PostgreSQL test service",
)


CUSTOMER_COUNT = 120
WORKER_COUNT = 12
IDLE_POLLS = 35


@dataclass(frozen=True)
class ClaimFixture:
    url: str
    schema: str
    engine: Engine
    admin_engine: Engine
    task_ids: tuple[str, ...]
    account_ids: tuple[str, ...]
    cookie_key: bytes
    original_accounts: dict[str, tuple[Any, ...]]
    original_tasks: dict[str, tuple[Any, ...]]
    original_identities: dict[str, tuple[Any, ...]]
    original_users: dict[str, tuple[Any, ...]]
    original_next_runs: dict[str, datetime]


def _id() -> str:
    return str(uuid.uuid4())


def _schema_name() -> str:
    return "claim_race_" + uuid.uuid4().hex


def _assert_local_test_url(url: str) -> None:
    """Refuse accidental use of a remote or production PostgreSQL target."""

    host = (make_url(url).host or "").strip("[]").lower()
    if host not in {"localhost", "127.0.0.1", "::1", "postgres"}:
        pytest.fail(
            "TEST_POSTGRES_URL must target the local CI PostgreSQL service; "
            f"refusing remote host {host or '<empty>'!r}"
        )


def _schema_engine(url: str, schema: str, **pool_options: Any) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={schema}"},
        **pool_options,
    )


def _account_snapshot(engine: Engine) -> dict[str, tuple[Any, ...]]:
    with session_scope(engine) as db:
        rows = db.execute(
            select(
                DouyinAccount.id,
                DouyinAccount.owner_user_id,
                DouyinAccount.display_name,
                DouyinAccount.encrypted_cookies,
                DouyinAccount.cookie_nonce,
                DouyinAccount.cookie_version,
                DouyinAccount.validation_state,
                DouyinAccount.dispatch_key,
            ).order_by(DouyinAccount.id)
        )
        return {row.id: tuple(row)[1:] for row in rows}


def _task_snapshot(engine: Engine) -> tuple[dict[str, tuple[Any, ...]], dict[str, datetime]]:
    with session_scope(engine) as db:
        rows = db.execute(
            select(
                SparkTask.id,
                SparkTask.owner_user_id,
                SparkTask.douyin_account_id,
                SparkTask.target_name,
                SparkTask.target_key,
                SparkTask.send_time,
                SparkTask.message_template,
                SparkTask.message_kind,
                SparkTask.enabled,
                SparkTask.next_run_at,
            ).order_by(SparkTask.id)
        )
        static: dict[str, tuple[Any, ...]] = {}
        next_runs: dict[str, datetime] = {}
        for row in rows:
            values = tuple(row)
            static[row.id] = values[1:9]
            next_runs[row.id] = values[9]
        return static, next_runs


def _identity_snapshot(engine: Engine) -> dict[str, tuple[Any, ...]]:
    with session_scope(engine) as db:
        rows = db.execute(
            select(SparkTaskTargetIdentity.task_id, SparkTaskTargetIdentity.sec_uid)
            .order_by(SparkTaskTargetIdentity.task_id)
        )
        return {row.task_id: (row.sec_uid,) for row in rows}


def _user_snapshot(engine: Engine) -> dict[str, tuple[Any, ...]]:
    with session_scope(engine) as db:
        rows = db.execute(
            select(User.id, User.username, User.role, User.status, User.admin_note)
            .order_by(User.id)
        )
        return {row.id: tuple(row)[1:] for row in rows}


@pytest.fixture(scope="module")
def claim_fixture(request: pytest.FixtureRequest) -> ClaimFixture:
    url = os.environ["TEST_POSTGRES_URL"]
    _assert_local_test_url(url)
    schema = _schema_name()
    admin_engine = create_engine(url, pool_pre_ping=True)
    quoted_schema = admin_engine.dialect.identifier_preparer.quote(schema)
    engine = _schema_engine(
        url,
        schema,
        pool_size=WORKER_COUNT,
        max_overflow=WORKER_COUNT,
        pool_timeout=30,
        pool_recycle=1800,
    )
    root_engine_ready = False
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
        root_engine_ready = True
        create_schema(engine)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        cookie_key = b"postgres-claim-cookie-key".ljust(32, b"!")
        cipher = CookieCipher(cookie_key)
        user_ids = [_id() for _ in range(CUSTOMER_COUNT)]
        account_ids = [_id() for _ in range(CUSTOMER_COUNT)]
        task_ids = [_id() for _ in range(CUSTOMER_COUNT)]
        user_rows = [
            {
                "id": user_id,
                "username": f"claim-race-user-{index:03d}",
                "password_hash": "synthetic-claim-hash",
                "role": "user",
                "status": "active",
                "admin_note": f"claim-race-{index}",
                "must_change_password": False,
            }
            for index, user_id in enumerate(user_ids)
        ]
        account_rows = []
        task_rows = []
        identity_rows = []
        for index, (user_id, account_id, task_id) in enumerate(
            zip(user_ids, account_ids, task_ids)
        ):
            sealed = cipher.encrypt(f"bound-cookie-{index}".encode())
            account_rows.append(
                {
                    "id": account_id,
                    "owner_user_id": user_id,
                    "display_name": f"并发账号-{index:03d}",
                    "encrypted_cookies": sealed.ciphertext,
                    "cookie_nonce": sealed.nonce,
                    "cookie_version": 1,
                    "validation_state": "valid",
                    "dispatch_key": index,
                }
            )
            task_rows.append(
                {
                    "id": task_id,
                    "owner_user_id": user_id,
                    "douyin_account_id": account_id,
                    "target_name": f"并发好友-{index:03d}",
                    "target_key": f"claim-race-target-{index:03d}",
                    "send_time": "09:00",
                    "message_template": "保留原消息",
                    "message_kind": "text",
                    "enabled": True,
                    "next_run_at": now - timedelta(seconds=1),
                }
            )
            identity_rows.append({"task_id": task_id, "sec_uid": f"sec-{index:03d}"})
        subscription_rows = [
            {
                "user_id": user_id,
                "valid_from": now - timedelta(days=1),
                "valid_until": now + timedelta(days=30),
                "max_accounts": 1,
                "max_tasks": 5,
            }
            for user_id in user_ids
        ]
        with session_scope(engine) as db:
            db.execute(insert(User), user_rows)
            db.execute(insert(UserSubscription), subscription_rows)
            db.execute(insert(DouyinAccount), account_rows)
            db.execute(insert(SparkTask), task_rows)
            db.execute(insert(SparkTaskTargetIdentity), identity_rows)
        original_accounts = _account_snapshot(engine)
        original_tasks, original_next_runs = _task_snapshot(engine)
        original_identities = _identity_snapshot(engine)
        original_users = _user_snapshot(engine)
        fixture = ClaimFixture(
            url=url,
            schema=schema,
            engine=engine,
            admin_engine=admin_engine,
            task_ids=tuple(task_ids),
            account_ids=tuple(account_ids),
            cookie_key=cookie_key,
            original_accounts=original_accounts,
            original_tasks=original_tasks,
            original_identities=original_identities,
            original_users=original_users,
            original_next_runs=original_next_runs,
        )
        def cleanup() -> None:
            engine.dispose()
            admin_engine.dispose()
            cleanup_engine = create_engine(url, pool_pre_ping=True)
            try:
                with cleanup_engine.begin() as connection:
                    connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE"))
            finally:
                cleanup_engine.dispose()
        request.addfinalizer(cleanup)
        return fixture
    except BaseException:
        engine.dispose()
        if root_engine_ready:
            with admin_engine.begin() as connection:
                connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE"))
        admin_engine.dispose()
        raise


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))
    return ordered[index]


def test_postgres_scheduler_claim_is_exactly_once_under_concurrency(
    claim_fixture: ClaimFixture,
):
    fixture = claim_fixture
    barrier = threading.Barrier(WORKER_COUNT)

    def worker(worker_number: int) -> tuple[list[str], list[str], list[float], list[str]]:
        # Each worker owns a distinct SQLAlchemy Engine and pool.  Sharing a
        # pool would hide connection/transaction behavior in the concurrency
        # signal this test is intended to exercise.
        worker_engine = _schema_engine(
            fixture.url,
            fixture.schema,
            pool_size=1,
            max_overflow=0,
            pool_timeout=30,
            pool_recycle=1800,
        )
        run_ids: list[str] = []
        task_ids: list[str] = []
        latencies: list[float] = []
        failures: list[str] = []
        idle = 0
        try:
            barrier.wait(timeout=30)
            while idle < IDLE_POLLS and len(run_ids) < CUSTOMER_COUNT:
                started = time.perf_counter()
                try:
                    with Session(worker_engine) as db:
                        run = claim_next_due_task(
                            db,
                            datetime.now(timezone.utc),
                            f"claim-race-worker-{worker_number}",
                        )
                        if run is None:
                            db.commit()
                        else:
                            run_ids.append(run.id)
                            task_ids.append(run.task_id)
                            db.commit()
                    latencies.append(time.perf_counter() - started)
                except BaseException as error:  # report every worker failure together
                    failures.append(f"{type(error).__name__}: {error}")
                    break
                if run is None:
                    idle += 1
                    time.sleep(0.01)
                else:
                    idle = 0
            return run_ids, task_ids, latencies, failures
        finally:
            worker_engine.dispose()

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=WORKER_COUNT) as pool:
        results = list(pool.map(worker, range(WORKER_COUNT)))
    elapsed = time.perf_counter() - started
    run_ids = [run_id for result in results for run_id in result[0]]
    claimed_task_ids = [task_id for result in results for task_id in result[1]]
    latencies = [latency for result in results for latency in result[2]]
    failures = [failure for result in results for failure in result[3]]
    print(
        "postgres scheduler claim concurrency: tasks=%d workers=%d elapsed=%.3fs "
        "p50=%.3fs p95=%.3fs claims=%d failures=%d"
        % (
            CUSTOMER_COUNT,
            WORKER_COUNT,
            elapsed,
            median(latencies) if latencies else 0.0,
            _percentile(latencies, 0.95),
            len(run_ids),
            len(failures),
        )
    )
    assert failures == []
    assert len(run_ids) == CUSTOMER_COUNT
    assert len(set(run_ids)) == CUSTOMER_COUNT
    assert claimed_task_ids == list(dict.fromkeys(claimed_task_ids))
    assert set(claimed_task_ids) == set(fixture.task_ids)

    with session_scope(fixture.engine) as db:
        runs = db.execute(
            select(
                TaskRun.id,
                TaskRun.task_id,
                TaskRun.status,
                TaskRun.stage,
                TaskRun.attempt,
            ).order_by(TaskRun.task_id)
        ).all()
        assert len(runs) == CUSTOMER_COUNT
        assert {row.task_id for row in runs} == set(fixture.task_ids)
        assert {row.id for row in runs} == set(run_ids)
        assert all(row.status == "running" and row.stage == "claimed" for row in runs)
        assert all(row.attempt == 1 for row in runs)

    assert _account_snapshot(fixture.engine) == fixture.original_accounts
    assert _identity_snapshot(fixture.engine) == fixture.original_identities
    assert _user_snapshot(fixture.engine) == fixture.original_users
    tasks, next_runs = _task_snapshot(fixture.engine)
    assert tasks == fixture.original_tasks
    assert set(next_runs) == set(fixture.original_next_runs)
    assert all(next_runs[task_id] > fixture.original_next_runs[task_id] for task_id in next_runs)

    cipher = CookieCipher(fixture.cookie_key)
    for account_id, snapshot in fixture.original_accounts.items():
        ciphertext = snapshot[2]
        nonce = snapshot[3]
        assert cipher.decrypt(ciphertext, nonce).startswith(b"bound-cookie-")


def test_postgres_scheduler_claim_fixture_teardown_isolated(claim_fixture: ClaimFixture):
    """The fixture's random schema is still isolated from the server default."""

    with claim_fixture.admin_engine.connect() as connection:
        assert connection.execute(
            text("SELECT current_schema()")
        ).scalar_one() in {"public", None}


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    # Kept intentionally empty: module fixture finalization owns schema teardown
    # and runs even when the assertion above fails.
    return None

