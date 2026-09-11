import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import create_schema, session_scope
from spark_console.models import (
    DouyinAccount,
    DouyinLoginAction,
    DouyinLoginInput,
    DouyinLoginSession,
    User,
)
from spark_console.security import PasswordService
from spark_console.services.audits import AuditService
from spark_console.services.scan_sessions import PNG_SIGNATURE, ScanSessionService
from spark_console.services.subscriptions import SubscriptionService
from spark_console.web.app import create_app


class AccountScanWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(
            data_dir=root,
            database_url=f"sqlite:///{root / 'test.db'}",
            cookie_key_file=root / "cookie.key",
            session_key_file=root / "session.key",
        )
        self.settings.cookie_key_file.write_bytes(b"c" * 32)
        self.settings.session_key_file.write_bytes(b"s" * 32)
        self.engine = create_engine(
            self.settings.database_url, connect_args={"check_same_thread": False}
        )
        create_schema(self.engine)
        password_hash = PasswordService().hash("Permanent-123!")
        with session_scope(self.engine) as db:
            owner = User(
                username="owner",
                password_hash=password_hash,
                role="user",
                must_change_password=False,
            )
            other = User(
                username="other",
                password_hash=password_hash,
                role="user",
                must_change_password=False,
            )
            admin = User(
                username="admin",
                password_hash=password_hash,
                role="admin",
                must_change_password=False,
            )
            db.add_all([owner, other, admin])
            db.flush()
            self.owner_id = owner.id
            self.other_id = other.id
            self.admin_id = admin.id
            account = DouyinAccount(
                owner_user_id=owner.id,
                display_name="旧备注",
                encrypted_cookies=b"encrypted",
                cookie_nonce=b"nonce",
                validation_state="valid",
                last_verified_at=datetime(2026, 8, 31, 4, 5, 6, tzinfo=timezone.utc),
            )
            db.add(account)
            db.flush()
            self.account_id = account.id
            SubscriptionService(db, AuditService(db)).extend(
                admin.id,
                owner.id,
                duration_days=30,
                max_accounts=2,
                max_tasks=20,
            )

        app = create_app(self.settings, self.engine)
        self.owner_client = TestClient(app, base_url="https://testserver")
        self.other_client = TestClient(app, base_url="https://testserver")
        self.admin_client = TestClient(app, base_url="https://testserver")
        self._login(self.owner_client, "owner")
        self._login(self.other_client, "other")
        self._login(self.admin_client, "admin")
        self.owner_csrf = self._csrf(self.owner_client)
        self.other_csrf = self._csrf(self.other_client)
        self.admin_csrf = self._csrf(self.admin_client)

    def tearDown(self):
        self.owner_client.close()
        self.other_client.close()
        self.admin_client.close()
        self.engine.dispose()
        self.temp.cleanup()

    @staticmethod
    def _login(client, username):
        response = client.post(
            "/login",
            data={"username": username, "password": "Permanent-123!"},
            follow_redirects=False,
        )
        if response.status_code != 303:
            raise AssertionError(response.text)

    @staticmethod
    def _csrf(client):
        page = client.get("/accounts")
        marker = (
            'data-csrf-token="'
            if 'data-csrf-token="' in page.text
            else 'name="csrf_token" value="'
        )
        return page.text.split(marker, 1)[1].split('"', 1)[0]

    @staticmethod
    def _request_id(number=1):
        return f"00000000-0000-4000-8000-{number:012d}"

    def _awaiting_scan(self):
        with session_scope(self.engine) as db:
            service = ScanSessionService(db)
            scan = service.start(self.owner_id)
            scan_id = scan.id
            service.claim_next()
            service.publish_qr(scan_id, PNG_SIGNATURE + b"fixture")
        return scan_id

    def test_authenticated_owner_can_start_with_csrf_and_client_fields_are_ignored(self):
        response = self.owner_client.post(
            "/accounts/scan",
            data={
                "csrf_token": self.owner_csrf,
                "owner_id": self.other_id,
                "status": "succeeded",
                "display_name": "browser supplied",
                "cookies": "credential-marker",
            },
        )

        self.assertEqual(201, response.status_code)
        self.assertEqual("no-store", response.headers["cache-control"])
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
                "can_resend",
                "resend_after_seconds",
                "resend_pending",
                "frame_revision",
            },
            set(response.json()),
        )
        with session_scope(self.engine) as db:
            scan = db.scalar(select(DouyinLoginSession))
            self.assertEqual(self.owner_id, scan.owner_user_id)
            self.assertEqual("queued", scan.status)
            self.assertEqual(1, len(db.scalars(select(DouyinAccount)).all()))

    def test_start_requires_login_csrf_and_active_membership(self):
        anonymous = TestClient(
            create_app(self.settings, self.engine), base_url="https://testserver"
        )
        try:
            redirected = anonymous.post(
                "/accounts/scan", data={"csrf_token": "unused"}, follow_redirects=False
            )
        finally:
            anonymous.close()
        self.assertEqual(303, redirected.status_code)
        self.assertEqual("/login", redirected.headers["location"])

        missing_csrf = self.owner_client.post("/accounts/scan")
        self.assertEqual(403, missing_csrf.status_code)

        created = self.owner_client.post(
            "/accounts/scan", data={"csrf_token": self.owner_csrf}
        )
        self.assertEqual(201, created.status_code)
        other = self.other_client.post(
            "/accounts/scan", data={"csrf_token": self.other_csrf}
        )
        self.assertEqual(403, other.status_code)
        self.assertEqual(
            {
                "error": "subscription_inactive",
                "message": "会员未开通或已过期，请开通后重试",
            },
            other.json(),
        )

    def test_start_rejects_exhausted_account_quota(self):
        with session_scope(self.engine) as db:
            db.add(
                DouyinAccount(
                    owner_user_id=self.owner_id,
                    display_name="第二个账号",
                    encrypted_cookies=b"encrypted-2",
                    cookie_nonce=b"nonce-2",
                    validation_state="valid",
                )
            )

        response = self.owner_client.post(
            "/accounts/scan", data={"csrf_token": self.owner_csrf}
        )

        self.assertEqual(409, response.status_code)
        self.assertEqual(
            {
                "error": "account_limit_reached",
                "message": "抖音账号额度已用完，请升级套餐或删除旧账号",
            },
            response.json(),
        )
        with Session(self.engine) as db:
            self.assertIsNone(db.scalar(select(DouyinLoginSession)))

    def test_relogin_existing_account_succeeds_when_account_quota_is_full(self):
        with session_scope(self.engine) as db:
            db.add(
                DouyinAccount(
                    owner_user_id=self.owner_id,
                    display_name="第二个账号",
                    encrypted_cookies=b"encrypted-2",
                    cookie_nonce=b"nonce-2",
                    validation_state="valid",
                )
            )

        response = self.owner_client.post(
            "/accounts/scan",
            data={"csrf_token": self.owner_csrf, "account_id": self.account_id},
        )

        self.assertEqual(201, response.status_code)
        self.assertEqual(self.account_id, response.json()["account_id"])
        with Session(self.engine) as db:
            scan = db.scalar(select(DouyinLoginSession))
            self.assertIsNotNone(scan)
            self.assertEqual(self.account_id, scan.account_id)

    def test_same_owner_resumes_active_scan_instead_of_reporting_busy(self):
        created = self.owner_client.post(
            "/accounts/scan", data={"csrf_token": self.owner_csrf}
        )

        resumed = self.owner_client.post(
            "/accounts/scan", data={"csrf_token": self.owner_csrf}
        )

        self.assertEqual(201, created.status_code)
        self.assertEqual(200, resumed.status_code)
        self.assertEqual(created.json()["id"], resumed.json()["id"])

    def test_same_owner_resumes_active_relogin_for_the_same_account(self):
        created = self.owner_client.post(
            "/accounts/scan",
            data={"csrf_token": self.owner_csrf, "account_id": self.account_id},
        )

        resumed = self.owner_client.post(
            "/accounts/scan",
            data={"csrf_token": self.owner_csrf, "account_id": self.account_id},
        )

        self.assertEqual(201, created.status_code)
        self.assertEqual(200, resumed.status_code)
        self.assertEqual(created.json()["id"], resumed.json()["id"])
        self.assertEqual(self.account_id, resumed.json()["account_id"])

    def test_active_relogin_rejects_a_different_account_target(self):
        with session_scope(self.engine) as db:
            second = DouyinAccount(
                owner_user_id=self.owner_id,
                display_name="第二个账号",
                encrypted_cookies=b"encrypted-2",
                cookie_nonce=b"nonce-2",
                validation_state="valid",
            )
            db.add(second)
            db.flush()
            second_id = second.id

        created = self.owner_client.post(
            "/accounts/scan",
            data={"csrf_token": self.owner_csrf, "account_id": self.account_id},
        )
        conflict = self.owner_client.post(
            "/accounts/scan",
            data={"csrf_token": self.owner_csrf, "account_id": second_id},
        )

        self.assertEqual(201, created.status_code)
        self.assertEqual(409, conflict.status_code)
        self.assertEqual(
            {
                "error": "scan_target_conflict",
                "message": "已有其他账号登录会话正在进行，请先完成或取消后再试",
            },
            conflict.json(),
        )
        with Session(self.engine) as db:
            scan = db.get(DouyinLoginSession, created.json()["id"])
            self.assertEqual(self.account_id, scan.account_id)

    def test_active_new_binding_rejects_a_relogin_target(self):
        created = self.owner_client.post(
            "/accounts/scan", data={"csrf_token": self.owner_csrf}
        )

        conflict = self.owner_client.post(
            "/accounts/scan",
            data={"csrf_token": self.owner_csrf, "account_id": self.account_id},
        )

        self.assertEqual(201, created.status_code)
        self.assertEqual(409, conflict.status_code)
        self.assertEqual("scan_target_conflict", conflict.json()["error"])
        with Session(self.engine) as db:
            scan = db.get(DouyinLoginSession, created.json()["id"])
            self.assertIsNone(scan.account_id)

    def test_active_relogin_rejects_a_new_binding_target(self):
        created = self.owner_client.post(
            "/accounts/scan",
            data={"csrf_token": self.owner_csrf, "account_id": self.account_id},
        )

        conflict = self.owner_client.post(
            "/accounts/scan", data={"csrf_token": self.owner_csrf}
        )

        self.assertEqual(201, created.status_code)
        self.assertEqual(409, conflict.status_code)
        self.assertEqual("scan_target_conflict", conflict.json()["error"])
        with Session(self.engine) as db:
            scan = db.get(DouyinLoginSession, created.json()["id"])
            self.assertEqual(self.account_id, scan.account_id)

    def test_start_requests_are_rate_limited_with_a_stable_error(self):
        responses = [
            self.owner_client.post(
                "/accounts/scan", data={"csrf_token": self.owner_csrf}
            )
            for _ in range(6)
        ]

        self.assertEqual([201, 200, 200, 200, 200, 429], [r.status_code for r in responses])
        self.assertEqual(
            {"error": "rate_limited", "message": "请求过于频繁，请稍后重试"},
            responses[-1].json(),
        )

    def test_polling_and_qr_requests_have_independent_safe_rate_limits(self):
        scan_id = self._awaiting_scan()

        status_responses = [
            self.owner_client.get(f"/accounts/scan/{scan_id}") for _ in range(91)
        ]
        qr_responses = [
            self.owner_client.get(f"/accounts/scan/{scan_id}/qr") for _ in range(91)
        ]

        self.assertTrue(all(response.status_code == 200 for response in status_responses[:90]))
        self.assertTrue(all(response.status_code == 200 for response in qr_responses[:90]))
        for response in (status_responses[-1], qr_responses[-1]):
            self.assertEqual(429, response.status_code)
            self.assertEqual(
                {"error": "rate_limited", "message": "请求过于频繁，请稍后重试"},
                response.json(),
            )

    def test_cancel_requests_are_rate_limited_independently(self):
        responses = []
        for _ in range(11):
            with session_scope(self.engine) as db:
                scan_id = ScanSessionService(db).start(self.owner_id).id
            responses.append(
                self.owner_client.post(
                    f"/accounts/scan/{scan_id}/cancel",
                    data={"csrf_token": self.owner_csrf},
                )
            )

        self.assertTrue(all(response.status_code == 200 for response in responses[:10]))
        self.assertEqual(429, responses[-1].status_code)
        self.assertEqual("rate_limited", responses[-1].json()["error"])

    def test_status_is_owner_only_no_store_and_exactly_public_projection(self):
        scan_id = self._awaiting_scan()

        response = self.owner_client.get(f"/accounts/scan/{scan_id}")

        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store", response.headers["cache-control"])
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
                "can_resend",
                "resend_after_seconds",
                "resend_pending",
                "frame_revision",
            },
            set(response.json()),
        )
        self.assertEqual("awaiting_scan", response.json()["status"])
        self.assertNotIn("qr_png", response.text)
        self.assertNotIn(self.owner_id, response.text)
        for client in (self.other_client, self.admin_client):
            hidden = client.get(f"/accounts/scan/{scan_id}")
            self.assertEqual(404, hidden.status_code)
            self.assertEqual(
                {"error": "not_found", "message": "未找到扫码会话"},
                hidden.json(),
            )

    def test_owner_can_fetch_no_store_qr_but_other_user_and_admin_get_404(self):
        scan_id = self._awaiting_scan()

        response = self.owner_client.get(f"/accounts/scan/{scan_id}/qr")

        self.assertEqual(200, response.status_code)
        self.assertEqual("image/png", response.headers["content-type"])
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertEqual(PNG_SIGNATURE + b"fixture", response.content)
        revision = response.headers["x-frame-revision"]
        self.assertEqual(f'"{revision}"', response.headers["etag"])
        unchanged = self.owner_client.get(
            f"/accounts/scan/{scan_id}/qr",
            headers={"If-None-Match": response.headers["etag"]},
        )
        self.assertEqual(304, unchanged.status_code)
        self.assertEqual(b"", unchanged.content)
        self.assertEqual("no-store", unchanged.headers["cache-control"])
        self.assertEqual(revision, unchanged.headers["x-frame-revision"])
        self.assertEqual(
            404, self.other_client.get(f"/accounts/scan/{scan_id}/qr").status_code
        )
        self.assertEqual(
            404, self.admin_client.get(f"/accounts/scan/{scan_id}/qr").status_code
        )

    def test_owner_can_forward_browser_click_but_other_users_cannot(self):
        scan_id = self._awaiting_scan()

        missing_csrf = self.owner_client.post(
            f"/accounts/scan/{scan_id}/interact",
            data={"kind": "click", "x": "0.25", "y": "0.75"},
        )
        accepted = self.owner_client.post(
            f"/accounts/scan/{scan_id}/interact",
            data={
                "csrf_token": self.owner_csrf,
                "kind": "click",
                "x": "0.25",
                "y": "0.75",
            },
        )

        self.assertEqual(403, missing_csrf.status_code)
        self.assertEqual(202, accepted.status_code)
        self.assertEqual({"accepted": True}, accepted.json())
        for client, csrf_token in (
            (self.other_client, self.other_csrf),
            (self.admin_client, self.admin_csrf),
        ):
            hidden = client.post(
                f"/accounts/scan/{scan_id}/interact",
                data={
                    "csrf_token": csrf_token,
                    "kind": "click",
                    "x": "0.25",
                    "y": "0.75",
                },
            )
            self.assertEqual(404, hidden.status_code)

        with session_scope(self.engine) as db:
            self.assertEqual(
                {"kind": "click", "x": 0.25, "y": 0.75},
                ScanSessionService(db).claim_interaction(scan_id),
            )

    def test_browser_click_missing_coordinates_and_unknown_kind_are_rejected(self):
        scan_id = self._awaiting_scan()

        invalid_requests = (
            {"csrf_token": self.owner_csrf},
            {
                "csrf_token": self.owner_csrf,
                "kind": "click",
                "x": "0.25",
            },
            {"csrf_token": self.owner_csrf, "kind": "pointer"},
        )

        for data in invalid_requests:
            with self.subTest(data=data):
                response = self.owner_client.post(
                    f"/accounts/scan/{scan_id}/interact", data=data
                )
                self.assertEqual(400, response.status_code)
                self.assertEqual("invalid_interaction", response.json()["error"])
        with session_scope(self.engine) as db:
            self.assertEqual([], list(db.scalars(select(DouyinLoginAction)).all()))

    def test_owner_can_submit_encrypted_numeric_text_during_any_active_scan(self):
        scan_id = self._awaiting_scan()

        hidden = self.other_client.post(
            f"/accounts/scan/{scan_id}/interact",
            data={
                "csrf_token": self.other_csrf,
                "kind": "text",
                "request_id": self._request_id(1),
                "text": "123456",
            },
        )
        response = self.owner_client.post(
            f"/accounts/scan/{scan_id}/interact",
            data={
                "csrf_token": self.owner_csrf,
                "kind": "text",
                "request_id": self._request_id(1),
                "text": "123456",
            },
        )

        self.assertEqual(404, hidden.status_code)
        self.assertEqual(202, response.status_code)
        receipt = response.json()
        self.assertTrue(receipt["accepted"])
        self.assertEqual(self._request_id(1), receipt["request_id"])
        self.assertEqual("queued", receipt["delivery_status"])
        self.assertEqual(1, receipt["session_text_attempt_number"])
        self.assertNotIn("123456", response.text)
        with session_scope(self.engine) as db:
            pending = db.scalar(select(DouyinLoginInput))
            self.assertIsNotNone(pending.ciphertext)
            self.assertNotIn(b"123456", pending.ciphertext)
            claimed = ScanSessionService(db).claim_interaction(
                scan_id, CookieCipher(b"c" * 32)
            )
            self.assertEqual("text", claimed["kind"])
            self.assertEqual("123456", claimed["text"])
            self.assertEqual(pending.id, claimed["interaction_id"])
            self.assertIsNone(pending.ciphertext)
            self.assertIsNone(pending.nonce)

    def test_owner_can_submit_encrypted_unicode_password_during_any_active_scan(self):
        scan_id = self._awaiting_scan()
        password = "密码-P@ss w0rd![]{}"

        response = self.owner_client.post(
            f"/accounts/scan/{scan_id}/interact",
            data={
                "csrf_token": self.owner_csrf,
                "kind": "text",
                "request_id": self._request_id(1),
                "text": password,
            },
        )

        self.assertEqual(202, response.status_code)
        receipt = response.json()
        self.assertTrue(receipt["accepted"])
        self.assertEqual(self._request_id(1), receipt["request_id"])
        self.assertNotIn(password, response.text)
        with session_scope(self.engine) as db:
            pending = db.scalar(select(DouyinLoginInput))
            self.assertIsNotNone(pending.ciphertext)
            self.assertNotIn(password.encode("utf-8"), pending.ciphertext)
            claimed = ScanSessionService(db).claim_interaction(
                scan_id, CookieCipher(b"c" * 32)
            )
            self.assertEqual("text", claimed["kind"])
            self.assertEqual(password, claimed["text"])
            self.assertIsNone(pending.ciphertext)
            self.assertIsNone(pending.nonce)

    def test_text_requests_are_idempotent_serialized_and_have_owner_receipts(self):
        scan_id = self._awaiting_scan()
        first_request = self._request_id(1)
        second_request = self._request_id(2)
        first = self.owner_client.post(
            f"/accounts/scan/{scan_id}/interact",
            data={
                "csrf_token": self.owner_csrf,
                "kind": "text",
                "request_id": first_request,
                "text": "first password",
            },
        )

        self.assertEqual(202, first.status_code)
        first_receipt = first.json()
        self.assertEqual("queued", first_receipt["delivery_status"])
        self.assertEqual(1, first_receipt["session_text_attempt_number"])
        interaction_id = first_receipt["interaction_id"]
        for _ in range(7):
            replay = self.owner_client.post(
                f"/accounts/scan/{scan_id}/interact",
                data={
                    "csrf_token": self.owner_csrf,
                    "kind": "text",
                    "request_id": first_request,
                    "text": "a replay cannot replace the accepted body",
                },
            )
            self.assertEqual(202, replay.status_code)
            self.assertEqual(interaction_id, replay.json()["interaction_id"])

        blocked = self.owner_client.post(
            f"/accounts/scan/{scan_id}/interact",
            data={
                "csrf_token": self.owner_csrf,
                "kind": "text",
                "request_id": second_request,
                "text": "second password",
            },
        )
        self.assertEqual(409, blocked.status_code)
        self.assertEqual("interaction_in_progress", blocked.json()["error"])
        self.assertEqual(interaction_id, blocked.json()["interaction_id"])

        receipt_url = (
            f"/accounts/scan/{scan_id}/interactions/{interaction_id}"
        )
        self.assertEqual(200, self.owner_client.get(receipt_url).status_code)
        self.assertEqual(404, self.other_client.get(receipt_url).status_code)
        self.assertEqual(404, self.admin_client.get(receipt_url).status_code)

        with session_scope(self.engine) as db:
            service = ScanSessionService(db)
            claimed = service.claim_interaction(
                scan_id, CookieCipher(b"c" * 32)
            )
            self.assertEqual("first password", claimed["text"])
            service.mark_text_interaction_failed(
                scan_id, claimed["interaction_id"]
            )
            self.assertEqual(
                1, len(list(db.scalars(select(DouyinLoginInput)).all()))
            )

        failed = self.owner_client.get(receipt_url)
        self.assertEqual("failed", failed.json()["delivery_status"])
        second = self.owner_client.post(
            f"/accounts/scan/{scan_id}/interact",
            data={
                "csrf_token": self.owner_csrf,
                "kind": "text",
                "request_id": second_request,
                "text": "second password",
            },
        )
        self.assertEqual(202, second.status_code)
        self.assertEqual(2, second.json()["session_text_attempt_number"])
        self.assertNotEqual(interaction_id, second.json()["interaction_id"])

    def test_text_request_requires_a_canonical_uuid(self):
        scan_id = self._awaiting_scan()

        for request_id in ("", "not-a-uuid", "{00000000-0000-4000-8000-000000000001}"):
            with self.subTest(request_id=request_id):
                response = self.owner_client.post(
                    f"/accounts/scan/{scan_id}/interact",
                    data={
                        "csrf_token": self.owner_csrf,
                        "kind": "text",
                        "request_id": request_id,
                        "text": "password",
                    },
                )
                self.assertEqual(400, response.status_code)
                self.assertEqual("invalid_request_id", response.json()["error"])

    def test_app_verification_keeps_live_page_and_accepts_numeric_text(self):
        scan_id = self._awaiting_scan()
        with session_scope(self.engine) as db:
            ScanSessionService(db).mark_needs_verification(
                scan_id,
                {
                    "kind": "app",
                    "masked_phone": "138****5678",
                    "verification_sent": True,
                    "can_resend": True,
                    "resend_after_seconds": 0,
                },
            )

        state = self.owner_client.get(f"/accounts/scan/{scan_id}")
        submitted = self.owner_client.post(
            f"/accounts/scan/{scan_id}/interact",
            data={
                "csrf_token": self.owner_csrf,
                "kind": "text",
                "request_id": self._request_id(1),
                "text": "123456",
            },
        )

        self.assertEqual(200, state.status_code)
        self.assertEqual("needs_app_verification", state.json()["error"])
        self.assertEqual("app", state.json()["verification_kind"])
        self.assertEqual("请在云端抖音页面完成额外验证", state.json()["message"])
        self.assertIsNone(state.json()["masked_phone"])
        self.assertFalse(state.json()["verification_sent"])
        self.assertFalse(state.json()["can_resend"])
        self.assertEqual(202, submitted.status_code)
        self.assertTrue(submitted.json()["accepted"])
        with session_scope(self.engine) as db:
            claimed = ScanSessionService(db).claim_interaction(
                scan_id, CookieCipher(b"c" * 32)
            )
            self.assertEqual("text", claimed["kind"])
            self.assertEqual("123456", claimed["text"])

    def test_browser_text_route_rejects_invalid_values_without_echo(self):
        scan_id = self._awaiting_scan()

        for value in ("a" * 257, "password\nwith-control"):
            with self.subTest(value=value):
                response = self.owner_client.post(
                    f"/accounts/scan/{scan_id}/interact",
                    data={
                        "csrf_token": self.owner_csrf,
                        "kind": "text",
                        "request_id": self._request_id(1),
                        "text": value,
                    },
                )
                self.assertEqual(400, response.status_code)
                self.assertNotIn(value, response.text)

        empty = self.owner_client.post(
            f"/accounts/scan/{scan_id}/interact",
            data={
                "csrf_token": self.owner_csrf,
                "kind": "text",
                "request_id": self._request_id(1),
                "text": "",
            },
        )
        self.assertEqual(400, empty.status_code)

    def test_interaction_route_rejects_unknown_kinds(self):
        scan_id = self._awaiting_scan()

        for kind in ("keyboard", "paste", "resend_sms"):
            with self.subTest(kind=kind):
                response = self.owner_client.post(
                    f"/accounts/scan/{scan_id}/interact",
                    data={
                        "csrf_token": self.owner_csrf,
                        "kind": kind,
                        "text": "123456",
                    },
                )
                self.assertEqual(400, response.status_code)
                self.assertNotIn("123456", response.text)

    def test_qr_before_publication_uses_a_stable_unavailable_error(self):
        response = self.owner_client.post(
            "/accounts/scan", data={"csrf_token": self.owner_csrf}
        )
        self.assertEqual(201, response.status_code)

        qr = self.owner_client.get(f"/accounts/scan/{response.json()['id']}/qr")

        self.assertEqual(404, qr.status_code)
        self.assertEqual("no-store", qr.headers["cache-control"])
        self.assertEqual(
            {"error": "qr_unavailable", "message": "二维码尚未就绪"}, qr.json()
        )

    def test_owner_can_cancel_with_csrf_and_cross_user_is_hidden(self):
        scan_id = self._awaiting_scan()

        no_csrf = self.owner_client.post(f"/accounts/scan/{scan_id}/cancel")
        self.assertEqual(403, no_csrf.status_code)
        hidden = self.other_client.post(
            f"/accounts/scan/{scan_id}/cancel",
            data={"csrf_token": self.other_csrf},
        )
        self.assertEqual(404, hidden.status_code)
        cancelled = self.owner_client.post(
            f"/accounts/scan/{scan_id}/cancel",
            data={"csrf_token": self.owner_csrf},
        )
        self.assertEqual(200, cancelled.status_code)
        self.assertEqual("cancelled", cancelled.json()["status"])
        self.assertEqual("no-store", cancelled.headers["cache-control"])
        with session_scope(self.engine) as db:
            scan = db.get(DouyinLoginSession, scan_id)
            self.assertIsNone(scan.qr_png)
            self.assertIsNone(scan.slot)

    def test_active_relogin_blocks_account_deletion_and_keeps_its_target(self):
        with session_scope(self.engine) as db:
            scan_id = ScanSessionService(db).start(
                self.owner_id, self.account_id
            ).id

        response = self.owner_client.post(
            f"/accounts/{self.account_id}/delete",
            data={"csrf_token": self.owner_csrf},
            follow_redirects=False,
        )

        self.assertEqual(303, response.status_code)
        self.assertEqual(
            "/accounts?notice=account-login-active", response.headers["location"]
        )
        notice = self.owner_client.get(response.headers["location"])
        self.assertIn(
            "该账号正在重新登录，请先完成或取消登录后再删除。", notice.text
        )
        with Session(self.engine) as db:
            self.assertIsNotNone(db.get(DouyinAccount, self.account_id))
            scan = db.get(DouyinLoginSession, scan_id)
            self.assertEqual(self.account_id, scan.account_id)
            self.assertIsNotNone(scan.slot)

    def test_owner_only_rename_accepts_1_to_64_characters(self):
        hidden = self.other_client.post(
            f"/accounts/{self.account_id}/rename",
            data={"csrf_token": self.other_csrf, "display_name": "越权改名"},
            follow_redirects=False,
        )
        self.assertEqual(404, hidden.status_code)

        invalid = self.owner_client.post(
            f"/accounts/{self.account_id}/rename",
            data={"csrf_token": self.owner_csrf, "display_name": " "},
            follow_redirects=False,
        )
        self.assertEqual(400, invalid.status_code)
        renamed = self.owner_client.post(
            f"/accounts/{self.account_id}/rename",
            data={"csrf_token": self.owner_csrf, "display_name": " 我的主账号 "},
            follow_redirects=False,
        )
        self.assertEqual(303, renamed.status_code)
        self.assertEqual("/accounts", renamed.headers["location"])
        with session_scope(self.engine) as db:
            self.assertEqual("我的主账号", db.get(DouyinAccount, self.account_id).display_name)

    def test_account_page_has_qr_dialog_and_rename_but_no_manual_credentials(self):
        response = self.owner_client.get("/accounts")

        self.assertEqual(200, response.status_code)
        self.assertIn("扫码绑定抖音账号", response.text)
        self.assertIn("<dialog", response.text)
        self.assertRegex(response.text, r'src="/static/account_scan\.js\?v=\d{8}-\d+"')
        self.assertIn('id="scan-qr-panel"', response.text)
        self.assertIn('id="scan-browser-hint"', response.text)
        self.assertIn('id="scan-text"', response.text)
        self.assertIn('id="scan-type"', response.text)
        self.assertIn('id="scan-interaction-status"', response.text)
        self.assertNotIn('id="scan-placeholder"', response.text)
        self.assertNotIn('id="scan-verification"', response.text)
        self.assertNotIn('id="scan-verification-sms"', response.text)
        self.assertNotIn('id="scan-verification-app"', response.text)
        self.assertNotIn('id="scan-masked-phone"', response.text)
        self.assertNotIn('id="scan-resend-countdown"', response.text)
        self.assertNotIn('id="scan-resend"', response.text)
        self.assertIn("健康状态：正常", response.text)
        self.assertIn("最近验证：2026-08-31 12:05:06", response.text)
        self.assertIn("重新登录", response.text)
        self.assertIn(f'data-scan-account-id="{self.account_id}"', response.text)
        self.assertIn("修改备注", response.text)
        self.assertIn(f'action="/accounts/{self.account_id}/rename"', response.text)
        self.assertNotIn('textarea name="cookies"', response.text)
        self.assertNotIn('name="cookies"', response.text)
        self.assertNotIn("Cookie JSON", response.text)
        self.assertNotIn("Token", response.text)
        self.assertNotIn("storage_state", response.text)

    def test_invalid_account_is_highlighted_as_needing_relogin(self):
        with session_scope(self.engine) as db:
            account = db.get(DouyinAccount, self.account_id)
            account.validation_state = "invalid"
            account.last_verified_at = None

        response = self.owner_client.get("/accounts")

        self.assertEqual(200, response.status_code)
        self.assertIn('data-health="invalid"', response.text)
        self.assertIn("健康状态：登录异常，待复核", response.text)
        self.assertIn("历史失效记录，尚无详细检测依据", response.text)
        self.assertIn("最近验证：尚未验证", response.text)
        self.assertIn("account-relogin-urgent", response.text)

    def test_quota_full_page_hides_new_binding_but_keeps_shared_relogin_dialog(self):
        with session_scope(self.engine) as db:
            second = DouyinAccount(
                owner_user_id=self.owner_id,
                display_name="第二个账号",
                encrypted_cookies=b"encrypted-2",
                cookie_nonce=b"nonce-2",
                validation_state="unknown",
            )
            db.add(second)
            db.flush()
            second_id = second.id

        response = self.owner_client.get("/accounts")

        self.assertEqual(200, response.status_code)
        self.assertNotIn('id="scan-start"', response.text)
        self.assertIn("当前账号额度已用完（2/2）", response.text)
        self.assertIn("已有账号仍可点击“重新登录”", response.text)
        self.assertIn(f'data-scan-account-id="{self.account_id}"', response.text)
        self.assertIn(f'data-scan-account-id="{second_id}"', response.text)
        self.assertEqual(1, response.text.count('id="scan-dialog"'))
        self.assertRegex(response.text, r'src="/static/account_scan\.js\?v=\d{8}-\d+"')

    def test_old_manual_account_submission_is_rejected(self):
        response = self.owner_client.post(
            "/accounts",
            data={
                "csrf_token": self.owner_csrf,
                "display_name": "手工账号",
                "cookies": '[{"name":"sid","value":"credential-marker"}]',
            },
            follow_redirects=False,
        )

        self.assertEqual(405, response.status_code)
        with session_scope(self.engine) as db:
            self.assertEqual(1, len(db.scalars(select(DouyinAccount)).all()))


if __name__ == "__main__":
    unittest.main()
