import asyncio
from datetime import timedelta
from sqlalchemy import select, exists
from spark_console.db import session_scope
from spark_console.models import AccountHealth, AccountOperation, DouyinAccount, DouyinLoginSession, User, utc_now
from spark_console.services.account_health import acquire, release, snapshot, record, is_relogging
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.services.subscriptions import SubscriptionService
from core.session_health import SessionEvidence, check_saved_session


async def process_health_check(engine, cipher, checker=None):
    now = utc_now()
    with session_scope(engine) as db:
        # A write first serializes admission against send/directory/scan leases on SQLite.
        from sqlalchemy import delete
        db.execute(delete(AccountOperation).where(AccountOperation.expires_at <= now))
        row = db.scalar(select(AccountHealth).join(DouyinAccount).where(AccountHealth.next_check_at <= now,
            AccountHealth.attempts < 2, DouyinAccount.validation_state == 'temporary',
            ~exists().where(AccountOperation.account_id == AccountHealth.account_id, AccountOperation.expires_at > now))
            .order_by(AccountHealth.next_check_at).limit(1))
        if not row or is_relogging(db, row.account_id):
            return False
        account = db.get(DouyinAccount, row.account_id)
        user = db.get(User, account.owner_user_id)
        if not user or user.status != 'active' or (user.role != 'admin' and not SubscriptionService(db, AuditService(db)).has_task_access(user.id)):
            row.next_check_at = None
            return False
        account_id, expected = account.id, snapshot(account)
        import uuid
        token = str(uuid.uuid4())
        if not acquire(db, account_id, token, 'health', now, 150):
            return False
        row.attempts += 1
        row.checked_at = now
        row.reason = f'正在进行第 {row.attempts} 次登录复核'
        row.next_check_at = now + timedelta(seconds=160) if row.attempts < 2 else None
        try:
            raw = AccountService(db, cipher, AuditService(db)).decrypt_for_worker(account_id)
        except Exception:
            record(db, account_id, expected, 'invalid', '登录资料无法解密，需要重新登录', recheck=True, count_attempt=False)
            release(db, account_id, token)
            return True
    reading = asyncio.create_task(asyncio.wait_for((checker or check_saved_session)(raw, expected[2]), timeout=100))
    try:
        while not reading.done():
            await asyncio.wait({reading}, timeout=1)
            if not reading.done():
                with session_scope(engine) as db:
                    if is_relogging(db, account_id):
                        return True
        try:
            evidence = await reading
        except Exception as error:
            evidence = SessionEvidence('temporary', f'复核未完成：{type(error).__name__}，未确认登录失效')
        try:
            with session_scope(engine) as db:
                accepted = record(db, account_id, expected, evidence.state, evidence.reason, recheck=True, count_attempt=False)
                if accepted and evidence.state == 'valid' and evidence.refreshed_storage_state:
                    try:
                        with db.begin_nested():
                            AccountService(db, cipher, AuditService(db)).record_verified(account_id,
                                expected_ciphertext=expected[0], expected_nonce=expected[1], expected_version=expected[2],
                                storage_state=evidence.refreshed_storage_state)
                    except Exception:
                        AuditService(db).write(None, 'account.health.refresh_deferred', 'douyin_account', account_id,
                            detail='复核成功，登录资料更新失败，保留原资料')
        finally:
            if evidence.refreshed_storage_state:
                evidence.refreshed_storage_state.clear()
    finally:
        if not reading.done():
            reading.cancel()
        await asyncio.gather(reading, return_exceptions=True)
        raw[:] = b'\0'*len(raw); raw.clear()
        with session_scope(engine) as db:
            release(db, account_id, token)
    return True
