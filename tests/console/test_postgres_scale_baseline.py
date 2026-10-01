"""Isolated PostgreSQL/API capacity baseline.

This is a repeatable engineering signal, not a production capacity claim.  It
creates a random PostgreSQL schema, loads a representative synthetic dataset,
then sends authenticated read-only API requests concurrently.  The schema is
dropped in the fixture teardown; no deployed database, Douyin session, or
production key is used.

The fixture intentionally preserves the same invariants as the local scale
baseline: existing encrypted account cookies, target identities, task times,
and task messages must be unchanged by read traffic.  The test prints p50/p95
latency and failure rate so CI history can reveal regressions without hiding
the limits of a single service-container benchmark.
"""

from __future__ import annotations

import json
import os
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, insert, select, text
from sqlalchemy.engine import Engine

from spark_console.config import Settings
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
from spark_console.security import SessionService
from spark_console.web.app import create_app


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="requires an explicitly configured isolated PostgreSQL test service",
)


CUSTOMER_COUNT = 500
ACCOUNT_COUNT = 500
TASK_COUNT = 2_500
RUN_COUNT = 75_000
TASKS_PER_ACCOUNT = TASK_COUNT // ACCOUNT_COUNT
RUNS_PER_TASK = RUN_COUNT // TASK_COUNT
CONCURRENCY = 12
REQUESTS_PER_WORKER = 8
P95_LIMIT_SECONDS = 10.0


@dataclass(frozen=True)
class PostgresScaleFixture:
    engine: Engine
    settings: Settings
    admin_token: str = field(repr=False)
    customer_token: str = field(repr=False)
    customer_id: str
    account_id: str
    task_ids: tuple[str, ...]
    cookie_key: bytes = field(repr=False)


def _id() -> str:
    return str(uuid.uuid4())


def _schema_name() -> str:
    # The value is generated locally and is deliberately restricted to the
    # identifier alphabet accepted by PostgreSQL without quoting surprises.
    return "scale_api_" + uuid.uuid4().hex


def _postgres_engine(url: str, schema: str, **pool_options) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={schema}"},
        **pool_options,
    )


@pytest.fixture(scope="module")
def postgres_scale_fixture(tmp_path_factory: pytest.TempPathFactory) -> PostgresScaleFixture:
    """Load 500 users, 500 accounts, 2,500 tasks, and 75,000 task runs."""

    url = os.environ["TEST_POSTGRES_URL"]
    schema = _schema_name()
    admin_engine = create_engine(url, pool_pre_ping=True)
    quoted_schema = admin_engine.dialect.identifier_preparer.quote(schema)
    with admin_engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))

    engine = _postgres_engine(
        url,
        schema,
        pool_size=CONCURRENCY,
        max_overflow=CONCURRENCY,
        pool_timeout=30,
        pool_recycle=1800,
    )
    root = tmp_path_factory.mktemp("dyfire-postgres-scale")
    cookie_key = b"postgres-scale-cookie-key".ljust(32, b"!")
    session_key = b"postgres-scale-session-key".ljust(32, b"!")
    cookie_key_file = Path(root) / "cookie.key"
    session_key_file = Path(root) / "session.key"
    cookie_key_file.write_bytes(cookie_key)
    session_key_file.write_bytes(session_key)
    settings = Settings(
        data_dir=Path(root),
        database_url=url,
        cookie_key_file=cookie_key_file,
        session_key_file=session_key_file,
        secure_cookies=False,
        database_pool_size=CONCURRENCY,
        database_max_overflow=CONCURRENCY,
        database_pool_timeout_seconds=30,
    )

    try:
        create_schema(engine)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        admin_id = _id()
        customer_ids = [_id() for _ in range(CUSTOMER_COUNT)]
        account_ids = [_id() for _ in range(ACCOUNT_COUNT)]
        task_ids = [_id() for _ in range(TASK_COUNT)]
        cipher = CookieCipher(cookie_key)
        users = [
            {
                "id": admin_id,
                "username": "pg-scale-admin",
                "password_hash": "synthetic-fixture-hash",
                "role": "admin",
                "status": "active",
                "must_change_password": False,
            },
            *[
                {
                    "id": user_id,
                    "username": f"pg-scale-user-{index:04d}",
                    "password_hash": "synthetic-fixture-hash",
                    "role": "user",
                    "status": "active",
                    "must_change_password": False,
                }
                for index, user_id in enumerate(customer_ids)
            ],
        ]
        account_rows = []
        for index, account_id in enumerate(account_ids):
            sealed = cipher.encrypt(f"preserved-postgres-cookie-{index}".encode())
            account_rows.append(
                {
                    "id": account_id,
                    "owner_user_id": customer_ids[index],
                    "display_name": f"PG 抖音账号-{index:04d}",
                    "encrypted_cookies": sealed.ciphertext,
                    "cookie_nonce": sealed.nonce,
                    "cookie_version": 1,
                    "validation_state": "valid",
                    "dispatch_key": index,
                }
            )
        task_rows = []
        identity_rows = []
        for account_index, account_id in enumerate(account_ids):
            owner_id = customer_ids[account_index]
            for offset in range(TASKS_PER_ACCOUNT):
                task_index = account_index * TASKS_PER_ACCOUNT + offset
                task_id = task_ids[task_index]
                task_rows.append(
                    {
                        "id": task_id,
                        "owner_user_id": owner_id,
                        "douyin_account_id": account_id,
                        "target_name": f"好友-{account_index:04d}-{offset}",
                        "target_key": f"pg-target-{account_index:04d}-{offset}",
                        "send_time": f"{(8 + offset) % 24:02d}:00",
                        "message_template": "保留的测试消息",
                        "message_kind": "douyin_sticker",
                        "enabled": True,
                        "next_run_at": now + timedelta(minutes=task_index + 1),
                    }
                )
                identity_rows.append(
                    {"task_id": task_id, "sec_uid": f"pg-sec-{account_index:04d}-{offset}"}
                )

        started = time.perf_counter()
        with session_scope(engine) as db:
            db.execute(insert(User), users)
            db.execute(
                insert(UserSubscription),
                [
                    {
                        "user_id": user_id,
                        "valid_from": now - timedelta(days=1),
                        "valid_until": now + timedelta(days=30),
                        "max_accounts": 1,
                        "max_tasks": 10,
                    }
                    for user_id in customer_ids
                ],
            )
            db.execute(insert(DouyinAccount), account_rows)
            db.execute(insert(SparkTask), task_rows)
            db.execute(insert(SparkTaskTargetIdentity), identity_rows)

            run_rows = []
            for task_index, task_id in enumerate(task_ids):
                for offset in range(RUNS_PER_TASK):
                    run_rows.append(
                        {
                            "id": _id(),
                            "task_id": task_id,
                            "scheduled_for": now - timedelta(days=offset + 1),
                            "status": "success" if offset % 5 else "failed",
                            "stage": "complete" if offset % 5 else "worker_error",
                            "attempt": 1,
                        }
                    )
                if len(run_rows) >= 5_000:
                    db.execute(insert(TaskRun), run_rows)
                    run_rows.clear()
            if run_rows:
                db.execute(insert(TaskRun), run_rows)

            sessions = SessionService(session_key)
            admin_token, admin_session = sessions.create_record(admin_id, now=now)
            customer_token, customer_session = sessions.create_record(customer_ids[0], now=now)
            db.add_all([admin_session, customer_session])
        build_seconds = time.perf_counter() - started
        print(
            "postgres isolated fixture: customers=%d accounts=%d tasks=%d runs=%d build=%.3fs"
            % (CUSTOMER_COUNT, ACCOUNT_COUNT, TASK_COUNT, RUN_COUNT, build_seconds)
        )
        return PostgresScaleFixture(
            engine=engine,
            settings=settings,
            admin_token=admin_token,
            customer_token=customer_token,
            customer_id=customer_ids[0],
            account_id=account_ids[0],
            task_ids=tuple(task_ids[:TASKS_PER_ACCOUNT]),
            cookie_key=cookie_key,
        )
    except BaseException:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {quoted_schema} CASCADE"))
        admin_engine.dispose()
        raise
    finally:
        # The returned fixture owns the application engine.  The admin engine
        # is only needed to create/drop the random schema and is closed here.
        admin_engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _dispose_postgres_scale_fixture(
    postgres_scale_fixture: PostgresScaleFixture,
):
    yield
    engine = postgres_scale_fixture.engine
    # ``search_path`` is a connection option and is not exposed in Engine.url.
    # Find the isolated schema through the current connection before disposing.
    with engine.connect() as connection:
        current_schema = connection.scalar(text("SELECT current_schema()"))
    engine.dispose()
    admin_engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_pre_ping=True)
    quoted_schema = admin_engine.dialect.identifier_preparer.quote(current_schema)
    with admin_engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE"))
    admin_engine.dispose()


def _client(fixture: PostgresScaleFixture, token: str) -> TestClient:
    client = TestClient(create_app(fixture.settings, fixture.engine), base_url="https://testserver")
    client.cookies.set("spark_session", token)
    return client


def _state_snapshot(fixture: PostgresScaleFixture) -> dict[str, object]:
    with session_scope(fixture.engine) as db:
        account = db.get(DouyinAccount, fixture.account_id)
        tasks = db.scalars(
            select(SparkTask)
            .where(SparkTask.owner_user_id == fixture.customer_id)
            .order_by(SparkTask.id)
        ).all()
        identities = db.scalars(
            select(SparkTaskTargetIdentity)
            .where(SparkTaskTargetIdentity.task_id.in_(fixture.task_ids))
            .order_by(SparkTaskTargetIdentity.task_id)
        ).all()
        assert account is not None
        return {
            "cookie": (bytes(account.encrypted_cookies), bytes(account.cookie_nonce)),
            "tasks": tuple(
                (task.id, task.douyin_account_id, task.target_name, task.send_time,
                 task.message_template, task.enabled, task.next_run_at)
                for task in tasks
            ),
            "identities": tuple((item.task_id, item.sec_uid) for item in identities),
        }


def _read_request(app, token: str, path: str, params: dict[str, object] | None = None):
    started = time.perf_counter()
    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set("spark_session", token)
        response = client.get(path, params=params)
    return time.perf_counter() - started, response.status_code, response.text[:240]


def _percentile(values: list[float], percentile: float) -> float:
    values = sorted(values)
    if not values:
        return float("nan")
    rank = (len(values) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (rank - lower)


def test_postgres_api_concurrency_baseline_preserves_bound_state(
    postgres_scale_fixture: PostgresScaleFixture,
):
    fixture = postgres_scale_fixture
    app = create_app(fixture.settings, fixture.engine)
    before = _state_snapshot(fixture)
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        operation = statement.lstrip().lower()
        if operation.startswith(("insert", "update", "delete", "merge", "replace")):
            statements.append(statement)

    event.listen(fixture.engine, "before_cursor_execute", capture)
    endpoints = (
        (fixture.customer_token, "/api/app/bootstrap", None),
        (fixture.customer_token, "/api/studio", None),
        (fixture.customer_token, "/api/studio/runs", {"page": 1}),
        (fixture.admin_token, "/api/admin/bootstrap", {"page": 1}),
        (fixture.admin_token, "/api/admin/runs", {"page": 1}),
        (fixture.admin_token, "/api/admin/subscriptions", {"page": 1}),
        (fixture.admin_token, "/api/admin/task-groups", {"page": 1}),
    )
    jobs = endpoints * REQUESTS_PER_WORKER
    started = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            results = list(
                pool.map(
                    lambda item: _read_request(app, item[0], item[1], item[2]),
                    jobs,
                )
            )
    finally:
        event.remove(fixture.engine, "before_cursor_execute", capture)
    elapsed = time.perf_counter() - started

    latencies = [item[0] for item in results]
    failures = [item for item in results if item[1] != 200]
    p50 = _percentile(latencies, 0.50)
    p95 = _percentile(latencies, 0.95)
    failure_rate = len(failures) / len(results)
    print(
        "postgres API baseline: requests=%d concurrency=%d elapsed=%.3fs "
        "p50=%.3fs p95=%.3fs failures=%d failure_rate=%.4f"
        % (len(results), CONCURRENCY, elapsed, p50, p95, len(failures), failure_rate)
    )
    if failures:
        print("postgres API failures: " + json.dumps(failures[:5], ensure_ascii=False))

    assert len(results) == len(jobs)
    assert failures == []
    assert failure_rate == 0
    assert p95 < P95_LIMIT_SECONDS
    assert statements == []
    assert _state_snapshot(fixture) == before

