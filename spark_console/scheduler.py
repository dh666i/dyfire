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
from spark_console.worker_sharding import validate_shard


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


def _protect_expired_submissions(
    session: Session,
    now_utc: datetime,
    *,
    shard: int = 0,
    shards: int = 1,
) -> None:
    """Never resend a run that crossed the submit boundary before a crash."""
    shard, shards = validate_shard(shard, shards)
    statement = update(TaskRun).where(
        TaskRun.status == "running",
        or_(
            TaskRun.submitted_at.is_not(None),
            TaskRun.stage == "submitted",
        ),
        _expired_lease(now_utc),
    )
    if shards > 1:
        owned_run_ids = (
            select(TaskRun.id)
            .join(SparkTask, SparkTask.id == TaskRun.task_id)
            .outerjoin(
                DouyinAccount,
                DouyinAccount.id == SparkTask.douyin_account_id,
            )
            .where(
                or_(
                    DouyinAccount.dispatch_key % shards == shard,
                    and_(
                        shard == 0,
                        DouyinAccount.dispatch_key.is_(None),
                    ),
                )
            )
        )
        statement = statement.where(TaskRun.id.in_(owned_run_ids))
    session.execute(
        statement.values(
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
    session: Session, now_utc: datetime, worker_id: str, *, shard: int = 0, shards: int = 1
) -> TaskRun | None:
    shard, shards = validate_shard(shard, shards)
    candidate_id = session.scalar(
        select(TaskRun.id)
        .join(SparkTask, SparkTask.id == TaskRun.task_id)
        .join(DouyinAccount, DouyinAccount.id == SparkTask.douyin_account_id)
        .where(
            TaskRun.status == "running",
            TaskRun.submitted_at.is_(None),
            TaskRun.stage != "submitted",
            ~exists().where(TaskHealthRetry.run_id == TaskRun.id),
            _expired_lease(now_utc),
            DouyinAccount.dispatch_key % shards == shard,
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


def _close_superseded_retries(
    session: Session,
    now_utc: datetime,
    *,
    shard: int = 0,
    shards: int = 1,
) -> None:
    """Close retry rows that already have a later execution for the task.

    Generic retries create one ``TaskRun`` per attempt, while health retries
    reuse the same row.  When a later daily run succeeds (or reaches a final
    state), the older generic rows must stop appearing as active queue work.
    They are still retained as failed attempts and are never replayed.
    """
    shard, shards = validate_shard(shard, shards)
    statement = select(TaskRun).where(
        TaskRun.status == "retrying", TaskRun.submitted_at.is_(None)
    )
    if shards > 1:
        statement = (
            statement.outerjoin(SparkTask, SparkTask.id == TaskRun.task_id)
            .outerjoin(
                DouyinAccount,
                DouyinAccount.id == SparkTask.douyin_account_id,
            )
            .where(
                or_(
                    DouyinAccount.dispatch_key % shards == shard,
                    and_(
                        shard == 0,
                        DouyinAccount.dispatch_key.is_(None),
                    ),
                )
            )
        )
    retrying = session.scalars(
        statement.order_by(TaskRun.scheduled_for, TaskRun.id)
    ).all()
    for run in retrying:
        later = session.scalar(
            select(TaskRun.id)
            .where(
                TaskRun.task_id == run.task_id,
                TaskRun.id != run.id,
                TaskRun.scheduled_for > run.scheduled_for,
                TaskRun.status != "retrying",
            )
            .limit(1)
        )
        if later is None:
            continue
        finish_run(
            run,
            "failed",
            run.stage or "selecting_target",
            now_utc,
            "retry_superseded",
            "该次重试已由后续执行记录接替，未进入发送步骤",
        )


def _due_task_statement(now_utc: datetime, *, shard: int = 0, shards: int = 1):
    """Build the ordered query for the next scheduler-eligible task.

    Keeping this query separate lets PostgreSQL add a row-level claim lock
    without changing the eligibility or fairness rules used by SQLite.
    """
    recent_task = aliased(SparkTask)
    last_owner_dispatch = (
        select(func.max(TaskRun.started_at))
        .join(recent_task, TaskRun.task_id == recent_task.id)
        .where(recent_task.owner_user_id == SparkTask.owner_user_id)
        .correlate(SparkTask)
        .scalar_subquery()
    )
    shard, shards = validate_shard(shard, shards)
    return (
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
            DouyinAccount.validation_state.not_in(
                ("temporary", "verification", "invalid")
            ),
            ~exists().where(
                AccountOperation.account_id == DouyinAccount.id,
                AccountOperation.expires_at > now_utc,
            ),
            ~exists().where(
                DouyinLoginSession.account_id == DouyinAccount.id,
                DouyinLoginSession.slot.is_not(None),
                DouyinLoginSession.status.in_(
                    ("queued", "loading_qr", "awaiting_scan", "confirming")
                ),
                DouyinLoginSession.expires_at > now_utc,
            ),
            SparkTask.douyin_account_id.is_not(None),
            SparkTask.next_run_at.is_not(None),
            SparkTask.next_run_at <= now_utc,
            DouyinAccount.dispatch_key % shards == shard,
        )
        .order_by(
            SparkTask.next_run_at,
            func.coalesce(
                last_owner_dispatch,
                datetime(1970, 1, 1, tzinfo=timezone.utc),
            ),
            SparkTask.id,
        )
        .limit(1)
    )


def _apply_due_task_claim_lock(statement, dialect_name: str):
    """Use PostgreSQL's non-blocking row claim while preserving SQLite SQL.

    ``OF SparkTask`` is intentional: a scheduler worker claims one task row,
    and should not lock the joined user, account, or subscription rows.  The
    lock is held until the surrounding session commits after ``TaskRun`` is
    inserted and ``next_run_at`` is advanced.
    """
    if dialect_name == "postgresql":
        return statement.with_for_update(of=SparkTask, skip_locked=True)
    return statement


def claim_next_due_task(
    session: Session, now: datetime, worker_id: str, *, shard: int = 0, shards: int = 1
) -> TaskRun | None:
    shard, shards = validate_shard(shard, shards)
    now_utc = _utc(now)
    _close_superseded_retries(session, now_utc, shard=shard, shards=shards)
    from spark_console.health_retry import queue_blocked_tasks
    queue_blocked_tasks(session, now_utc, shard=shard, shards=shards)
    _protect_expired_submissions(session, now_utc, shard=shard, shards=shards)
    from spark_console.health_retry import claim as claim_health_retry
    recovery = claim_health_retry(session, now_utc, worker_id, shard=shard, shards=shards)
    if recovery is not None:
        return recovery
    reclaimed = _reclaim_expired_run(session, now_utc, worker_id, shard=shard, shards=shards)
    if reclaimed is not None:
        return reclaimed

    due_task = _apply_due_task_claim_lock(
        _due_task_statement(now_utc, shard=shard, shards=shards),
        session.get_bind().dialect.name,
    )
    task = session.scalar(due_task)
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
    from spark_console.run_attempts import save
    from spark_console.run_events import record as record_event
    save(run, status, stage, now_utc, error_summary)
    record_event(
        run,
        "finished" if status not in {"retrying", "running"} else status,
        stage,
        error_summary or "执行阶段完成",
        now_utc,
    )
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
