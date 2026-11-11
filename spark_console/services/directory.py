import asyncio
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update, delete, exists, not_, func, text
from sqlalchemy.orm import aliased
from core.avatars import safe_avatar_url
from core.chat_errors import WebChatLoginRequiredError
from spark_console.db import session_scope
from spark_console.models import (AccountOperation, ContactDirectoryJob, DouyinAccount, DouyinAccountIdentity,
    DouyinAccountProfile, DouyinContactAvatar, DouyinContactIdentity, DouyinConversation,
    DouyinLoginSession, SparkTask, SparkTaskTargetIdentity, TaskRun, User, utc_now)
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.services.subscriptions import SubscriptionService
from spark_console.services.streaks import record_observations


AUTO_DIRECTORY_SYNC_INTERVAL = timedelta(hours=24)
AUTO_DIRECTORY_SYNC_LIMIT = 100
AUTO_DIRECTORY_QUEUE_LIMIT = 4
AUTO_DIRECTORY_MARKER = '_automatic_directory_sync'


def profile_map(db, account_ids):
    return {p.account_id: p for p in db.scalars(select(DouyinAccountProfile).where(DouyinAccountProfile.account_id.in_(account_ids)))} if account_ids else {}


def save_profile(db, account, values):
    nickname = str(values.get("nickname") or "").strip()[:256]
    if not nickname:
        return
    profile = db.get(DouyinAccountProfile, account.id)
    if profile is None:
        profile = DouyinAccountProfile(account_id=account.id,
            custom_remark=account.display_name not in {"抖音账号", nickname[:64]})
        db.add(profile)
    if not profile.custom_remark:
        account.display_name = nickname[:64]
    profile.nickname = nickname
    if safe_avatar_url(values.get("avatar_url")):
        profile.avatar_url = safe_avatar_url(values["avatar_url"])
    if values.get("unique_id"):
        profile.unique_id = str(values["unique_id"])[:128]
    profile.synced_at = utc_now()


def save_contacts(db, account_id, items):
    for item in items:
        name = str(item.get("name") or item.get("remark_name") or item.get("nickname") or "").strip()[:256]
        uid = str(item.get("sec_uid") or "").strip()[:256]
        if not name:
            continue
        if not db.get(DouyinConversation, (account_id, name)):
            db.add(DouyinConversation(account_id=account_id, display_name=name))
        if not uid:
            continue
        identity = db.get(DouyinContactIdentity, (account_id, uid))
        if identity is None:
            identity = DouyinContactIdentity(account_id=account_id, sec_uid=uid)
            db.add(identity)
        for field in ("nickname", "remark_name", "short_id", "unique_id"):
            if item.get(field):
                setattr(identity, field, str(item[field])[:64 if field == 'short_id' else 128 if field == 'unique_id' else 256])
        if not identity.nickname and not identity.remark_name:
            identity.nickname = name
        url = safe_avatar_url(item.get("avatar_url"))
        if url:
            avatar = db.get(DouyinContactAvatar, (account_id, uid))
            if avatar is None:
                db.add(DouyinContactAvatar(account_id=account_id, sec_uid=uid, avatar_url=url))
            else:
                avatar.avatar_url = url
                avatar.updated_at = utc_now()


def task_avatar_map(db, tasks):
    if not tasks:
        return {}
    rows = db.execute(select(SparkTask.id, DouyinContactAvatar.avatar_url)
        .join(SparkTaskTargetIdentity, SparkTaskTargetIdentity.task_id == SparkTask.id)
        .join(DouyinContactAvatar, (DouyinContactAvatar.account_id == SparkTask.douyin_account_id)
              & (DouyinContactAvatar.sec_uid == SparkTaskTargetIdentity.sec_uid))
        .where(SparkTask.id.in_([t.id for t in tasks]))).all()
    result = {key: safe_avatar_url(url) for key, url in rows if safe_avatar_url(url)}
    bound_ids = set(db.scalars(select(SparkTaskTargetIdentity.task_id).where(SparkTaskTargetIdentity.task_id.in_([t.id for t in tasks]))))
    legacy = [t for t in tasks if t.id not in bound_ids and t.douyin_account_id]
    if legacy:
        contacts = db.execute(select(DouyinContactIdentity, DouyinContactAvatar.avatar_url)
            .outerjoin(DouyinContactAvatar, (DouyinContactAvatar.account_id == DouyinContactIdentity.account_id)
                       & (DouyinContactAvatar.sec_uid == DouyinContactIdentity.sec_uid))
            .where(DouyinContactIdentity.account_id.in_({t.douyin_account_id for t in legacy}))).all()
        for task in legacy:
            matches = [(identity, url) for identity, url in contacts if identity.account_id == task.douyin_account_id
                       and task.target_name in (identity.nickname, identity.remark_name, identity.unique_id, identity.short_id)]
            if len(matches) == 1 and safe_avatar_url(matches[0][1]):
                result[task.id] = safe_avatar_url(matches[0][1])
    return result


def job_view(job):
    if job.status in ('queued', 'running') and job.created_at.replace(tzinfo=timezone.utc) < utc_now() - timedelta(minutes=5):
        return {"id": job.id, "status": "failed", "error": "同步超时，请重试", "limit": job.limit, "query": job.query}
    result = json.loads(job.result_json) if job.result_json else {}
    result.pop(AUTO_DIRECTORY_MARKER, None)
    return {"id": job.id, "status": job.status, "error": job.error,
            "limit": job.limit, "query": job.query,
            "finished_at": job.finished_at.replace(tzinfo=timezone.utc).isoformat() if job.finished_at else None,
            **result}


def is_automatic_directory_job(job):
    if not job or not job.result_json:
        return False
    try:
        return bool(json.loads(job.result_json).get(AUTO_DIRECTORY_MARKER))
    except (TypeError, ValueError):
        return False


def enqueue_due_directory_syncs(engine, *, now=None, queue_limit=AUTO_DIRECTORY_QUEUE_LIMIT):
    """Queue bounded refreshes for valid accounts that have enabled tasks."""
    now = now or utc_now()
    cutoff = now - AUTO_DIRECTORY_SYNC_INTERVAL
    running_task = aliased(SparkTask)
    with session_scope(engine) as db:
        if engine.dialect.name == 'sqlite':
            db.execute(text('BEGIN IMMEDIATE'))
        active_jobs = db.scalar(select(func.count()).select_from(ContactDirectoryJob).where(
            ContactDirectoryJob.status.in_(('queued', 'running')),
        )) or 0
        remaining = max(0, int(queue_limit) - int(active_jobs))
        if remaining == 0:
            return 0

        candidates = db.execute(
            select(DouyinAccount.id, DouyinAccount.owner_user_id)
            .join(User, User.id == DouyinAccount.owner_user_id)
            .where(
                exists().where(
                    SparkTask.douyin_account_id == DouyinAccount.id,
                    SparkTask.enabled.is_(True),
                ),
                DouyinAccount.validation_state == 'valid',
                User.status == 'active',
                not_(exists().where(
                    ContactDirectoryJob.owner_user_id == DouyinAccount.owner_user_id,
                    ContactDirectoryJob.status.in_(('queued', 'running')),
                )),
                not_(exists().where(
                    TaskRun.status == 'running',
                    TaskRun.task_id == running_task.id,
                    running_task.douyin_account_id == DouyinAccount.id,
                )),
                not_(exists().where(
                    ContactDirectoryJob.account_id == DouyinAccount.id,
                    ContactDirectoryJob.created_at >= cutoff,
                )),
                not_(exists().where(
                    AccountOperation.account_id == DouyinAccount.id,
                    AccountOperation.expires_at > now,
                )),
                not_(exists().where(
                    DouyinLoginSession.account_id == DouyinAccount.id,
                    DouyinLoginSession.slot.is_not(None),
                    DouyinLoginSession.status.in_(('queued', 'loading_qr', 'awaiting_scan', 'confirming')),
                    DouyinLoginSession.expires_at > now,
                )),
            )
            .order_by(DouyinAccount.updated_at, DouyinAccount.id)
            .limit(remaining)
            .with_for_update(of=DouyinAccount, skip_locked=True)
        ).all()

        queued = 0
        for account_id, owner_user_id in candidates:
            # Respect the existing free-tier and subscription entitlement rules.
            if not SubscriptionService(db, AuditService(db)).has_task_access(owner_user_id):
                continue
            db.add(ContactDirectoryJob(
                account_id=account_id,
                owner_user_id=owner_user_id,
                limit=AUTO_DIRECTORY_SYNC_LIMIT,
                result_json=json.dumps({AUTO_DIRECTORY_MARKER: True}),
            ))
            queued += 1
        if queued:
            db.flush()
        return queued


async def process_directory_job(engine, cipher, reader=None):
    from spark_console.services.account_health import acquire, release, is_relogging, record, BLOCKED
    from core.session_health import SessionUnavailable
    from spark_console.directory_browser import read_directory
    now = utc_now()
    with session_scope(engine) as db:
        db.execute(update(ContactDirectoryJob).where(ContactDirectoryJob.status.in_(["queued", "running"]),
            ContactDirectoryJob.created_at < now - timedelta(minutes=5)).values(status="failed", error="同步超时，请重试", finished_at=now))
        db.execute(delete(ContactDirectoryJob).where(ContactDirectoryJob.finished_at < now - timedelta(days=7)))
        job = db.scalar(select(ContactDirectoryJob).where(ContactDirectoryJob.status == "queued").order_by(ContactDirectoryJob.created_at).limit(1))
        if not job:
            return False
        # Give active sends priority; this job is read-only and never refreshes cookies.
        if db.scalar(select(TaskRun.id).join(SparkTask).where(SparkTask.douyin_account_id == job.account_id, TaskRun.status == "running").limit(1)):
            return False
        account = db.get(DouyinAccount, job.account_id)
        user = db.get(User, job.owner_user_id)
        if not account or not user or account.owner_user_id != user.id or user.status != "active" or (user.role != "admin" and not SubscriptionService(db, AuditService(db)).has_task_access(user.id)):
            job.status, job.error, job.finished_at = "failed", "账号或会员暂不可用", now
            return True
        if account.validation_state in BLOCKED:
            job.status, job.error, job.finished_at = 'failed', '请先完成账号复核或登录验证，再同步好友', now
            return True
        if is_relogging(db, account.id) or not acquire(db, account.id, job.id, 'directory', now, 145):
            return False
        claimed = db.execute(update(ContactDirectoryJob).where(ContactDirectoryJob.id == job.id, ContactDirectoryJob.status == "queued")
                             .values(status="running", started_at=now))
        if claimed.rowcount != 1:
            release(db, account.id, job.id)
            return False
        job_id, account_id, query, limit = job.id, account.id, job.query, job.limit
        automatic_job = is_automatic_directory_job(job)
        expected_cookies, expected_nonce, expected_version = account.encrypted_cookies, account.cookie_nonce, account.cookie_version
        identity = db.get(DouyinAccountIdentity, account.id)
        trusted_self_uid = identity.douyin_user_id if identity else None
        try:
            payload = AccountService(db, cipher, AuditService(db)).decrypt_for_worker(account.id)
        except Exception:
            job.status, job.error, job.finished_at = "failed", "登录资料不可用，请重新登录", now
            release(db, account.id, job.id)
            return True
        version = account.cookie_version
    operation = (read_directory(payload, version, limit, query, trusted_self_uid=trusted_self_uid)
                 if reader is None else reader(payload, version, limit, query))
    reading = asyncio.create_task(asyncio.wait_for(operation, timeout=120))
    try:
        # Login takes priority over optional directory sync; relinquish the browser promptly.
        from spark_console.models import DouyinLoginSession
        from spark_console.services.scan_sessions import ACTIVE_STATUSES
        while not reading.done():
            await asyncio.wait({reading}, timeout=1.5)
            if reading.done():
                break
            with session_scope(engine) as db:
                pending_login = db.scalar(select(DouyinLoginSession.id).where(
                    DouyinLoginSession.slot.is_not(None), DouyinLoginSession.status.in_(ACTIVE_STATUSES)).limit(1))
                if pending_login:
                    job = db.get(ContactDirectoryJob, job_id)
                    if job and job.status == 'running':
                        job.status, job.started_at = 'queued', None
            if pending_login:
                return True
        result = await reading
        with session_scope(engine) as db:
            job = db.get(ContactDirectoryJob, job_id)
            account = db.get(DouyinAccount, account_id)
            if not job or job.status != "running":
                return True
            if not account or (account.encrypted_cookies, account.cookie_nonce, account.cookie_version) != (expected_cookies, expected_nonce, expected_version):
                job.status, job.error = "failed", "登录状态已更新，请重新同步"
            else:
                save_contacts(db, account_id, result["items"])
                save_profile(db, account, result.get("profile", {}))
                # Optional platform metadata must never be inferred from task
                # delivery or nicknames; the collector only returns validated
                # observations matched by this account's stable sec_uid.
                record_observations(db, account_id, result.get("streaks", []))
                result_payload = {
                    "items": result["items"],
                    "has_more": result.get("has_more", False),
                    "streak_diagnostics": result.get("streak_diagnostics", {}),
                }
                if automatic_job:
                    result_payload[AUTO_DIRECTORY_MARKER] = True
                job.result_json = json.dumps(result_payload, ensure_ascii=False)
                job.status = "succeeded"
            job.finished_at = utc_now()
    except Exception as error:
        message = "同步未完成，请稍后重试"
        if isinstance(error, SessionUnavailable):
            message = error.evidence.reason
        elif isinstance(error, WebChatLoginRequiredError):
            message = "抖音登录已失效或需要验证，请重新登录后同步"
        elif isinstance(error, ValueError) and str(error).startswith("当前抖音页面"):
            message = str(error)
        elif isinstance(error, ValueError) and str(error).startswith("抖音账号身份"):
            message = str(error)
        elif isinstance(error, TimeoutError):
            message = "抖音响应超时，请稍后重试"
        with session_scope(engine) as db:
            job = db.get(ContactDirectoryJob, job_id)
            if job and job.status == "running":
                job.status, job.error, job.finished_at = "failed", message, utc_now()
                if isinstance(error, SessionUnavailable) and error.evidence.state != 'temporary':
                    record(db, account_id, (expected_cookies, expected_nonce, expected_version), error.evidence.state, error.evidence.reason)
                elif isinstance(error, SessionUnavailable):
                    AuditService(db).write(None, 'account.directory.unavailable', 'douyin_account', account_id, detail=message[:240])
    finally:
        if not reading.done():
            reading.cancel()
        await asyncio.gather(reading, return_exceptions=True)
        payload[:] = b"\0" * len(payload)
        payload.clear()
        with session_scope(engine) as db:
            release(db, account_id, job_id)
    return True
