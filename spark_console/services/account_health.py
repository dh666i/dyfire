"""Bounded read-only rechecks, account operation leases, and credential CAS."""
from datetime import timedelta, timezone
from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from spark_console.models import AccountHealth, AccountOperation, DouyinAccount, DouyinLoginSession, SparkTask, utc_now
from spark_console.services.audits import AuditService

BLOCKED = ('temporary', 'verification', 'invalid')
LABELS = {'valid': '正常', 'unknown': '待验证', 'temporary': '暂时异常', 'verification': '需要验证', 'invalid': '确认失效', 'relogin': '正在重新登录'}


def snapshot(account):
    return (bytes(account.encrypted_cookies), bytes(account.cookie_nonce), account.cookie_version)


def acquire(db, account_id, token, kind, now=None, seconds=120):
    now = now or utc_now()
    db.execute(delete(AccountOperation).where(AccountOperation.account_id == account_id, AccountOperation.expires_at <= now))
    try:
        with db.begin_nested():
            db.add(AccountOperation(account_id=account_id, token=token, kind=kind, expires_at=now+timedelta(seconds=seconds)))
            db.flush()
    except IntegrityError:
        return False
    return True


def release(db, account_id, token):
    db.execute(delete(AccountOperation).where(AccountOperation.account_id == account_id, AccountOperation.token == token))


def is_relogging(db, account_id):
    return db.scalar(select(DouyinLoginSession.id).where(DouyinLoginSession.account_id == account_id,
        DouyinLoginSession.slot.is_not(None), DouyinLoginSession.status.in_(['queued','loading_qr','awaiting_scan','confirming']),
        DouyinLoginSession.expires_at > utc_now()).limit(1)) is not None


def record(db, account_id, expected, state, reason, *, now=None, recheck=False, count_attempt=True):
    now = now or utc_now()
    matched = db.execute(update(DouyinAccount).where(DouyinAccount.id == account_id,
        DouyinAccount.encrypted_cookies == expected[0], DouyinAccount.cookie_nonce == expected[1],
        DouyinAccount.cookie_version == expected[2]).values(updated_at=now))
    if matched.rowcount != 1:
        return False
    account = db.get(DouyinAccount, account_id)
    health = db.get(AccountHealth, account_id)
    if health is None:
        health = AccountHealth(account_id=account_id, attempts=0, negative_checks=0)
        db.add(health)
    if recheck and count_attempt:
        health.attempts += 1
    elif account.validation_state not in BLOCKED:
        health.attempts = health.negative_checks = 0
    # Only repeated explicit negative evidence confirms logout. A redirect is never enough.
    health.negative_checks = health.negative_checks + 1 if state == 'logged_out' else 0
    resolved = ('invalid' if health.negative_checks >= 2 else 'temporary') if state == 'logged_out' else state
    previous = account.validation_state
    account.validation_state = resolved
    health.reason, health.checked_at = reason[:240], now
    health.next_check_at = None
    if resolved == 'valid':
        account.last_verified_at = now
        health.attempts = health.negative_checks = 0
        if previous in BLOCKED:
            skip_missed(db, account_id, now, preserve_recent=previous == "temporary")
    elif resolved == 'temporary' and health.attempts < 2:
        health.next_check_at = now + timedelta(seconds=30 if health.attempts == 0 else 120)
    AuditService(db).write(account.owner_user_id, 'account.health.'+resolved, 'douyin_account', account_id,
        detail=f'{reason[:165]}；复核 {health.attempts}/2；来源 {"复核" if recheck else "操作"}')
    return True


def skip_missed(db, account_id, now, *, preserve_recent=False):
    from spark_console.scheduler import compute_next_run
    for task in db.scalars(select(SparkTask).where(SparkTask.douyin_account_id == account_id,
        SparkTask.enabled.is_(True), SparkTask.next_run_at <= now)):
        from spark_console.health_retry import aware, WINDOW
        from spark_console.models import TaskRun
        from spark_console.scheduler import finish_run
        scheduled = aware(task.next_run_at)
        if preserve_recent and aware(now) < scheduled + WINDOW:
            from spark_console.health_retry import queue_waiting_task
            queue_waiting_task(db, task, now)
            continue
        existing = db.scalar(select(TaskRun).where(TaskRun.task_id == task.id,
                                                   TaskRun.scheduled_for == scheduled))
        if existing is None:
            run = TaskRun(task_id=task.id, scheduled_for=scheduled, attempt=0)
            db.add(run)
            finish_run(run, "skipped", "authenticating", now,
                       "account_recovery_skipped", "账号恢复时已超过发送窗口或完成重新登录，本次未发送")
        task.next_run_at = compute_next_run(task.send_time, now)


def recovered(db, account):
    now = utc_now()
    health = db.get(AccountHealth, account.id)
    if health:
        health.next_check_at = None
        health.attempts = health.negative_checks = 0
        health.checked_at, health.reason = now, '已完成抖音登录验证'
    from spark_console.models import TaskRun, TaskHealthRetry
    from spark_console.scheduler import finish_run
    for run in db.scalars(select(TaskRun).join(TaskHealthRetry, TaskHealthRetry.run_id == TaskRun.id)
        .join(SparkTask, SparkTask.id == TaskRun.task_id).where(
            SparkTask.douyin_account_id == account.id, TaskRun.status == "retrying")):
        finish_run(run, "skipped" if run.attempt == 0 else "failed", "authenticating", now,
                   "health_retry_reauth", "账号已重新登录，取消旧会话下的恢复任务")
    skip_missed(db, account.id, now)


def views(db, accounts):
    accounts = list(accounts)
    account_ids = [account.id for account in accounts]
    if not account_ids:
        return {}
    now = utc_now()
    health_by_id = {
        item.account_id: item
        for item in db.scalars(
            select(AccountHealth).where(AccountHealth.account_id.in_(account_ids))
        ).all()
    }
    operation_by_id = {
        item.account_id: item
        for item in db.scalars(
            select(AccountOperation).where(
                AccountOperation.account_id.in_(account_ids),
                AccountOperation.expires_at > now,
            )
        ).all()
    }
    relogging_ids = set(
        db.scalars(
            select(DouyinLoginSession.account_id)
            .where(
                DouyinLoginSession.account_id.in_(account_ids),
                DouyinLoginSession.slot.is_not(None),
                DouyinLoginSession.status.in_(
                    ['queued', 'loading_qr', 'awaiting_scan', 'confirming']
                ),
                DouyinLoginSession.expires_at > now,
            )
        ).all()
    )
    result = {}
    for account in accounts:
        state = 'relogin' if account.id in relogging_ids else account.validation_state
        health = health_by_id.get(account.id)
        operation = operation_by_id.get(account.id)
        operation_expires = operation.expires_at if operation is not None else None
        if operation_expires is not None and operation_expires.tzinfo is None:
            operation_expires = operation_expires.replace(tzinfo=timezone.utc)
        checking = bool(operation and operation.kind == 'health' and operation_expires > now)
        result[account.id] = {'state':state, 'label':('登录异常，待复核' if state == 'invalid' and not health else LABELS.get(state,'待验证')), 'blocked':state in (*BLOCKED,'relogin'),
            'checking':checking,
            'reason':health.reason if health else ('历史失效记录，尚无详细检测依据' if state == 'invalid' else ''),
            'last_verified_at':account.last_verified_at.replace(tzinfo=timezone.utc) if account.last_verified_at else None,
            'checked_at':health.checked_at.replace(tzinfo=timezone.utc) if health and health.checked_at else None,
            'next_check_at':health.next_check_at.replace(tzinfo=timezone.utc) if health and health.next_check_at else None,
            'attempts':health.attempts if health else 0}
    return result
