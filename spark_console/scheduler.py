from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select, update, exists
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from spark_console.models import (
    DouyinAccount,
    AccountOperation,
    DouyinLoginSession,
    SparkTask,
    TaskRun,
    TaskHealthRetry,
    User,
    UserSubscription,
)


RUN_LEASE = timedelta(minutes=20)


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def compute_next_run(send_time: str, now_utc: datetime) -> datetime:
    hour, minute = map(int, send_time.split(":"))
    local_now = _utc(now_utc).astimezone(ZoneInfo("Asia/Shanghai"))
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _expired_lease(now_utc: datetime):
    return or_(
        TaskRun.lease_until <= now_utc,
        and_(
            TaskRun.lease_until.is_(None),
            TaskRun.started_at.is_not(None),
            TaskRun.started_at <= now_utc - RUN_LEASE,
        ),
    )


def _protect_expired_submissions(session: Session, now_utc: datetime) -> None:
    """Never resend a run that crossed the submit boundary before a crash."""
    session.execute(
        update(TaskRun)
        .where(
            TaskRun.status == "running",
            or_(
                TaskRun.submitted_at.is_not(None),
                TaskRun.stage == "submitted",
            ),
            _expired_lease(now_utc),
        )
        .values(
            status="submitted_unconfirmed",
            stage="submitted",
            finished_at=now_utc,
            lease_until=None,
            error_code="delivery_confirmation_unavailable",
            error_summary="消息已提交，但执行器中断，未自动重复发送",
            submitted_at=func.coalesce(
                TaskRun.submitted_at,
                TaskRun.started_at,
                TaskRun.scheduled_for,
                now_utc,
            ),
        )
    )


def _reclaim_expired_run(
    session: Session, now_utc: datetime, worker_id: str
) -> TaskRun | None:
    candidate_id = session.scalar(
        select(TaskRun.id)
        .where(
            TaskRun.status == "running",
            TaskRun.submitted_at.is_(None),
            TaskRun.stage != "submitted",
            ~exists().where(TaskHealthRetry.run_id == TaskRun.id),
            _expired_lease(now_utc),
        )
        .order_by(TaskRun.started_at, TaskRun.id)
        .limit(1)
    )
    if candidate_id is None:
        return None
    claimed = session.execute(
        update(TaskRun)
        .where(
            TaskRun.id == candidate_id,
            TaskRun.status == "running",
            TaskRun.submitted_at.is_(None),
            TaskRun.stage != "submitted",
            _expired_lease(now_utc),
        )
        .values(
            worker_id=worker_id,
            attempt=func.coalesce(TaskRun.attempt, 1) + 1,
            lease_until=now_utc + RUN_LEASE,
            started_at=now_utc,
            finished_at=None,
            stage="reclaimed",
            error_code=None,
            error_summary=None,
        )
    )
    if claimed.rowcount != 1:
        return None
    session.expire_all()
    return session.get(TaskRun, candidate_id)


def claim_next_due_task(
    session: Session, now: datetime, worker_id: str
) -> TaskRun | None:
    now_utc = _utc(now)
    _protect_expired_submissions(session, now_utc)
    from spark_console.health_retry import claim as claim_health_retry
    recovery = claim_health_retry(session, now_utc, worker_id)
    if recovery is not None:
        return recovery
    reclaimed = _reclaim_expired_run(session, now_utc, worker_id)
    if reclaimed is not None:
        return reclaimed

    recent_task = aliased(SparkTask)
    last_owner_dispatch = select(func.max(TaskRun.started_at)).join(recent_task,TaskRun.task_id==recent_task.id).where(recent_task.owner_user_id==SparkTask.owner_user_id).correlate(SparkTask).scalar_subquery()
    task = session.scalar(
        select(SparkTask)
        .join(User, User.id == SparkTask.owner_user_id)
        .join(
            DouyinAccount,
            and_(
                DouyinAccount.id == SparkTask.douyin_account_id,
                DouyinAccount.owner_user_id == SparkTask.owner_user_id,
            ),
        )
        .outerjoin(
            UserSubscription,
            UserSubscription.user_id == SparkTask.owner_user_id,
        )
        .where(
            User.status == "active",
            or_(
                User.role == "admin",
                and_(
                    UserSubscription.valid_from <= now_utc,
                    UserSubscription.valid_until > now_utc,
                ),
            ),
            SparkTask.enabled.is_(True),
            DouyinAccount.validation_state.not_in(('temporary', 'verification', 'invalid')),
            ~exists().where(AccountOperation.account_id == DouyinAccount.id, AccountOperation.expires_at > now_utc),
            ~exists().where(DouyinLoginSession.account_id == DouyinAccount.id, DouyinLoginSession.slot.is_not(None),
                DouyinLoginSession.status.in_(('queued','loading_qr','awaiting_scan','confirming')), DouyinLoginSession.expires_at > now_utc),
            SparkTask.douyin_account_id.is_not(None),
            SparkTask.next_run_at.is_not(None),
            SparkTask.next_run_at <= now_utc,
        )
        .order_by(SparkTask.next_run_at, func.coalesce(last_owner_dispatch,datetime(1970,1,1,tzinfo=timezone.utc)), SparkTask.id)
        .limit(1)
    )
    if task is None:
        return None
    scheduled_for = _utc(task.next_run_at)
    previous_retry = session.scalar(
        select(TaskRun)
        .where(
            TaskRun.task_id == task.id,
            TaskRun.status == "retrying",
            TaskRun.retry_after == task.next_run_at,
            TaskRun.retry_after <= now_utc,
        )
        .order_by(TaskRun.attempt.desc(), TaskRun.finished_at.desc())
        .limit(1)
    )
    run = TaskRun(
        task_id=task.id,
        scheduled_for=scheduled_for,
        status="running",
        stage="claimed",
        worker_id=worker_id,
        attempt=(previous_retry.attempt + 1) if previous_retry else 1,
        lease_until=now_utc + RUN_LEASE,
        started_at=now_utc,
    )
    session.add(run)
    task.next_run_at = compute_next_run(task.send_time, now_utc)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return None
    return run


def finish_run(
    run: TaskRun,
    status: str,
    stage: str,
    now: datetime,
    error_code: str | None = None,
    error_summary: str | None = None,
    retry_after: datetime | None = None,
) -> TaskRun:
    now_utc = _utc(now)
    run.status = status
    run.stage = stage
    run.finished_at = now_utc
    run.lease_until = None
    run.retry_after = _utc(retry_after) if retry_after else None
    if status == "submitted_unconfirmed" and run.submitted_at is None:
        run.submitted_at = now_utc
    run.error_code = error_code
    run.error_summary = error_summary[:240] if error_summary else None
    return run
