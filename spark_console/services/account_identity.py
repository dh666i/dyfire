"""Bounded enrollment of legacy identities; never re-login or send a message."""
from sqlalchemy import select, exists, func
from sqlalchemy.orm import Session

from core.account_profile import normalize_platform_user_id, probe_saved_profile
from spark_console.credentials import CredentialPayload
from spark_console.db import session_scope
from spark_console.models import DouyinAccount, DouyinAccountIdentity, AccountOperation, SparkTask, utc_now
from spark_console.services.accounts import AccountService, AccountIdentityAlreadyBound, AccountIdentityMismatch
from spark_console.services.audits import AuditService
from spark_console.services import Conflict


def _original_credentials(engine, cipher, account_id):
    with Session(engine) as db:
        account = db.get(DouyinAccount, account_id)
        operation = db.get(AccountOperation, account_id)
        if operation and operation.expires_at.replace(tzinfo=utc_now().tzinfo) > utc_now():
            return None
        expected = (bytes(account.encrypted_cookies), bytes(account.cookie_nonce), account.cookie_version)
        raw = AccountService(db, cipher, AuditService(db)).decrypt_for_worker(account_id)
        try:
            payload = CredentialPayload.parse(bytes(raw), account.cookie_version)
            state = payload.context_options().get("storage_state")
            if state is None:
                state = {"cookies": payload.cookies_to_add(), "origins": []}
            return expected, state
        finally:
            raw[:] = b"\0" * len(raw)
            raw.clear()


async def enroll_legacy_identities(engine, cipher, *, apply=False, limit=100, active_only=False, reader=None):
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("一次核对的账号数量须为1–100")
    reader = reader or probe_saved_profile
    with Session(engine) as db:
        query = select(DouyinAccount.id).outerjoin(
            DouyinAccountIdentity, DouyinAccountIdentity.account_id == DouyinAccount.id
        ).where(DouyinAccountIdentity.douyin_user_id.is_(None))
        if active_only:
            query = query.where(exists().where(SparkTask.douyin_account_id == DouyinAccount.id, SparkTask.enabled.is_(True)))
        ids = list(db.scalars(query.order_by(DouyinAccount.id).limit(limit)))
    results, claims, seen = [], [], {}
    for account_id in ids:
        try:
            original = _original_credentials(engine, cipher, account_id)
        except Exception:
            results.append({"account_id": account_id, "result": "credentials_unreadable"})
            continue
        if original is None:
            results.append({"account_id": account_id, "result": "busy"})
            continue
        expected, state = original
        try:
            profile = await reader(state)
            uid = normalize_platform_user_id(profile.get("platform_user_id"))
        except Exception:
            uid = None
        finally:
            if isinstance(state, dict):
                state.clear()
        result = {"account_id": account_id, "result": "verified" if uid else "unavailable"}
        results.append(result)
        if uid is None:
            continue
        with Session(engine) as db:
            other = db.scalar(select(DouyinAccountIdentity.account_id).where(
                DouyinAccountIdentity.douyin_user_id == uid,
                DouyinAccountIdentity.account_id != account_id,
            ).limit(1))
        if other or uid in seen:
            result["result"] = "identity_conflict"
            if uid in seen:
                seen[uid]["result"] = "identity_conflict"
        else:
            seen[uid] = result
        claims.append((account_id, expected, uid, result))
    if apply:
        for account_id, expected, uid, result in claims:
            if result["result"] != "verified":
                continue
            try:
                with session_scope(engine) as db:
                    AccountService(db, cipher, AuditService(db)).record_identity_if_current(account_id, expected, uid)
                    AuditService(db).write(None, "account.identity_verified", "douyin_account", account_id)
                result["result"] = "enrolled"
            except (AccountIdentityAlreadyBound, AccountIdentityMismatch):
                result["result"] = "identity_conflict"
            except Conflict:
                result["result"] = "credentials_changed"
    with Session(engine) as db:
        pending_query = select(DouyinAccount.id).outerjoin(
            DouyinAccountIdentity, DouyinAccountIdentity.account_id == DouyinAccount.id
        ).where(DouyinAccountIdentity.douyin_user_id.is_(None))
        pending_count = db.scalar(select(func.count()).select_from(pending_query.subquery()))
        active_pending = db.scalar(pending_query.where(exists().where(
            SparkTask.douyin_account_id == DouyinAccount.id, SparkTask.enabled.is_(True)
        )).limit(1))
    return {"applied": apply, "pending_accounts": pending_count,
            "ready_for_new_bindings": active_pending is None, "accounts": results}
