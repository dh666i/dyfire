import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from spark_console.config import Settings
from spark_console.db import create_schema, session_scope
from spark_console.models import AuditEvent, User, WebSession
from spark_console.security import PasswordService
from spark_console.web.app import create_app


class ElevationWebTests(unittest.TestCase):
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
            secure_cookies=True,
        )
        self.engine = create_engine(
            self.settings.database_url, connect_args={"check_same_thread": False}
        )
        create_schema(self.engine)
        with session_scope(self.engine) as db:
            admin = User(
                username="step-admin",
                password_hash=PasswordService().hash("AdminPass-123"),
                role="admin",
                must_change_password=False,
            )
            db.add(admin)
            db.flush()
            self.admin_id = admin.id
        self.client = TestClient(
            create_app(self.settings, self.engine), base_url="https://testserver"
        )

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.temp.cleanup()

    def _login(self):
        response = self.client.post(
            "/login",
            data={"username": "step-admin", "password": "AdminPass-123"},
            follow_redirects=False,
        )
        self.assertEqual(303, response.status_code)
        return self.client.get("/api/auth/bootstrap").json()

    def test_wrong_password_is_audited_and_does_not_elevate(self):
        bootstrap = self._login()
        response = self.client.post(
            "/api/auth/elevate",
            json={"csrf_token": bootstrap["csrf"], "password": "wrong-password"},
        )
        self.assertEqual(403, response.status_code)
        current = self.client.get("/api/auth/bootstrap").json()
        self.assertFalse(current["elevated"])
        with session_scope(self.engine) as db:
            event = db.scalar(
                select(AuditEvent).where(
                    AuditEvent.action == "auth.step_up_failed",
                    AuditEvent.actor_user_id == self.admin_id,
                )
            )
            self.assertIsNotNone(event)
            record = db.scalar(select(WebSession).where(WebSession.user_id == self.admin_id))
            self.assertIsNone(record.elevated_until)

    def test_correct_password_elevates_only_current_session(self):
        bootstrap = self._login()
        response = self.client.post(
            "/api/auth/elevate",
            json={"csrf_token": bootstrap["csrf"], "password": "AdminPass-123"},
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue(response.json()["elevated"])
        current = self.client.get("/api/auth/bootstrap").json()
        self.assertTrue(current["elevated"])
        self.assertIsNotNone(current["elevated_until"])
        with session_scope(self.engine) as db:
            self.assertEqual(
                1,
                db.query(WebSession)
                .filter(WebSession.user_id == self.admin_id)
                .filter(WebSession.elevated_until.is_not(None))
                .count(),
            )


if __name__ == "__main__":
    unittest.main()
