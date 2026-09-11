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
            skip_missed(db, account_id, now)
    elif resolved == 'temporary' and health.attempts < 2:
        health.next_check_at = now + timedelta(seconds=30 if health.attempts == 0 else 120)
    AuditService(db).write(account.owner_user_id, 'account.health.'+resolved, 'douyin_account', account_id,
        detail=f'{reason[:165]}；复核 {health.attempts}/2；来源 {"复核" if recheck else "操作"}')
    return True


def skip_missed(db, account_id, now):
    from spark_console.scheduler import compute_next_run
    for task in db.scalars(select(SparkTask).where(SparkTask.douyin_account_id == account_id,
        SparkTask.enabled.is_(True), SparkTask.next_run_at <= now)):
        task.next_run_at = compute_next_run(task.send_time, now)


def recovered(db, account):
    now = utc_now()
    health = db.get(AccountHealth, account.id)
    if health:
        health.next_check_at = None
        health.attempts = health.negative_checks = 0
        health.checked_at, health.reason = now, '已完成抖音登录验证'
    skip_missed(db, account.id, now)


def views(db, accounts):
    result = {}
    for account in accounts:
        state = 'relogin' if is_relogging(db, account.id) else account.validation_state
        health = db.get(AccountHealth, account.id)
        operation = db.get(AccountOperation, account.id)
        checking = bool(operation and operation.kind == 'health' and operation.expires_at.replace(tzinfo=timezone.utc) > utc_now())
        result[account.id] = {'state':state, 'label':('登录异常，待复核' if state == 'invalid' and not health else LABELS.get(state,'待验证')), 'blocked':state in (*BLOCKED,'relogin'),
            'checking':checking,
            'reason':health.reason if health else ('历史失效记录，尚无详细检测依据' if state == 'invalid' else ''),
            'last_verified_at':account.last_verified_at.replace(tzinfo=timezone.utc) if account.last_verified_at else None,
            'checked_at':health.checked_at.replace(tzinfo=timezone.utc) if health and health.checked_at else None,
            'next_check_at':health.next_check_at.replace(tzinfo=timezone.utc) if health and health.next_check_at else None,
            'attempts':health.attempts if health else 0}
    return result
