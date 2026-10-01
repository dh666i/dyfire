"""Recovery policy for temporary login checks; never a generic resend queue."""
from datetime import timedelta, timezone
import os
from sqlalchemy import select, update
from spark_console.models import (
    TaskHealthRetry, TaskRun, SparkTask, DouyinAccount, User, UserSubscription,
    AccountOperation, DouyinLoginSession,
)
from spark_console.worker_sharding import validate_shard

WINDOW = timedelta(minutes=max(10, int(os.environ.get("SPARK_HEALTH_RETRY_WINDOW_MINUTES", "30"))))
MAX_ATTEMPTS = 3  # Initial execution plus at most two retries.
FLAKE_ATTEMPT_CAP = 7  # Chat-page empty-list flakes may keep retrying until the window ends.
CHAT_FLAKE = "未检测到已登录会话列表"


def retry_wait(attempt):
    """Backoff after a failed login check so retries do not hammer Douyin."""
    first = max(1, int(os.environ.get("SPARK_HEALTH_RETRY_WAIT_1_MINUTES", "3")))
    second = max(1, int(os.environ.get("SPARK_HEALTH_RETRY_WAIT_2_MINUTES", "8")))
    minutes = first if max(1, int(attempt or 1)) <= 1 else second
    return timedelta(minutes=minutes)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def invalid_reason(db, run, pending, now, *, require_valid=False):
    if run.submitted_at is not None or run.stage == "submitted":
        return "health_retry_submitted", "已进入发送步骤，不会自动重发"
    if aware(now) >= aware(pending.expires_at):
        return "health_retry_expired", f"超过原计划 {int(WINDOW.total_seconds() // 60)} 分钟恢复窗口，本次不再补发"
    if run.attempt > MAX_ATTEMPTS:
        if not keep_retrying_chat_flake(run, pending, now, pending.last_reason):
            return "health_retry_exhausted", "已达到最多 2 次重试，本次停止"
    task = db.get(SparkTask, run.task_id)
    if not task or not task.enabled or aware(task.updated_at) != aware(pending.task_revision):
        return "health_retry_cancelled", "任务已暂停或修改，取消本次恢复重试"
    user = db.get(User, task.owner_user_id)
    sub = db.get(UserSubscription, task.owner_user_id)
    if not user or user.status != "active" or (user.role != "admin" and (
        not sub or not aware(sub.valid_from) <= aware(now) < aware(sub.valid_until)
    )):
        return "health_retry_cancelled", "账号或会员当前不可用，取消本次恢复重试"
    account = db.get(DouyinAccount, task.douyin_account_id) if task.douyin_account_id else None
    if not account or account.owner_user_id != task.owner_user_id or account.validation_state in ("verification", "invalid"):
        return "health_retry_cancelled", "账号需要人工验证或已失效，本次不再自动补发"
    if db.scalar(select(DouyinLoginSession.id).where(
        DouyinLoginSession.account_id == account.id,
        DouyinLoginSession.slot.is_not(None),
        DouyinLoginSession.status.in_(("queued","loading_qr","awaiting_scan","confirming")),
        DouyinLoginSession.expires_at > now,
    ).limit(1)):
        return "health_retry_cancelled", "账号正在重新登录，取消本次自动补发"
    if require_valid and account.validation_state != "valid":
        return "health_retry_cancelled", "账号尚未复核正常，本次暂不发送"
    return None


def keep_retrying_chat_flake(run, pending, now, reason):
    text = str(reason or (pending.last_reason if pending is not None else "") or "")
    if CHAT_FLAKE not in text:
        return False
    if run.attempt >= FLAKE_ATTEMPT_CAP:
        return False
    remaining = aware(pending.expires_at) - aware(now)
    return remaining > timedelta(seconds=30)


def original_schedule(db, run):
    origin = aware(run.scheduled_for)
    for _ in range(MAX_ATTEMPTS):
        prior = db.scalar(select(TaskRun).where(TaskRun.task_id == run.task_id,
            TaskRun.status == "retrying", TaskRun.retry_after == origin,
            TaskRun.scheduled_for < origin).order_by(TaskRun.scheduled_for).limit(1))
        if prior is None:
            break
        origin = aware(prior.scheduled_for)
    return origin


def queue(db, task, run, now, reason):
    from spark_console.scheduler import finish_run
    pending = db.get(TaskHealthRetry, run.id)
    if pending is None:
        db.flush()
        pending = TaskHealthRetry(run_id=run.id, task_revision=task.updated_at,
            expires_at=original_schedule(db, run) + WINDOW, last_reason=reason[:240])
        db.add(pending)
    pending.last_reason = reason[:240]
    invalid = invalid_reason(db, run, pending, now)
    if invalid or (run.attempt >= MAX_ATTEMPTS and not keep_retrying_chat_flake(run, pending, now, reason)):
        code, message = invalid or ("health_retry_exhausted", "已重试 2 次仍未通过登录检测，本次停止")
        return finish_run(run, "failed", "authenticating", now, code, message)
    delay = retry_wait(run.attempt)
    remaining = aware(pending.expires_at) - aware(now)
    if delay >= remaining:
        delay = max(timedelta(seconds=30), remaining - timedelta(seconds=15))
    minutes = max(1, int(delay.total_seconds() // 60))
    return finish_run(run, "retrying", "authenticating", now,
        "health_retry_waiting", f"发送前登录检测暂时异常，{minutes} 分钟后重试；原计划后 {int(WINDOW.total_seconds() // 60)} 分钟内继续尝试",
        retry_after=now + delay)


def claim(db, now, worker_id, *, shard=0, shards=1):
    from spark_console.scheduler import finish_run, RUN_LEASE
    shard, shards = validate_shard(shard, shards)
    # Only records explicitly created by the new policy are eligible. Historical
    # failed runs never become candidates after an upgrade.
    rows = db.execute(select(TaskRun, TaskHealthRetry).join(
        TaskHealthRetry, TaskHealthRetry.run_id == TaskRun.id
    ).join(SparkTask, SparkTask.id == TaskRun.task_id).join(
        DouyinAccount, DouyinAccount.id == SparkTask.douyin_account_id
    ).where(TaskRun.status.in_(("retrying", "running")),
        DouyinAccount.dispatch_key % shards == shard).order_by(
        TaskRun.scheduled_for, TaskRun.attempt, TaskRun.id
    )).all()
    for run, pending in rows:
        if run.status == "running" and run.lease_until and aware(run.lease_until) > aware(now):
            continue
        if run.status == "running":
            from spark_console.run_attempts import save
            save(run, "failed", run.stage, now, "执行租约超时，发送未提交")
        waiting = (
            run.status == "retrying"
            and run.retry_after is not None
            and aware(run.retry_after) > aware(now)
            and aware(now) < aware(pending.expires_at)
        )
        if waiting:
            continue
        invalid = invalid_reason(db, run, pending, now)
        if invalid or (run.attempt >= MAX_ATTEMPTS and not keep_retrying_chat_flake(run, pending, now, pending.last_reason)):
            code, message = invalid or ("health_retry_exhausted", "已达到最多 2 次重试，本次停止")
            status = "submitted_unconfirmed" if code == "health_retry_submitted" else ("skipped" if run.attempt == 0 else "failed")
            finish_run(run, status, "submitted" if status == "submitted_unconfirmed" else "authenticating", now, code, message)
            continue
        task = db.get(SparkTask, run.task_id)
        account = db.get(DouyinAccount, task.douyin_account_id)
        if account.validation_state != "valid" or (run.retry_after and aware(run.retry_after) > aware(now)):
            continue
        if db.scalar(select(AccountOperation.account_id).where(
            AccountOperation.account_id == account.id, AccountOperation.expires_at > now)):
            continue
        claimed = db.execute(update(TaskRun).where(
            TaskRun.id == run.id, TaskRun.status == run.status,
            TaskRun.attempt == run.attempt, TaskRun.submitted_at.is_(None),
        ).values(status="running", stage="health_retry", worker_id=worker_id,
            attempt=run.attempt+1, started_at=now, finished_at=None,
            lease_until=now+RUN_LEASE, retry_after=None, error_code=None, error_summary=None
        ).execution_options(synchronize_session=False))
        if claimed.rowcount == 1:
            db.flush()
            db.refresh(run)
            return run
    return None


def queue_blocked_tasks(db, now):
    """Make due tasks visible while their account awaits a read-only recheck."""
    from spark_console.scheduler import compute_next_run, finish_run
    tasks = db.scalars(select(SparkTask).join(DouyinAccount,
        DouyinAccount.id == SparkTask.douyin_account_id).where(
        SparkTask.enabled.is_(True), SparkTask.next_run_at <= now,
        DouyinAccount.validation_state.in_(("temporary", "verification", "invalid"))
    ).order_by(SparkTask.next_run_at, SparkTask.id)).all()
    for task in tasks:
        queue_waiting_task(db, task, now)
    db.flush()


def queue_waiting_task(db, task, now):
    from spark_console.scheduler import compute_next_run, finish_run
    scheduled = aware(task.next_run_at)
    existing = db.scalar(select(TaskRun).where(TaskRun.task_id == task.id,
                                              TaskRun.scheduled_for == scheduled))
    if existing:
        return
    prior = db.scalar(select(TaskRun).where(TaskRun.task_id == task.id,
        TaskRun.status == "retrying", TaskRun.retry_after == scheduled).order_by(TaskRun.attempt.desc()).limit(1))
    task.next_run_at = compute_next_run(task.send_time, now)
    db.flush()
    run = TaskRun(task_id=task.id, scheduled_for=scheduled, attempt=prior.attempt if prior else 0,
                  status="retrying", stage="authenticating", retry_after=now)
    db.add(run)
    db.flush()
    pending = TaskHealthRetry(run_id=run.id, task_revision=task.updated_at,
        expires_at=original_schedule(db, run)+WINDOW, last_reason="账号暂时异常，尚未开始发送")
    db.add(pending)
    invalid = invalid_reason(db, run, pending, now)
    if invalid:
        finish_run(run, "skipped", "authenticating", now, *invalid)
    else:
        run.error_code = "health_retry_waiting"
        run.error_summary = f"等待账号复核，尚未开始发送；超过计划 {int(WINDOW.total_seconds() // 60)} 分钟将跳过并保留记录"
    db.flush()
