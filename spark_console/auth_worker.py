from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from sqlalchemy.orm import Session

from spark_console.auth_scanner import (
    BrowserTextDelivery,
    DouyinQrScanner,
    LoginTimedOut,
    QrLoadFailed,
    ScanCancelled,
    VerificationRequired,
)
from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.credentials import CredentialPayload
from core.account_profile import normalize_platform_user_id, probe_saved_profile
from spark_console.db import create_engine_for, create_schema, session_scope
from spark_console.models import DouyinAccount, DouyinAccountIdentity, DouyinLoginSession, ScanStatus, User
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.accounts import (
    AccountIdentityAlreadyBound,
    AccountIdentityMismatch,
    AccountIdentityUnavailable,
    AccountService,
)
from spark_console.services.audits import AuditService
from spark_console.services.scan_sessions import ACTIVE_STATUSES, ScanSessionService
from spark_console.services.subscriptions import SubscriptionService


logger = logging.getLogger(__name__)
AUTH_HEARTBEAT_INTERVAL_SECONDS = 10

ERROR_CODES = {
    QrLoadFailed: "qr_load_failed",
    LoginTimedOut: "login_timeout",
    VerificationRequired: "verification_required",
    ScanCancelled: "cancelled",
}


class _CredentialInvalid(Exception):
    pass


class _EntitlementFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class AuthWorker:
    def __init__(self, settings: Settings, engine, scanner=None, identity_reader=None):
        self.settings = settings
        self.engine = engine
        self.scanner = scanner or DouyinQrScanner()
        self.identity_reader = identity_reader or probe_saved_profile
        key = bytearray(settings.cookie_key_file.read_bytes())
        try:
            self.cipher = CookieCipher(bytes(key))
        finally:
            key[:] = b"\0" * len(key)
            key.clear()
        with session_scope(self.engine) as db:
            service = ScanSessionService(db)
            service.expire_stale()
            service.fail_abandoned_browser_sessions()

    async def run_once(self, stopping: asyncio.Event | None = None) -> bool:
        with session_scope(self.engine) as db:
            scan_service = ScanSessionService(db)
            scan = scan_service.claim_next()
            if scan is None:
                return False
            scan_id = scan.id
            owner_id = scan.owner_user_id
            target_account_id = scan.account_id
            recovery_mode = getattr(scan, "mode", "login") == "recovery"
            expires_at = scan.expires_at
            entitlement_error = self._account_entitlement_error(
                db, owner_id, target_account_id
            )
            if entitlement_error is not None:
                if target_account_id:
                    from spark_console.services.account_health import release
                    release(db, target_account_id, scan_id)
                scan_service.fail(scan_id, entitlement_error)
                logger.warning(
                    "auth scan ended session_id=%s code=%s",
                    scan_id,
                    entitlement_error,
                )
                return True

        def on_qr(png: bytes) -> None:
            with session_scope(self.engine) as db:
                ScanSessionService(db).publish_qr(scan_id, png)

        def on_confirming(_confirmed: bool) -> None:
            with session_scope(self.engine) as db:
                ScanSessionService(db).mark_confirming(scan_id)

        def on_view(png: bytes) -> None:
            with session_scope(self.engine) as db:
                ScanSessionService(db).publish_view(scan_id, png)

        def on_needs_verification(
            details: dict[str, object] | bool = True,
        ) -> None:
            with session_scope(self.engine) as db:
                ScanSessionService(db).mark_needs_verification(scan_id, details)

        def next_interaction():
            with session_scope(self.engine) as db:
                return ScanSessionService(db).claim_interaction(
                    scan_id, self.cipher
                )

        def on_interaction_result(result: dict[str, object]) -> None:
            if isinstance(result,dict) and result.get('kind')=='browser':
                with session_scope(self.engine) as db:
                    ScanSessionService(db).finish_browser_action(scan_id,result.get('interaction_id'),result.get('delivery'))
                return
            if not isinstance(result, dict) or result.get("kind") != "text":
                return
            interaction_id = result.get("interaction_id")
            if isinstance(interaction_id, bool) or not isinstance(
                interaction_id, int
            ):
                raise ValidationError("invalid_interaction")
            delivery = result.get("delivery")
            if isinstance(delivery, BrowserTextDelivery):
                delivery = delivery.value
            if delivery == BrowserTextDelivery.NOT_READY.value:
                return
            with session_scope(self.engine) as db:
                service = ScanSessionService(db)
                if delivery == BrowserTextDelivery.DELIVERED.value:
                    service.mark_text_interaction_delivered(
                        scan_id, interaction_id
                    )
                elif delivery == BrowserTextDelivery.UNCERTAIN.value:
                    service.mark_text_interaction_uncertain(
                        scan_id, interaction_id
                    )
                elif delivery == BrowserTextDelivery.FAILED.value:
                    service.mark_text_interaction_failed(
                        scan_id, interaction_id
                    )
                else:
                    raise ValidationError("invalid_interaction")

        def cancelled() -> bool:
            if stopping is not None and stopping.is_set():
                return True
            with Session(self.engine) as db:
                persisted = db.get(DouyinLoginSession, scan_id)
                return (
                    persisted is None
                    or persisted.slot is None
                    or ScanStatus(persisted.status) not in ACTIVE_STATUSES
                )

        scanned = None
        storage_state = None
        recovery_storage_state = None
        try:
            if target_account_id:
                with session_scope(self.engine) as db:
                    account = db.get(DouyinAccount, target_account_id)
                    identity = db.get(DouyinAccountIdentity, target_account_id)
                    needs_identity = not (identity and identity.douyin_user_id)
                    expected = (bytes(account.encrypted_cookies), bytes(account.cookie_nonce), account.cookie_version)
                    raw = AccountService(db, self.cipher, AuditService(db)).decrypt_for_worker(target_account_id)
                    try:
                        payload = CredentialPayload.parse(bytes(raw), account.cookie_version)
                        original_state = payload.context_options().get("storage_state")
                        if original_state is None and payload.cookies_to_add():
                            original_state = {"cookies": payload.cookies_to_add(), "origins": []}
                    finally:
                        raw[:] = b"\0" * len(raw)
                        raw.clear()
                if not isinstance(original_state, dict):
                    raise _CredentialInvalid()
                recovery_storage_state = original_state
                if needs_identity:
                    # This request uses original credentials before opening an
                    # interactive page. The newly scanned account cannot become
                    # its own evidence for a legacy account's original identity.
                    profile = await self.identity_reader(original_state)
                    with session_scope(self.engine) as db:
                        AccountService(db, self.cipher, AuditService(db)).record_identity_if_current(
                            target_account_id, expected, profile.get("platform_user_id")
                        )
            scanned = await self.scanner.run(
                on_qr,
                on_confirming,
                cancelled,
                expires_at=expires_at,
                on_view=on_view,
                next_interaction=next_interaction,
                on_needs_verification=on_needs_verification,
                on_interaction_result=on_interaction_result,
                recovery_storage_state=recovery_storage_state if recovery_mode else None,
            )
            if stopping is not None and stopping.is_set():
                raise ScanCancelled()
            storage_state = scanned.storage_state
            platform_uid = normalize_platform_user_id(getattr(scanned, "platform_user_id", None))
            if platform_uid is None:
                raise AccountIdentityUnavailable()
            if (
                not isinstance(storage_state, dict)
                or not isinstance(storage_state.get("cookies"), list)
                or not storage_state["cookies"]
            ):
                raise _CredentialInvalid()

            with session_scope(self.engine) as db:
                entitlement_error = self._account_entitlement_error(
                    db, owner_id, target_account_id
                )
                if entitlement_error is not None:
                    raise _EntitlementFailure(entitlement_error)
                account_service = AccountService(
                    db, self.cipher, AuditService(db)
                )
                if target_account_id is not None:
                    account = account_service.reauthenticate_owned(
                        owner_id,
                        target_account_id,
                        storage_state,
                        scanned.unique_id,
                        platform_user_id=platform_uid,
                    )
                    account_service.sync_contact_avatars(account.id, {
                        identity.sec_uid: getattr(identity, "avatar_url", None)
                        for identity in scanned.contact_identities
                    })
                else:
                    account_service.require_legacy_identities_ready()
                    account = account_service.create_from_storage_state(
                        owner_id,
                        scanned.display_name,
                        storage_state,
                        scanned.unique_id,
                        scanned.conversation_names,
                        scanned.contact_identities,
                        platform_user_id=platform_uid,
                    )
                from spark_console.services.directory import save_profile
                try:
                    with db.begin_nested():
                        save_profile(db, account, getattr(scanned, "profile", None) or {"nickname": scanned.display_name if scanned.display_name != "抖音账号" else ""})
                        from spark_console.models import ContactDirectoryJob
                        db.add(ContactDirectoryJob(account_id=account.id, owner_user_id=owner_id, limit=20))
                except Exception:
                    logger.warning("Optional account directory update deferred after login")
                completed = ScanSessionService(db).complete(scan_id, account.id)
                if ScanStatus(completed.status) == ScanStatus.EXPIRED:
                    raise LoginTimedOut()
                if (
                    ScanStatus(completed.status) != ScanStatus.SUCCEEDED
                    or completed.account_id != account.id
                ):
                    raise Conflict("transition_conflict")
            return True
        except tuple(ERROR_CODES) as error:
            code = ERROR_CODES[type(error)]
        except _EntitlementFailure as error:
            code = error.code
        except AccountIdentityMismatch:
            code = "account_identity_mismatch"
        except AccountIdentityUnavailable:
            code = "account_identity_unavailable"
        except AccountIdentityAlreadyBound:
            code = "account_already_bound"
        except (_CredentialInvalid, ValidationError):
            code = "credential_invalid"
        except Exception:
            code = "automation_failed"
        finally:
            if target_account_id:
                from spark_console.services.account_health import release
                with session_scope(self.engine) as db:
                    release(db, target_account_id, scan_id)
            if isinstance(storage_state, dict):
                storage_state.clear()
            storage_state = None
            if isinstance(recovery_storage_state, dict):
                recovery_storage_state.clear()
            recovery_storage_state = None
            scanned = None

        self._record_failure(scan_id, code)
        logger.warning("auth scan ended session_id=%s code=%s", scan_id, code)
        return True

    @staticmethod
    def _account_entitlement_error(
        db: Session, owner_id: str, account_id: str | None = None
    ) -> str | None:
        owner = db.get(User, owner_id)
        if owner is None or owner.status != "active":
            return "subscription_inactive"
        if account_id is not None:
            account = db.get(DouyinAccount, account_id)
            if account is None or account.owner_user_id != owner_id:
                return "credential_invalid"
            if owner.role == "admin":
                return None
            return None if SubscriptionService(
                db, AuditService(db)
            ).has_task_access(owner_id) else "subscription_inactive"
        if owner.role == "admin":
            return None
        try:
            SubscriptionService(
                db, AuditService(db)
            ).require_account_capacity(owner_id)
        except ValidationError:
            return "subscription_inactive"
        except Conflict:
            return "account_limit_reached"
        return None

    def _record_failure(self, scan_id: str, code: str) -> None:
        try:
            with session_scope(self.engine) as db:
                scan = db.get(DouyinLoginSession, scan_id)
                if (
                    scan is None
                    or scan.slot is None
                    or ScanStatus(scan.status) not in ACTIVE_STATUSES
                ):
                    return
                ScanSessionService(db).fail(scan_id, code)
        except (Conflict, NotFound):
            return


async def run_loop() -> None:
    settings = Settings.from_env(os.environ, service='auth')
    engine = create_engine_for(settings)
    create_schema(engine)
    worker = AuthWorker(settings, engine)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stopping.set)
        except NotImplementedError:
            pass
    heartbeat = asyncio.create_task(
        _heartbeat_loop(settings.data_dir / "auth-worker.heartbeat", stopping)
    )
    try:
        from spark_console.auth_dispatch import dispatch
        background_turn=0
        async def background():
            nonlocal background_turn
            from spark_console.health_worker import process_health_check
            from spark_console.services.directory import enqueue_due_directory_syncs, process_directory_job
            from spark_console.services.probes import process
            actions=[lambda:process_health_check(engine,worker.cipher),lambda:process_directory_job(engine,worker.cipher),lambda:process(engine,worker.cipher),lambda:bool(enqueue_due_directory_syncs(engine))]
            # Rotate even when one kind has a backlog so self-checks and
            # directory reads cannot permanently starve one another.
            first=background_turn%len(actions);background_turn+=1
            for offset in range(len(actions)):
                if await actions[(first+offset)%len(actions)]():return True
            return False
        await dispatch(worker,stopping,background,slots=settings.auth_browser_slots,poll_seconds=settings.auth_poll_seconds)
    finally:
        stopping.set()
        await heartbeat


async def _heartbeat_loop(path: Path, stopping: asyncio.Event) -> None:
    while not stopping.is_set():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        except OSError:
            logger.error("auth worker heartbeat write failed")
        try:
            await asyncio.wait_for(
                stopping.wait(), timeout=AUTH_HEARTBEAT_INTERVAL_SECONDS
            )
        except TimeoutError:
            continue


if __name__ == "__main__":
    asyncio.run(run_loop())
