"""Repeatable local scale baseline for the customer workspace and admin APIs.

This module deliberately uses a temporary SQLite database and sequential
TestClient requests.  It verifies data volume, query-count behavior, ownership
boundaries and credential/task preservation.  It does not simulate concurrent
customers, measure production capacity or execute a real Douyin browser flow.
It never connects to a deployed instance or changes production schema/data.
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, insert, select, text
from sqlalchemy.engine import Engine

from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import create_engine_for, create_schema, session_scope
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


CUSTOMER_COUNT = 500
ACCOUNT_COUNT = 500
TASK_COUNT = 2_500
RUN_COUNT = 75_000
TASKS_PER_ACCOUNT = TASK_COUNT // ACCOUNT_COUNT
RUNS_PER_TASK = RUN_COUNT // TASK_COUNT
PAGE_SIZE = 20


@dataclass
class ScaleFixture:
    engine: Engine
    settings: Settings
    admin_token: str = field(repr=False)
    customer_token: str = field(repr=False)
    customer_id: str
    customer_account_id: str
    customer_task_ids: tuple[str, ...]
    small_customer_token: str = field(repr=False)
    large_customer_token: str = field(repr=False)


def _id() -> str:
    return str(uuid.uuid4())


@pytest.fixture(scope="module")
def scale_fixture(tmp_path_factory: pytest.TempPathFactory) -> ScaleFixture:
    """Create 500 customers, 500 accounts, 2,500 tasks and 75,000 runs."""

    root = tmp_path_factory.mktemp("dyfire-scale-baseline")
    cookie_key = root / "cookie.key"
    session_key = root / "session.key"
    cookie_key.write_bytes(b"c" * 32)
    session_key.write_bytes(b"s" * 32)
    settings = Settings(
        data_dir=root,
        database_url=f"sqlite:///{root / 'scale.db'}",
        cookie_key_file=cookie_key,
        session_key_file=session_key,
        secure_cookies=False,
    )
    engine = create_engine_for(settings)
    create_schema(engine)
    started = time.perf_counter()

    admin_id = _id()
    customer_ids = [_id() for _ in range(CUSTOMER_COUNT)]
    customer_hash = "test-scale-password-hash"
    now = datetime.now(timezone.utc).replace(microsecond=0)

    # Explicit identifiers make the large fixture an efficient executemany
    # insert while preserving the same model constraints as normal writes.
    user_rows = [
        {
            "id": admin_id,
            "username": "scale-admin",
            "password_hash": "test-scale-password-hash",
            "role": "admin",
            "status": "active",
            "must_change_password": False,
        },
        *[
            {
                "id": customer_id,
                "username": f"scale-user-{index:04d}",
                "password_hash": customer_hash,
                "role": "user",
                "status": "active",
                "must_change_password": False,
            }
            for index, customer_id in enumerate(customer_ids)
        ],
    ]

    account_ids = [_id() for _ in range(ACCOUNT_COUNT)]
    task_ids = [_id() for _ in range(TASK_COUNT)]
    cookie_cipher = CookieCipher(cookie_key.read_bytes())
    encrypted_cookies = [cookie_cipher.encrypt(f"bound-cookie-{index}".encode()) for index in range(ACCOUNT_COUNT)]
    account_rows = [
        {
            "id": account_id,
            "owner_user_id": customer_ids[index],
            "display_name": f"抖音账号-{index:04d}",
            # These values represent an already-bound login state.  The test
            # later proves that reads do not rewrite either credential field.
            "encrypted_cookies": encrypted_cookies[index].ciphertext,
            "cookie_nonce": encrypted_cookies[index].nonce,
            "cookie_version": 1,
            "validation_state": "valid",
            "dispatch_key": index,
        }
        for index, account_id in enumerate(account_ids)
    ]
    task_rows = []
    identity_rows = []
    task_counts = [TASKS_PER_ACCOUNT] * ACCOUNT_COUNT
    # Two comparison customers have different workspace sizes, while the
    # dataset remains exactly 2,500 tasks and 75,000 runs.
    task_counts[1], task_counts[2] = 1, 9
    task_index = 0
    for account_index, account_id in enumerate(account_ids):
        owner_id = customer_ids[account_index]
        for task_offset in range(task_counts[account_index]):
            task_id = task_ids[task_index]
            target_name = f"好友-{account_index:04d}-{task_offset}"
            task_rows.append(
                {
                    "id": task_id,
                    "owner_user_id": owner_id,
                    "douyin_account_id": account_id,
                    "target_name": target_name,
                    "target_key": f"target-{account_index:04d}-{task_offset}",
                    "send_time": f"{8 + task_offset:02d}:00",
                    "message_template": "续火花",
                    "message_kind": "douyin_sticker",
                    "enabled": True,
                    "next_run_at": now + timedelta(minutes=task_index + 1),
                }
            )
            identity_rows.append(
                {"task_id": task_id, "sec_uid": f"sec-{account_index:04d}-{task_offset}"}
            )
            task_index += 1

    with session_scope(engine) as db:
        db.execute(insert(User), user_rows)
        db.execute(
            insert(UserSubscription),
            [
                {
                    "user_id": customer_id,
                    "valid_from": now - timedelta(days=1),
                    "valid_until": now + timedelta(days=30),
                    "max_accounts": 1,
                    "max_tasks": 10,
                }
                for customer_id in customer_ids
            ],
        )
        db.execute(insert(DouyinAccount), account_rows)
        db.execute(insert(SparkTask), task_rows)
        db.execute(insert(SparkTaskTargetIdentity), identity_rows)

        run_rows = []
        for task_index, task_id in enumerate(task_ids):
            for run_offset in range(RUNS_PER_TASK):
                success = run_offset % 5 != 0
                run_rows.append(
                    {
                        "id": _id(),
                        "task_id": task_id,
                        "scheduled_for": now - timedelta(days=run_offset + 1),
                        "status": "success" if success else "failed",
                        "stage": "complete" if success else "worker_error",
                        "attempt": 1,
                    }
                )
            if len(run_rows) >= 5_000:
                db.execute(insert(TaskRun), run_rows)
                run_rows.clear()
        if run_rows:
            db.execute(insert(TaskRun), run_rows)

        sessions = SessionService(session_key.read_bytes())
        admin_token, admin_session = sessions.create_record(admin_id, now=now)
        customer_token, customer_session = sessions.create_record(customer_ids[0], now=now)
        small_customer_token, small_customer_session = sessions.create_record(customer_ids[1], now=now)
        large_customer_token, large_customer_session = sessions.create_record(customer_ids[2], now=now)
        db.add_all([admin_session, customer_session, small_customer_session, large_customer_session])

    elapsed = time.perf_counter() - started
    print(
        "local data fixture: customers=%d admins=1 accounts=%d tasks=%d runs=%d build=%.3fs"
        % (CUSTOMER_COUNT, ACCOUNT_COUNT, TASK_COUNT, RUN_COUNT, elapsed)
    )
    customer_task_ids = tuple(task_ids[:TASKS_PER_ACCOUNT])
    fixture = ScaleFixture(
        engine=engine,
        settings=settings,
        admin_token=admin_token,
        customer_token=customer_token,
        customer_id=customer_ids[0],
        customer_account_id=account_ids[0],
        customer_task_ids=customer_task_ids,
        small_customer_token=small_customer_token,
        large_customer_token=large_customer_token,
    )
    try:
        yield fixture
    finally:
        engine.dispose()


def _client(fixture: ScaleFixture, token: str) -> TestClient:
    client = TestClient(create_app(fixture.settings, fixture.engine), base_url="https://testserver")
    client.cookies.set("spark_session", token)
    return client


def _count_selects(engine: Engine):
    statements: list[str] = []

    def listener(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().lower().startswith("select"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    return statements, listener


def _count_writes(engine: Engine):
    statements: list[str] = []

    def listener(_connection, _cursor, statement, _parameters, _context, _many):
        operation = statement.lstrip().lower()
        if operation.startswith(("insert", "update", "delete", "replace")):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    return statements, listener


def _preserved_state(engine: Engine):
    """Credential, task and target fields protected by the user's invariant."""

    account_columns = [
        column for column in DouyinAccount.__table__.columns if column.name != "dispatch_key"
    ]
    with session_scope(engine) as db:
        return {
            "accounts": db.execute(select(*account_columns).order_by(DouyinAccount.id)).all(),
            "tasks": db.execute(select(SparkTask.__table__).order_by(SparkTask.id)).all(),
            "identities": db.execute(
                select(SparkTaskTargetIdentity.__table__).order_by(SparkTaskTargetIdentity.task_id)
            ).all(),
        }


def test_admin_user_and_run_pagination_is_bounded_and_repeatable(scale_fixture: ScaleFixture):
    fixture = scale_fixture
    client = _client(fixture, fixture.admin_token)
    try:
        first_statements, listener = _count_selects(fixture.engine)
        first_started = time.perf_counter()
        first = client.get("/api/admin/bootstrap", params={"page": 1})
        first_elapsed = time.perf_counter() - first_started
        first_count = len(first_statements)
        event.remove(fixture.engine, "before_cursor_execute", listener)

        second_statements, listener = _count_selects(fixture.engine)
        second_started = time.perf_counter()
        second = client.get("/api/admin/bootstrap", params={"page": 2})
        second_elapsed = time.perf_counter() - second_started
        second_count = len(second_statements)
        event.remove(fixture.engine, "before_cursor_execute", listener)

        assert first.status_code == second.status_code == 200
        first_payload, second_payload = first.json(), second.json()
        assert first_payload["counts"] == {
            "users": CUSTOMER_COUNT + 1,
            "accounts": ACCOUNT_COUNT,
            "tasks": TASK_COUNT,
            "runs": RUN_COUNT,
        }
        assert first_payload["usersPagination"]["total"] == CUSTOMER_COUNT + 1
        assert len(first_payload["users"]) == len(second_payload["users"]) == PAGE_SIZE
        assert first_payload["users"][0]["id"] != second_payload["users"][0]["id"]
        # The same endpoint and page shape issue the same number of SELECTs;
        # this is the useful regression signal, independent of wall-clock time.
        assert first_count == second_count

        first_statements, listener = _count_selects(fixture.engine)
        first_started = time.perf_counter()
        runs_first = client.get("/api/admin/runs", params={"page": 1})
        runs_first_elapsed = time.perf_counter() - first_started
        runs_first_count = len(first_statements)
        event.remove(fixture.engine, "before_cursor_execute", listener)

        second_statements, listener = _count_selects(fixture.engine)
        second_started = time.perf_counter()
        runs_last = client.get("/api/admin/runs", params={"page": 3_750})
        runs_last_elapsed = time.perf_counter() - second_started
        runs_last_count = len(second_statements)
        event.remove(fixture.engine, "before_cursor_execute", listener)

        assert runs_first.status_code == runs_last.status_code == 200
        assert runs_first.json()["pagination"]["total"] == RUN_COUNT
        assert len(runs_first.json()["items"]) == len(runs_last.json()["items"]) == PAGE_SIZE
        assert runs_first_count == runs_last_count

        # Account presentation is batched.  A page containing records from
        # many accounts must issue the same number of SQL statements as a
        # page filtered to one customer; it must not regress to one account
        # lookup per row.
        filtered_statements, listener = _count_selects(fixture.engine)
        filtered = client.get("/api/admin/runs", params={"q": "scale-user-0000", "page": 1})
        filtered_count = len(filtered_statements)
        event.remove(fixture.engine, "before_cursor_execute", listener)
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["pagination"]["total"] == TASKS_PER_ACCOUNT * RUNS_PER_TASK
        assert filtered_count == runs_first_count
        # The account lookup itself stays batched independently of page
        # contents, in addition to the total SELECT count staying constant.
        account_selects = [
            statement
            for statement in filtered_statements
            if "from douyin_accounts" in statement.lower()
            and "douyin_accounts.id in" in statement.lower()
        ]
        assert len(account_selects) <= 1
        print(
            "admin pagination: bootstrap_page1=%.3fs/%d SELECTs "
            "bootstrap_page2=%.3fs/%d SELECTs runs_page1=%.3fs/%d SELECTs "
            "runs_page3750=%.3fs/%d SELECTs"
            % (
                first_elapsed,
                first_count,
                second_elapsed,
                second_count,
                runs_first_elapsed,
                runs_first_count,
                runs_last_elapsed,
                runs_last_count,
            )
        )
    finally:
        client.close()


def test_customer_sql_count_does_not_grow_with_task_count(scale_fixture: ScaleFixture):
    fixture = scale_fixture
    for path in ("/api/studio", "/api/app/bootstrap", "/api/studio/runs"):
        select_counts = []
        for token in (fixture.small_customer_token, fixture.large_customer_token):
            statements, listener = _count_selects(fixture.engine)
            try:
                with _client(fixture, token) as customer:
                    response = customer.get(path)
                    assert response.status_code == 200, response.text
                select_counts.append(len(statements))
            finally:
                event.remove(fixture.engine, "before_cursor_execute", listener)
        assert select_counts[0] == select_counts[1]
        print("customer query count: %s tasks=1/9 SELECTs=%s" % (path, select_counts))


def test_concurrent_read_only_workspaces_preserve_customer_isolation(
    scale_fixture: ScaleFixture,
):
    """Exercise a small concurrent read burst without touching production data."""
    fixture = scale_fixture
    writes, listener = _count_writes(fixture.engine)
    started = time.perf_counter()

    def request(index: int):
        token = fixture.customer_token if index % 2 == 0 else fixture.small_customer_token
        with _client(fixture, token) as client:
            workspace = client.get("/api/studio")
            runs = client.get("/api/studio/runs", params={"page": 1})
            bootstrap = client.get("/api/app/bootstrap")
            return workspace, runs, bootstrap

    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            responses = list(pool.map(request, range(36)))
    finally:
        event.remove(fixture.engine, "before_cursor_execute", listener)

    elapsed = time.perf_counter() - started
    assert len(responses) == 36
    assert all(response.status_code == 200 for group in responses for response in group), [
        response.text for group in responses for response in group
        if response.status_code != 200
    ]
    for index, (workspace, runs, bootstrap) in enumerate(responses):
        expected_account = (
            fixture.customer_account_id
            if index % 2 == 0
            else None
        )
        assert workspace.json()["accounts"]
        if expected_account:
            assert {item["id"] for item in workspace.json()["accounts"]} == {expected_account}
        else:
            assert all(item["id"] != fixture.customer_account_id for item in workspace.json()["accounts"])
        assert runs.json()["total"] in {RUNS_PER_TASK, RUNS_PER_TASK * TASKS_PER_ACCOUNT}
        assert bootstrap.json()["csrf"]
    assert writes == []
    print("concurrent read burst: requests=%d workers=12 elapsed=%.3fs" % (len(responses) * 3, elapsed))


def test_customer_owner_isolation_and_read_preserves_bound_account_state(
    scale_fixture: ScaleFixture,
):
    fixture = scale_fixture
    client = _client(fixture, fixture.customer_token)
    try:
        writes, write_listener = _count_writes(fixture.engine)
        with session_scope(fixture.engine) as db:
            account_before = db.get(DouyinAccount, fixture.customer_account_id)
            task_before = db.scalars(
                select(SparkTask).where(SparkTask.owner_user_id == fixture.customer_id).order_by(SparkTask.id)
            ).all()
            cookie_snapshot = (account_before.encrypted_cookies, account_before.cookie_nonce)
            task_snapshot = [
                (task.id, task.owner_user_id, task.douyin_account_id, task.target_name, task.send_time,
                 task.message_template, task.enabled, task.next_run_at)
                for task in task_before
            ]

        started = time.perf_counter()
        workspace = client.get("/api/studio")
        workspace_elapsed = time.perf_counter() - started
        assert workspace.status_code == 200, workspace.text
        payload = workspace.json()
        assert len(payload["accounts"]) == 1
        assert payload["accounts"][0]["id"] == fixture.customer_account_id
        assert len(payload["tasks"]) == TASKS_PER_ACCOUNT
        assert {item["accountId"] for item in payload["tasks"]} == {fixture.customer_account_id}
        assert {item["id"] for item in payload["tasks"]} == set(fixture.customer_task_ids)

        runs = client.get("/api/studio/runs", params={"page": 1})
        assert runs.status_code == 200, runs.text
        run_payload = runs.json()
        assert run_payload["total"] == TASKS_PER_ACCOUNT * RUNS_PER_TASK
        assert len(run_payload["items"]) == 12
        assert {item["name"] for item in run_payload["items"]}.issubset(
            {f"好友-0000-{offset}" for offset in range(TASKS_PER_ACCOUNT)}
        )
        with session_scope(fixture.engine) as db:
            foreign_task = db.scalar(
                select(SparkTask).where(SparkTask.owner_user_id != fixture.customer_id).limit(1)
            )
            foreign_account_id, foreign_task_id = foreign_task.douyin_account_id, foreign_task.id
        assert client.get(
            "/api/studio/schedule", params={"account_id": foreign_account_id}
        ).status_code == 404
        assert client.post(
            f"/api/studio/tasks/{foreign_task_id}/enabled",
            json={"enabled": False},
            headers={"X-CSRF-Token": payload["csrf"]},
        ).status_code == 404
        assert client.get("/api/admin/bootstrap").status_code == 404
        event.remove(fixture.engine, "before_cursor_execute", write_listener)
        assert writes == []

        with session_scope(fixture.engine) as db:
            account_after = db.get(DouyinAccount, fixture.customer_account_id)
            tasks_after = db.scalars(
                select(SparkTask).where(SparkTask.owner_user_id == fixture.customer_id).order_by(SparkTask.id)
            ).all()
            assert (account_after.encrypted_cookies, account_after.cookie_nonce) == cookie_snapshot
            assert [
                (task.id, task.owner_user_id, task.douyin_account_id, task.target_name, task.send_time,
                 task.message_template, task.enabled, task.next_run_at)
                for task in tasks_after
            ] == task_snapshot
        print(
            "customer workspace: accounts=1 tasks=%d own_runs=%d bootstrap=%.3fs"
            % (len(payload["tasks"]), run_payload["total"], workspace_elapsed)
        )
    finally:
        if event.contains(fixture.engine, "before_cursor_execute", write_listener):
            event.remove(fixture.engine, "before_cursor_execute", write_listener)
        client.close()


def test_admin_customer_workspace_is_owner_scoped_and_preserves_state(
    scale_fixture: ScaleFixture,
):
    fixture = scale_fixture
    client = _client(fixture, fixture.admin_token)
    try:
        with session_scope(fixture.engine) as db:
            before = db.get(DouyinAccount, fixture.customer_account_id)
            before_cookie = (before.encrypted_cookies, before.cookie_nonce)

        response = client.get(f"/api/admin/users/{fixture.customer_id}/workspace")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert [account["id"] for account in payload["accounts"]] == [fixture.customer_account_id]
        assert {task["id"] for task in payload["tasks"]} == set(fixture.customer_task_ids)
        assert all(run["taskId"] in fixture.customer_task_ids for run in payload["runs"])
        assert len(payload["runs"]) <= 30

        with session_scope(fixture.engine) as db:
            after = db.get(DouyinAccount, fixture.customer_account_id)
            assert (after.encrypted_cookies, after.cookie_nonce) == before_cookie
        print(
            "admin workspace: accounts=%d tasks=%d runs_returned=%d"
            % (len(payload["accounts"]), len(payload["tasks"]), len(payload["runs"]))
        )
    finally:
        client.close()


def test_repeated_schema_initialization_preserves_bound_accounts_and_tasks(
    scale_fixture: ScaleFixture,
):
    """Schema upgrades are additive and must never rotate login state."""

    fixture = scale_fixture
    state = _preserved_state(fixture.engine)

    # Reapply the scale upgrades over the populated synthetic database, as an
    # upgrade from v9 would.  Reset only test migration metadata and shard keys;
    # the protected account credentials and task columns remain untouched.
    with fixture.engine.begin() as connection:
        connection.execute(text("DELETE FROM schema_migrations WHERE version >= 10"))
        connection.execute(text("UPDATE douyin_accounts SET dispatch_key = NULL"))

    # Running initialization more than once models a restart after an
    # additive migration.  It must be a no-op for existing data.
    create_schema(fixture.engine)
    create_schema(fixture.engine)

    assert _preserved_state(fixture.engine) == state

    with session_scope(fixture.engine) as db:
        accounts = db.scalars(select(DouyinAccount)).all()
        assert len(accounts) == ACCOUNT_COUNT
        assert all(account.dispatch_key is not None for account in accounts)
        cipher = CookieCipher(fixture.settings.cookie_key_file.read_bytes())
        assert {
            cipher.decrypt(account.encrypted_cookies, account.cookie_nonce)
            for account in accounts
        } == {f"bound-cookie-{index}".encode() for index in range(ACCOUNT_COUNT)}


def test_all_api_reads_preserve_every_bound_account_and_task(scale_fixture: ScaleFixture):
    fixture = scale_fixture
    before = _preserved_state(fixture.engine)
    assert len(before["accounts"]) == ACCOUNT_COUNT
    assert len(before["tasks"]) == TASK_COUNT
    assert len(before["identities"]) == TASK_COUNT

    writes, listener = _count_writes(fixture.engine)
    try:
        with _client(fixture, fixture.customer_token) as customer:
            bootstrap = customer.get("/api/app/bootstrap")
            assert bootstrap.status_code == 200, bootstrap.text
            assert {account["id"] for account in bootstrap.json()["accounts"]} == {fixture.customer_account_id}
            assert {task["id"] for task in bootstrap.json()["tasks"]} == set(fixture.customer_task_ids)
            assert customer.get("/api/studio").status_code == 200
            assert customer.get("/api/studio/runs").status_code == 200
        with _client(fixture, fixture.admin_token) as admin:
            for path in (
                "/api/admin/bootstrap",
                "/api/admin/runs",
                "/api/admin/subscriptions",
                "/api/admin/task-groups",
                f"/api/admin/users/{fixture.customer_id}/workspace",
            ):
                response = admin.get(path)
                assert response.status_code == 200, response.text
    finally:
        event.remove(fixture.engine, "before_cursor_execute", listener)

    assert writes == []
    assert _preserved_state(fixture.engine) == before
