import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from spark_console.crypto import CookieCipher
from spark_console.db import create_schema
from spark_console.models import (
    Base,
    DouyinAccount,
    DouyinLoginAction,
    DouyinLoginChallenge,
    DouyinLoginInput,
    DouyinLoginSession,
    ScanStatus,
    User,
)
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.scan_sessions import (
    DELIVERY_CLAIMED,
    DELIVERY_DELIVERED,
    DELIVERY_FAILED,
    DELIVERY_QUEUED,
    DELIVERY_UNCERTAIN,
    MAX_QR_PNG_BYTES,
    NEEDS_APP_VERIFICATION,
    NEEDS_VERIFICATION,
    OTP_LIFETIME,
    OTP_PREFIX,
    PNG_SIGNATURE,
    ScanSessionService,
    TEXT_MAX_LENGTH,
    browser_view_revision,
    scan_slot_for_owner,
)


class MutableClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class ScanSessionServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        create_schema(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.clock = MutableClock(datetime(2026, 8, 25, 4, 0, tzinfo=timezone.utc))
        self.service = ScanSessionService(self.session, now=self.clock)
        self.cipher = CookieCipher(b"c" * 32)
        self.owner = User(username="owner", password_hash="hash", role="user")
        self.other = User(username="other", password_hash="hash", role="user")
        self.session.add_all([self.owner, self.other])
        self.session.flush()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def _claim_and_publish(self):
        scan = self.service.start(self.owner.id)
        claimed = self.service.claim_next()
        self.assertEqual(scan.id, claimed.id)
        return self.service.publish_qr(scan.id, PNG_SIGNATURE + b"fixture")

    def test_busy_first_account_does_not_block_other_user(self):
        from spark_console.services.account_health import acquire
        account=self._account()
        first=self.service.start(self.owner.id,account.id)
        acquire(self.session,account.id,'busy-token','send',self.clock(),120)
        self.clock.value+=timedelta(seconds=1)
        second=self.service.start(self.other.id)
        claimed=self.service.claim_next()
        self.assertEqual(second.id,claimed.id)
        self.assertEqual(ScanStatus.QUEUED,self.session.get(DouyinLoginSession,first.id).status)

    def _account(self) -> DouyinAccount:
        account = DouyinAccount(
            owner_user_id=self.owner.id,
            display_name="scan result",
            encrypted_cookies=b"ciphertext",
            cookie_nonce=b"n" * 12,
        )
        self.session.add(account)
        self.session.flush()
        return account

    def test_global_queue_limit_and_expired_capacity(self):
        for index in range(20):
            user = User(username=f'queue-{index}', password_hash='hash', role='user')
            self.session.add(user)
            self.session.flush()
            self.service.start(user.id)
        with self.assertRaisesRegex(Conflict, 'queue_full'):
            self.service.start(self.owner.id)
        self.clock.value += timedelta(hours=1)
        self.assertEqual(ScanStatus.QUEUED, self.service.start(self.owner.id).status)

    def _needs_verification(self):
        scan = self._claim_and_publish()
        return self.service.mark_needs_verification(scan.id)

    def _claim_text(self, scan_id, expected_text):
        claimed = self.service.claim_interaction(scan_id, self.cipher)
        self.assertEqual("text", claimed["kind"])
        self.assertEqual(expected_text, claimed["text"])
        self.assertIsInstance(claimed["interaction_id"], int)
        self.assertGreaterEqual(claimed["session_text_attempt_number"], 1)
        return claimed

    def test_start_reserves_owner_slot_for_thirty_minutes_while_queued(self):
        scan = self.service.start(self.owner.id)

        self.assertEqual(self.owner.id, scan.owner_user_id)
        self.assertEqual(scan_slot_for_owner(self.owner.id), scan.slot)
        self.assertEqual(16, len(scan.slot))
        self.assertEqual(ScanStatus.QUEUED, scan.status)
        self.assertEqual(self.clock.value + timedelta(minutes=30), scan.expires_at)

    def test_claim_resets_the_login_operation_deadline_to_five_minutes(self):
        scan = self.service.start(self.owner.id)
        self.clock.value += timedelta(minutes=4)

        claimed = self.service.claim_next()

        self.assertEqual(scan.id, claimed.id)
        self.assertEqual(self.clock.value + timedelta(minutes=5), claimed.expires_at)

    def test_same_owner_is_busy_while_different_owner_can_queue(self):
        active = self.service.start(self.owner.id)
        other_scan = self.service.start(self.other.id)

        with self.assertRaisesRegex(Conflict, "^slot_busy$"):
            self.service.start(self.owner.id)

        retained = self.session.get(type(active), active.id)
        self.assertEqual(scan_slot_for_owner(self.owner.id), retained.slot)
        self.assertEqual(ScanStatus.QUEUED, retained.status)
        self.assertEqual(scan_slot_for_owner(self.other.id), other_scan.slot)
        self.assertNotEqual(retained.slot, other_scan.slot)

    def test_owner_only_access_and_cancel_hide_session_from_other_users(self):
        scan = self.service.start(self.owner.id)

        self.assertEqual(scan.id, self.service.get_owned(self.owner.id, scan.id).id)
        with self.assertRaises(NotFound):
            self.service.get_owned(self.other.id, scan.id)
        with self.assertRaises(NotFound):
            self.service.cancel_owned(self.other.id, scan.id)

        self.assertEqual(scan_slot_for_owner(self.owner.id), scan.slot)
        self.assertEqual(ScanStatus.QUEUED, scan.status)

    def test_claim_publish_confirm_and_complete_follow_legal_transitions(self):
        awaiting = self._claim_and_publish()
        self.assertEqual(ScanStatus.AWAITING_SCAN, awaiting.status)
        self.assertTrue(awaiting.qr_png.startswith(PNG_SIGNATURE))

        confirming = self.service.mark_confirming(awaiting.id)
        self.assertEqual(ScanStatus.CONFIRMING, confirming.status)
        account = self._account()
        completed = self.service.complete(confirming.id, account.id)

        self.assertEqual(ScanStatus.SUCCEEDED, completed.status)
        self.assertEqual(account.id, completed.account_id)
        self.assertIsNone(completed.qr_png)
        self.assertIsNone(completed.slot)
        self.assertIsNone(completed.error_code)
        self.assertEqual(self.clock.value, completed.finished_at)

    def test_browser_click_is_owner_scoped_and_claimed_once(self):
        scan = self._claim_and_publish()
        with self.assertRaises(NotFound):
            self.service.queue_click(self.other.id, scan.id, 0.25, 0.75)
        action = self.service.queue_click(self.owner.id, scan.id, 0.25, 0.75)
        self.assertEqual({"kind": "click", "x": 0.25, "y": 0.75}, self.service.claim_interaction(scan.id))
        self.assertIsNotNone(action.consumed_at)
        self.assertIsNone(self.service.claim_interaction(scan.id))

    def test_verification_signal_is_active_idempotent_and_public(self):
        scan = self._claim_and_publish()

        first = self.service.mark_needs_verification(scan.id)
        second = self.service.mark_needs_verification(scan.id)
        public = self.service.public_status(second)

        self.assertEqual(ScanStatus.CONFIRMING, first.status)
        self.assertEqual(NEEDS_VERIFICATION, second.error_code)
        self.assertTrue(public["needs_verification"])
        self.assertEqual("sms", public["verification_kind"])
        self.assertEqual(NEEDS_VERIFICATION, public["error"])
        self.assertEqual("manual_required", public["send_status"])
        self.assertFalse(public["can_retry_send"])
        self.assertEqual("请在云端抖音页面完成短信验证", public["message"])
        self.assertNotIn("text", repr(public))

    def test_app_verification_preserves_queued_click_and_numeric_text(self):
        scan = self._claim_and_publish()
        self.service.queue_click(self.owner.id, scan.id, 0.25, 0.75)
        pending = self.service.queue_text(
            self.owner.id, scan.id, "123456", self.cipher
        )

        signalled = self.service.mark_needs_verification(scan.id, {"kind": "app"})
        public = self.service.public_status(signalled)

        self.assertEqual(NEEDS_APP_VERIFICATION, public["error"])
        self.assertEqual("app", public["verification_kind"])
        self.assertEqual(
            "请在云端抖音页面完成额外验证", public["message"]
        )
        self.assertIsNotNone(pending.ciphertext)
        self.assertEqual(
            {"kind": "click", "x": 0.25, "y": 0.75},
            self.service.claim_interaction(scan.id, self.cipher),
        )
        self._claim_text(scan.id, "123456")
        self.assertIsNone(self.service.claim_interaction(scan.id, self.cipher))

    def test_browser_text_is_owner_scoped_and_validated(self):
        scan = self._claim_and_publish()
        with self.assertRaises(NotFound):
            self.service.queue_text(self.other.id, scan.id, "123456", self.cipher)
        for value in (
            "",
            "a" * (TEXT_MAX_LENGTH + 1),
            "password\nwith-control",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    self.service.queue_text(self.owner.id, scan.id, value, self.cipher)
        self.service.queue_text(self.owner.id, scan.id, "123456", self.cipher)
        self._claim_text(scan.id, "123456")

    def test_request_id_replay_is_idempotent_and_new_request_waits_for_result(self):
        scan = self._claim_and_publish()
        first_request = "00000000-0000-4000-8000-000000000001"
        second_request = "00000000-0000-4000-8000-000000000002"

        first = self.service.queue_text(
            self.owner.id,
            scan.id,
            "first candidate",
            self.cipher,
            request_id=first_request,
        )
        replay = self.service.queue_text(
            self.owner.id,
            scan.id,
            "different body is ignored on an idempotent replay",
            self.cipher,
            request_id=first_request,
        )

        self.assertEqual(first.id, replay.id)
        self.assertEqual(1, self.service.public_text_interaction(replay)[
            "session_text_attempt_number"
        ])
        with self.assertRaisesRegex(Conflict, "^interaction_in_progress$"):
            self.service.queue_text(
                self.owner.id,
                scan.id,
                "second candidate",
                self.cipher,
                request_id=second_request,
            )

        claimed = self._claim_text(scan.id, "first candidate")
        self.assertEqual(DELIVERY_CLAIMED, first.delivery_status)
        with self.assertRaisesRegex(Conflict, "^interaction_in_progress$"):
            self.service.queue_text(
                self.owner.id,
                scan.id,
                "second candidate",
                self.cipher,
                request_id=second_request,
            )

        self.service.mark_text_interaction_uncertain(
            scan.id, claimed["interaction_id"]
        )
        second = self.service.queue_text(
            self.owner.id,
            scan.id,
            "second candidate",
            self.cipher,
            request_id=second_request,
        )
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(
            2,
            self.service.public_text_interaction(second)[
                "session_text_attempt_number"
            ],
        )

    def test_delivery_receipt_transitions_are_persistent_idempotent_and_private(self):
        scan = self._claim_and_publish()
        pending = self.service.queue_text(
            self.owner.id,
            scan.id,
            "private password",
            self.cipher,
            request_id="00000000-0000-4000-8000-000000000001",
        )
        queued = self.service.public_text_interaction(pending)

        self.assertEqual(DELIVERY_QUEUED, queued["delivery_status"])
        self.assertIsNone(queued["attempted_at"])
        self.assertIsNone(queued["finished_at"])
        self.assertNotIn("private password", repr(queued))
        with self.assertRaises(NotFound):
            self.service.get_text_interaction_owned(
                self.other.id, scan.id, pending.id
            )

        claimed = self._claim_text(scan.id, "private password")
        receipt = self.service.public_text_interaction(pending)
        self.assertEqual(DELIVERY_CLAIMED, receipt["delivery_status"])
        self.assertIsNotNone(receipt["attempted_at"])
        self.assertIsNone(receipt["finished_at"])

        delivered = self.service.mark_text_interaction_delivered(
            scan.id, claimed["interaction_id"]
        )
        repeated = self.service.mark_text_interaction_delivered(
            scan.id, claimed["interaction_id"]
        )
        self.assertEqual(delivered.id, repeated.id)
        self.assertEqual(DELIVERY_DELIVERED, delivered.delivery_status)
        self.assertIsNotNone(delivered.finished_at)
        with self.assertRaisesRegex(Conflict, "^interaction_state_conflict$"):
            self.service.mark_text_interaction_failed(
                scan.id, claimed["interaction_id"]
            )

    def test_terminal_scan_marks_claimed_text_uncertain(self):
        scan = self._claim_and_publish()
        pending = self.service.queue_text(
            self.owner.id, scan.id, "password", self.cipher
        )
        self._claim_text(scan.id, "password")

        self.service.fail(scan.id, "automation_failed")

        self.assertEqual(DELIVERY_UNCERTAIN, pending.delivery_status)
        self.assertIsNotNone(pending.finished_at)

    def test_unicode_password_is_encrypted_and_claimed_once(self):
        scan = self._claim_and_publish()
        password = " 密码-P@ss w0rd![]{} "

        pending = self.service.queue_text(
            self.owner.id, scan.id, password, self.cipher
        )

        self.assertIsNotNone(pending.ciphertext)
        self.assertIsNotNone(pending.nonce)
        self.assertNotIn(password.encode("utf-8"), pending.ciphertext)
        self.assertNotIn(password.encode("utf-8"), repr(pending).encode())
        claimed = self._claim_text(scan.id, password)
        self.assertEqual(pending.id, claimed["interaction_id"])
        self.assertEqual(pending.request_id, claimed["request_id"])
        self.assertIsNone(pending.ciphertext)
        self.assertIsNone(pending.nonce)
        self.assertIsNone(self.service.claim_interaction(scan.id, self.cipher))

    def test_browser_text_accepts_printable_unicode_length_boundaries(self):
        scan = self._claim_and_publish()

        for value in (" ", "密" * TEXT_MAX_LENGTH):
            with self.subTest(length=len(value)):
                pending = self.service.queue_text(
                    self.owner.id, scan.id, value, self.cipher
                )
                self.assertIsNotNone(pending.ciphertext)
                claimed = self._claim_text(scan.id, value)
                self.service.mark_text_interaction_delivered(
                    scan.id, claimed["interaction_id"]
                )

    def test_legacy_unprefixed_otp_is_still_claimable(self):
        scan = self._claim_and_publish()
        encrypted = self.cipher.encrypt(b"246810")
        pending = DouyinLoginInput(
            scan_id=scan.id,
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            created_at=self.clock.value,
        )
        self.session.add(pending)
        self.session.flush()

        self._claim_text(scan.id, "246810")
        self.assertIsNone(pending.ciphertext)
        self.assertIsNone(pending.nonce)

    def test_legacy_prefixed_otp_is_still_claimable(self):
        scan = self._claim_and_publish()
        encrypted = self.cipher.encrypt(OTP_PREFIX + b"135790")
        pending = DouyinLoginInput(
            scan_id=scan.id,
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            created_at=self.clock.value,
        )
        self.session.add(pending)
        self.session.flush()

        self._claim_text(scan.id, "135790")

    def test_new_request_is_accepted_after_previous_attempt_finishes(self):
        scan = self._needs_verification()
        older = self.service.queue_text(
            self.owner.id,
            scan.id,
            "123456",
            self.cipher,
            request_id="00000000-0000-4000-8000-000000000001",
        )
        first_claim = self._claim_text(scan.id, "123456")
        self.service.mark_text_interaction_failed(
            scan.id, first_claim["interaction_id"]
        )
        latest = self.service.queue_text(
            self.owner.id,
            scan.id,
            "654321",
            self.cipher,
            request_id="00000000-0000-4000-8000-000000000002",
        )

        self.assertIsNone(older.ciphertext)
        self.assertIsNone(older.nonce)
        self.assertIsNotNone(older.consumed_at)
        self.assertEqual(DELIVERY_FAILED, older.delivery_status)
        self.assertNotIn(b"654321", latest.ciphertext)
        claimed = self._claim_text(scan.id, "654321")
        self.assertEqual(2, claimed["session_text_attempt_number"])
        self.assertIsNone(latest.ciphertext)
        self.assertIsNone(latest.nonce)
        self.assertIsNotNone(latest.consumed_at)
        self.assertIsNone(self.service.claim_interaction(scan.id, self.cipher))

    def test_expired_verification_code_is_erased_without_decryption(self):
        scan = self._needs_verification()
        pending = self.service.queue_text(
            self.owner.id, scan.id, "123456", self.cipher
        )
        self.clock.value += OTP_LIFETIME

        self.assertIsNone(self.service.claim_interaction(scan.id, self.cipher))
        self.assertIsNone(pending.ciphertext)
        self.assertIsNone(pending.nonce)
        self.assertIsNotNone(pending.consumed_at)

    def test_terminal_paths_erase_pending_codes_and_consume_pending_actions(self):
        for terminal in (
            "complete",
            "fail",
            "cancel",
            "expire_stale",
            "expire_one",
            "abandoned",
        ):
            with self.subTest(terminal=terminal):
                scan = self._needs_verification()
                pending = self.service.queue_text(
                    self.owner.id, scan.id, "123456", self.cipher
                )
                pending_action = DouyinLoginAction(
                    scan_id=scan.id,
                    kind="click",
                    x_million=250_000,
                    y_million=750_000,
                    created_at=self.clock.value,
                )
                consumed_at = self.clock.value - timedelta(seconds=1)
                consumed_action = DouyinLoginAction(
                    scan_id=scan.id,
                    kind="click",
                    x_million=500_000,
                    y_million=500_000,
                    created_at=consumed_at,
                    consumed_at=consumed_at,
                )
                self.session.add_all([pending_action, consumed_action])
                self.session.flush()
                if terminal == "complete":
                    self.service.complete(scan.id, self._account().id)
                elif terminal == "fail":
                    self.service.fail(scan.id, "automation_failed")
                elif terminal == "cancel":
                    self.service.cancel_owned(self.owner.id, scan.id)
                elif terminal == "expire_stale":
                    self.clock.value += timedelta(minutes=5)
                    self.service.expire_stale()
                elif terminal == "expire_one":
                    self.clock.value += timedelta(minutes=5)
                    self.service.complete(scan.id, self._account().id)
                else:
                    self.service.fail_abandoned_browser_sessions()
                self.assertIsNone(pending.ciphertext)
                self.assertIsNone(pending.nonce)
                self.assertIsNotNone(pending.consumed_at)
                self.assertIsNotNone(pending_action.consumed_at)
                self.assertEqual(consumed_at, consumed_action.consumed_at)
                self.assertIsNone(
                    self.session.get(DouyinLoginChallenge, scan.id)
                )

    def test_claim_next_returns_none_when_no_queued_session_exists(self):
        self.assertIsNone(self.service.claim_next())

    def test_claim_next_uses_fifo_across_owner_slots(self):
        first = self.service.start(self.owner.id)
        self.clock.value += timedelta(seconds=1)
        second = self.service.start(self.other.id)

        self.assertEqual(first.id, self.service.claim_next().id)
        self.assertEqual(second.id, self.service.claim_next().id)
        self.assertIsNone(self.service.claim_next())

    def test_invalid_transition_is_rejected_without_mutating_session(self):
        scan = self.service.start(self.owner.id)

        with self.assertRaisesRegex(Conflict, "^invalid_transition$"):
            self.service.mark_confirming(scan.id)

        self.assertEqual(ScanStatus.QUEUED, scan.status)
        self.assertEqual(scan_slot_for_owner(self.owner.id), scan.slot)

    def test_browser_view_requires_png_signature_and_enforces_four_mib_limit(self):
        scan = self.service.start(self.owner.id)
        self.service.claim_next()

        self.assertEqual(4 * 1024 * 1024, MAX_QR_PNG_BYTES)
        with self.assertRaises(ValidationError):
            self.service.publish_qr(scan.id, b"not-a-png")

        valid = PNG_SIGNATURE + b"x" * (
            2 * 1024 * 1024 - len(PNG_SIGNATURE)
        )
        published = self.service.publish_qr(scan.id, valid)
        self.assertEqual(valid, published.qr_png)

        oversized = PNG_SIGNATURE + b"x" * (
            MAX_QR_PNG_BYTES - len(PNG_SIGNATURE) + 1
        )
        with self.assertRaises(ValidationError):
            self.service.publish_view(scan.id, oversized)

        self.assertEqual(ScanStatus.AWAITING_SCAN, published.status)

    def test_browser_view_revision_is_stable_and_changes_only_with_content(self):
        scan = self._claim_and_publish()
        original = scan.qr_png
        original_revision = browser_view_revision(original)

        same = self.service.publish_view(scan.id, original)
        self.assertEqual(original_revision, browser_view_revision(same.qr_png))
        self.assertEqual(original, same.qr_png)

        changed = self.service.publish_view(scan.id, PNG_SIGNATURE + b"changed")
        self.assertNotEqual(original_revision, browser_view_revision(changed.qr_png))
        self.assertEqual(PNG_SIGNATURE + b"changed", changed.qr_png)

    def test_cancel_clears_qr_and_releases_owner_slot(self):
        scan = self._claim_and_publish()

        cancelled = self.service.cancel_owned(self.owner.id, scan.id)

        self.assertEqual(ScanStatus.CANCELLED, cancelled.status)
        self.assertEqual("cancelled", cancelled.error_code)
        self.assertIsNone(cancelled.qr_png)
        self.assertIsNone(cancelled.slot)
        self.service.start(self.owner.id)

    def test_failure_clears_qr_and_releases_owner_slot(self):
        scan = self._claim_and_publish()

        failed = self.service.fail(scan.id, "qr_load_failed")

        self.assertEqual(ScanStatus.FAILED, failed.status)
        self.assertEqual("qr_load_failed", failed.error_code)
        self.assertIsNone(failed.qr_png)
        self.assertIsNone(failed.slot)
        self.service.start(self.owner.id)

    def test_failure_rejects_non_public_error_codes(self):
        scan = self.service.start(self.owner.id)

        with self.assertRaises(ValidationError):
            self.service.fail(scan.id, "browser stack trace")

        self.assertEqual(ScanStatus.QUEUED, scan.status)
        self.assertIsNone(scan.error_code)

    def test_expire_stale_clears_qr_releases_slot_and_is_idempotent(self):
        scan = self._claim_and_publish()
        self.clock.value += timedelta(minutes=5)

        self.assertEqual(1, self.service.expire_stale())

        self.assertEqual(ScanStatus.EXPIRED, scan.status)
        self.assertEqual("login_timeout", scan.error_code)
        self.assertIsNone(scan.qr_png)
        self.assertIsNone(scan.slot)
        self.assertEqual(self.clock.value, scan.finished_at)
        self.assertEqual(0, self.service.expire_stale())
        self.service.start(self.owner.id)

    def test_worker_startup_cleanup_expires_abandoned_queued_session(self):
        abandoned = self.service.start(self.owner.id)
        self.clock.value += timedelta(minutes=31)

        cleaned = ScanSessionService(self.session, now=self.clock).expire_stale()

        self.assertEqual(1, cleaned)
        self.assertEqual(ScanStatus.EXPIRED, abandoned.status)
        self.assertIsNone(abandoned.slot)

    def test_public_status_has_only_owner_safe_fields(self):
        scan = self._claim_and_publish()
        self.clock.value += timedelta(seconds=61)

        public = self.service.public_status(scan, self.clock.value)

        self.assertEqual(
            {
                "id",
                "status",
                "remaining_seconds",
                "error",
                "message",
                "account_id",
                "needs_verification",
                "verification_kind",
                "masked_phone",
                "verification_sent",
                "send_status",
                "can_retry_send",
                "resend_pending",
                "can_resend",
                "resend_after_seconds",
                "frame_revision",
            },
            set(public),
        )
        self.assertEqual(scan.id, public["id"])
        self.assertEqual("awaiting_scan", public["status"])
        self.assertEqual(239, public["remaining_seconds"])
        self.assertIsNone(public["error"])
        self.assertFalse(public["needs_verification"])
        self.assertIsNone(public["verification_kind"])
        self.assertIsNone(public["account_id"])
        self.assertEqual(
            browser_view_revision(PNG_SIGNATURE + b"fixture"),
            public["frame_revision"],
        )
        self.assertIsInstance(public["message"], str)
        serialized = repr(public)
        self.assertNotIn("owner_user_id", serialized)
        self.assertNotIn("qr_png", serialized)
        self.assertNotIn("slot", serialized)

    def test_stale_worker_cannot_publish_after_owner_cancels(self):
        scan = self.service.start(self.owner.id)
        self.session.commit()

        with Session(self.engine, expire_on_commit=False) as worker_session:
            worker = ScanSessionService(worker_session, now=self.clock)
            worker_scan = worker.claim_next()
            worker_session.commit()
            self.assertEqual(ScanStatus.LOADING_QR, worker_scan.status)

            self.service.cancel_owned(self.owner.id, scan.id)
            self.session.commit()

            with self.assertRaisesRegex(Conflict, "^transition_conflict$"):
                worker.publish_qr(scan.id, PNG_SIGNATURE + b"late")

        with Session(self.engine) as verification:
            persisted = verification.get(DouyinLoginSession, scan.id)
            self.assertEqual(ScanStatus.CANCELLED, persisted.status)
            self.assertIsNone(persisted.slot)
            self.assertIsNone(persisted.qr_png)

    def test_stale_worker_cannot_complete_after_session_fails(self):
        account = self._account()
        scan = self.service.start(self.owner.id)
        self.session.commit()

        with Session(self.engine, expire_on_commit=False) as worker_session:
            worker = ScanSessionService(worker_session, now=self.clock)
            worker.claim_next()
            worker_scan = worker.publish_qr(scan.id, PNG_SIGNATURE + b"fixture")
            worker_session.commit()
            self.assertEqual(ScanStatus.AWAITING_SCAN, worker_scan.status)

            self.service.fail(scan.id, "automation_failed")
            self.session.commit()

            with self.assertRaisesRegex(Conflict, "^transition_conflict$"):
                worker.complete(scan.id, account.id)

        with Session(self.engine) as verification:
            persisted = verification.get(DouyinLoginSession, scan.id)
            self.assertEqual(ScanStatus.FAILED, persisted.status)
            self.assertEqual("automation_failed", persisted.error_code)
            self.assertIsNone(persisted.account_id)

    def test_transition_requires_persisted_owner_slot(self):
        scan = self.service.start(self.owner.id)
        self.service.claim_next()
        self.session.commit()

        with Session(self.engine) as concurrent:
            concurrent_scan = concurrent.get(DouyinLoginSession, scan.id)
            concurrent_scan.slot = None
            concurrent.commit()

        with self.assertRaisesRegex(Conflict, "^transition_conflict$"):
            self.service.publish_qr(scan.id, PNG_SIGNATURE + b"late")

        with Session(self.engine) as verification:
            persisted = verification.get(DouyinLoginSession, scan.id)
            self.assertEqual(ScanStatus.LOADING_QR, persisted.status)
            self.assertIsNone(persisted.slot)
            self.assertIsNone(persisted.qr_png)

    def test_claim_next_expires_queued_session_at_deadline(self):
        scan = self.service.start(self.owner.id)
        self.session.commit()
        self.clock.value += timedelta(minutes=30)

        self.assertIsNone(self.service.claim_next())

        with Session(self.engine) as verification:
            persisted = verification.get(DouyinLoginSession, scan.id)
            self.assertEqual(ScanStatus.EXPIRED, persisted.status)
            self.assertEqual("login_timeout", persisted.error_code)
            self.assertIsNone(persisted.slot)

    def test_every_active_transition_expires_session_at_deadline(self):
        cases = (
            ("publish", ScanStatus.LOADING_QR),
            ("confirm", ScanStatus.AWAITING_SCAN),
            ("complete", ScanStatus.AWAITING_SCAN),
            ("fail", ScanStatus.LOADING_QR),
            ("cancel", ScanStatus.QUEUED),
        )
        for case, expected_source in cases:
            with self.subTest(case=case):
                scan = self.service.start(self.owner.id)
                if case != "cancel":
                    self.service.claim_next()
                if case in {"confirm", "complete"}:
                    self.service.publish_qr(scan.id, PNG_SIGNATURE + b"fixture")
                self.session.commit()
                self.assertEqual(expected_source, scan.status)
                self.clock.value += timedelta(
                    minutes=30 if case == "cancel" else 5
                )

                try:
                    if case == "publish":
                        result = self.service.publish_qr(
                            scan.id, PNG_SIGNATURE + b"late"
                        )
                    elif case == "confirm":
                        result = self.service.mark_confirming(scan.id)
                    elif case == "complete":
                        result = self.service.complete(scan.id, self._account().id)
                    elif case == "fail":
                        result = self.service.fail(scan.id, "automation_failed")
                    else:
                        result = self.service.cancel_owned(self.owner.id, scan.id)

                    self.assertEqual(ScanStatus.EXPIRED, result.status)
                finally:
                    self.service.expire_stale()

                self.session.commit()
                with Session(self.engine) as verification:
                    persisted = verification.get(DouyinLoginSession, scan.id)
                    self.assertEqual(ScanStatus.EXPIRED, persisted.status)
                    self.assertEqual("login_timeout", persisted.error_code)
                    self.assertIsNone(persisted.slot)
                    self.assertIsNone(persisted.qr_png)
                    self.assertIsNone(persisted.account_id)

    def test_public_status_sanitizes_unknown_persisted_error_code(self):
        scan = self.service.start(self.owner.id)
        scan.status = ScanStatus.FAILED
        scan.slot = None
        scan.error_code = "internal-driver-diagnostic"
        self.session.flush()

        public = self.service.public_status(scan, self.clock.value)

        self.assertEqual("automation_failed", public["error"])
        self.assertEqual("自动化登录失败，请重试", public["message"])
        self.assertNotIn("internal-driver-diagnostic", repr(public))

    def test_existing_sqlite_session_rows_need_no_verification_kind_migration(self):
        legacy_engine = create_engine("sqlite:///:memory:")
        try:
            legacy_tables = tuple(
                table
                for table in Base.metadata.sorted_tables
                if table.name != "douyin_login_challenge_kinds"
            )
            Base.metadata.create_all(legacy_engine, tables=legacy_tables)
            with Session(legacy_engine) as legacy:
                owner = User(
                    username="legacy-owner",
                    password_hash="hash",
                    role="user",
                )
                legacy.add(owner)
                legacy.flush()
                scan = DouyinLoginSession(
                    owner_user_id=owner.id,
                    slot="legacy-slot",
                    status=ScanStatus.CONFIRMING,
                    error_code=NEEDS_VERIFICATION,
                    expires_at=self.clock.value + timedelta(minutes=5),
                    created_at=self.clock.value,
                    updated_at=self.clock.value,
                )
                legacy.add(scan)
                legacy.commit()
                scan_id = scan.id

            session_columns_before = {
                column["name"]
                for column in inspect(legacy_engine).get_columns(
                    "douyin_login_sessions"
                )
            }
            create_schema(legacy_engine)
            session_columns_after = {
                column["name"]
                for column in inspect(legacy_engine).get_columns(
                    "douyin_login_sessions"
                )
            }

            self.assertEqual(session_columns_before, session_columns_after)
            with Session(legacy_engine) as upgraded:
                persisted = upgraded.get(DouyinLoginSession, scan_id)
                public = ScanSessionService(
                    upgraded, now=self.clock
                ).public_status(persisted)
                self.assertEqual("sms", public["verification_kind"])
                self.assertEqual(NEEDS_VERIFICATION, public["error"])
        finally:
            legacy_engine.dispose()


if __name__ == "__main__":
    unittest.main()
