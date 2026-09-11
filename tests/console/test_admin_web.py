import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import create_schema, session_scope
from spark_console.models import (
    AuditEvent,
    DouyinAccount,
    InviteCode,
    RedemptionCode,
    SparkTask,
    TaskRun,
    User,
    UserSubscription,
    WebSession,
)
from spark_console.security import PasswordService, SessionService
from spark_console.services.audits import AuditService
from spark_console.services.invites import InviteService
from spark_console.services.subscriptions import SubscriptionService
from spark_console.web.admin_routes import build_admin_router
from spark_console.web.app import create_app
from spark_console.web.auth import WebAuth


def _item_summary(item):
    source = getattr(item, "user", item)
    if hasattr(item, "invite"):
        source = item.invite
    elif hasattr(item, "code"):
        source = item.code
    elif hasattr(item, "task"):
        source = item.task
    elif hasattr(item, "run"):
        source = item.run
    elif hasattr(item, "event"):
        source = item.event
    result = {"id": getattr(source, "id", None)}
    for name in (
        "username",
        "status",
        "membership_status",
        "enabled",
        "target_name",
        "resource_type",
        "action",
    ):
        if hasattr(item, name):
            result[name] = getattr(item, name)
        elif hasattr(source, name):
            result[name] = getattr(source, name)
    return result


def capture_page(request, name, status_code=200, **context):
    collections = {}
    if 'groups' in context:
        collections['groups'] = [dict(item) for item in context['groups']]
    for key in (
        "users",
        "subscriptions",
        "tasks",
        "runs",
        "audit_events",
        "invites",
        "redemption_codes",
        "recent_runs",
        "expiring_users",
    ):
        if key in context:
            collections[key] = [_item_summary(item) for item in context[key]]
    payload = {
        "template": name,
        "keys": sorted(context),
        "q": context.get("q"),
        "status": context.get("status"),
        "pagination": context.get("pagination"),
        "invite_pagination": context.get("invite_pagination"),
        "redemption_pagination": context.get("redemption_pagination"),
        "collections": collections,
        "stats": context.get("stats"),
        "system_settings": context.get("system_settings"),
        "worker": context.get("worker"),
        "one_time_password": context.get("one_time_password"),
        "one_time_codes": context.get("one_time_codes"),
        "error": context.get("error"),
    }
    return JSONResponse(payload, status_code=status_code)


class AdminWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(
            data_dir=root,
            database_url=f"sqlite:///{root / 'test.db'}",
            cookie_key_file=root / "cookie.key",
            session_key_file=root / "session.key",
            secure_cookies=True,
        )
        self.settings.cookie_key_file.write_bytes(b"c" * 32)
        self.settings.session_key_file.write_bytes(b"s" * 32)
        self.engine = create_engine(
            self.settings.database_url,
            connect_args={"check_same_thread": False},
        )
        create_schema(self.engine)
        self.passwords = PasswordService()
        password_hash = self.passwords.hash("AdminPass123")
        customer_hash = self.passwords.hash("CustomerPass123")
        now = datetime.now(timezone.utc)
        with session_scope(self.engine) as db:
            self.admin = User(
                username="admin",
                password_hash=password_hash,
                role="admin",
                must_change_password=False,
            )
            self.customer = User(
                username="customer",
                password_hash=customer_hash,
                role="user",
                must_change_password=False,
            )
            users = [
                User(
                    username=f"user{index:02d}",
                    password_hash=customer_hash,
                    role="user",
                    status="disabled" if index == 0 else "active",
                    must_change_password=False,
                )
                for index in range(25)
            ]
            db.add_all([self.admin, self.customer, *users])
            db.flush()
            self.disabled_user_id = users[0].id
            self.active_user_id = users[1].id
            db.add_all(
                [
                    UserSubscription(
                        user_id=self.customer.id,
                        valid_from=now,
                        valid_until=now + timedelta(days=30),
                        max_accounts=2,
                        max_tasks=10,
                    ),
                    UserSubscription(
                        user_id=users[2].id,
                        valid_from=now - timedelta(days=30),
                        valid_until=now - timedelta(days=1),
                        max_accounts=1,
                        max_tasks=2,
                    ),
                ]
            )
            account = DouyinAccount(
                owner_user_id=self.customer.id,
                display_name="主账号",
                encrypted_cookies=b"encrypted",
                cookie_nonce=b"nonce",
            )
            db.add(account)
            db.flush()
            enabled_task = SparkTask(
                owner_user_id=self.customer.id,
                douyin_account_id=account.id,
                target_name="启用好友",
                send_time="08:00",
                message_template="早上好",
                enabled=True,
            )
            disabled_task = SparkTask(
                owner_user_id=self.customer.id,
                douyin_account_id=account.id,
                target_name="暂停好友",
                send_time="09:00",
                message_template="早上好",
                enabled=False,
            )
            db.add_all([enabled_task, disabled_task])
            db.flush()
            self.enabled_task_id = enabled_task.id
            self.disabled_task_id = disabled_task.id
            db.add_all(
                [
                    TaskRun(
                        task_id=enabled_task.id,
                        scheduled_for=now,
                        status="success",
                        stage="complete",
                    ),
                    TaskRun(
                        task_id=disabled_task.id,
                        scheduled_for=now - timedelta(minutes=1),
                        status="failed",
                        stage="worker_error",
                    ),
                ]
            )
            audit = AuditService(db)
            audit.write(self.admin.id, "task.disabled", "spark_task", enabled_task.id)
            audit.write(self.admin.id, "user.disabled", "user", users[0].id)
            invites = InviteService(
                db,
                audit,
                CookieCipher(self.settings.cookie_key_file.read_bytes()),
            )
            active_invite, _ = invites.create(self.admin.id)
            revoked_invite, _ = invites.create(self.admin.id)
            invites.revoke(self.admin.id, revoked_invite.id)
            self.active_invite_id = active_invite.id
            self.revoked_invite_id = revoked_invite.id
            subscriptions = SubscriptionService(db, audit)
            active_code, _ = subscriptions.create_redemption_code(self.admin.id)
            revoked_code, _ = subscriptions.create_redemption_code(self.admin.id)
            subscriptions.revoke_redemption_code(self.admin.id, revoked_code.id)
            self.active_code_id = active_code.id
            self.revoked_code_id = revoked_code.id
            session_service = SessionService(
                self.settings.session_key_file.read_bytes()
            )
            self.admin_token, admin_session = session_service.create_record(
                self.admin.id
            )
            self.admin_csrf = admin_session.csrf_token
            self.customer_token, customer_session = session_service.create_record(
                self.customer.id
            )
            db.add_all([admin_session, customer_session])

        app = FastAPI()
        sessions = SessionService(self.settings.session_key_file.read_bytes())
        auth = WebAuth(sessions)

        @app.exception_handler(401)
        async def unauthorized(_request, _exc):
            return RedirectResponse("/login", status_code=303)

        app.include_router(
            build_admin_router(
                self.engine,
                self.settings,
                auth,
                self.passwords,
                CookieCipher(self.settings.cookie_key_file.read_bytes()),
                capture_page,
            )
        )
        self.client = TestClient(app, base_url="https://testserver")
        self.client.cookies.set("spark_session", self.admin_token)
        self.real_client = TestClient(
            create_app(self.settings, self.engine), base_url="https://testserver"
        )
        self.real_client.cookies.set("spark_session", self.admin_token)

    def tearDown(self):
        self.client.close()
        self.real_client.close()
        self.engine.dispose()
        self.temp.cleanup()

    def test_real_admin_templates_render_for_every_section(self):
        root = self.real_client.get("/admin", follow_redirects=False)
        self.assertEqual(303, root.status_code)
        self.assertEqual("/admin/overview", root.headers["location"])
        for path in (
            "/admin/overview",
            "/admin/users",
            "/admin/subscriptions",
            "/admin/codes",
            "/admin/tasks",
            "/admin/runs",
            "/admin/audit",
            "/admin/settings",
        ):
            with self.subTest(path=path):
                response = self.real_client.get(path)
                self.assertEqual(200, response.status_code)
                self.assertIn('data-design="dyfire-studio"', response.text)
                self.assertIn('class="studio-admin-tabs"', response.text)
                self.assertNotIn('/static/mineradio_admin.css', response.text)

    def test_admin_sections_require_an_active_admin_session(self):
        anonymous = TestClient(
            create_app(self.settings, self.engine), base_url="https://testserver"
        )
        try:
            response = anonymous.get(
                "/admin/overview", follow_redirects=False
            )
            self.assertEqual(303, response.status_code)
            self.assertEqual("/login", response.headers["location"])
            anonymous.cookies.set("spark_session", self.customer_token)
            self.assertEqual(404, anonymous.get("/admin/overview").status_code)
        finally:
            anonymous.close()

    def test_admin_pages_expose_separate_contexts_and_server_side_pagination(self):
        pages = {
            "/admin/overview": "admin_overview.html",
            "/admin/users": "admin_users.html",
            "/admin/subscriptions": "admin_subscriptions.html",
            "/admin/codes": "admin_codes.html",
            "/admin/tasks": "admin_tasks.html",
            "/admin/runs": "admin_runs.html",
            "/admin/audit": "admin_audit.html",
            "/admin/settings": "admin_settings.html",
        }
        for path, template in pages.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(200, response.status_code)
                self.assertEqual(template, response.json()["template"])

        page_two = self.client.get("/admin/users?page=2").json()
        self.assertEqual(27, page_two["pagination"]["total"])
        self.assertEqual(2, page_two["pagination"]["page"])
        self.assertEqual(7, len(page_two["collections"]["users"]))
        searched = self.client.get("/admin/users?q=user24").json()
        self.assertEqual(1, searched["pagination"]["total"])
        self.assertEqual(
            ["user24"],
            [item["username"] for item in searched["collections"]["users"]],
        )
        settings = self.client.get("/admin/settings").json()
        self.assertEqual("sqlite", settings["system_settings"]["database_backend"])
        self.assertNotIn("database_url", settings["system_settings"])

    def test_filters_cover_users_subscriptions_tasks_runs_audit_and_codes(self):
        disabled = self.client.get("/admin/users?status=disabled").json()
        self.assertEqual(1, disabled["pagination"]["total"])
        self.assertEqual(
            "disabled", disabled["collections"]["users"][0]["status"]
        )

        expired = self.client.get(
            "/admin/subscriptions?status=expired"
        ).json()
        self.assertEqual(1, expired["pagination"]["total"])
        self.assertEqual(
            "expired",
            expired["collections"]["subscriptions"][0]["membership_status"],
        )

        paused = self.client.get("/admin/tasks?status=disabled").json()
        self.assertEqual(1, paused["pagination"]["total"])
        self.assertEqual(0, paused["collections"]["groups"][0]["enabled"])

        failed = self.client.get("/admin/runs?status=failed").json()
        self.assertEqual(1, failed["pagination"]["total"])
        self.assertEqual("failed", failed["collections"]["runs"][0]["status"])

        audited = self.client.get("/admin/audit?status=spark_task").json()
        self.assertGreaterEqual(audited["pagination"]["total"], 1)
        self.assertTrue(
            all(
                item["resource_type"] == "spark_task"
                for item in audited["collections"]["audit_events"]
            )
        )

        revoked = self.client.get("/admin/codes?status=revoked").json()
        self.assertEqual(1, revoked["invite_pagination"]["total"])
        self.assertEqual(1, revoked["redemption_pagination"]["total"])

    def test_reset_password_requires_csrf_revokes_sessions_and_audits(self):
        rejected = self.client.post(
            f"/admin/users/{self.customer.id}/reset-password", data={}
        )
        self.assertEqual(403, rejected.status_code)

        response = self.client.post(
            f"/admin/users/{self.customer.id}/reset-password",
            data={"csrf_token": self.admin_csrf},
        )
        self.assertEqual(200, response.status_code)
        temporary = response.json()["one_time_password"]
        self.assertTrue(temporary)
        with Session(self.engine) as db:
            customer = db.get(User, self.customer.id)
            self.assertTrue(customer.must_change_password)
            self.assertTrue(
                self.passwords.verify(customer.password_hash, temporary)
            )
            self.assertIsNone(
                db.scalar(
                    select(WebSession).where(WebSession.user_id == self.customer.id)
                )
            )
            event = db.scalar(
                select(AuditEvent).where(
                    AuditEvent.action == "user.password_reset",
                    AuditEvent.resource_id == self.customer.id,
                )
            )
            self.assertIsNotNone(event)

    def test_modern_action_aliases_redirect_to_their_sections(self):
        toggled = self.client.post(
            f"/admin/users/{self.disabled_user_id}/toggle",
            data={"csrf_token": self.admin_csrf},
            follow_redirects=False,
        )
        self.assertEqual("/admin/users", toggled.headers["location"])

        extended = self.client.post(
            f"/admin/subscriptions/{self.active_user_id}/extend",
            data={
                "csrf_token": self.admin_csrf,
                "duration_days": "30",
                "max_accounts": "2",
                "max_tasks": "4",
            },
            follow_redirects=False,
        )
        self.assertEqual("/admin/subscriptions", extended.headers["location"])
        limited = self.client.post(
            f"/admin/subscriptions/{self.active_user_id}/limits",
            data={
                "csrf_token": self.admin_csrf,
                "max_accounts": "3",
                "max_tasks": "5",
            },
            follow_redirects=False,
        )
        self.assertEqual("/admin/subscriptions", limited.headers["location"])

        task = self.client.post(
            f"/admin/tasks/{self.enabled_task_id}/toggle",
            data={"csrf_token": self.admin_csrf},
            follow_redirects=False,
        )
        self.assertEqual("/admin/tasks", task.headers["location"])

        invite = self.client.post(
            "/admin/codes/invites",
            data={"csrf_token": self.admin_csrf},
            follow_redirects=False,
        )
        self.assertEqual("/admin/codes", invite.headers["location"])
        codes = self.client.post(
            "/admin/codes/redemptions",
            data={
                "csrf_token": self.admin_csrf,
                "duration_days": "30",
                "max_accounts": "1",
                "max_tasks": "20",
                "quantity": "2",
            },
        )
        self.assertEqual(200, codes.status_code)
        self.assertEqual(2, len(codes.json()["one_time_codes"]))

        with Session(self.engine) as db:
            self.assertEqual("active", db.get(User, self.disabled_user_id).status)
            subscription = db.get(UserSubscription, self.active_user_id)
            self.assertEqual(3, subscription.max_accounts)
            self.assertEqual(5, subscription.max_tasks)
            self.assertFalse(db.get(SparkTask, self.enabled_task_id).enabled)
            self.assertGreaterEqual(
                len(db.scalars(select(InviteCode)).all()), 3
            )
            self.assertGreaterEqual(
                len(db.scalars(select(RedemptionCode)).all()), 4
            )

    def test_bulk_actions_change_status_without_offering_bulk_delete(self):
        rejected = self.client.post(
            "/admin/users/bulk",
            data={
                "action": "disable",
                "user_ids": [self.active_user_id],
            },
        )
        self.assertEqual(403, rejected.status_code)

        disabled = self.client.post(
            "/admin/users/bulk",
            data={
                "csrf_token": self.admin_csrf,
                "action": "disable",
                "user_ids": [self.customer.id, self.active_user_id],
            },
            follow_redirects=False,
        )
        self.assertEqual(303, disabled.status_code)
        self.assertEqual("/admin/users", disabled.headers["location"])
        enabled = self.client.post(
            "/admin/users/bulk",
            data={
                "csrf_token": self.admin_csrf,
                "action": "enable",
                "user_ids": [self.customer.id, self.active_user_id],
            },
            follow_redirects=False,
        )
        self.assertEqual(303, enabled.status_code)

        paused = self.client.post(
            "/admin/tasks/bulk",
            data={
                "csrf_token": self.admin_csrf,
                "action": "pause",
                "task_ids": [self.enabled_task_id, self.disabled_task_id],
            },
            follow_redirects=False,
        )
        self.assertEqual(303, paused.status_code)
        self.assertEqual("/admin/tasks", paused.headers["location"])
        resumed = self.client.post(
            "/admin/tasks/bulk",
            data={
                "csrf_token": self.admin_csrf,
                "action": "enable",
                "task_ids": [self.enabled_task_id, self.disabled_task_id],
            },
            follow_redirects=False,
        )
        self.assertEqual(303, resumed.status_code)

        no_user_delete = self.client.post(
            "/admin/users/bulk",
            data={
                "csrf_token": self.admin_csrf,
                "action": "delete",
                "user_ids": [self.active_user_id],
            },
        )
        no_task_delete = self.client.post(
            "/admin/tasks/bulk",
            data={
                "csrf_token": self.admin_csrf,
                "action": "delete",
                "task_ids": [self.enabled_task_id],
            },
        )
        self.assertEqual(400, no_user_delete.status_code)
        self.assertEqual(400, no_task_delete.status_code)
        with Session(self.engine) as db:
            self.assertEqual("active", db.get(User, self.customer.id).status)
            self.assertEqual("active", db.get(User, self.active_user_id).status)
            self.assertTrue(db.get(SparkTask, self.enabled_task_id).enabled)
            self.assertTrue(db.get(SparkTask, self.disabled_task_id).enabled)
            self.assertIsNotNone(db.get(User, self.active_user_id))
            self.assertIsNotNone(db.get(SparkTask, self.enabled_task_id))


if __name__ == "__main__":
    unittest.main()
