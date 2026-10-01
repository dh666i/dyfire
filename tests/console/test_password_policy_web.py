import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, unquote_plus

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from spark_console.config import Settings
from spark_console.db import create_schema, session_scope
from spark_console.models import MailDelivery, MailPreference, MailSettings, PasswordReset, User, WebSession, utc_now
from spark_console.security import PasswordService, SessionService
from spark_console.web.registration_routes import registration_client_key
from starlette.requests import Request
from spark_console.web.app import create_app
from spark_console.crypto import CookieCipher
from spark_console.services import mail


class PasswordPolicyWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        cookie_key = root / "cookie.key"
        session_key = root / "session.key"
        cookie_key.write_bytes(b"c" * 32)
        session_key.write_bytes(b"s" * 32)
        self.settings = Settings(
            data_dir=root,
            database_url=f"sqlite:///{root / 'test.db'}",
            cookie_key_file=cookie_key,
            session_key_file=session_key,
        )
        self.engine = create_engine(
            self.settings.database_url,
            connect_args={"check_same_thread": False},
        )
        create_schema(self.engine)
        self.passwords = PasswordService()
        with session_scope(self.engine) as db:
            self.user = User(
                username="policy-user",
                password_hash=self.passwords.hash("Original-123"),
                role="user",
                must_change_password=True,
            )
            db.add(self.user)
            db.flush()
            self.user_id = self.user.id
        self.client = TestClient(create_app(self.settings, self.engine), base_url="https://testserver")

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.temp.cleanup()

    def test_change_password_applies_registration_policy(self):
        login = self.client.post(
            "/login",
            data={"username": "policy-user", "password": "Original-123"},
            follow_redirects=False,
        )
        self.assertEqual(303, login.status_code)
        csrf = self.client.get("/api/auth/bootstrap").json()["csrf"]

        response = self.client.post(
            "/change-password",
            data={
                "csrf_token": csrf,
                "current_password": "Original-123",
                "new_password": "weak",
                "new_password_confirmation": "weak",
            },
            follow_redirects=False,
        )
        self.assertEqual(303, response.status_code)
        self.assertIn("密码至少 12 位", unquote_plus(response.headers["location"]))
        with session_scope(self.engine) as db:
            user = db.get(User, self.user_id)
            self.assertTrue(user.must_change_password)
            self.assertTrue(self.passwords.verify(user.password_hash, "Original-123"))
            self.assertFalse(self.passwords.verify(user.password_hash, "weak"))

    def test_registration_uses_generic_policy_message_and_accepts_strong_password(self):
        weak = self.client.post(
            "/register",
            data={
                "username": "new-policy-user",
                "password": "weak",
                "password_confirmation": "weak",
            },
            follow_redirects=False,
        )
        self.assertEqual(303, weak.status_code)
        self.assertIn("密码至少 12 位", unquote_plus(weak.headers["location"]))
        self.assertNotIn("用户名已存在", unquote_plus(weak.headers["location"]))

        valid = self.client.post(
            "/register",
            data={
                "username": "new-policy-user",
                "password": "Strong-Password-123",
                "password_confirmation": "Strong-Password-123",
            },
            follow_redirects=False,
        )
        self.assertEqual(303, valid.status_code)
        with session_scope(self.engine) as db:
            user = db.query(User).filter(User.username == "new-policy-user").one()
            self.assertFalse(user.must_change_password)
            self.assertTrue(
                self.passwords.verify(user.password_hash, "Strong-Password-123")
            )

    def test_login_lockout_is_persistent_and_expires(self):
        for _ in range(5):
            response = self.client.post(
                "/login",
                data={"username": "policy-user", "password": "wrong"},
                follow_redirects=False,
            )
            self.assertEqual(303, response.status_code)

        with session_scope(self.engine) as db:
            user = db.get(User, self.user_id)
            self.assertEqual(5, user.failed_login_count)
            self.assertIsNotNone(user.locked_until)
            self.assertGreater(user.locked_until.replace(tzinfo=timezone.utc), utc_now())

        blocked = self.client.post(
            "/login",
            data={"username": "policy-user", "password": "Original-123"},
            follow_redirects=False,
        )
        self.assertEqual(303, blocked.status_code)
        self.assertNotIn("spark_session", self.client.cookies)

        with session_scope(self.engine) as db:
            user = db.get(User, self.user_id)
            user.locked_until = utc_now() - timedelta(seconds=1)
        accepted = self.client.post(
            "/login",
            data={"username": "policy-user", "password": "Original-123"},
            follow_redirects=False,
        )
        self.assertEqual(303, accepted.status_code)
        self.assertIn("spark_session", self.client.cookies)
        with session_scope(self.engine) as db:
            user = db.get(User, self.user_id)
            self.assertEqual(0, user.failed_login_count)
            self.assertIsNone(user.locked_until)

    def test_password_change_rotates_current_session_and_revokes_others(self):
        login = self.client.post(
            "/login",
            data={"username": "policy-user", "password": "Original-123"},
            follow_redirects=False,
        )
        self.assertEqual(303, login.status_code)
        old_cookie = self.client.cookies.get("spark_session")
        csrf = self.client.get("/api/auth/bootstrap").json()["csrf"]

        other_client = TestClient(create_app(self.settings, self.engine), base_url="https://testserver")
        other_raw, other_record = SessionService(b"s" * 32).create_record(self.user_id)
        with session_scope(self.engine) as db:
            db.add(other_record)

        response = self.client.post(
            "/change-password",
            data={
                "csrf_token": csrf,
                "current_password": "Original-123",
                "new_password": "Updated-456!",
                "new_password_confirmation": "Updated-456!",
            },
            follow_redirects=False,
        )
        self.assertEqual(303, response.status_code)
        new_cookie = self.client.cookies.get("spark_session")
        self.assertNotEqual(old_cookie, new_cookie)
        self.assertEqual(200, self.client.get("/api/auth/bootstrap").status_code)
        self.assertEqual(
            401,
            other_client.get(
                "/api/app/bootstrap", headers={"Cookie": f"spark_session={other_raw}"}
            ).status_code,
        )

        old_client = TestClient(create_app(self.settings, self.engine), base_url="https://testserver")
        self.assertEqual(
            401,
            old_client.get(
                "/api/auth/bootstrap", headers={"Cookie": f"spark_session={old_cookie}"}
            ).status_code,
        )
        with session_scope(self.engine) as db:
            sessions = db.query(WebSession).filter(WebSession.user_id == self.user_id).all()
            self.assertEqual(1, len(sessions))
            self.assertEqual(SessionService(b"s" * 32).token_hash(new_cookie), sessions[0].token_hash)
        old_client.close()
        other_client.close()

    def test_client_key_ignores_untrusted_x_real_ip(self):
        request = Request(
            {
                "type": "http",
                "headers": [(b"x-real-ip", b"203.0.113.77")],
                "client": ("127.0.0.1", 443),
            }
        )
        self.assertEqual("127.0.0.1", registration_client_key(request))

    def test_recovery_keeps_code_after_weak_password_and_accepts_valid_retry(self):
        code = "AB12CD34"
        now = datetime.now(timezone.utc)
        with session_scope(self.engine) as db:
            user = db.get(User, self.user_id)
            db.add(
                MailPreference(
                    user_id=user.id,
                    email="policy@example.com",
                    verified=True,
                    enabled=True,
                )
            )
            db.add(
                PasswordReset(
                    user_id=user.id,
                    digest=hashlib.sha256((user.id + code).encode()).hexdigest(),
                    created_at=now,
                    expires_at=now + timedelta(minutes=10),
                    attempts=0,
                )
            )

        weak = self.client.post(
            "/forgot-password/reset",
            data={
                "username": "policy-user",
                "code": code,
                "password": "weak",
                "confirmation": "weak",
            },
            follow_redirects=False,
        )
        self.assertEqual(303, weak.status_code)
        self.assertIn("重置未完成", unquote(weak.headers["location"]))
        with session_scope(self.engine) as db:
            reset = db.get(PasswordReset, self.user_id)
            self.assertIsNotNone(reset)
            self.assertEqual(0, reset.attempts)

        valid = self.client.post(
            "/forgot-password/reset",
            data={
                "username": "policy-user",
                "code": code,
                "password": "Recovered-123",
                "confirmation": "Recovered-123",
            },
            follow_redirects=False,
        )
        self.assertEqual(303, valid.status_code)
        self.assertEqual("/login", valid.headers["location"])
        with session_scope(self.engine) as db:
            self.assertIsNone(db.get(PasswordReset, self.user_id))
            user = db.get(User, self.user_id)
            self.assertFalse(user.must_change_password)
            self.assertTrue(self.passwords.verify(user.password_hash, "Recovered-123"))

    def test_new_recovery_code_has_12_hex_characters(self):
        cipher = CookieCipher(self.settings.cookie_key_file.read_bytes())
        config = dict(mail.DEFAULTS)
        config["enabled"] = True
        encrypted = cipher.encrypt(json.dumps(config).encode())
        with session_scope(self.engine) as db:
            user = db.get(User, self.user_id)
            db.add(MailSettings(
                id=1,
                ciphertext=encrypted.ciphertext,
                nonce=encrypted.nonce,
            ))
            db.add(MailPreference(
                user_id=user.id,
                email="policy@example.com",
                verified=True,
                enabled=True,
            ))
        response = self.client.post(
            "/forgot-password",
            data={"username": "policy-user", "email": "policy@example.com"},
        )
        self.assertEqual(200, response.status_code)
        with session_scope(self.engine) as db:
            delivery = db.scalar(select(MailDelivery).where(MailDelivery.event == "reset"))
            self.assertIsNotNone(delivery)
            code = delivery.body.split("重置代码：", 1)[1].splitlines()[0]
            self.assertRegex(code, r"^[0-9A-F]{12}$")


if __name__ == "__main__":
    unittest.main()
