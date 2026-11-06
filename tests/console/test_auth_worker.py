import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.web_chat import DouyinUserIdentity
from spark_console.auth_scanner import (
    BrowserTextDelivery,
    LoginTimedOut,
    QrLoadFailed,
    ScanCancelled,
    ScannedAccount,
    VerificationRequired,
)
from spark_console.auth_worker import AuthWorker, _heartbeat_loop
from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import create_engine_for, create_schema, session_scope
from spark_console.models import (
    AuditEvent,
    DouyinAccount,
    DouyinContactIdentity,
    DouyinConversation,
    DouyinLoginInput,
    DouyinLoginSession,
    ScanStatus,
    SparkTask,
    User,
    UserSubscription,
)
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.services.scan_sessions import (
    DELIVERY_DELIVERED,
    DELIVERY_FAILED,
    DELIVERY_UNCERTAIN,
    NEEDS_APP_VERIFICATION,
    PNG_SIGNATURE,
    ScanSessionService,
)
from spark_console.services.subscriptions import SubscriptionService


COOKIE_MARKER = "worker-cookie-marker"
STORAGE_MARKER = "worker-storage-marker"
EXCEPTION_MARKER = "worker-exception-marker"
QR_MARKER = "worker-qr-marker"


def _storage_state(cookies=True):
    cookie_rows = []
    if cookies:
        cookie_rows.append(
            {
                "name": "sid",
                "value": COOKIE_MARKER,
                "domain": ".douyin.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        )
    return {
        "cookies": cookie_rows,
        "origins": [
            {
                "origin": "https://www.douyin.com",
                "localStorage": [{"name": "token", "value": STORAGE_MARKER}],
            }
        ],
    }


class _LifecycleScanner:
    def __init__(
        self,
        engine,
        scan_id,
        *,
        state=None,
        cancel_before_return=False,
        expire_before_return=False,
        before_return=None,
        platform_user_id="self",
    ):
        self.engine = engine
        self.scan_id = scan_id
        self.state = state if state is not None else _storage_state()
        self.cancel_before_return = cancel_before_return
        self.expire_before_return = expire_before_return
        self.before_return = before_return
        self.observed_statuses = []
        self.platform_user_id = platform_user_id

    async def run(self, on_qr, on_confirming, cancelled, *, expires_at=None, **_kwargs):
        on_qr(PNG_SIGNATURE + b"worker-qr-fixture")
        with Session(self.engine) as session:
            self.observed_statuses.append(
                session.get(DouyinLoginSession, self.scan_id).status
            )
        on_confirming(True)
        with Session(self.engine) as session:
            self.observed_statuses.append(
                session.get(DouyinLoginSession, self.scan_id).status
            )
        if self.cancel_before_return:
            with session_scope(self.engine) as session:
                scan = session.get(DouyinLoginSession, self.scan_id)
                ScanSessionService(session).cancel_owned(scan.owner_user_id, scan.id)
            self.observed_statuses.append("cancelled" if cancelled() else "active")
        if self.expire_before_return:
            with session_scope(self.engine) as session:
                scan = session.get(DouyinLoginSession, self.scan_id)
                scan.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        if self.before_return is not None:
            self.before_return()
        return ScannedAccount(
            " 测试昵称 ",
            " douyin-123 ",
            self.state,
            ("wzlovegsy", "gsy", "wzlovegsy"),
            (
                DouyinUserIdentity(
                    sec_uid="stable-user-id",
                    nickname="新的昵称",
                    remark_name="我的备注",
                ),
            ),
            platform_user_id=self.platform_user_id,
        )


class _RaisingScanner:
    def __init__(self, error):
        self.error = error
        self.called = False

    async def run(
        self, _on_qr, _on_confirming, _cancelled, *, expires_at=None, **_kwargs
    ):
        self.called = True
        raise self.error


class _SensitiveFailureScanner:
    async def run(
        self, on_qr, _on_confirming, _cancelled, *, expires_at=None, **_kwargs
    ):
        on_qr(PNG_SIGNATURE + QR_MARKER.encode())
        raise RuntimeError(
            "|".join(
                (EXCEPTION_MARKER, COOKIE_MARKER, STORAGE_MARKER, QR_MARKER)
            )
        )


class _DeadlineScanner:
    def __init__(self):
        self.expires_at = None

    async def run(
        self, on_qr, _on_confirming, _cancelled, *, expires_at=None, **_kwargs
    ):
        self.expires_at = expires_at
        on_qr(PNG_SIGNATURE + b"deadline-qr-fixture")
        raise LoginTimedOut()


class _VerificationInputScanner:
    def __init__(self, engine, scan_id, owner_id):
        self.engine = engine
        self.scan_id = scan_id
        self.owner_id = owner_id
        self.interaction = None
        self.public = None

    async def run(
        self,
        on_qr,
        on_confirming,
        _cancelled,
        *,
        on_needs_verification=None,
        next_interaction=None,
        **_kwargs,
    ):
        on_qr(PNG_SIGNATURE + b"verification-qr")
        on_confirming(True)
        on_needs_verification(
            {
                "required": True,
                "masked_phone": "138****5678",
                "verification_sent": True,
                "can_resend": False,
                "resend_after_seconds": 60,
            }
        )
        with session_scope(self.engine) as session:
            service = ScanSessionService(session)
            scan = session.get(DouyinLoginSession, self.scan_id)
            self.public = service.public_status(scan)
            service.queue_text(
                self.owner_id,
                self.scan_id,
                "246810",
                CookieCipher(b"k" * 32),
            )
        self.interaction = next_interaction()
        raise VerificationRequired()


class _InteractionReceiptScanner:
    def __init__(
        self,
        engine,
        scan_id,
        owner_id,
        *,
        delivery=None,
        error=None,
    ):
        self.engine = engine
        self.scan_id = scan_id
        self.owner_id = owner_id
        self.delivery = delivery
        self.error = error
        self.interaction = None
        self.state = _storage_state()

    async def run(
        self,
        on_qr,
        on_confirming,
        _cancelled,
        *,
        next_interaction=None,
        on_interaction_result=None,
        **_kwargs,
    ):
        on_qr(PNG_SIGNATURE + b"interaction-receipt-qr")
        on_confirming(True)
        with session_scope(self.engine) as session:
            ScanSessionService(session).queue_text(
                self.owner_id,
                self.scan_id,
                "password candidate",
                CookieCipher(b"k" * 32),
            )
        self.interaction = next_interaction()
        if self.delivery is not None:
            on_interaction_result(
                {
                    "kind": "text",
                    "interaction_id": self.interaction["interaction_id"],
                    "delivery": self.delivery.value,
                }
            )
        if self.error is not None:
            raise self.error
        return ScannedAccount(
            "回执测试账号",
            f"receipt-{self.scan_id}",
            self.state,
            platform_user_id="self",
        )


class _AppVerificationScanner:
    def __init__(self, engine, scan_id):
        self.engine = engine
        self.scan_id = scan_id
        self.public = None

    async def run(
        self,
        on_qr,
        on_confirming,
        _cancelled,
        *,
        on_needs_verification=None,
        next_interaction=None,
        **_kwargs,
    ):
        on_qr(PNG_SIGNATURE + b"app-verification-qr")
        on_confirming(True)
        on_needs_verification(
            {
                "kind": "app",
                "masked_phone": "138****5678",
                "verification_sent": True,
                "can_resend": True,
                "resend_after_seconds": 0,
            }
        )
        with session_scope(self.engine) as session:
            service = ScanSessionService(session)
            scan = session.get(DouyinLoginSession, self.scan_id)
            self.public = service.public_status(scan)
        raise VerificationRequired()


class _StopAwareScanner:
    def __init__(self):
        self.started = asyncio.Event()
        self.expires_at = None

    async def run(
        self, _on_qr, _on_confirming, cancelled, *, expires_at=None, **_kwargs
    ):
        self.expires_at = expires_at
        self.started.set()
        while not cancelled():
            await asyncio.sleep(0)
        raise ScanCancelled()


class AuthWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1.0
        self.original_identity_reader = AsyncMock(return_value={"platform_user_id": "self"})
        self.identity_patch = patch("spark_console.auth_worker.probe_saved_profile", self.original_identity_reader)
        self.identity_patch.start()
        self.addCleanup(self.identity_patch.stop)
        self.temporary = tempfile.TemporaryDirectory()
        data_dir = Path(self.temporary.name)
        cookie_key_file = data_dir / "cookie.key"
        session_key_file = data_dir / "session.key"
        cookie_key_file.write_bytes(b"k" * 32)
        session_key_file.write_bytes(b"s" * 32)
        self.settings = Settings(
            data_dir=data_dir,
            database_url=f"sqlite:///{data_dir / 'auth-worker.db'}",
            cookie_key_file=cookie_key_file,
            session_key_file=session_key_file,
            worker_poll_seconds=1,
        )
        self.engine = create_engine_for(self.settings)
        create_schema(self.engine)
        with session_scope(self.engine) as session:
            owner = User(username="auth-owner", password_hash="hash", role="user")
            admin = User(
                username="auth-admin", password_hash="hash", role="admin"
            )
            session.add_all((owner, admin))
            session.flush()
            self.owner_id = owner.id
            self.admin_id = admin.id
            SubscriptionService(session, AuditService(session)).extend(
                admin.id,
                owner.id,
                duration_days=30,
                max_accounts=1,
                max_tasks=20,
            )

    async def asyncTearDown(self):
        self.engine.dispose()
        self.temporary.cleanup()

    def _start_scan(self, *, now=None, account_id=None, owner_id=None):
        with session_scope(self.engine) as session:
            service = ScanSessionService(session, now=now or (lambda: datetime.now(timezone.utc)))
            return service.start(owner_id or self.owner_id, account_id).id

    def _persisted(self, scan_id):
        with Session(self.engine) as session:
            scan = session.get(DouyinLoginSession, scan_id)
            return {
                "status": scan.status,
                "slot": scan.slot,
                "qr_png": scan.qr_png,
                "account_id": scan.account_id,
                "error_code": scan.error_code,
            }

    def _set_expiry(self, scan_id, expires_at):
        with session_scope(self.engine) as session:
            session.get(DouyinLoginSession, scan_id).expires_at = expires_at

    async def test_auth_worker_heartbeat_is_written_and_stops_cleanly(self):
        heartbeat_path = self.settings.data_dir / "test-auth-worker.heartbeat"
        stopping = asyncio.Event()
        task = asyncio.create_task(_heartbeat_loop(heartbeat_path, stopping))
        for _ in range(20):
            if heartbeat_path.exists():
                break
            await asyncio.sleep(0.01)
        self.assertTrue(heartbeat_path.exists())
        stopping.set()
        await asyncio.wait_for(task, timeout=1)

    async def test_success_uses_real_state_machine_and_encrypted_account_service(self):
        scan_id = self._start_scan()
        scanner = _LifecycleScanner(self.engine, scan_id)

        claimed = await AuthWorker(
            self.settings, self.engine, scanner=scanner
        ).run_once()

        persisted = self._persisted(scan_id)
        self.assertTrue(claimed)
        self.assertEqual(
            [ScanStatus.AWAITING_SCAN, ScanStatus.CONFIRMING],
            scanner.observed_statuses,
        )
        self.assertEqual(ScanStatus.SUCCEEDED, persisted["status"])
        self.assertIsNone(persisted["slot"])
        self.assertIsNone(persisted["qr_png"])
        self.assertIsNotNone(persisted["account_id"])
        with Session(self.engine) as session:
            account = session.get(DouyinAccount, persisted["account_id"])
            conversations = session.scalars(
                select(DouyinConversation.display_name)
                .where(DouyinConversation.account_id == account.id)
                .order_by(DouyinConversation.display_name)
            ).all()
            contact = session.get(
                DouyinContactIdentity, (account.id, "stable-user-id")
            )
            audits = session.scalars(select(AuditEvent)).all()
            self.assertEqual(2, account.cookie_version)
            self.assertEqual("valid", account.validation_state)
            self.assertFalse(COOKIE_MARKER.encode() in account.encrypted_cookies)
            self.assertFalse(STORAGE_MARKER.encode() in account.encrypted_cookies)
            self.assertFalse(
                any(
                    marker in (event.detail or "")
                    for event in audits
                    for marker in (COOKIE_MARKER, STORAGE_MARKER)
                )
            )
            self.assertEqual(["gsy", "wzlovegsy"], conversations)
            self.assertEqual("新的昵称", contact.nickname)
            self.assertEqual("我的备注", contact.remark_name)
        self.assertEqual(0, len(scanner.state))

    async def test_relogin_reuses_account_id_and_preserves_task_relationship(self):
        with session_scope(self.engine) as session:
            account = AccountService(
                session, CookieCipher(b"k" * 32), AuditService(session)
            ).create_from_storage_state(
                self.owner_id,
                "保留的账号备注",
                _storage_state(),
                "douyin-123",
            )
            task = SparkTask(
                owner_user_id=self.owner_id,
                douyin_account_id=account.id,
                target_name="好友",
                send_time="09:00",
                message_template="今日火花",
                enabled=True,
            )
            session.add(task)
            session.flush()
            account_id = account.id
            task_id = task.id

        scan_id = self._start_scan(account_id=account_id)
        new_state = _storage_state()
        new_state["cookies"][0]["value"] = "reauthenticated-cookie-marker"
        scanner = _LifecycleScanner(self.engine, scan_id, state=new_state)

        self.assertTrue(
            await AuthWorker(
                self.settings, self.engine, scanner=scanner
            ).run_once()
        )

        persisted = self._persisted(scan_id)
        self.assertEqual(ScanStatus.SUCCEEDED, persisted["status"])
        self.assertEqual(account_id, persisted["account_id"])
        with Session(self.engine) as session:
            accounts = session.scalars(
                select(DouyinAccount).where(
                    DouyinAccount.owner_user_id == self.owner_id
                )
            ).all()
            account = session.get(DouyinAccount, account_id)
            task = session.get(SparkTask, task_id)
            plaintext = CookieCipher(b"k" * 32).decrypt(
                account.encrypted_cookies, account.cookie_nonce
            )
            self.assertEqual(1, len(accounts))
            self.assertEqual(account_id, task.douyin_account_id)
            self.assertEqual("保留的账号备注", account.display_name)
            self.assertEqual("valid", account.validation_state)
            self.assertIn(b"reauthenticated-cookie-marker", plaintext)

    async def test_relogin_rejects_scanning_a_different_douyin_account(self):
        with session_scope(self.engine) as session:
            account = AccountService(
                session, CookieCipher(b"k" * 32), AuditService(session)
            ).create_from_storage_state(
                self.owner_id,
                "不能串号的账号",
                _storage_state(),
                "bound-douyin-id",
            )
            account_id = account.id
            original_ciphertext = bytes(account.encrypted_cookies)

        scan_id = self._start_scan(account_id=account_id)
        scanner = _LifecycleScanner(self.engine, scan_id, platform_user_id="another-account")

        self.assertTrue(
            await AuthWorker(
                self.settings, self.engine, scanner=scanner
            ).run_once()
        )

        persisted = self._persisted(scan_id)
        self.assertEqual(ScanStatus.FAILED, persisted["status"])
        self.assertEqual("account_identity_mismatch", persisted["error_code"])
        with Session(self.engine) as session:
            account = session.get(DouyinAccount, account_id)
            self.assertEqual(original_ciphertext, account.encrypted_cookies)

    async def test_new_binding_without_verified_platform_identity_never_saves_credentials(self):
        scan_id = self._start_scan()
        scanner = _LifecycleScanner(self.engine, scan_id, platform_user_id=None)
        await AuthWorker(self.settings, self.engine, scanner=scanner).run_once()
        self.assertEqual("account_identity_unavailable", self._persisted(scan_id)["error_code"])
        with Session(self.engine) as db:
            self.assertEqual([], db.scalars(select(DouyinAccount)).all())

    async def test_unverifiable_original_identity_does_not_open_or_replace_old_session(self):
        with session_scope(self.engine) as db:
            account = AccountService(db, CookieCipher(b"k" * 32), AuditService(db)).create_from_storage_state(
                self.owner_id, "原备注", _storage_state(),
            )
            account_id, expected = account.id, account.encrypted_cookies
        self.original_identity_reader.return_value = {}
        scan_id = self._start_scan(account_id=account_id)
        scanner = _LifecycleScanner(self.engine, scan_id)
        await AuthWorker(self.settings, self.engine, scanner=scanner).run_once()
        self.assertEqual("account_identity_unavailable", self._persisted(scan_id)["error_code"])
        self.assertEqual([], scanner.observed_statuses)
        with Session(self.engine) as db:
            self.assertEqual(expected, db.get(DouyinAccount, account_id).encrypted_cookies)
            self.assertEqual("原备注", db.get(DouyinAccount, account_id).display_name)

    async def test_recovery_real_scanner_preserves_account_with_verified_platform_identity(self):
        from tests.console.test_auth_recovery import make_recovery_scanner
        from spark_console.models import AccountHealth, AccountOperation
        from spark_console.services.account_health import record, snapshot

        with session_scope(self.engine) as session:
            account = AccountService(
                session, CookieCipher(b"k" * 32), AuditService(session)
            ).create_from_storage_state(self.owner_id, "原账号备注", _storage_state(), "original-account")
            account_id = account.id
            record(session, account_id, snapshot(account), "verification", "之前出现验证控件")
            task = SparkTask(
                owner_user_id=self.owner_id, douyin_account_id=account_id,
                target_name="保留的好友", send_time="09:00", message_template="今日火花", enabled=False,
            )
            session.add(task)
            session.flush()
            task_id = task.id
            scan_id = ScanSessionService(session).start_recovery(self.owner_id, account_id).id

        scanner, _, context, _ = make_recovery_scanner(timeout=2, api="valid")
        self.assertTrue(await AuthWorker(self.settings, self.engine, scanner=scanner).run_once())
        persisted = self._persisted(scan_id)
        self.assertEqual(ScanStatus.SUCCEEDED, persisted["status"])
        self.assertEqual(account_id, persisted["account_id"])
        self.assertIsNone(persisted["slot"])
        self.assertIsNone(persisted["qr_png"])
        with Session(self.engine) as session:
            account = session.get(DouyinAccount, account_id)
            self.assertEqual("valid", account.validation_state)
            self.assertEqual("原账号备注", account.display_name)
            self.assertIsNone(session.get(AccountOperation, account_id))
            self.assertEqual("已完成抖音登录验证", session.get(AccountHealth, account_id).reason)
            self.assertIsNone(session.get(AccountHealth, account_id).next_check_at)
            self.assertEqual(account_id, session.get(SparkTask, task_id).douyin_account_id)
            self.assertFalse(session.get(SparkTask, task_id).enabled)
        self.assertEqual({}, context.storage_state.return_value)  # Worker clears decrypted state.

    async def test_typed_scanner_errors_map_to_fixed_public_codes(self):
        cases = (
            (QrLoadFailed(), "qr_load_failed"),
            (LoginTimedOut(), "login_timeout"),
            (VerificationRequired(), "verification_required"),
            (ScanCancelled(), "cancelled"),
        )
        for error, expected_code in cases:
            with self.subTest(code=expected_code):
                scan_id = self._start_scan()
                worker = AuthWorker(
                    self.settings,
                    self.engine,
                    scanner=_RaisingScanner(error),
                )

                self.assertTrue(await worker.run_once())

                persisted = self._persisted(scan_id)
                self.assertEqual(ScanStatus.FAILED, persisted["status"])
                self.assertEqual(expected_code, persisted["error_code"])
                self.assertIsNone(persisted["slot"])
                self.assertIsNone(persisted["qr_png"])

    async def test_empty_cookies_fail_before_account_creation(self):
        scan_id = self._start_scan()
        scanner = _LifecycleScanner(
            self.engine, scan_id, state=_storage_state(cookies=False)
        )

        self.assertTrue(
            await AuthWorker(self.settings, self.engine, scanner=scanner).run_once()
        )

        persisted = self._persisted(scan_id)
        self.assertEqual(ScanStatus.FAILED, persisted["status"])
        self.assertEqual("credential_invalid", persisted["error_code"])
        with Session(self.engine) as session:
            self.assertEqual(0, len(session.scalars(select(DouyinAccount)).all()))
        self.assertEqual(0, len(scanner.state))

    async def test_cancel_before_final_transaction_rolls_back_new_account(self):
        scan_id = self._start_scan()
        scanner = _LifecycleScanner(
            self.engine, scan_id, cancel_before_return=True
        )

        self.assertTrue(
            await AuthWorker(self.settings, self.engine, scanner=scanner).run_once()
        )

        persisted = self._persisted(scan_id)
        self.assertEqual("cancelled", scanner.observed_statuses[-1])
        self.assertEqual(ScanStatus.CANCELLED, persisted["status"])
        self.assertEqual("cancelled", persisted["error_code"])
        self.assertIsNone(persisted["account_id"])
        with Session(self.engine) as session:
            self.assertEqual(0, len(session.scalars(select(DouyinAccount)).all()))
        self.assertEqual(0, len(scanner.state))

    async def test_expiry_at_final_transaction_rolls_back_new_account(self):
        scan_id = self._start_scan()
        scanner = _LifecycleScanner(
            self.engine, scan_id, expire_before_return=True
        )

        self.assertTrue(
            await AuthWorker(self.settings, self.engine, scanner=scanner).run_once()
        )

        persisted = self._persisted(scan_id)
        self.assertEqual(ScanStatus.EXPIRED, persisted["status"])
        self.assertEqual("login_timeout", persisted["error_code"])
        self.assertIsNone(persisted["account_id"])
        with Session(self.engine) as session:
            self.assertEqual(0, len(session.scalars(select(DouyinAccount)).all()))
        self.assertEqual(0, len(scanner.state))

    async def test_unknown_error_log_contains_only_session_and_public_code(self):
        scan_id = self._start_scan()
        worker = AuthWorker(
            self.settings,
            self.engine,
            scanner=_SensitiveFailureScanner(),
        )

        with self.assertLogs("spark_console.auth_worker", level="WARNING") as captured:
            self.assertTrue(await worker.run_once())

        persisted = self._persisted(scan_id)
        self.assertEqual("automation_failed", persisted["error_code"])
        self.assertTrue(any(scan_id in line for line in captured.output))
        self.assertTrue(any("automation_failed" in line for line in captured.output))
        self.assertFalse(any(EXCEPTION_MARKER in line for line in captured.output))
        self.assertFalse(any(COOKIE_MARKER in line for line in captured.output))
        self.assertFalse(any(STORAGE_MARKER in line for line in captured.output))
        self.assertFalse(any(QR_MARKER in line for line in captured.output))
        self.assertIsNone(persisted["qr_png"])

    async def test_startup_expires_abandoned_session(self):
        old_now = datetime.now(timezone.utc) - timedelta(minutes=40)
        scan_id = self._start_scan(now=lambda: old_now)

        AuthWorker(self.settings, self.engine, scanner=_RaisingScanner(QrLoadFailed()))

        persisted = self._persisted(scan_id)
        self.assertEqual(ScanStatus.EXPIRED, persisted["status"])
        self.assertEqual("login_timeout", persisted["error_code"])
        self.assertIsNone(persisted["slot"])

    async def test_startup_releases_nonexpired_scan_whose_browser_was_lost(self):
        scan_id = self._start_scan()
        with session_scope(self.engine) as session:
            service = ScanSessionService(session)
            service.claim_next()
            service.publish_qr(scan_id, PNG_SIGNATURE + b"active-browser")

        AuthWorker(self.settings, self.engine, scanner=_RaisingScanner(QrLoadFailed()))

        persisted = self._persisted(scan_id)
        self.assertEqual(ScanStatus.FAILED, persisted["status"])
        self.assertEqual("automation_failed", persisted["error_code"])
        self.assertIsNone(persisted["slot"])
        self.assertIsNone(persisted["qr_png"])

    async def test_run_once_returns_false_when_no_scan_is_queued(self):
        worker = AuthWorker(
            self.settings, self.engine, scanner=_RaisingScanner(QrLoadFailed())
        )

        self.assertFalse(await worker.run_once())

    async def test_expired_membership_keeps_free_scan_access(self):
        scan_id = self._start_scan()
        with session_scope(self.engine) as session:
            subscription = session.get(UserSubscription, self.owner_id)
            expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            subscription.valid_from = expired_at - timedelta(days=30)
            subscription.valid_until = expired_at
        scanner = _RaisingScanner(QrLoadFailed())

        self.assertTrue(
            await AuthWorker(
                self.settings, self.engine, scanner=scanner
            ).run_once()
        )

        persisted = self._persisted(scan_id)
        # Expired subscribers fall back to the free tier, so the one-account
        # experience remains available. The scanner is reached and reports
        # its own simulated QR failure.
        self.assertTrue(scanner.called)
        self.assertEqual(ScanStatus.FAILED, persisted["status"])
        self.assertEqual("qr_load_failed", persisted["error_code"])
        self.assertIsNone(persisted["slot"])

    async def test_account_quota_fails_queued_scan_before_browser_launch(self):
        scan_id = self._start_scan()
        with session_scope(self.engine) as session:
            session.add(
                DouyinAccount(
                    owner_user_id=self.owner_id,
                    display_name="已绑定账号",
                    encrypted_cookies=b"encrypted",
                    cookie_nonce=b"nonce",
                )
            )
        scanner = _RaisingScanner(QrLoadFailed())

        self.assertTrue(
            await AuthWorker(
                self.settings, self.engine, scanner=scanner
            ).run_once()
        )

        persisted = self._persisted(scan_id)
        self.assertFalse(scanner.called)
        self.assertEqual(ScanStatus.FAILED, persisted["status"])
        self.assertEqual("account_limit_reached", persisted["error_code"])
        self.assertIsNone(persisted["slot"])

    async def test_account_quota_is_rechecked_immediately_before_save(self):
        scan_id = self._start_scan()

        def occupy_capacity():
            with session_scope(self.engine) as session:
                session.add(
                    DouyinAccount(
                        owner_user_id=self.owner_id,
                        display_name="并发绑定账号",
                        encrypted_cookies=b"encrypted",
                        cookie_nonce=b"nonce",
                    )
                )

        scanner = _LifecycleScanner(
            self.engine,
            scan_id,
            before_return=occupy_capacity,
        )

        self.assertTrue(
            await AuthWorker(
                self.settings, self.engine, scanner=scanner
            ).run_once()
        )

        persisted = self._persisted(scan_id)
        self.assertEqual(ScanStatus.FAILED, persisted["status"])
        self.assertEqual("account_limit_reached", persisted["error_code"])
        self.assertIsNone(persisted["account_id"])
        with Session(self.engine) as session:
            accounts = session.scalars(
                select(DouyinAccount).where(
                    DouyinAccount.owner_user_id == self.owner_id
                )
            ).all()
            self.assertEqual(1, len(accounts))

    async def test_claim_resets_queued_expiry_before_scanner_and_cleans_failure(self):
        scan_id = self._start_scan()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=0.05)
        self._set_expiry(scan_id, expires_at)
        scanner = _DeadlineScanner()

        self.assertTrue(
            await AuthWorker(self.settings, self.engine, scanner=scanner).run_once()
        )

        persisted = self._persisted(scan_id)
        self.assertIsNotNone(scanner.expires_at)
        scanner_expiry = scanner.expires_at
        if scanner_expiry.tzinfo is None:
            scanner_expiry = scanner_expiry.replace(tzinfo=timezone.utc)
        remaining = (scanner_expiry - datetime.now(timezone.utc)).total_seconds()
        self.assertGreater(remaining, 290)
        self.assertLessEqual(remaining, 301)
        self.assertEqual(ScanStatus.FAILED, persisted["status"])
        self.assertEqual("login_timeout", persisted["error_code"])
        self.assertIsNone(persisted["slot"])
        self.assertIsNone(persisted["qr_png"])

    async def test_worker_delivers_encrypted_otp_only_after_verification_signal(self):
        scan_id = self._start_scan()
        scanner = _VerificationInputScanner(
            self.engine, scan_id, self.owner_id
        )

        with self.assertLogs(
            "spark_console.auth_worker", level="WARNING"
        ) as captured:
            self.assertTrue(
                await AuthWorker(
                    self.settings, self.engine, scanner=scanner
                ).run_once()
            )

        self.assertTrue(scanner.public["needs_verification"])
        self.assertEqual("138****5678", scanner.public["masked_phone"])
        self.assertEqual(60, scanner.public["resend_after_seconds"])
        self.assertEqual("text", scanner.interaction["kind"])
        self.assertEqual("246810", scanner.interaction["text"])
        self.assertIsInstance(scanner.interaction["interaction_id"], int)
        self.assertEqual(
            1, scanner.interaction["session_text_attempt_number"]
        )
        self.assertFalse(any("246810" in line for line in captured.output))
        with Session(self.engine) as session:
            pending = session.scalar(
                select(DouyinLoginInput).where(
                    DouyinLoginInput.scan_id == scan_id
                )
            )
            self.assertIsNotNone(pending)
            self.assertIsNone(pending.ciphertext)
            self.assertIsNone(pending.nonce)
            self.assertIsNotNone(pending.consumed_at)
            self.assertEqual(DELIVERY_UNCERTAIN, pending.delivery_status)
            self.assertIsNotNone(pending.finished_at)

    async def test_worker_persists_terminal_text_delivery_receipts(self):
        cases = (
            (BrowserTextDelivery.DELIVERED, DELIVERY_DELIVERED),
            (BrowserTextDelivery.UNCERTAIN, DELIVERY_UNCERTAIN),
            (BrowserTextDelivery.FAILED, DELIVERY_FAILED),
        )
        for delivery, expected_status in cases:
            with self.subTest(delivery=delivery.value):
                scan_id = self._start_scan()
                scanner = _InteractionReceiptScanner(
                    self.engine,
                    scan_id,
                    self.owner_id,
                    delivery=delivery,
                    error=VerificationRequired(),
                )

                self.assertTrue(
                    await AuthWorker(
                        self.settings, self.engine, scanner=scanner
                    ).run_once()
                )

                with Session(self.engine) as session:
                    pending = session.scalar(
                        select(DouyinLoginInput).where(
                            DouyinLoginInput.scan_id == scan_id
                        )
                    )
                    self.assertIsNotNone(pending)
                    self.assertEqual(expected_status, pending.delivery_status)
                    self.assertIsNotNone(pending.attempted_at)
                    self.assertIsNotNone(pending.finished_at)
                    self.assertIsNone(pending.ciphertext)
                    self.assertIsNone(pending.nonce)

    async def test_terminal_scan_paths_finalize_unreported_claim_as_uncertain(self):
        cases = (
            ("success", None, ScanStatus.SUCCEEDED, None),
            (
                "failure",
                RuntimeError("driver stopped"),
                ScanStatus.FAILED,
                "automation_failed",
            ),
            ("cancel", ScanCancelled(), ScanStatus.FAILED, "cancelled"),
            ("timeout", LoginTimedOut(), ScanStatus.FAILED, "login_timeout"),
        )
        for name, error, expected_status, expected_code in cases:
            with self.subTest(path=name):
                scan_id = self._start_scan(owner_id=self.admin_id)
                scanner = _InteractionReceiptScanner(
                    self.engine,
                    scan_id,
                    self.admin_id,
                    error=error,
                )

                self.assertTrue(
                    await AuthWorker(
                        self.settings, self.engine, scanner=scanner
                    ).run_once()
                )

                persisted = self._persisted(scan_id)
                self.assertEqual(expected_status, persisted["status"])
                self.assertEqual(expected_code, persisted["error_code"])
                with Session(self.engine) as session:
                    pending = session.scalar(
                        select(DouyinLoginInput).where(
                            DouyinLoginInput.scan_id == scan_id
                        )
                    )
                    self.assertIsNotNone(pending)
                    self.assertEqual(DELIVERY_UNCERTAIN, pending.delivery_status)
                    self.assertIsNotNone(pending.attempted_at)
                    self.assertIsNotNone(pending.finished_at)
                    self.assertIsNone(pending.ciphertext)
                    self.assertIsNone(pending.nonce)

    async def test_worker_projects_app_verification_without_sms_actions(self):
        scan_id = self._start_scan()
        scanner = _AppVerificationScanner(self.engine, scan_id)

        self.assertTrue(
            await AuthWorker(
                self.settings, self.engine, scanner=scanner
            ).run_once()
        )

        self.assertTrue(scanner.public["needs_verification"])
        self.assertEqual(NEEDS_APP_VERIFICATION, scanner.public["error"])
        self.assertEqual("app", scanner.public["verification_kind"])
        self.assertIsNone(scanner.public["masked_phone"])
        self.assertFalse(scanner.public["verification_sent"])

    async def test_stopping_event_cancels_active_scan_and_releases_slot(self):
        scan_id = self._start_scan()
        scanner = _StopAwareScanner()
        stopping = asyncio.Event()
        worker = AuthWorker(self.settings, self.engine, scanner=scanner)
        task = asyncio.create_task(worker.run_once(stopping))
        await asyncio.wait_for(scanner.started.wait(), timeout=0.2)

        stopping.set()

        self.assertTrue(await asyncio.wait_for(task, timeout=0.2))
        persisted = self._persisted(scan_id)
        self.assertIsNotNone(scanner.expires_at)
        self.assertEqual(ScanStatus.FAILED, persisted["status"])
        self.assertEqual("cancelled", persisted["error_code"])
        self.assertIsNone(persisted["slot"])
        self.assertIsNone(persisted["qr_png"])


if __name__ == "__main__":
    unittest.main()
