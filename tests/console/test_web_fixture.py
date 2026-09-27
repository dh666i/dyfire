"""Shared authenticated fixture for API and service tests.

The old server-rendered page test suite used to provide this setup indirectly.
Keep the fixture independent from any page markup so the React shell can evolve
without coupling business tests to retired HTML templates.
"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from spark_console.config import Settings
from spark_console.db import create_schema, session_scope
from spark_console.models import User, UserSubscription
from spark_console.security import PasswordService
from spark_console.web.app import create_app


class UserFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(
            data_dir=root,
            database_url=f"sqlite:///{root}/test.db",
            cookie_key_file=root / "cookie.key",
            session_key_file=root / "session.key",
        )
        self.settings.cookie_key_file.write_bytes(b"c" * 32)
        self.settings.session_key_file.write_bytes(b"s" * 32)
        self.engine = create_engine(
            self.settings.database_url,
            connect_args={"check_same_thread": False},
        )
        create_schema(self.engine)
        with session_scope(self.engine) as session:
            user = User(
                username="friend",
                password_hash=PasswordService().hash("Temporary-123!"),
                role="user",
                must_change_password=True,
            )
            session.add(user)
            session.flush()
            self.uid = user.id
            now = datetime.now(timezone.utc)
            session.add(
                UserSubscription(
                    user_id=user.id,
                    valid_from=now - timedelta(days=1),
                    valid_until=now + timedelta(days=30),
                    max_accounts=5,
                    max_tasks=20,
                )
            )
        self.client = TestClient(
            create_app(self.settings, self.engine),
            base_url="https://testserver",
        )

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.temp.cleanup()

    def login(self):
        return self.client.post(
            "/login",
            data={"username": "friend", "password": "Temporary-123!"},
            follow_redirects=False,
        )

    def csrf(self):
        self.login()
        return self.client.get("/api/app/bootstrap").json()["csrf"]
