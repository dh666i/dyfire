import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from spark_console.config import Settings
from spark_console.db import create_schema, session_scope
from spark_console.models import MailPreference, PasswordReset, User
from spark_console.security import PasswordService
from spark_console.web.app import create_app


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
        self.assertIn("密码需同时包含字母和数字", unquote(response.headers["location"]))
        with session_scope(self.engine) as db:
            user = db.get(User, self.user_id)
            self.assertTrue(user.must_change_password)
            self.assertTrue(self.passwords.verify(user.password_hash, "Original-123"))
            self.assertFalse(self.passwords.verify(user.password_hash, "weak"))

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
            self.assertEqual(1, reset.attempts)

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


if __name__ == "__main__":
    unittest.main()
