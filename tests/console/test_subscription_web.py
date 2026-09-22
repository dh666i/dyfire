import hashlib
import re
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from spark_console.config import Settings
from spark_console.db import create_schema, session_scope
from spark_console.models import RedemptionCode, SubscriptionGrant, User, UserSubscription
from spark_console.security import PasswordService
from spark_console.services.audits import AuditService
from spark_console.services.subscriptions import SubscriptionService
from spark_console.web.app import create_app


class SubscriptionWebTests(unittest.TestCase):
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
        passwords = PasswordService()
        with session_scope(self.engine) as db:
            self.admin = User(
                username="admin",
                password_hash=passwords.hash("AdminPassword123"),
                role="admin",
                must_change_password=False,
            )
            self.customer = User(
                username="customer",
                password_hash=passwords.hash("CustomerPass123"),
                role="user",
                must_change_password=False,
            )
            db.add_all([self.admin, self.customer])
            db.flush()
        self.client = TestClient(
            create_app(self.settings, self.engine), base_url="https://testserver"
        )

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.temp.cleanup()

    def login(self, username: str, password: str):
        return self.client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )

    def csrf(self, path: str) -> str:
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        return response.text.split('name="csrf_token" value="', 1)[1].split(
            '"', 1
        )[0]

    def test_unsubscribed_customer_can_login_and_open_redeem_page(self):
        self.login("customer", "CustomerPass123")

        response = self.client.get("/subscription")

        self.assertEqual(200, response.status_code)
        self.assertIn("未开通", response.text)
        self.assertIn('action="/subscription/redeem"', response.text)
        self.assertIn("如何购买或续费", response.text)
        self.assertIn("通过原购买渠道联系管理员", response.text)
        self.assertIn("到期后会怎样", response.text)

    def test_admin_generated_code_is_shown_once_and_customer_can_redeem_it(self):
        self.login("admin", "AdminPassword123")
        response = self.client.post(
            "/admin/redemption-codes",
            data={
                "csrf_token": self.csrf("/admin"),
                "duration_days": "30",
                "max_accounts": "2",
                "max_tasks": "8",
                "quantity": "1",
            },
        )

        self.assertEqual(200, response.status_code)
        plaintext = re.search(r'data-copy-invite="([^"]+)"', response.text).group(1)
        self.assertNotIn(plaintext, self.client.get("/admin").text)
        with Session(self.engine) as db:
            record = db.scalar(select(RedemptionCode))
            self.assertEqual(
                hashlib.sha256(plaintext.encode()).hexdigest(), record.code_hash
            )

        self.login("customer", "CustomerPass123")
        token = self.csrf('/subscription')
        self.assertEqual(403, self.client.post('/subscription/preview', data={'code': plaintext}).status_code)
        preview = self.client.post('/subscription/preview', data={'csrf_token': token, 'code': plaintext})
        self.assertEqual(200, preview.status_code)
        self.assertIn('30 天', preview.json()['message'])
        with Session(self.engine) as db:
            self.assertIsNone(db.scalar(select(RedemptionCode)).redeemed_at)
            self.assertIsNone(db.get(UserSubscription, self.customer.id))
        redeemed = self.client.post(
            "/subscription/redeem",
            data={"csrf_token": self.csrf("/subscription"), "code": plaintext},
            follow_redirects=False,
        )

        self.assertEqual(303, redeemed.status_code)
        self.assertEqual("/subscription?redeemed=1", redeemed.headers["location"])
        with Session(self.engine) as db:
            subscription = db.get(UserSubscription, self.customer.id)
            self.assertEqual(2, subscription.max_accounts)
            self.assertEqual(8, subscription.max_tasks)
            self.assertTrue(
                SubscriptionService(db, AuditService(db)).active(self.customer.id)
            )

    def test_admin_can_extend_and_revoke_customer_membership(self):
        self.login("admin", "AdminPassword123")
        csrf = self.csrf("/admin")

        extended = self.client.post(
            f"/admin/users/{self.customer.id}/subscription",
            data={
                "csrf_token": csrf,
                "duration_days": "90",
                "max_accounts": "3",
                "max_tasks": "12",
            },
            follow_redirects=False,
        )
        revoked = self.client.post(
            f"/admin/users/{self.customer.id}/subscription/revoke",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )

        self.assertEqual(303, extended.status_code)
        self.assertEqual(303, revoked.status_code)
        with Session(self.engine) as db:
            self.assertFalse(
                SubscriptionService(db, AuditService(db)).active(self.customer.id)
            )
            sources = set(db.scalars(select(SubscriptionGrant.source)).all())
            self.assertEqual({"admin_extend", "admin_revoke"}, sources)

    def test_invalid_code_returns_generic_error_without_creating_membership(self):
        self.login("customer", "CustomerPass123")
        response = self.client.post(
            "/subscription/redeem",
            data={"csrf_token": self.csrf("/subscription"), "code": "not-valid"},
        )

        self.assertEqual(400, response.status_code)
        self.assertIn("兑换码无效或已使用", response.text)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(UserSubscription, self.customer.id))

    def test_redeem_api_returns_json_and_updates_membership(self):
        self.login("customer", "CustomerPass123")
        with Session(self.engine) as db:
            _record, plaintext = SubscriptionService(db, AuditService(db)).create_redemption_code(
                self.admin.id, duration_days=30, max_accounts=2, max_tasks=8
            )
            db.commit()
        csrf = self.client.get('/api/app/bootstrap').json()['csrf']
        response = self.client.post('/api/subscription/redeem', json={'csrf_token': csrf, 'code': plaintext})
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()['ok'])
        self.assertEqual(8, response.json()['membership']['max_tasks'])


if __name__ == "__main__":
    unittest.main()
