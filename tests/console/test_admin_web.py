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

    def test_real_admin_routes_use_the_canonical_spa_shell(self):
        root = self.real_client.get("/admin", follow_redirects=False)
        self.assertEqual(200, root.status_code)
        self.assertIn('<div id="app"></div>', root.text)
        for path in (
            "/admin/overview",
            "/admin/users",
            "/admin/subscriptions",
            "/admin/codes",
            "/admin/tasks",
            "/admin/runs",
            "/admin/today",
            "/admin/audit",
            "/admin/diagnostics",
            "/admin/settings",
        ):
            with self.subTest(path=path):
                response = self.real_client.get(path)
                self.assertEqual(200, response.status_code)
                self.assertIn('<div id="app"></div>', response.text)
                self.assertIn("/assets/index-", response.text)
                self.assertNotIn('/static/mineradio_admin.css', response.text)

    def test_today_send_is_a_view_of_the_canonical_run_workbench(self):
        from zoneinfo import ZoneInfo

        overview = self.real_client.get("/admin/overview")
        today = self.real_client.get("/admin/today")
        self.assertEqual(200, overview.status_code)
        self.assertEqual(200, today.status_code)
        self.assertIn('<div id="app"></div>', overview.text)
        self.assertIn('<div id="app"></div>', today.text)
        board = self.real_client.get("/api/admin/runs", params={"day": "today"})
        self.assertEqual(200, board.status_code, board.text)
        self.assertEqual(datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(), board.json()["today_board"]["day"])

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
            "/admin/today": "admin_today.html",
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

    def test_customer_workbench_includes_account_health_and_today_run_counts(self):
        with session_scope(self.engine) as db:
            account = db.scalar(
                select(DouyinAccount).where(
                    DouyinAccount.owner_user_id == self.customer.id
                )
            )
            account.validation_state = "invalid"

        response = self.real_client.get("/api/admin/bootstrap", params={"q": "customer"})
        self.assertEqual(200, response.status_code)
        customer = next(
            item for item in response.json()["users"] if item["id"] == self.customer.id
        )
        self.assertEqual("需处理", customer["account_health_status"])
        self.assertEqual("1 个账号需复核", customer["account_health_label"])
        self.assertEqual(1, customer["today_success"])
        self.assertEqual(1, customer["today_failed"])

    def test_customer_workbench_tolerates_legacy_tasks_without_identity_rows(self):
        response = self.real_client.get(
            f"/api/admin/users/{self.customer.id}/workspace"
        )
        self.assertEqual(200, response.status_code, response.text)
        task = next(
            item for item in response.json()["tasks"] if item["id"] == self.enabled_task_id
        )
        self.assertEqual("", task["sec_uid"])
        self.assertEqual("no-store", response.headers["cache-control"])

    def test_role_changes_revoke_existing_sessions_in_both_directions(self):
        promotion = self.real_client.post(
            f"/api/admin/users/{self.customer.id}/role",
            json={"csrf_token": self.admin_csrf, "role": "admin"},
        )
        self.assertEqual(200, promotion.status_code, promotion.text)
        self.assertEqual("admin", promotion.json()["role"])
        with Session(self.engine) as db:
            self.assertEqual("admin", db.get(User, self.customer.id).role)
            self.assertIsNone(
                db.scalar(select(WebSession).where(WebSession.user_id == self.customer.id))
            )

        # The cookie issued before promotion must not gain admin access or stay
        # valid after the role change.
        self.real_client.cookies.set("spark_session", self.customer_token)
        self.assertEqual(401, self.real_client.get("/api/auth/bootstrap").status_code)
        self.real_client.cookies.set("spark_session", self.admin_token)

        # Issue a fresh session while promoted, then demote the user and make
        # sure that newly-issued administrator sessions are revoked as well.
        session_service = SessionService(self.settings.session_key_file.read_bytes())
        promoted_token, promoted_session = session_service.create_record(self.customer.id)
        with session_scope(self.engine) as db:
            db.add(promoted_session)
        demotion = self.real_client.post(
            f"/api/admin/users/{self.customer.id}/role",
            json={"csrf_token": self.admin_csrf, "role": "user"},
        )
        self.assertEqual(200, demotion.status_code, demotion.text)
        self.assertEqual("user", demotion.json()["role"])
        with Session(self.engine) as db:
            self.assertEqual("user", db.get(User, self.customer.id).role)
            self.assertIsNone(
                db.scalar(select(WebSession).where(WebSession.user_id == self.customer.id))
            )
            events = db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "user.role_changed",
                    AuditEvent.resource_id == self.customer.id,
                )
            ).all()
            self.assertEqual(2, len(events))

        self.real_client.cookies.set("spark_session", promoted_token)
        self.assertEqual(401, self.real_client.get("/api/auth/bootstrap").status_code)
        self.real_client.cookies.set("spark_session", self.admin_token)

    def test_role_route_rejects_self_demotion_and_keeps_last_admin(self):
        invalid_csrf = self.real_client.post(
            f"/api/admin/users/{self.customer.id}/role",
            json={"csrf_token": None, "role": "admin"},
        )
        self.assertEqual(403, invalid_csrf.status_code)
        response = self.real_client.post(
            f"/api/admin/users/{self.admin.id}/role",
            json={"csrf_token": self.admin_csrf, "role": "user"},
        )
        self.assertEqual(400, response.status_code, response.text)
        with Session(self.engine) as db:
            self.assertEqual("admin", db.get(User, self.admin.id).role)
            self.assertEqual(1, db.query(User).filter(User.role == "admin").count())

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

    def test_react_admin_workbench_filters_insights_audit_and_bulk_operations(self):
        users = self.real_client.get("/api/admin/bootstrap", params={"status": "disabled", "page": 1})
        self.assertEqual(200, users.status_code, users.text)
        self.assertEqual("user00", users.json()["users"][0]["username"])
        self.assertGreater(users.json()["usersPagination"]["total"], 0)

        subscriptions = self.real_client.get("/api/admin/subscriptions", params={"status": "expired"})
        self.assertEqual(200, subscriptions.status_code, subscriptions.text)
        self.assertTrue(any(row["membership_status"] == "expired" for row in subscriptions.json()["items"]))

        groups = self.real_client.get("/api/admin/task-groups", params={"q": "customer"})
        self.assertEqual(200, groups.status_code, groups.text)
        self.assertEqual(1, len(groups.json()["items"]))
        self.assertEqual(2, groups.json()["items"][0]["total"])

        insights = self.real_client.get("/api/admin/insights", params={"days": 7})
        self.assertEqual(200, insights.status_code, insights.text)
        self.assertEqual(7, len(insights.json()["trend"]))
        self.assertTrue(any(row["name"] == "暂停好友" for row in insights.json()["alerts"]["failed_runs"]))

        audit = self.real_client.get("/api/admin/audit", params={"q": "task.disabled"})
        self.assertEqual(200, audit.status_code, audit.text)
        event = audit.json()["items"][0]
        detail = self.real_client.get(f"/api/admin/audit/{event['id']}")
        self.assertEqual(200, detail.status_code, detail.text)
        self.assertEqual("admin", detail.json()["actor"])

        headers = {"Content-Type": "application/json"}
        paused = self.real_client.post("/api/admin/tasks/bulk", json={
            "csrf_token": self.admin_csrf,
            "task_ids": [self.enabled_task_id, self.disabled_task_id],
            "action": "pause",
        }, headers=headers)
        self.assertEqual(200, paused.status_code, paused.text)
        self.assertEqual(2, paused.json()["updated"])
        resumed = self.real_client.post("/api/admin/tasks/bulk", json={
            "csrf_token": self.admin_csrf,
            "task_ids": [self.enabled_task_id, self.disabled_task_id],
            "action": "resume",
        }, headers=headers)
        self.assertEqual(200, resumed.status_code, resumed.text)

        limits = self.real_client.post(f"/api/admin/subscriptions/{self.customer.id}/limits", json={
            "csrf_token": self.admin_csrf, "max_accounts": 3, "max_tasks": 12,
        })
        self.assertEqual(200, limits.status_code, limits.text)
        self.assertEqual(12, limits.json()["max_tasks"])

        bulk_extend = self.real_client.post("/api/admin/subscriptions/bulk", json={
            "csrf_token": self.admin_csrf,
            "user_ids": [self.customer.id, self.active_user_id],
            "action": "extend", "duration_days": 30, "max_accounts": 1, "max_tasks": 10,
        })
        self.assertEqual(200, bulk_extend.status_code, bulk_extend.text)
        self.assertEqual(2, bulk_extend.json()["updated"])
        bulk_revoke = self.real_client.post("/api/admin/subscriptions/bulk", json={
            "csrf_token": self.admin_csrf,
            "user_ids": [self.customer.id, self.active_user_id],
            "action": "revoke",
        })
        self.assertEqual(200, bulk_revoke.status_code, bulk_revoke.text)

        self.real_client.cookies.set("spark_session", self.customer_token)
        denied = self.real_client.get("/api/admin/insights")
        self.assertEqual(404, denied.status_code)
        denied_bulk = self.real_client.post("/api/admin/tasks/bulk", json={
            "csrf_token": self.admin_csrf, "task_ids": [self.enabled_task_id], "action": "pause",
        })
        self.assertEqual(404, denied_bulk.status_code)
        self.real_client.cookies.set("spark_session", self.admin_token)

    def test_admin_user_notes_are_private_editable_and_audited(self):
        bootstrap = self.real_client.get("/api/admin/bootstrap")
        self.assertEqual(200, bootstrap.status_code, bootstrap.text)
        self.assertEqual("", self.real_client.get(f"/api/admin/users/{self.customer.id}/workspace").json()["user"]["admin_note"])

        saved = self.real_client.post(
            f"/api/admin/users/{self.customer.id}/note",
            json={"csrf_token": self.admin_csrf, "admin_note": "朋友介绍 / 免费续费"},
        )
        self.assertEqual(200, saved.status_code, saved.text)
        self.assertEqual("朋友介绍 / 免费续费", saved.json()["admin_note"])

        workspace = self.real_client.get(f"/api/admin/users/{self.customer.id}/workspace")
        self.assertEqual("朋友介绍 / 免费续费", workspace.json()["user"]["admin_note"])
        listed = self.real_client.get(f"/api/admin/users/{self.customer.id}/workspace").json()
        self.assertEqual("朋友介绍 / 免费续费", listed["user"]["admin_note"])

        self.real_client.cookies.set("spark_session", self.customer_token)
        denied = self.real_client.post(
            f"/api/admin/users/{self.customer.id}/note",
            json={"csrf_token": self.admin_csrf, "admin_note": "不应保存"},
        )
        self.assertEqual(404, denied.status_code)
        self.real_client.cookies.set("spark_session", self.admin_token)
        with Session(self.engine) as db:
            self.assertEqual("朋友介绍 / 免费续费", db.get(User, self.customer.id).admin_note)
            self.assertTrue(
                db.scalar(select(AuditEvent).where(
                    AuditEvent.action == "user.admin_note_updated",
                    AuditEvent.resource_id == self.customer.id,
                ))
            )

    def test_admin_bootstrap_defers_redemption_codes_to_paginated_endpoint(self):
        created = self.real_client.post(
            "/api/admin/redemption-codes",
            json={
                "csrf_token": self.admin_csrf,
                "duration_days": 30,
                "max_accounts": 1,
                "max_tasks": 5,
                "quantity": 1,
            },
        )
        self.assertEqual(200, created.status_code, created.text)
        plaintext = created.json()["codes"][0]
        self.assertTrue(plaintext)

        bootstrap = self.real_client.get("/api/admin/bootstrap")
        self.assertEqual(200, bootstrap.status_code, bootstrap.text)
        # Codes are credentials and must not be included in every admin page
        # bootstrap response. They are loaded only by the codes workbench.
        self.assertEqual([], bootstrap.json()["redemptionCodes"])

        codes = self.real_client.get(
            "/api/admin/redemption-codes", params={"q": plaintext}
        )
        self.assertEqual(200, codes.status_code, codes.text)
        row = next(
            item for item in codes.json()["items"]
            if item["code"] == plaintext
        )
        self.assertEqual("待兑换", row["status"])
        self.assertIsNone(row["redeemed_at"])
        self.assertIsNone(row["expires_at"])
        self.assertEqual(1, codes.json()["pagination"]["total"])

    def test_admin_bootstrap_defers_mail_settings_to_dedicated_endpoint(self):
        bootstrap = self.real_client.get("/api/admin/bootstrap")
        self.assertEqual(200, bootstrap.status_code, bootstrap.text)
        self.assertEqual({}, bootstrap.json()["mail"])

        mail = self.real_client.get("/api/admin/mail")
        self.assertEqual(200, mail.status_code, mail.text)
        self.assertFalse(mail.json()["enabled"])
        self.assertIn("deliveries", mail.json())

    def test_react_admin_run_workbench_read_model_and_retry_security(self):
        from zoneinfo import ZoneInfo

        today = self.real_client.get("/api/admin/runs", params={"day": "today"})
        self.assertEqual(200, today.status_code, today.text)
        payload = today.json()
        self.assertEqual(datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(), payload["today_board"]["day"])
        enabled_run = next(item for item in payload["items"] if item["target"] == "启用好友")
        self.assertEqual("主账号", enabled_run["account"])
        self.assertEqual(self.customer.id, enabled_run["owner_id"])
        self.assertIn("attempts", enabled_run)
        self.assertTrue(any(group["counts"]["planned"] for group in payload["today_board"]["groups"]))

        missing_csrf = self.real_client.post(
            f"/api/admin/runs/{enabled_run['id']}/retry", json={}
        )
        self.assertEqual(403, missing_csrf.status_code)
        completed_retry = self.real_client.post(
            f"/api/admin/runs/{enabled_run['id']}/retry",
            json={"csrf_token": self.admin_csrf},
        )
        self.assertEqual(409, completed_retry.status_code)

        self.real_client.cookies.set("spark_session", self.customer_token)
        denied = self.real_client.get("/api/admin/runs")
        self.assertEqual(404, denied.status_code)
        denied_retry = self.real_client.post(
            f"/api/admin/runs/{enabled_run['id']}/retry",
            json={"csrf_token": self.admin_csrf},
        )
        self.assertEqual(404, denied_retry.status_code)
        self.real_client.cookies.set("spark_session", self.admin_token)

        with session_scope(self.engine) as db:
            retryable = TaskRun(
                task_id=self.enabled_task_id,
                scheduled_for=datetime.now(timezone.utc),
                status="failed",
                stage="worker_error",
                error_code="health_retry_exhausted",
            )
            db.add(retryable)
            db.flush()
            retryable_id = retryable.id
        accepted = self.real_client.post(
            f"/api/admin/runs/{retryable_id}/retry",
            json={"csrf_token": self.admin_csrf},
        )
        self.assertEqual(200, accepted.status_code, accepted.text)
        self.assertTrue(accepted.json()["ok"])


if __name__ == "__main__":
    unittest.main()
