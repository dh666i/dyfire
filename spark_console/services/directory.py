import asyncio
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update, delete
from core.avatars import safe_avatar_url
from core.web_chat import WebChatLoginRequiredError
from spark_console.db import session_scope
from spark_console.models import (ContactDirectoryJob, DouyinAccount, DouyinAccountProfile,
    DouyinContactAvatar, DouyinContactIdentity, DouyinConversation, SparkTask, SparkTaskTargetIdentity,
    TaskRun, User, utc_now)
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.services.subscriptions import SubscriptionService


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
    return {"id": job.id, "status": job.status, "error": job.error,
            "limit": job.limit, "query": job.query,
            "finished_at": job.finished_at.replace(tzinfo=timezone.utc).isoformat() if job.finished_at else None,
            **(json.loads(job.result_json) if job.result_json else {})}


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
        if not account or not user or account.owner_user_id != user.id or user.status != "active" or (user.role != "admin" and not SubscriptionService(db, AuditService(db)).active(user.id)):
            job.status, job.error, job.finished_at = "failed", "账号或会员暂不可用", now
            return True
        if account.validation_state in BLOCKED:
            job.status, job.error, job.finished_at = 'failed', '请先完成账号复核或登录验证，再同步好友', now
            return True
        if is_relogging(db, account.id) or not acquire(db, account.id, job.id, 'directory', now, 110):
            return False
        claimed = db.execute(update(ContactDirectoryJob).where(ContactDirectoryJob.id == job.id, ContactDirectoryJob.status == "queued")
                             .values(status="running", started_at=now))
        if claimed.rowcount != 1:
            release(db, account.id, job.id)
            return False
        job_id, account_id, query, limit = job.id, account.id, job.query, job.limit
        expected_cookies, expected_nonce, expected_version = account.encrypted_cookies, account.cookie_nonce, account.cookie_version
        try:
            payload = AccountService(db, cipher, AuditService(db)).decrypt_for_worker(account.id)
        except Exception:
            job.status, job.error, job.finished_at = "failed", "登录资料不可用，请重新登录", now
            release(db, account.id, job.id)
            return True
        version = account.cookie_version
    reading = asyncio.create_task(asyncio.wait_for((reader or read_directory)(payload, version, limit, query), timeout=75))
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
                job.result_json = json.dumps({"items": result["items"], "has_more": result.get("has_more", False)}, ensure_ascii=False)
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
        elif isinstance(error, TimeoutError):
            message = "抖音响应超时，请稍后重试"
        with session_scope(engine) as db:
            job = db.get(ContactDirectoryJob, job_id)
            if job and job.status == "running":
                job.status, job.error, job.finished_at = "failed", message, utc_now()
                if isinstance(error, SessionUnavailable):
                    record(db, account_id, (expected_cookies, expected_nonce, expected_version), error.evidence.state, error.evidence.reason)
    finally:
        if not reading.done():
            reading.cancel()
        await asyncio.gather(reading, return_exceptions=True)
        payload[:] = b"\0" * len(payload)
        payload.clear()
        with session_scope(engine) as db:
            release(db, account_id, job_id)
    return True
