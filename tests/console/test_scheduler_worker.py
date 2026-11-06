import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import create_schema
from spark_console.executor import ExecutionResult, ExecutionStage
from spark_console.models import (
    DouyinAccount,
    DouyinContactAvatar,
    DouyinContactIdentity,
    SparkTask,
    SparkTaskTargetIdentity,
    TaskRun,
    User,
    UserSubscription,
    WorkerLock,
)
from spark_console.scheduler import (
    MAX_SUBMISSION_PROTECTION_SCAN,
    MAX_SUPERSEDED_RETRY_SCAN,
    _close_superseded_retries,
    _protect_expired_submissions,
    claim_next_due_task,
    compute_next_run,
)
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.worker import Worker
from spark_console.worker_sharding import account_shard


def _activate(session: Session, user: User, at: datetime) -> None:
    session.add(
        UserSubscription(
            user_id=user.id,
            valid_from=at - timedelta(days=1),
            valid_until=at + timedelta(days=30),
            max_accounts=5,
            max_tasks=20,
        )
    )


def _worker_storage_state(marker: str) -> dict[str, object]:
    return {
        "cookies": [
            {
                "name": "sid",
                "value": marker,
                "domain": ".douyin.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }


class SchedulerTests(unittest.TestCase):
    def test_free_user_with_historical_overage_only_claims_earliest_enabled_task(self):
        engine = create_engine("sqlite:///:memory:")
        create_schema(engine)
        now = datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)
        with Session(engine) as session:
            user = User(username="free-overage", password_hash="hash")
            session.add(user)
            session.flush()
            account = DouyinAccount(
                owner_user_id=user.id,
                display_name="main",
                encrypted_cookies=b"x",
                cookie_nonce=b"n",
            )
            session.add(account)
            session.flush()
            first = SparkTask(
                owner_user_id=user.id,
                douyin_account_id=account.id,
                target_name="好友一",
                send_time="09:00",
                message_template="x",
                enabled=True,
                next_run_at=now,
                created_at=now - timedelta(minutes=2),
            )
            second = SparkTask(
                owner_user_id=user.id,
                douyin_account_id=account.id,
                target_name="好友二",
                send_time="09:00",
                message_template="x",
                enabled=True,
                next_run_at=now,
                created_at=now - timedelta(minutes=1),
            )
            session.add_all((first, second))
            session.commit()

            run = claim_next_due_task(session, now, "free-worker")
            self.assertIsNotNone(run)
            self.assertEqual(first.id, run.task_id)
            session.commit()
            self.assertIsNone(claim_next_due_task(session, now, "free-worker-2"))
            self.assertEqual(now, second.next_run_at.replace(tzinfo=timezone.utc))
        engine.dispose()

    def test_equal_schedule_prefers_owner_not_recently_dispatched(self):
        engine = create_engine("sqlite:///:memory:")
        create_schema(engine)
        now = datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)
        with Session(engine) as session:
            tasks = []
            for index in range(2):
                user = User(username=f'fair-{index}', password_hash='hash')
                session.add(user); session.flush()
                _activate(session, user, now)
                account = DouyinAccount(owner_user_id=user.id, display_name='main', encrypted_cookies=b'x', cookie_nonce=b'n')
                session.add(account); session.flush()
                task = SparkTask(id=f'task-{index}', owner_user_id=user.id, douyin_account_id=account.id, target_name='friend', send_time='09:00', message_template='x', enabled=True, next_run_at=now)
                session.add(task); session.flush(); tasks.append(task)
            session.add(TaskRun(task_id=tasks[0].id, scheduled_for=now-timedelta(days=1), started_at=now-timedelta(minutes=1), finished_at=now-timedelta(seconds=30), status='success'))
            session.commit()
            run = claim_next_due_task(session, now, 'fair-worker')
            self.assertEqual(tasks[1].id, run.task_id)
        engine.dispose()

    def test_compute_next_run_uses_shanghai_timezone(self):
        now = datetime(2026, 8, 25, 0, 30, tzinfo=timezone.utc)
        self.assertEqual(
            datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc),
            compute_next_run("09:00", now),
        )

    def test_same_scheduled_run_can_only_be_claimed_once(self):
        engine = create_engine("sqlite:///:memory:")
        create_schema(engine)
        session = Session(engine)
        now = datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)
        user = User(username="friend", password_hash="hash")
        session.add(user); session.flush()
        _activate(session, user, now)
        account = DouyinAccount(owner_user_id=user.id, display_name="main", encrypted_cookies=b"x", cookie_nonce=b"n")
        session.add(account); session.flush()
        task = SparkTask(owner_user_id=user.id, douyin_account_id=account.id, target_name="好友", send_time="09:00", message_template="今日火花", enabled=True, next_run_at=now)
        session.add(task); session.commit()
        first = claim_next_due_task(session, now, "one")
        session.commit()
        second = claim_next_due_task(session, now, "two")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual("one", first.worker_id)
        self.assertEqual(1, first.attempt)
        self.assertEqual(now + timedelta(minutes=20), first.lease_until.replace(tzinfo=timezone.utc))
        self.assertEqual(now + timedelta(days=1), task.next_run_at.replace(tzinfo=timezone.utc))
        session.close(); engine.dispose()

    def test_sharded_claim_only_returns_accounts_owned_by_this_worker(self):
        engine = create_engine("sqlite:///:memory:")
        create_schema(engine)
        now = datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)
        with Session(engine) as session:
            selected = []
            for index in range(8):
                account_id = f"shard-account-{index}"
                selected.append((account_id, account_shard(account_id, 2)))
            wanted = {
                shard: next(item for item in selected if item[1] == shard)
                for shard in (0, 1)
            }
            for shard, (account_id, _owner_shard) in wanted.items():
                user = User(username=f"shard-owner-{shard}", password_hash="hash")
                session.add(user)
                session.flush()
                _activate(session, user, now)
                account = DouyinAccount(
                    id=account_id,
                    owner_user_id=user.id,
                    display_name="main",
                    encrypted_cookies=b"x",
                    cookie_nonce=b"n",
                )
                session.add(account)
                session.flush()
                session.add(SparkTask(
                    owner_user_id=user.id,
                    douyin_account_id=account.id,
                    target_name=f"friend-{shard}",
                    send_time="09:00",
                    message_template="今日火花",
                    enabled=True,
                    next_run_at=now,
                ))
            session.commit()
            first = claim_next_due_task(session, now, "shard-0", shard=0, shards=2)
            self.assertIsNotNone(first)
            self.assertEqual(wanted[0][0], session.get(SparkTask, first.task_id).douyin_account_id)
            session.commit()
            second = claim_next_due_task(session, now, "shard-1", shard=1, shards=2)
            self.assertIsNotNone(second)
            self.assertEqual(wanted[1][0], session.get(SparkTask, second.task_id).douyin_account_id)
        engine.dispose()

    def test_sharded_submission_protection_does_not_update_another_shard(self):
        engine = create_engine("sqlite:///:memory:")
        create_schema(engine)
        now = datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)
        with Session(engine) as session:
            tasks = {}
            for shard in (0, 1):
                account_id = next(
                    f"submission-isolation-{index}"
                    for index in range(100)
                    if account_shard(f"submission-isolation-{index}", 2) == shard
                )
                user = User(
                    username=f"submission-isolation-{shard}",
                    password_hash="hash",
                    role="admin",
                )
                session.add(user)
                session.flush()
                account = DouyinAccount(
                    id=account_id,
                    owner_user_id=user.id,
                    display_name="main",
                    encrypted_cookies=b"x",
                    cookie_nonce=b"n",
                )
                session.add(account)
                session.flush()
                task = SparkTask(
                    owner_user_id=user.id,
                    douyin_account_id=account.id,
                    target_name=f"submission-{shard}",
                    send_time="09:00",
                    message_template="x",
                    enabled=True,
                )
                session.add(task)
                session.flush()
                run = TaskRun(
                    task_id=task.id,
                    scheduled_for=now - timedelta(minutes=30),
                    status="running",
                    stage="submitted",
                    submitted_at=now - timedelta(minutes=25),
                    started_at=now - timedelta(minutes=30),
                    lease_until=now - timedelta(minutes=10),
                )
                session.add(run)
                tasks[shard] = (task, run)
            session.commit()

            _protect_expired_submissions(session, now, shard=0, shards=2)
            session.flush()
            self.assertEqual(
                "submitted_unconfirmed",
                session.get(TaskRun, tasks[0][1].id).status,
            )
            self.assertEqual("running", session.get(TaskRun, tasks[1][1].id).status)

        engine.dispose()

    def test_submission_protection_processes_a_bounded_batch(self):
        """A large crash backlog is drained over polls instead of one huge update."""
        engine = create_engine("sqlite:///:memory:")
        create_schema(engine)
        now = datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)
        with Session(engine) as session:
            user = User(username="submission-batch-owner", password_hash="hash", role="admin")
            session.add(user)
            session.flush()
            account = DouyinAccount(
                owner_user_id=user.id,
                display_name="main",
                encrypted_cookies=b"x",
                cookie_nonce=b"n",
            )
            session.add(account)
            session.flush()
            for index in range(MAX_SUBMISSION_PROTECTION_SCAN + 1):
                task = SparkTask(
                    owner_user_id=user.id,
                    douyin_account_id=account.id,
                    target_name=f"submission-batch-{index}",
                    send_time="09:00",
                    message_template="x",
                    enabled=True,
                )
                session.add(task)
                session.flush()
                session.add(
                    TaskRun(
                        task_id=task.id,
                        scheduled_for=now - timedelta(minutes=index + 1),
                        status="running",
                        stage="submitted",
                        submitted_at=now - timedelta(minutes=index + 1),
                        started_at=now - timedelta(minutes=index + 2),
                        lease_until=now - timedelta(minutes=index + 3),
                    )
                )
            session.commit()

            _protect_expired_submissions(session, now)
            session.flush()
            protected = session.query(TaskRun).filter(TaskRun.status == "submitted_unconfirmed").count()
            pending = session.query(TaskRun).filter(TaskRun.status == "running").count()
            self.assertEqual(MAX_SUBMISSION_PROTECTION_SCAN, protected)
            self.assertEqual(1, pending)

            _protect_expired_submissions(session, now)
            session.flush()
            self.assertEqual(0, session.query(TaskRun).filter(TaskRun.status == "running").count())
        engine.dispose()

    def test_sharded_superseded_retry_cleanup_does_not_update_another_shard(self):
        engine = create_engine("sqlite:///:memory:")
        create_schema(engine)
        now = datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)
        with Session(engine) as session:
            runs = {}
            for shard in (0, 1):
                account_id = next(
                    f"retry-isolation-{index}"
                    for index in range(100)
                    if account_shard(f"retry-isolation-{index}", 2) == shard
                )
                user = User(
                    username=f"retry-isolation-{shard}",
                    password_hash="hash",
                    role="admin",
                )
                session.add(user)
                session.flush()
                account = DouyinAccount(
                    id=account_id,
                    owner_user_id=user.id,
                    display_name="main",
                    encrypted_cookies=b"x",
                    cookie_nonce=b"n",
                )
                session.add(account)
                session.flush()
                task = SparkTask(
                    owner_user_id=user.id,
                    douyin_account_id=account.id,
                    target_name=f"retry-{shard}",
                    send_time="09:00",
                    message_template="x",
                    enabled=True,
                )
                session.add(task)
                session.flush()
                old = TaskRun(
                    task_id=task.id,
                    scheduled_for=now - timedelta(minutes=10),
                    status="retrying",
                    stage="selecting_target",
                    retry_after=now - timedelta(minutes=5),
                )
                later = TaskRun(
                    task_id=task.id,
                    scheduled_for=now - timedelta(minutes=1),
                    status="success",
                    stage="complete",
                    finished_at=now - timedelta(seconds=30),
                )
                session.add_all([old, later])
                runs[shard] = old
            session.commit()

            _close_superseded_retries(session, now, shard=0, shards=2)
            session.flush()
            self.assertEqual("failed", session.get(TaskRun, runs[0].id).status)
            self.assertEqual("retrying", session.get(TaskRun, runs[1].id).status)

        engine.dispose()

    def test_superseded_retry_rows_leave_the_active_queue(self):
        engine = create_engine("sqlite:///:memory:")
        create_schema(engine)
        session = Session(engine)
        now = datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)
        user = User(username="retry-history-owner", password_hash="hash")
        session.add(user); session.flush()
        _activate(session, user, now)
        account = DouyinAccount(
            owner_user_id=user.id,
            display_name="main",
            encrypted_cookies=b"x",
            cookie_nonce=b"n",
        )
        session.add(account); session.flush()
        task = SparkTask(
            owner_user_id=user.id,
            douyin_account_id=account.id,
            target_name="好友",
            send_time="09:00",
            message_template="今日火花",
            enabled=True,
            next_run_at=now + timedelta(days=1),
        )
        session.add(task); session.flush()
        old_retry = TaskRun(
            task_id=task.id,
            scheduled_for=now - timedelta(minutes=5),
            status="retrying",
            stage="selecting_target",
            attempt=1,
            retry_after=now - timedelta(minutes=4),
            error_code="target_retry_scheduled_1m",
        )
        later = TaskRun(
            task_id=task.id,
            scheduled_for=now,
            status="success",
            stage="complete",
            attempt=1,
            finished_at=now,
        )
        session.add_all([old_retry, later]); session.commit()

        self.assertIsNone(claim_next_due_task(session, now, "cleanup-worker"))
        session.commit(); session.refresh(old_retry)
        self.assertEqual("failed", old_retry.status)
        self.assertEqual("retry_superseded", old_retry.error_code)
        self.assertIsNone(old_retry.retry_after)
        session.close(); engine.dispose()

    def test_superseded_retry_cleanup_processes_a_bounded_batch(self):
        """Cleanup must not materialize an outage-sized retry backlog."""
        engine = create_engine("sqlite:///:memory:")
        create_schema(engine)
        now = datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)
        with Session(engine) as session:
            user = User(username="retry-batch-owner", password_hash="hash", role="admin")
            session.add(user)
            session.flush()
            account = DouyinAccount(
                owner_user_id=user.id,
                display_name="main",
                encrypted_cookies=b"x",
                cookie_nonce=b"n",
            )
            session.add(account)
            session.flush()
            for index in range(MAX_SUPERSEDED_RETRY_SCAN + 1):
                task = SparkTask(
                    owner_user_id=user.id,
                    douyin_account_id=account.id,
                    target_name=f"retry-batch-{index}",
                    send_time="09:00",
                    message_template="x",
                    enabled=True,
                )
                session.add(task)
                session.flush()
                session.add_all(
                    [
                        TaskRun(
                            task_id=task.id,
                            scheduled_for=now - timedelta(days=2, minutes=index),
                            status="retrying",
                            stage="selecting_target",
                            retry_after=now - timedelta(minutes=1),
                        ),
                        TaskRun(
                            task_id=task.id,
                            scheduled_for=now - timedelta(days=1, minutes=index),
                            status="success",
                            stage="complete",
                            finished_at=now - timedelta(hours=1),
                        ),
                    ]
                )
            session.commit()

            _close_superseded_retries(session, now)
            session.flush()
            closed = session.query(TaskRun).filter(TaskRun.error_code == "retry_superseded").count()
            pending = session.query(TaskRun).filter(TaskRun.status == "retrying").count()
            self.assertEqual(MAX_SUPERSEDED_RETRY_SCAN, closed)
            self.assertEqual(1, pending)

            _close_superseded_retries(session, now)
            session.flush()
            self.assertEqual(0, session.query(TaskRun).filter(TaskRun.status == "retrying").count())
        engine.dispose()

    def test_expired_unsubmitted_run_is_reclaimed_without_creating_duplicate(self):
        engine = create_engine("sqlite:///:memory:")
        create_schema(engine)
        session = Session(engine)
        now = datetime(2026, 8, 25, 1, 30, tzinfo=timezone.utc)
        user = User(username="reclaim-owner", password_hash="hash")
        session.add(user); session.flush()
        account = DouyinAccount(owner_user_id=user.id, display_name="main", encrypted_cookies=b"x", cookie_nonce=b"n")
        session.add(account); session.flush()
        task = SparkTask(owner_user_id=user.id, douyin_account_id=account.id, target_name="好友", send_time="09:00", message_template="今日火花", enabled=True, next_run_at=now + timedelta(days=1))
        session.add(task); session.flush()
        stale = TaskRun(
            task_id=task.id,
            scheduled_for=now - timedelta(minutes=30),
            status="running",
            stage="sending",
            worker_id="dead-worker",
            attempt=1,
            lease_until=now - timedelta(seconds=1),
            started_at=now - timedelta(minutes=30),
        )
        session.add(stale); session.commit()

        reclaimed = claim_next_due_task(session, now, "replacement")
        session.commit()

        self.assertEqual(stale.id, reclaimed.id)
        self.assertEqual("replacement", reclaimed.worker_id)
        self.assertEqual(2, reclaimed.attempt)
        self.assertEqual("reclaimed", reclaimed.stage)
        self.assertEqual(1, len(session.scalars(select(TaskRun)).all()))
        session.close(); engine.dispose()

    def test_expired_submitted_run_is_frozen_and_never_reclaimed(self):
        engine = create_engine("sqlite:///:memory:")
        create_schema(engine)
        session = Session(engine)
        now = datetime(2026, 8, 25, 1, 30, tzinfo=timezone.utc)
        user = User(username="submitted-owner", password_hash="hash")
        session.add(user); session.flush()
        account = DouyinAccount(owner_user_id=user.id, display_name="main", encrypted_cookies=b"x", cookie_nonce=b"n")
        session.add(account); session.flush()
        task = SparkTask(owner_user_id=user.id, douyin_account_id=account.id, target_name="好友", send_time="09:00", message_template="今日火花", enabled=True, next_run_at=now + timedelta(days=1))
        session.add(task); session.flush()
        submitted = TaskRun(
            task_id=task.id,
            scheduled_for=now - timedelta(minutes=30),
            status="running",
            stage="submitted",
            worker_id="dead-worker",
            attempt=1,
            lease_until=now - timedelta(seconds=1),
            started_at=now - timedelta(minutes=30),
            submitted_at=now - timedelta(minutes=1),
        )
        session.add(submitted); session.commit()

        self.assertIsNone(claim_next_due_task(session, now, "replacement"))
        session.commit(); session.refresh(submitted)

        self.assertEqual("submitted_unconfirmed", submitted.status)
        self.assertEqual("delivery_confirmation_unavailable", submitted.error_code)
        self.assertEqual("dead-worker", submitted.worker_id)
        session.close(); engine.dispose()


class _RecordingExecutor:
    def __init__(self):
        self.credential_version = None
        self.payload_reference = None
        self.target_sec_uid = None
        self.message_kind = None

    async def execute(
        self,
        cookie_payload,
        target,
        message,
        credential_version=1,
        target_sec_uid=None,
        message_kind="text",
        **_kwargs,
    ):
        self.credential_version = credential_version
        self.payload_reference = cookie_payload
        self.target_sec_uid = target_sec_uid
        self.message_kind = message_kind
        return ExecutionResult(True, ExecutionStage.COMPLETE)


class _RaisingExecutor:
    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("sensitive-worker-exception")


class _RetryableExecutor:
    async def execute(self, *_args, **_kwargs):
        return ExecutionResult(
            False,
            ExecutionStage.AUTHENTICATING,
            "network_unavailable",
            "网络暂时不可用",
            retryable=True,
        )


class _RefreshingExecutor:
    async def execute(self, *_args, **_kwargs):
        return ExecutionResult(
            True,
            ExecutionStage.COMPLETE,
            refreshed_storage_state=_worker_storage_state("refreshed-worker-marker"),
        )


class _CookieInvalidExecutor:
    def __init__(self):
        self.called = False

    async def execute(self, *_args, **_kwargs):
        self.called = True
        return ExecutionResult(
            False,
            ExecutionStage.AUTHENTICATING,
            "cookie_invalid",
            "登录状态已失效",
        )


class _SubmittedThenRaisingExecutor:
    def __init__(self):
        self.checkpointed = False

    async def execute(self, *_args, on_submitted=None, **_kwargs):
        if on_submitted is None:
            raise AssertionError("submission callback was not provided")
        on_submitted()
        self.checkpointed = True
        raise RuntimeError("executor failed after submission checkpoint")


class _LockLossExecutor:
    def __init__(self):
        self.ready = asyncio.Event()
        self.release = asyncio.Event()
        self.submitted = False

    async def execute(self, *_args, on_submitted=None, **_kwargs):
        if on_submitted is None:
            raise AssertionError("submission callback was not provided")
        self.ready.set()
        await self.release.wait()
        on_submitted()
        self.submitted = True
        return ExecutionResult(True, ExecutionStage.COMPLETE)


class _ReauthThenCookieInvalidExecutor:
    def __init__(self, engine, account_id: str):
        self.engine = engine
        self.account_id = account_id

    async def execute(self, *_args, **_kwargs):
        with Session(self.engine) as session:
            account = session.get(DouyinAccount, self.account_id)
            accounts = AccountService(
                session, CookieCipher(b"w" * 32), AuditService(session)
            )
            accounts.record_identity_if_current(account.id, (account.encrypted_cookies, account.cookie_nonce, account.cookie_version), account.id)
            accounts.reauthenticate_owned(
                account.owner_user_id,
                account.id,
                _worker_storage_state("fresh-worker-marker"),
                platform_user_id=account.id,
            )
            session.commit()
        return ExecutionResult(
            False,
            ExecutionStage.AUTHENTICATING,
            "cookie_invalid",
            "旧凭据已失效",
        )


class WorkerCredentialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1.0

    @staticmethod
    def _settings(data_dir: Path) -> Settings:
        cookie_key = data_dir / "cookie.key"
        session_key = data_dir / "session.key"
        cookie_key.write_bytes(b"w" * 32)
        session_key.write_bytes(b"s" * 32)
        return Settings(
            data_dir=data_dir,
            database_url=f"sqlite:///{data_dir / 'worker.db'}",
            cookie_key_file=cookie_key,
            session_key_file=session_key,
        )

    @staticmethod
    def _seed_health_case(
        engine,
        now: datetime,
        *,
        validation_state: str = "valid",
        task_count: int = 1,
    ) -> tuple[str, tuple[str, ...]]:
        with Session(engine) as session:
            user = User(username="health-owner", password_hash="hash")
            session.add(user)
            session.flush()
            _activate(session, user, now)
            account = AccountService(
                session, CookieCipher(b"w" * 32), AuditService(session)
            ).create_from_storage_state(
                user.id,
                "健康账号",
                _worker_storage_state("initial-worker-marker"),
            )
            account.validation_state = validation_state
            tasks = []
            for index in range(task_count):
                task = SparkTask(
                    owner_user_id=user.id,
                    douyin_account_id=account.id,
                    target_name=f"好友{index}",
                    send_time=f"09:{index:02d}",
                    message_template="今日火花",
                    enabled=True,
                    next_run_at=(
                        now - timedelta(seconds=5)
                        if index == 0
                        else now + timedelta(days=1)
                    ),
                )
                session.add(task)
                tasks.append(task)
            session.commit()
            return account.id, tuple(task.id for task in tasks)

    @staticmethod
    def _seed_free_case(engine, now: datetime) -> tuple[str, str]:
        """Create one free-tier user/task without a subscription row."""
        with Session(engine) as session:
            user = User(username="free-worker-owner", password_hash="hash")
            session.add(user); session.flush()
            account = AccountService(
                session, CookieCipher(b"w" * 32), AuditService(session)
            ).create_from_storage_state(
                user.id, "免费账号", _worker_storage_state("free-worker-marker")
            )
            task = SparkTask(
                owner_user_id=user.id,
                douyin_account_id=account.id,
                target_name="免费好友",
                send_time="09:00",
                message_template="今日火花",
                enabled=True,
                next_run_at=now,
            )
            session.add(task); session.commit()
            return account.id, task.id

    async def test_free_tier_task_is_executed_by_worker(self):
        now = datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            engine = create_engine(settings.database_url)
            create_schema(engine)
            try:
                _account_id, task_id = self._seed_free_case(engine, now)
                executor = _RecordingExecutor()
                result = await Worker(
                    settings,
                    engine,
                    executor=executor,
                    started_at=now - timedelta(minutes=1),
                ).run_once(now)
                self.assertEqual("success", result.status)
                with Session(engine) as session:
                    self.assertEqual("success", session.scalar(select(TaskRun.status).where(TaskRun.task_id == task_id)))
            finally:
                engine.dispose()

    async def test_contact_avatar_sync_does_not_change_delivery_or_overwrite_relogin(self):
        now = datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc)
        url = "https://p3.douyinpic.com/avatar.jpeg"
        for case in ("success", "submitted", "failed", "relogin", "storage_error"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                settings = self._settings(Path(directory))
                engine = create_engine(settings.database_url)
                create_schema(engine)
                try:
                    account_id, _ = self._seed_health_case(engine, now)
                    with Session(engine) as session:
                        session.add(DouyinContactIdentity(account_id=account_id, sec_uid="stable", nickname="Friend"))
                        session.commit()

                    class AvatarExecutor:
                        async def execute(self, *_args, **_kwargs):
                            if case == "relogin":
                                with Session(engine) as session:
                                    account = session.get(DouyinAccount, account_id)
                                    accounts = AccountService(session, CookieCipher(b"w" * 32), AuditService(session))
                                    accounts.record_identity_if_current(account.id, (account.encrypted_cookies, account.cookie_nonce, account.cookie_version), account.id)
                                    accounts.reauthenticate_owned(
                                        account.owner_user_id, account_id, _worker_storage_state("new-login"), platform_user_id=account.id
                                    )
                                    session.commit()
                            return ExecutionResult(case != "failed", "submitted" if case == "submitted" else "complete",
                                                   contact_avatars={"stable": url})

                    with patch.object(AccountService, "sync_contact_avatars", autospec=True,
                                      side_effect=RuntimeError("optional storage error") if case == "storage_error" else AccountService.sync_contact_avatars):
                        result = await Worker(settings, engine, executor=AvatarExecutor(),
                                              started_at=now - timedelta(minutes=1)).run_once(now)
                    expected = "failed" if case == "failed" else "submitted_unconfirmed" if case == "submitted" else "success"
                    self.assertEqual(expected, result.status)
                    with Session(engine) as session:
                        avatar = session.get(DouyinContactAvatar, (account_id, "stable"))
                        if case in ("success", "submitted"):
                            self.assertEqual(url, avatar.avatar_url)
                        else:
                            self.assertIsNone(avatar)
                finally:
                    engine.dispose()

    async def test_success_refreshes_encrypted_storage_state_and_verification_time(self):
        now = datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            engine = create_engine(settings.database_url)
            create_schema(engine)
            try:
                account_id, _task_ids = self._seed_health_case(engine, now)
                with Session(engine) as session:
                    before = bytes(
                        session.get(DouyinAccount, account_id).encrypted_cookies
                    )

                result = await Worker(
                    settings,
                    engine,
                    executor=_RefreshingExecutor(),
                    started_at=now - timedelta(minutes=1),
                ).run_once(now)

                self.assertEqual("success", result.status)
                with Session(engine) as session:
                    account = session.get(DouyinAccount, account_id)
                    plaintext = CookieCipher(b"w" * 32).decrypt(
                        account.encrypted_cookies, account.cookie_nonce
                    )
                    self.assertNotEqual(before, account.encrypted_cookies)
                    self.assertIn(b"refreshed-worker-marker", plaintext)
                    self.assertEqual("valid", account.validation_state)
                    self.assertIsNotNone(account.last_verified_at)
            finally:
                engine.dispose()

    async def test_worker_passes_native_sticker_kind_to_executor(self):
        now = datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            engine = create_engine(settings.database_url)
            create_schema(engine)
            try:
                _account_id, task_ids = self._seed_health_case(engine, now)
                with Session(engine) as session:
                    task = session.get(SparkTask, task_ids[0])
                    task.message_kind = "douyin_sticker"
                    task.message_template = "续火花"
                    session.commit()
                executor = _RecordingExecutor()

                result = await Worker(
                    settings,
                    engine,
                    executor=executor,
                    started_at=now - timedelta(minutes=1),
                ).run_once(now)

                self.assertEqual("success", result.status)
                self.assertEqual("douyin_sticker", executor.message_kind)
            finally:
                engine.dispose()

    async def test_login_anomaly_preserves_tasks_and_schedules_health_recheck(self):
        now = datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            engine = create_engine(settings.database_url)
            create_schema(engine)
            try:
                account_id, task_ids = self._seed_health_case(
                    engine, now, task_count=2
                )
                executor = _CookieInvalidExecutor()

                result = await Worker(
                    settings,
                    engine,
                    executor=executor,
                    started_at=now - timedelta(minutes=1),
                ).run_once(now)

                self.assertTrue(executor.called)
                self.assertEqual("cookie_invalid", result.error_code)
                with Session(engine) as session:
                    account = session.get(DouyinAccount, account_id)
                    tasks = [session.get(SparkTask, task_id) for task_id in task_ids]
                    self.assertEqual("temporary", account.validation_state)
                    self.assertTrue(all(task.enabled for task in tasks))
            finally:
                engine.dispose()

    async def test_invalid_account_is_blocked_before_executor_runs(self):
        now = datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            engine = create_engine(settings.database_url)
            create_schema(engine)
            try:
                _account_id, task_ids = self._seed_health_case(
                    engine, now, validation_state="invalid"
                )
                executor = _CookieInvalidExecutor()

                result = await Worker(
                    settings,
                    engine,
                    executor=executor,
                    started_at=now - timedelta(minutes=1),
                ).run_once(now)

                self.assertFalse(executor.called)
                self.assertIsNone(result)
                with Session(engine) as session:
                    self.assertTrue(session.get(SparkTask, task_ids[0]).enabled)
            finally:
                engine.dispose()

    async def test_reclaimed_old_run_executes_instead_of_startup_or_late_skip(self):
        now = datetime(2026, 9, 2, 1, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            engine = create_engine(settings.database_url)
            create_schema(engine)
            try:
                _account_id, task_ids = self._seed_health_case(engine, now)
                with Session(engine) as session:
                    task = session.get(SparkTask, task_ids[0])
                    task.next_run_at = now + timedelta(days=1)
                    stale = TaskRun(
                        task_id=task.id,
                        scheduled_for=now - timedelta(minutes=30),
                        status="running",
                        stage="sending",
                        worker_id="dead-worker",
                        attempt=1,
                        lease_until=now - timedelta(seconds=1),
                        started_at=now - timedelta(minutes=30),
                    )
                    session.add(stale)
                    session.commit()
                    stale_id = stale.id

                executor = _RecordingExecutor()
                result = await Worker(
                    settings,
                    engine,
                    executor=executor,
                    started_at=now,
                    clock=lambda: now,
                ).run_once()

                self.assertEqual(stale_id, result.id)
                self.assertEqual("success", result.status)
                self.assertEqual(2, result.attempt)
                self.assertIsNotNone(executor.payload_reference)
                with Session(engine) as session:
                    runs = session.scalars(select(TaskRun)).all()
                    self.assertEqual(1, len(runs))
                    self.assertEqual(stale_id, runs[0].id)
                    self.assertEqual("success", runs[0].status)
                    self.assertEqual(2, runs[0].attempt)
            finally:
                engine.dispose()

    async def test_account_admission_failure_clears_decrypted_payload(self):
        now = datetime(2026, 9, 9, 2, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            engine = create_engine(settings.database_url)
            create_schema(engine)
            try:
                self._seed_health_case(engine, now)
                payload = bytearray(b'test-login-state')
                executor = _RecordingExecutor()
                with patch.object(AccountService, 'decrypt_for_worker', return_value=payload), \
                    patch('spark_console.services.account_health.acquire', side_effect=RuntimeError('database unavailable')):
                    with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
                        await Worker(settings, engine, executor=executor, started_at=now, clock=lambda: now).run_once()
                self.assertEqual(bytearray(), payload)
                self.assertIsNone(executor.payload_reference)
            finally:
                engine.dispose()

    async def test_lost_account_operation_prevents_submission(self):
        from spark_console.models import AccountOperation
        now = datetime(2026, 9, 9, 2, 0, tzinfo=timezone.utc)
        for replacement in (False, True):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as directory:
                settings = self._settings(Path(directory))
                engine = create_engine(settings.database_url)
                create_schema(engine)
                try:
                    account_id, _ = self._seed_health_case(engine, now)
                    executor = _LockLossExecutor()
                    worker = Worker(settings, engine, executor=executor, started_at=now, clock=lambda: now)
                    in_flight = asyncio.create_task(worker.run_once())
                    await asyncio.wait_for(executor.ready.wait(), timeout=2)
                    with Session(engine) as db:
                        operation = db.get(AccountOperation, account_id)
                        if replacement:
                            operation.token, operation.kind = 'new-login', 'login'
                        else:
                            operation.expires_at = now - timedelta(seconds=1)
                        db.commit()
                    executor.release.set()
                    result = await asyncio.wait_for(in_flight, timeout=2)
                    self.assertFalse(executor.submitted)
                    self.assertNotEqual('success', result.status)
                    with Session(engine) as db:
                        self.assertIsNone(db.scalar(select(TaskRun)).submitted_at)
                        if replacement:
                            self.assertEqual('new-login', db.get(AccountOperation, account_id).token)
                finally:
                    engine.dispose()

    async def test_lost_global_lock_prevents_submission_and_finalization(self):
        now = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            engine = create_engine(settings.database_url)
            create_schema(engine)
            try:
                _account_id, task_ids = self._seed_health_case(engine, now)
                executor = _LockLossExecutor()
                worker = Worker(
                    settings,
                    engine,
                    executor=executor,
                    started_at=now - timedelta(minutes=1),
                    clock=lambda: now,
                )
                in_flight = asyncio.create_task(worker.run_once())
                await asyncio.wait_for(executor.ready.wait(), timeout=2)

                with Session(engine) as session:
                    lock = session.get(WorkerLock, 1)
                    self.assertEqual(worker.worker_id, lock.worker_id)
                    lock.worker_id = "replacement-worker"
                    lock.lease_until = now + timedelta(minutes=1)
                    session.commit()

                executor.release.set()
                result = await asyncio.wait_for(in_flight, timeout=2)

                self.assertIsNone(result)
                self.assertFalse(executor.submitted)
                with Session(engine) as session:
                    run = session.scalar(select(TaskRun))
                    self.assertEqual("running", run.status)
                    self.assertEqual("claimed", run.stage)
                    self.assertEqual(worker.worker_id, run.worker_id)
                    self.assertIsNone(run.submitted_at)
                    self.assertIsNone(run.finished_at)
                    self.assertIsNone(run.retry_after)
                    self.assertEqual(
                        "replacement-worker",
                        session.get(WorkerLock, 1).worker_id,
                    )
                    self.assertGreater(
                        session.get(SparkTask, task_ids[0])
                        .next_run_at.replace(tzinfo=timezone.utc),
                        now + timedelta(hours=20),
                    )
            finally:
                engine.dispose()

    async def test_executor_error_after_submission_checkpoint_is_unconfirmed(self):
        now = datetime(2026, 9, 2, 2, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            engine = create_engine(settings.database_url)
            create_schema(engine)
            try:
                self._seed_health_case(engine, now)
                executor = _SubmittedThenRaisingExecutor()

                result = await Worker(
                    settings,
                    engine,
                    executor=executor,
                    started_at=now - timedelta(minutes=1),
                    clock=lambda: now,
                ).run_once()

                self.assertTrue(executor.checkpointed)
                self.assertEqual("submitted_unconfirmed", result.status)
                self.assertEqual("submitted", result.stage)
                self.assertEqual(
                    "delivery_confirmation_unavailable", result.error_code
                )
                self.assertEqual(now, result.submitted_at.replace(tzinfo=timezone.utc))
                self.assertIsNone(result.retry_after)
                with Session(engine) as session:
                    persisted = session.scalar(select(TaskRun))
                    self.assertEqual("submitted_unconfirmed", persisted.status)
                    self.assertIsNotNone(persisted.submitted_at)
            finally:
                engine.dispose()

    async def test_stale_cookie_invalid_does_not_override_concurrent_relogin(self):
        now = datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            engine = create_engine(settings.database_url)
            create_schema(engine)
            try:
                account_id, task_ids = self._seed_health_case(
                    engine, now, task_count=2
                )
                result = await Worker(
                    settings,
                    engine,
                    executor=_ReauthThenCookieInvalidExecutor(engine, account_id),
                    started_at=now - timedelta(minutes=1),
                    clock=lambda: now,
                ).run_once()

                self.assertEqual("failed", result.status)
                self.assertEqual("cookie_invalid", result.error_code)
                with Session(engine) as session:
                    account = session.get(DouyinAccount, account_id)
                    plaintext = CookieCipher(b"w" * 32).decrypt(
                        account.encrypted_cookies, account.cookie_nonce
                    )
                    self.assertIn(b"fresh-worker-marker", plaintext)
                    self.assertEqual("valid", account.validation_state)
                    self.assertTrue(
                        all(
                            session.get(SparkTask, task_id).enabled
                            for task_id in task_ids
                        )
                    )
            finally:
                engine.dispose()

    async def test_temporary_credential_load_error_uses_one_minute_retry(self):
        now = datetime(2026, 9, 2, 3, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            engine = create_engine(settings.database_url)
            create_schema(engine)
            try:
                account_id, task_ids = self._seed_health_case(engine, now)
                executor = _RecordingExecutor()
                with patch.object(
                    AccountService,
                    "decrypt_for_worker",
                    side_effect=RuntimeError("temporary credential store outage"),
                ):
                    result = await Worker(
                        settings,
                        engine,
                        executor=executor,
                        started_at=now - timedelta(minutes=1),
                        clock=lambda: now,
                    ).run_once()

                self.assertEqual("retrying", result.status)
                self.assertEqual("credential_load", result.stage)
                self.assertEqual("retry_scheduled_1m", result.error_code)
                self.assertIsNone(executor.payload_reference)
                with Session(engine) as session:
                    account = session.get(DouyinAccount, account_id)
                    task = session.get(SparkTask, task_ids[0])
                    self.assertEqual("valid", account.validation_state)
                    self.assertTrue(task.enabled)
                    self.assertEqual(
                        now + timedelta(minutes=1),
                        task.next_run_at.replace(tzinfo=timezone.utc),
                    )
            finally:
                engine.dispose()

    async def test_corrupt_encrypted_credentials_are_not_retried(self):
        now = datetime(2026, 9, 2, 4, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            engine = create_engine(settings.database_url)
            create_schema(engine)
            try:
                account_id, task_ids = self._seed_health_case(
                    engine, now, task_count=2
                )
                with Session(engine) as session:
                    account = session.get(DouyinAccount, account_id)
                    ciphertext = bytes(account.encrypted_cookies)
                    account.encrypted_cookies = (
                        ciphertext[:-1] + bytes((ciphertext[-1] ^ 1,))
                    )
                    session.commit()

                executor = _RecordingExecutor()
                result = await Worker(
                    settings,
                    engine,
                    executor=executor,
                    started_at=now - timedelta(minutes=1),
                    clock=lambda: now,
                ).run_once()

                self.assertEqual("failed", result.status)
                self.assertEqual(ExecutionStage.AUTHENTICATING, result.stage)
                self.assertEqual("cookie_invalid", result.error_code)
                self.assertIsNone(result.retry_after)
                self.assertIsNone(executor.payload_reference)
                with Session(engine) as session:
                    self.assertEqual(
                        "invalid",
                        session.get(DouyinAccount, account_id).validation_state,
                    )
                    self.assertTrue(
                        all(
                            session.get(SparkTask, task_id).enabled
                            for task_id in task_ids
                        )
                    )
            finally:
                engine.dispose()

    async def test_worker_passes_account_version_and_clears_decrypted_payload(self):
        now = datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)
        scheduled = now - timedelta(seconds=5)
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            cookie_key = data_dir / "cookie.key"
            session_key = data_dir / "session.key"
            cookie_key.write_bytes(b"w" * 32)
            session_key.write_bytes(b"s" * 32)
            settings = Settings(
                data_dir=data_dir,
                database_url=f"sqlite:///{data_dir / 'worker.db'}",
                cookie_key_file=cookie_key,
                session_key_file=session_key,
            )
            engine = create_engine(settings.database_url)
            create_schema(engine)
            with Session(engine) as session:
                user = User(username="worker-owner", password_hash="hash")
                session.add(user)
                session.flush()
                _activate(session, user, now)
                account = AccountService(
                    session, CookieCipher(b"w" * 32), AuditService(session)
                ).create_from_storage_state(
                    user.id,
                    "扫码账号",
                    {
                        "cookies": [
                            {
                                "name": "sid",
                                "value": "worker-secret-marker",
                                "domain": ".douyin.com",
                                "path": "/",
                                "expires": -1,
                                "httpOnly": True,
                                "secure": True,
                                "sameSite": "Lax",
                            }
                        ],
                        "origins": [],
                    },
                )
                task = SparkTask(
                    owner_user_id=user.id,
                    douyin_account_id=account.id,
                    target_name="好友",
                    send_time="09:00",
                    message_template="今日火花",
                    enabled=True,
                    next_run_at=scheduled,
                )
                session.add(task)
                session.flush()
                session.add(
                    SparkTaskTargetIdentity(
                        task_id=task.id,
                        sec_uid="stable-user-id",
                    )
                )
                session.commit()

            executor = _RecordingExecutor()
            payload = bytearray(b"worker-secret-marker")
            decrypted = False
            original_get = Session.get

            def decrypt_for_worker(_service, _account_id):
                nonlocal decrypted
                decrypted = True
                return payload

            def reject_post_decrypt_lookup(db, entity, ident, **kwargs):
                if entity is DouyinAccount and decrypted:
                    raise RuntimeError("database lookup after credential decryption")
                return original_get(db, entity, ident, **kwargs)

            try:
                with patch.object(
                    AccountService, "decrypt_for_worker", decrypt_for_worker
                ), patch.object(Session, "get", reject_post_decrypt_lookup):
                    result = await Worker(
                        settings, engine, executor=executor, started_at=now
                    ).run_once(now)
            finally:
                engine.dispose()
            self.assertEqual("success", result.status)
            self.assertEqual(2, executor.credential_version)
            self.assertEqual("stable-user-id", executor.target_sec_uid)
            self.assertEqual(0, len(payload))

    async def test_retryable_failure_retries_after_one_then_five_minutes(self):
        now = datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            cookie_key = data_dir / "cookie.key"
            session_key = data_dir / "session.key"
            cookie_key.write_bytes(b"w" * 32)
            session_key.write_bytes(b"s" * 32)
            settings = Settings(
                data_dir=data_dir,
                database_url=f"sqlite:///{data_dir / 'worker.db'}",
                cookie_key_file=cookie_key,
                session_key_file=session_key,
            )
            engine = create_engine(settings.database_url)
            create_schema(engine)
            try:
                with Session(engine) as session:
                    user = User(username="retry-owner", password_hash="hash")
                    session.add(user)
                    session.flush()
                    _activate(session, user, now)
                    account = AccountService(
                        session, CookieCipher(b"w" * 32), AuditService(session)
                    ).create_from_storage_state(
                        user.id,
                        "扫码账号",
                        {
                            "cookies": [
                                {
                                    "name": "sid",
                                    "value": "retry-secret-marker",
                                    "domain": ".douyin.com",
                                    "path": "/",
                                    "expires": -1,
                                    "httpOnly": True,
                                    "secure": True,
                                    "sameSite": "Lax",
                                }
                            ],
                            "origins": [],
                        },
                    )
                    task = SparkTask(
                        owner_user_id=user.id,
                        douyin_account_id=account.id,
                        target_name="好友",
                        send_time="09:00",
                        message_template="今日火花",
                        enabled=True,
                        next_run_at=now,
                    )
                    session.add(task)
                    session.commit()
                    task_id = task.id

                worker = Worker(
                    settings,
                    engine,
                    executor=_RetryableExecutor(),
                    started_at=now,
                )
                first = await worker.run_once(now)
                self.assertEqual("retry_scheduled_1m", first.error_code)
                self.assertEqual("retrying", first.status)
                self.assertEqual(1, first.attempt)
                self.assertEqual(now + timedelta(minutes=1), first.retry_after.replace(tzinfo=timezone.utc))
                with Session(engine) as session:
                    self.assertEqual(
                        now + timedelta(minutes=1),
                        session.get(SparkTask, task_id).next_run_at.replace(tzinfo=timezone.utc),
                    )

                second_time = now + timedelta(minutes=1)
                second = await worker.run_once(second_time)
                self.assertEqual("retry_scheduled_5m", second.error_code)
                self.assertEqual("retrying", second.status)
                self.assertEqual(2, second.attempt)
                with Session(engine) as session:
                    self.assertEqual(
                        second_time + timedelta(minutes=5),
                        session.get(SparkTask, task_id).next_run_at.replace(tzinfo=timezone.utc),
                    )

                third_time = second_time + timedelta(minutes=5)
                third = await worker.run_once(third_time)
                self.assertEqual("network_unavailable", third.error_code)
                self.assertEqual("failed", third.status)
                self.assertEqual(3, third.attempt)
                with Session(engine) as session:
                    task = session.get(SparkTask, task_id)
                    self.assertGreater(
                        task.next_run_at.replace(tzinfo=timezone.utc),
                        third_time + timedelta(hours=20),
                    )
                    self.assertEqual(3, len(session.scalars(select(TaskRun)).all()))
            finally:
                engine.dispose()

    async def test_global_worker_lease_blocks_a_second_worker_until_expiry(self):
        now = datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            cookie_key = data_dir / "cookie.key"
            session_key = data_dir / "session.key"
            cookie_key.write_bytes(b"w" * 32)
            session_key.write_bytes(b"s" * 32)
            settings = Settings(
                data_dir=data_dir,
                database_url=f"sqlite:///{data_dir / 'worker.db'}",
                cookie_key_file=cookie_key,
                session_key_file=session_key,
            )
            engine = create_engine(settings.database_url)
            create_schema(engine)
            first = Worker(settings, engine, executor=_RecordingExecutor(), started_at=now)
            second = Worker(settings, engine, executor=_RecordingExecutor(), started_at=now)

            self.assertIsNone(await first.run_once(now))
            self.assertIsNone(await second.run_once(now))
            with Session(engine) as session:
                self.assertEqual(first.worker_id, session.get(WorkerLock, 1).worker_id)

            self.assertIsNone(await second.run_once(now + timedelta(seconds=31)))
            with Session(engine) as session:
                self.assertEqual(second.worker_id, session.get(WorkerLock, 1).worker_id)
            engine.dispose()

    async def test_worker_startup_records_but_never_sends_tasks_missed_while_offline(self):
        scheduled = datetime(2026, 8, 26, 8, 36, tzinfo=timezone.utc)
        started = scheduled + timedelta(minutes=20)
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            cookie_key = data_dir / "cookie.key"
            session_key = data_dir / "session.key"
            cookie_key.write_bytes(b"w" * 32)
            session_key.write_bytes(b"s" * 32)
            settings = Settings(
                data_dir=data_dir,
                database_url=f"sqlite:///{data_dir / 'worker.db'}",
                cookie_key_file=cookie_key,
                session_key_file=session_key,
            )
            engine = create_engine(settings.database_url)
            create_schema(engine)
            with Session(engine) as session:
                user = User(username="offline-owner", password_hash="hash")
                session.add(user)
                session.flush()
                _activate(session, user, scheduled)
                account = DouyinAccount(
                    owner_user_id=user.id,
                    display_name="main",
                    encrypted_cookies=b"not-needed",
                    cookie_nonce=b"not-needed",
                )
                session.add(account)
                session.flush()
                task = SparkTask(
                    owner_user_id=user.id,
                    douyin_account_id=account.id,
                    target_name="好友",
                    send_time="16:36",
                    message_template="今日火花",
                    enabled=True,
                    next_run_at=scheduled,
                )
                session.add(task)
                session.commit()

            executor = _RecordingExecutor()
            result = await Worker(
                settings,
                engine,
                executor=executor,
                started_at=started,
            ).run_once(started)

            self.assertEqual("skipped", result.status)
            self.assertEqual("missed_startup", result.stage)
            self.assertEqual("worker_was_offline", result.error_code)
            self.assertIsNone(executor.payload_reference)
            with Session(engine) as session:
                persisted = session.scalar(select(TaskRun))
                self.assertEqual("skipped", persisted.status)
            engine.dispose()

    async def test_unexpected_executor_error_is_recorded_without_crashing_worker(self):
        now = datetime(2026, 8, 26, 9, 0, 5, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            cookie_key = data_dir / "cookie.key"
            session_key = data_dir / "session.key"
            cookie_key.write_bytes(b"w" * 32)
            session_key.write_bytes(b"s" * 32)
            settings = Settings(
                data_dir=data_dir,
                database_url=f"sqlite:///{data_dir / 'worker.db'}",
                cookie_key_file=cookie_key,
                session_key_file=session_key,
            )
            engine = create_engine(settings.database_url)
            create_schema(engine)
            try:
                with Session(engine) as session:
                    user = User(username="error-owner", password_hash="hash")
                    session.add(user)
                    session.flush()
                    _activate(session, user, now)
                    account = AccountService(
                        session, CookieCipher(b"w" * 32), AuditService(session)
                    ).create_from_storage_state(
                        user.id,
                        "扫码账号",
                        {
                            "cookies": [
                                {
                                    "name": "sid",
                                    "value": "worker-secret-marker",
                                    "domain": ".douyin.com",
                                    "path": "/",
                                    "expires": -1,
                                    "httpOnly": True,
                                    "secure": True,
                                    "sameSite": "Lax",
                                }
                            ],
                            "origins": [],
                        },
                    )
                    session.add(
                        SparkTask(
                            owner_user_id=user.id,
                            douyin_account_id=account.id,
                            target_name="好友",
                            send_time="17:00",
                            message_template="今日火花",
                            enabled=True,
                            next_run_at=now - timedelta(seconds=5),
                        )
                    )
                    session.commit()

                result = await Worker(
                    settings,
                    engine,
                    executor=_RaisingExecutor(),
                    started_at=now - timedelta(minutes=1),
                ).run_once(now)

                self.assertEqual("retrying", result.status)
                self.assertEqual("worker_error", result.stage)
                self.assertEqual("retry_scheduled_1m", result.error_code)
                self.assertNotIn("sensitive-worker-exception", result.error_summary)
                with Session(engine) as session:
                    persisted = session.scalar(select(TaskRun))
                    self.assertEqual("retrying", persisted.status)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
