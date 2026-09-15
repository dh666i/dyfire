from __future__ import annotations

import asyncio
import inspect
import logging
import os
import signal
import socket
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidTag
from sqlalchemy import exists, or_, select, update

from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import create_engine_for, create_schema, session_scope
from spark_console.executor import DouyinExecutor, ExecutionResult, ExecutionStage
from spark_console.models import (
    AccountOperation,
    DouyinAccount,
    SparkTask,
    SparkTaskTargetIdentity,
    TaskRun,
    TaskHealthRetry,
    User,
    WorkerLock,
)
from spark_console.scheduler import RUN_LEASE, claim_next_due_task, finish_run
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.services.subscriptions import SubscriptionService


logger = logging.getLogger(__name__)


class _CredentialLoadInvalid(RuntimeError):
    pass


class _LeaseOwnershipLost(RuntimeError):
    pass


class Worker:
    STARTUP_GRACE = timedelta(minutes=5)
    RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5))
    WORKER_LEASE = timedelta(seconds=30)
    HEARTBEAT_INTERVAL_SECONDS = 10

    def __init__(
        self,
        settings: Settings,
        engine,
        executor=None,
        clock_offset_seconds=0.0,
        started_at: datetime | None = None,
        clock=None,
    ):
        self.settings = settings
        self.engine = engine
        self.executor = executor or DouyinExecutor()
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.clock_offset_seconds = clock_offset_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._custom_clock = clock is not None
        self.started_at = started_at or self._clock()
        self.cipher = CookieCipher(settings.cookie_key_file.read_bytes())

    async def run_once(self, now: datetime | None = None):
        current_time = now or self._clock()
        if not self._acquire_worker_lock(current_time):
            return None
        with session_scope(self.engine) as db:
            run = claim_next_due_task(db, current_time, self.worker_id)
            if run is None:
                return None
            run_id = run.id
            reclaimed = run.stage in ("reclaimed", "health_retry")
            scheduled = (
                run.scheduled_for
                if run.scheduled_for.tzinfo
                else run.scheduled_for.replace(tzinfo=timezone.utc)
            )

        if abs(self.clock_offset_seconds) > self.settings.clock_offset_limit_seconds:
            return self._finish_claimed(
                run_id,
                "failed",
                "clock_check",
                current_time,
                "system_time_unhealthy",
                "服务器时间未同步",
            )
        if (
            not reclaimed
            and scheduled < self.started_at
            and current_time - scheduled > self.STARTUP_GRACE
        ):
            return self._finish_claimed(
                run_id,
                "skipped",
                "missed_startup",
                current_time,
                "worker_was_offline",
                "执行器离线期间任务已错过，未补发",
            )
        if not reclaimed and current_time - scheduled > timedelta(minutes=10):
            return self._finish_claimed(
                run_id,
                "skipped",
                "late",
                current_time,
                "missed_window",
                "任务已超过 10 分钟发送窗口",
            )

        cookies = None
        account_id = None
        credential_snapshot = None
        try:
            with session_scope(self.engine) as db:
                run = db.get(TaskRun, run_id)
                if run is None or run.worker_id != self.worker_id:
                    return None
                task = db.get(SparkTask, run.task_id)
                if task is None or not task.enabled:
                    return finish_run(
                        run,
                        "skipped",
                        "entitlement_check",
                        current_time,
                        "task_inactive",
                        "任务已暂停或不存在",
                    )
                owner = db.get(User, task.owner_user_id)
                if owner is None or owner.status != "active":
                    return finish_run(
                        run,
                        "skipped",
                        "entitlement_check",
                        current_time,
                        "user_inactive",
                        "用户账号已停用",
                    )
                if owner.role != "admin" and not SubscriptionService(
                    db, AuditService(db)
                ).active(owner.id):
                    return finish_run(
                        run,
                        "skipped",
                        "entitlement_check",
                        current_time,
                        "subscription_inactive",
                        "会员未开通或已过期",
                    )
                account = db.scalar(
                    select(DouyinAccount).where(
                        DouyinAccount.id == task.douyin_account_id,
                        DouyinAccount.owner_user_id == task.owner_user_id,
                    )
                )
                if account is None:
                    return finish_run(
                        run,
                        "failed",
                        "entitlement_check",
                        current_time,
                        "account_unavailable",
                        "发送账号不存在或归属异常",
                    )
                if account.validation_state in ("invalid", "temporary", "verification"):
                    return finish_run(
                        run,
                        "skipped",
                        "credential_load",
                        current_time,
                        "account_health_blocked",
                        "等待账号恢复，任务开关保持不变",
                    )
                account_service = AccountService(db, self.cipher, AuditService(db))
                account_id = account.id
                credential_version = account.cookie_version
                credential_snapshot = (
                    bytes(account.encrypted_cookies),
                    bytes(account.cookie_nonce),
                    account.cookie_version,
                )
                target_identity = db.get(SparkTaskTargetIdentity, task.id)
                target_sec_uid = target_identity.sec_uid if target_identity else None
                target_name = task.target_name
                message_template = task.message_template
                message_kind = getattr(task, "message_kind", "text") or "text"
                try:
                    cookies = account_service.decrypt_for_worker(
                        task.douyin_account_id
                    )
                except (InvalidTag, ValueError) as error:
                    raise _CredentialLoadInvalid from error
        except _CredentialLoadInvalid:
            return self._persist_result(
                run_id,
                ExecutionResult(
                    False,
                    ExecutionStage.AUTHENTICATING,
                    "cookie_invalid",
                    "账号凭据格式无效，请重新登录",
                    retryable=False,
                    health_state='invalid', health_reason='保存的登录资料无法解密，需要重新登录',
                ),
                self._event_time(current_time, now is not None),
                account_id=account_id,
                credential_snapshot=credential_snapshot,
            )
        except Exception:
            self._clear_cookie_payload(cookies)
            return self._persist_result(
                run_id,
                ExecutionResult(
                    False,
                    "credential_load",
                    "credential_unavailable",
                    "账号凭据暂时无法读取，Worker 已继续运行",
                    retryable=True,
                ),
                self._event_time(current_time, now is not None),
                account_id=account_id,
                credential_snapshot=credential_snapshot,
            )

        from spark_console.services.account_health import acquire, release, is_relogging
        try:
            with session_scope(self.engine) as db:
                admitted = acquire(db, account_id, run_id, 'send', current_time, 1200)
                current = db.execute(select(DouyinAccount.validation_state, DouyinAccount.encrypted_cookies,
                    DouyinAccount.cookie_nonce, DouyinAccount.cookie_version).where(DouyinAccount.id == account_id)).first()
                if current is None or current[0] in ('temporary','verification','invalid'):
                    release(db, account_id, run_id)
                    self._clear_cookie_payload(cookies)
                    return finish_run(db.get(TaskRun, run_id), 'skipped', 'credential_load', current_time,
                        'account_health_blocked', '账号暂缓发送，等待健康状态恢复')
                if is_relogging(db, account_id) or not admitted or tuple(current[1:]) != credential_snapshot:
                    release(db, account_id, run_id)
                    self._clear_cookie_payload(cookies)
                    run = db.get(TaskRun, run_id)
                    task = db.get(SparkTask, run.task_id)
                    retry_at = current_time + timedelta(seconds=30)
                    if db.get(TaskHealthRetry, run.id) is None:
                        task.next_run_at = retry_at
                    return finish_run(run, 'retrying', 'account_queue', current_time, 'account_busy', '等待该账号的云端操作完成', retry_after=retry_at)
        except BaseException:
            self._clear_cookie_payload(cookies)
            raise
        heartbeat_stop = asyncio.Event()
        lease_lost = asyncio.Event()
        execution = None
        heartbeat = None
        try:
            kwargs = dict(
                credential_version=credential_version,
                target_sec_uid=target_sec_uid,
            )
            if self._supports_message_kind():
                kwargs["message_kind"] = message_kind
            if self._supports_submission_callback():
                kwargs["on_submitted"] = lambda: self._mark_submitted(
                    run_id,
                    self._event_time(current_time, now is not None),
                )
            execution = asyncio.create_task(
                self.executor.execute(
                    cookies, target_name, message_template, **kwargs
                )
            )
            heartbeat = asyncio.create_task(
                self._heartbeat(run_id, heartbeat_stop, lease_lost, execution)
            )
            try:
                result = await execution
            except asyncio.CancelledError:
                if lease_lost.is_set():
                    return None
                raise
            except Exception:
                result = ExecutionResult(
                    False,
                    "worker_error",
                    "unexpected_error",
                    "任务执行发生意外异常，Worker 已继续运行",
                    retryable=True,
                )
        finally:
            heartbeat_stop.set()
            if heartbeat is not None:
                await heartbeat
            self._clear_cookie_payload(cookies)
            if execution is None or execution.cancelled():
                with session_scope(self.engine) as db:
                    release(db, account_id, run_id)

        finished_at = self._event_time(current_time, now is not None)
        try:
            return self._persist_result(run_id, result, finished_at,
                account_id=account_id, credential_snapshot=credential_snapshot)
        finally:
            with session_scope(self.engine) as db:
                release(db, account_id, run_id)

    def _event_time(
        self, deterministic_fallback: datetime, explicit_now: bool
    ) -> datetime:
        if explicit_now and not self._custom_clock:
            return deterministic_fallback
        return self._clock()

    def _acquire_worker_lock(self, now: datetime) -> bool:
        with session_scope(self.engine) as db:
            claimed = db.execute(
                update(WorkerLock)
                .where(
                    WorkerLock.id == 1,
                    or_(
                        WorkerLock.worker_id == self.worker_id,
                        WorkerLock.worker_id.is_(None),
                        WorkerLock.lease_until.is_(None),
                        WorkerLock.lease_until <= now,
                    ),
                )
                .values(
                    worker_id=self.worker_id,
                    lease_until=now + self.WORKER_LEASE,
                )
            )
            return claimed.rowcount == 1

    def _has_valid_worker_lock(self, now: datetime):
        return exists().where(
            WorkerLock.id == 1,
            WorkerLock.worker_id == self.worker_id,
            WorkerLock.lease_until.is_not(None),
            WorkerLock.lease_until > now,
        )

    def _renew_leases_in_session(self, db, run_id: str, now: datetime) -> bool:
        from spark_console.models import AccountOperation
        db.execute(update(AccountOperation).where(AccountOperation.token == run_id, AccountOperation.kind == 'send')
            .values(expires_at=now + RUN_LEASE))
        run = db.execute(
            update(TaskRun)
            .where(
                TaskRun.id == run_id,
                TaskRun.status == "running",
                TaskRun.worker_id == self.worker_id,
                self._has_valid_worker_lock(now),
            )
            .values(lease_until=now + RUN_LEASE)
            .execution_options(synchronize_session=False)
        )
        if run.rowcount != 1:
            return False
        worker = db.execute(
            update(WorkerLock)
            .where(
                WorkerLock.id == 1,
                WorkerLock.worker_id == self.worker_id,
                WorkerLock.lease_until.is_not(None),
                WorkerLock.lease_until > now,
            )
            .values(lease_until=now + self.WORKER_LEASE)
            .execution_options(synchronize_session=False)
        )
        if worker.rowcount != 1:
            raise _LeaseOwnershipLost("global worker lease no longer owned")
        return True

    def _renew_leases(self, run_id: str, now: datetime) -> bool:
        try:
            with session_scope(self.engine) as db:
                return self._renew_leases_in_session(db, run_id, now)
        except _LeaseOwnershipLost:
            return False

    async def _heartbeat(
        self,
        run_id: str,
        stopping: asyncio.Event,
        lease_lost: asyncio.Event,
        execution: asyncio.Task,
    ) -> None:
        while not stopping.is_set():
            try:
                await asyncio.wait_for(
                    stopping.wait(), timeout=self.HEARTBEAT_INTERVAL_SECONDS
                )
            except TimeoutError:
                try:
                    renewed = self._renew_leases(run_id, self._clock())
                except Exception:
                    logger.warning("worker lease heartbeat failed")
                    renewed = False
                if not renewed:
                    lease_lost.set()
                    if not execution.done():
                        execution.cancel()
                    return

    def _supports_submission_callback(self) -> bool:
        try:
            parameters = inspect.signature(self.executor.execute).parameters.values()
        except (TypeError, ValueError):
            return False
        return any(
            parameter.name == "on_submitted"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )

    def _supports_message_kind(self) -> bool:
        try:
            parameters = inspect.signature(self.executor.execute).parameters.values()
        except (TypeError, ValueError):
            return False
        return any(
            parameter.name == "message_kind"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )

    def _mark_submitted(
        self, run_id: str, submitted_at: datetime | None = None
    ) -> None:
        checkpoint_time = submitted_at or self._clock()
        try:
            with session_scope(self.engine) as db:
                pending = db.get(TaskHealthRetry, run_id)
                if pending is not None:
                    from spark_console.health_retry import invalid_reason
                    retry_run = db.get(TaskRun, run_id)
                    if invalid_reason(db, retry_run, pending, checkpoint_time, require_valid=True):
                        raise RuntimeError("health retry is no longer eligible")
                updated = db.execute(
                    update(TaskRun)
                    .where(
                        TaskRun.id == run_id,
                        TaskRun.status == "running",
                        TaskRun.worker_id == self.worker_id,
                        self._has_valid_worker_lock(checkpoint_time),
                        exists().where(AccountOperation.token == run_id,
                            AccountOperation.kind == 'send', AccountOperation.expires_at > checkpoint_time),
                    )
                    .values(
                        stage="submitted",
                        submitted_at=checkpoint_time,
                        lease_until=checkpoint_time + RUN_LEASE,
                    )
                    .execution_options(synchronize_session=False)
                )
                if updated.rowcount != 1:
                    raise _LeaseOwnershipLost("task run lease no longer owned")
                worker = db.execute(
                    update(WorkerLock)
                    .where(
                        WorkerLock.id == 1,
                        WorkerLock.worker_id == self.worker_id,
                        WorkerLock.lease_until.is_not(None),
                        WorkerLock.lease_until > checkpoint_time,
                    )
                    .values(lease_until=checkpoint_time + self.WORKER_LEASE)
                    .execution_options(synchronize_session=False)
                )
                if worker.rowcount != 1:
                    raise _LeaseOwnershipLost("global worker lease no longer owned")
        except _LeaseOwnershipLost as error:
            raise RuntimeError("task run lease no longer owned") from error

    def _persist_result(
        self,
        run_id: str,
        result: ExecutionResult,
        finished_at: datetime,
        *,
        account_id: str | None = None,
        credential_snapshot: tuple[bytes, bytes, int] | None = None,
    ):
        try:
            with session_scope(self.engine) as db:
                if not self._renew_leases_in_session(db, run_id, finished_at):
                    return None
                run = db.get(TaskRun, run_id)
                if (
                    run is None
                    or run.worker_id != self.worker_id
                    or run.status != "running"
                ):
                    return None
                task = db.get(SparkTask, run.task_id)
                if task is None:
                    return finish_run(
                        run,
                        "failed",
                        result.stage,
                        finished_at,
                        "task_deleted_during_run",
                        "任务执行期间已被删除",
                    )
                account_service = AccountService(db, self.cipher, AuditService(db))
                avatars = getattr(result, "contact_avatars", {})
                if result.success and avatars and account_id and credential_snapshot:
                    current_account = db.get(DouyinAccount, account_id)
                    if (
                        current_account is not None
                        and task.douyin_account_id == account_id
                        and (current_account.encrypted_cookies, current_account.cookie_nonce,
                             current_account.cookie_version) == credential_snapshot
                    ):
                        try:
                            with db.begin_nested():
                                account_service.sync_contact_avatars(account_id, avatars)
                        except Exception:
                            logger.warning("contact avatars were not persisted")
                refreshed_state = getattr(result, "refreshed_storage_state", None)
                if (
                    result.success
                    and account_id
                    and credential_snapshot
                    and isinstance(refreshed_state, dict)
                ):
                    try:
                        account_service.record_verified(
                            account_id,
                            expected_ciphertext=credential_snapshot[0],
                            expected_nonce=credential_snapshot[1],
                            expected_version=credential_snapshot[2],
                            storage_state=refreshed_state,
                        )
                    except Exception:
                        logger.warning(
                            "successful send credential refresh was not persisted"
                        )
                    finally:
                        refreshed_state.clear()

                crossed_submit_boundary = (
                    run.submitted_at is not None or result.stage == "submitted"
                )
                if crossed_submit_boundary and (
                    not result.success or result.stage == "submitted"
                ):
                    if result.stage == "submitted":
                        error_code = (
                            result.error_code
                            or "delivery_confirmation_unavailable"
                        )
                        error_summary = (
                            result.error_summary
                            or "消息已提交，但页面未能二次确认"
                        )
                    else:
                        error_code = "delivery_confirmation_unavailable"
                        error_summary = "消息已提交，但执行器中断，未自动重复发送"
                    return finish_run(
                        run,
                        "submitted_unconfirmed",
                        "submitted",
                        finished_at,
                        error_code,
                        error_summary,
                    )

                from spark_console.run_attempts import save as save_attempt
                save_attempt(run, "success" if result.success else "failed", result.stage,
                             finished_at, result.error_summary)
                health_recorded = False
                if (
                    (result.health_state or result.error_code == "cookie_invalid")
                    and account_id
                    and credential_snapshot
                ):
                    from spark_console.services.account_health import record
                    health_recorded = record(db, account_id, credential_snapshot, result.health_state or 'temporary',
                        result.health_reason or '登录检测报告异常，尚未复核', now=finished_at)
                if (not result.success and result.stage == "authenticating"
                    and result.error_code == "account_health_pending"
                    and result.health_state == "temporary" and health_recorded):
                    from spark_console.health_retry import queue
                    return queue(db, task, run, finished_at, result.error_summary or "临时登录检测异常")
                pending = db.get(TaskHealthRetry, run.id)
                if pending is not None and not result.success:
                    from spark_console.health_retry import invalid_reason
                    invalid = invalid_reason(db, run, pending, finished_at)
                    if invalid:
                        return finish_run(run, "failed", result.stage, finished_at, *invalid)
                if not result.success and result.retryable and pending is None:
                    retry = self._schedule_retry(
                        db, task, run, finished_at, result.stage
                    )
                    if retry is not None:
                        return retry
                return finish_run(
                    run,
                    "success" if result.success else "failed",
                    result.stage,
                    finished_at,
                    result.error_code,
                    result.error_summary,
                )
        except _LeaseOwnershipLost:
            return None

    def _finish_claimed(
        self,
        run_id: str,
        status: str,
        stage: str,
        now: datetime,
        error_code: str | None = None,
        error_summary: str | None = None,
    ):
        try:
            with session_scope(self.engine) as db:
                if not self._renew_leases_in_session(db, run_id, now):
                    return None
                run = db.get(TaskRun, run_id)
                if (
                    run is None
                    or run.worker_id != self.worker_id
                    or run.status != "running"
                ):
                    return None
                return finish_run(
                    run,
                    status,
                    stage,
                    now,
                    error_code,
                    error_summary,
                )
        except _LeaseOwnershipLost:
            return None

    @staticmethod
    def _clear_cookie_payload(cookies: bytearray | None) -> None:
        if cookies is None:
            return
        cookies[:] = b"\0" * len(cookies)
        cookies.clear()

    def _schedule_retry(self, db, task, run, now, stage):
        if run.attempt > len(self.RETRY_DELAYS):
            return None
        delay = self.RETRY_DELAYS[run.attempt - 1]
        retry_after = now + delay
        minutes = int(delay.total_seconds() // 60)
        task.next_run_at = retry_after
        return finish_run(
            run,
            "retrying",
            stage,
            now,
            f"retry_scheduled_{minutes}m",
            f"发送前遇到临时故障，已安排 {minutes} 分钟后重试",
            retry_after=retry_after,
        )


async def run_loop() -> None:
    settings = Settings.from_env(os.environ)
    engine = create_engine_for(settings)
    create_schema(engine)
    worker = Worker(settings, engine, clock_offset_seconds=float(os.environ.get("SPARK_CLOCK_OFFSET_SECONDS", "0")))
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stopping.set)
        except NotImplementedError:
            pass
    while not stopping.is_set():
        try:
            processed = await worker.run_once()
        except Exception as error:
            logger.error(
                "worker iteration failed exception=%s", type(error).__name__
            )
            processed = None
        if processed is not None:
            continue
        try:
            await asyncio.wait_for(stopping.wait(), timeout=settings.worker_poll_seconds)
        except TimeoutError:
            continue


if __name__ == "__main__":
    asyncio.run(run_loop())
