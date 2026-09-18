import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select

from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import create_schema, session_scope
from spark_console.models import (
    DouyinAccount,
    DouyinContactIdentity,
    DouyinContactAvatar,
    DouyinConversation,
    SparkTask,
    SparkTaskTargetIdentity,
    TaskRun,
    User,
    UserSubscription,
)
from spark_console.security import PasswordService
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.web.app import create_app


class UserWebTests(unittest.TestCase):
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
        with session_scope(self.engine) as session:
            user = User(
                username="friend",
                password_hash=PasswordService().hash("Temporary-123!"),
                role="user",
                must_change_password=True,
            )
            session.add(user)
            session.flush()
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
            create_app(self.settings, self.engine), base_url="https://testserver"
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

    def test_first_login_is_forced_to_change_password(self):
        response = self.login()
        self.assertEqual(303, response.status_code)
        self.assertEqual("/change-password", response.headers["location"])
        dashboard = self.client.get("/dashboard", follow_redirects=False)
        self.assertEqual(303, dashboard.status_code)
        self.assertEqual("/change-password", dashboard.headers["location"])

    def test_post_without_csrf_is_rejected(self):
        self.login()
        response = self.client.post(
            "/change-password",
            data={"current_password": "Temporary-123!", "new_password": "Permanent-123!"},
        )
        self.assertEqual(403, response.status_code)

    def test_logged_in_user_has_change_password_navigation(self):
        self.login()
        page = self.client.get("/change-password")
        marker = 'name="csrf_token" value="'
        csrf = page.text.split(marker, 1)[1].split('"', 1)[0]
        self.client.post(
            "/change-password",
            data={
                "csrf_token": csrf,
                "current_password": "Temporary-123!",
                "new_password": "Permanent-123!",
                "new_password_confirmation": "Permanent-123!",
            },
        )

        dashboard = self.client.get("/dashboard")
        change_page = self.client.get("/change-password")

        self.assertIn('href="/account-center"', dashboard.text)
        self.assertIn('href="/change-password"', self.client.get('/account-center').text)
        self.assertIn("修改密码", change_page.text)
        self.assertIn('name="new_password_confirmation"', change_page.text)

    def test_change_password_page_uses_centered_security_layout(self):
        self.login()

        page = self.client.get("/change-password")

        self.assertEqual(200, page.status_code)
        self.assertIn('class="security-page"', page.text)
        self.assertIn('class="auth-card security-card"', page.text)
        self.assertNotIn('minlength="12"', page.text)
        self.assertEqual(3, page.text.count("data-password-toggle"))
        self.assertIn('/static/password_toggle.js?v=20260902-1', page.text)

    def test_change_password_accepts_short_nonempty_password(self):
        self.login()
        page = self.client.get("/change-password")
        csrf = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

        response = self.client.post(
            "/change-password",
            data={
                "csrf_token": csrf,
                "current_password": "Temporary-123!",
                "new_password": "A1",
                "new_password_confirmation": "A1",
            },
            follow_redirects=False,
        )

        self.assertEqual(303, response.status_code)
        self.assertEqual("/dashboard?notice=password-updated", response.headers["location"])
        with session_scope(self.engine) as session:
            user = session.scalar(select(User).where(User.username == "friend"))
            self.assertTrue(PasswordService().verify(user.password_hash, "A1"))

    def test_change_password_rejects_empty_password(self):
        self.login()
        page = self.client.get("/change-password")
        csrf = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

        response = self.client.post(
            "/change-password",
            data={
                "csrf_token": csrf,
                "current_password": "Temporary-123!",
                "new_password": "",
                "new_password_confirmation": "",
            },
        )

        self.assertEqual(400, response.status_code)
        self.assertIn("新密码不能为空", response.text)

    def test_change_password_rejects_mismatched_confirmation(self):
        self.login()
        page = self.client.get("/change-password")
        marker = 'name="csrf_token" value="'
        csrf = page.text.split(marker, 1)[1].split('"', 1)[0]

        response = self.client.post(
            "/change-password",
            data={
                "csrf_token": csrf,
                "current_password": "Temporary-123!",
                "new_password": "Permanent-123!",
                "new_password_confirmation": "Different-123!",
            },
        )

        self.assertEqual(400, response.status_code)
        self.assertIn("两次输入的新密码不一致", response.text)

    def test_wrong_password_renders_visible_login_error(self):
        response = self.client.post(
            "/login",
            data={"username": "friend", "password": "definitely-wrong"},
            follow_redirects=False,
        )
        self.assertEqual(400, response.status_code)
        self.assertIn("用户名或密码错误", response.text)
        self.assertIn('name="password"', response.text)
        self.assertIn('value="friend"', response.text)

    def test_dashboard_shows_four_step_onboarding_and_health_summary(self):
        self.login()
        with session_scope(self.engine) as session:
            user = session.scalar(select(User).where(User.username == "friend"))
            user.must_change_password = False

        response = self.client.get("/dashboard")

        self.assertEqual(200, response.status_code)
        self.assertIn("首次设置", response.text)
        self.assertIn("开通会员", response.text)
        self.assertIn("绑定抖音账号", response.text)
        self.assertIn("创建任务", response.text)
        self.assertIn("确认发送", response.text)
        self.assertIn("已发出", response.text)

    def test_user_task_pages_show_operational_details_and_confirm_delete(self):
        self.login()
        with session_scope(self.engine) as session:
            user = session.scalar(select(User).where(User.username == "friend"))
            user.must_change_password = False
            account = AccountService(
                session,
                CookieCipher(self.settings.cookie_key_file.read_bytes()),
                AuditService(session),
            ).create(
                user.id,
                "我的抖音",
                b'[{"name":"sessionid","value":"secret","url":"https://www.douyin.com"}]',
            )
            task = SparkTask(
                owner_user_id=user.id,
                douyin_account_id=account.id,
                target_name="好友",
                send_time="09:00",
                message_template="今日火花",
                enabled=True,
                next_run_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
            session.add(task)
            session.flush()
            session.add(
                TaskRun(
                    task_id=task.id,
                    scheduled_for=datetime.now(timezone.utc),
                    status="failed",
                    stage="authenticating",
                    error_code="cookie_invalid",
                    error_summary="账号凭据格式无效",
                )
            )

        tasks_page = self.client.get("/tasks")
        runs_page = self.client.get("/runs")

        self.assertIn("发送账号", tasks_page.text)
        self.assertIn("下次执行", tasks_page.text)
        self.assertIn("最近结果", tasks_page.text)
        self.assertIn("data-confirm=", tasks_page.text)
        self.assertIn("查看复核结果或重新登录", runs_page.text)
        self.assertIn("已启用任务将在恢复后继续后续计划", runs_page.text)

    def test_task_page_fetches_all_latest_runs_in_one_query(self):
        self.login()
        now = datetime.now(timezone.utc)
        with session_scope(self.engine) as session:
            user = session.scalar(select(User).where(User.username == "friend"))
            user.must_change_password = False
            account = AccountService(
                session,
                CookieCipher(self.settings.cookie_key_file.read_bytes()),
                AuditService(session),
            ).create(
                user.id,
                "我的抖音",
                b'[{"name":"sessionid","value":"secret","url":"https://www.douyin.com"}]',
            )
            first = SparkTask(
                owner_user_id=user.id,
                douyin_account_id=account.id,
                target_name="好友甲",
                send_time="09:00",
                message_template="今日火花",
                enabled=True,
            )
            second = SparkTask(
                owner_user_id=user.id,
                douyin_account_id=account.id,
                target_name="好友乙",
                send_time="10:00",
                message_template="今日火花",
                enabled=True,
            )
            session.add_all([first, second])
            session.flush()
            session.add_all(
                [
                    TaskRun(
                        task_id=first.id,
                        scheduled_for=now - timedelta(hours=2),
                        status="success",
                        stage="complete",
                    ),
                    TaskRun(
                        task_id=first.id,
                        scheduled_for=now - timedelta(hours=1),
                        status="failed",
                        stage="sending",
                    ),
                    TaskRun(
                        task_id=second.id,
                        scheduled_for=now - timedelta(hours=2),
                        status="failed",
                        stage="sending",
                    ),
                    TaskRun(
                        task_id=second.id,
                        scheduled_for=now - timedelta(minutes=30),
                        status="submitted_unconfirmed",
                        stage="submitted",
                    ),
                ]
            )

        statements = []

        def capture_statement(
            _connection, _cursor, statement, _parameters, _context, _executemany
        ):
            statements.append(" ".join(statement.split()))

        event.listen(self.engine, "before_cursor_execute", capture_statement)
        try:
            response = self.client.get("/tasks")
        finally:
            event.remove(self.engine, "before_cursor_execute", capture_statement)

        self.assertEqual(200, response.status_code)
        task_run_queries = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
            and "FROM task_runs" in statement
        ]
        self.assertEqual(1, len(task_run_queries), task_run_queries)
        self.assertIn("失败", response.text)
        self.assertIn("已提交，待核实", response.text)

    def test_login_page_offers_invite_registration(self):
        response = self.client.get("/login")

        self.assertEqual(200, response.status_code)
        self.assertIn('href="/register"', response.text)
        self.assertIn('class="studio-auth-layout"', response.text)
        self.assertIn('/static/studio_site.css?v=0.1.1', response.text)
        self.assertNotIn("登录控制台", response.text)
        self.assertIn("<span>登录</span>", response.text)
        self.assertIn('/static/studio_site.js?v=0.1.1', response.text)
        self.assertEqual(1, response.text.count("data-password-toggle"))
        self.assertIn('aria-label="显示密码"', response.text)

    def test_login_stylesheet_uses_same_origin_path_behind_https_proxy(self):
        with TestClient(
            create_app(self.settings, self.engine), base_url="http://internal"
        ) as client:
            response = client.get(
                "/login",
                headers={
                    "host": "henng.cn",
                    "x-forwarded-proto": "https",
                },
            )

        self.assertEqual(200, response.status_code)
        self.assertIn('href="/static/studio_site.css?', response.text)
        self.assertNotIn('href="/static/mineradio.css?', response.text)
        self.assertNotIn('href="http://henng.cn/static/app.css"', response.text)

    def test_http_mode_issues_a_session_cookie_the_browser_can_return(self):
        http_settings = replace(self.settings, secure_cookies=False)
        with TestClient(
            create_app(http_settings, self.engine), base_url="http://testserver"
        ) as client:
            response = client.post(
                "/login",
                data={"username": "friend", "password": "Temporary-123!"},
                follow_redirects=False,
            )
            self.assertNotIn("secure", response.headers["set-cookie"].lower())
            self.assertEqual(200, client.get("/change-password").status_code)

    def test_account_page_never_accepts_or_renders_manual_credentials(self):
        self.login()
        page = self.client.get("/change-password")
        marker = 'name="csrf_token" value="'
        csrf = page.text.split(marker, 1)[1].split('"', 1)[0]
        self.client.post(
            "/change-password",
            data={
                "csrf_token": csrf,
                "current_password": "Temporary-123!",
                "new_password": "Permanent-123!",
                "new_password_confirmation": "Permanent-123!",
            },
        )
        page = self.client.get("/accounts")
        self.assertNotIn('name="cookies"', page.text)
        self.assertNotIn("Cookie JSON", page.text)
        response = self.client.post(
            "/accounts",
            data={
                "csrf_token": page.text.split(marker, 1)[1].split('"', 1)[0],
                "display_name": "主账号",
                "cookies": '[{"name":"sessionid","value":"sessionid-secret"}]',
            },
            follow_redirects=False,
        )
        self.assertEqual(405, response.status_code)

    def test_task_page_loads_saved_account_conversations_without_browser_access(self):
        self.login()
        page = self.client.get("/change-password")
        marker = 'name="csrf_token" value="'
        csrf = page.text.split(marker, 1)[1].split('"', 1)[0]
        self.client.post(
            "/change-password",
            data={
                "csrf_token": csrf,
                "current_password": "Temporary-123!",
                "new_password": "Permanent-123!",
                "new_password_confirmation": "Permanent-123!",
            },
        )
        with session_scope(self.engine) as session:
            user = session.scalar(select(User).where(User.username == "friend"))
            account = AccountService(
                session,
                CookieCipher(self.settings.cookie_key_file.read_bytes()),
                AuditService(session),
            ).create(
                user.id,
                "我的抖音",
                b'[{"name":"sessionid","value":"secret-marker","url":"https://www.douyin.com"}]',
            )
            account_id = account.id
            session.add_all(
                [
                    DouyinConversation(account_id=account_id, display_name="wzlovegsy"),
                    DouyinConversation(account_id=account_id, display_name="gsy"),
                    DouyinContactIdentity(
                        account_id=account_id,
                        sec_uid="stable-user-id",
                        unique_id="wzlovegsy",
                        nickname="新的昵称",
                        remark_name="我的备注",
                    ),
                ]
            )
        tasks_page = self.client.get("/tasks")
        response = self.client.get(f"/accounts/{account_id}/conversations")

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {
                "items": [
                    {"name": "gsy", "sec_uid": None},
                    {"name": "我的备注", "sec_uid": "stable-user-id", "nickname": "新的昵称", "remark_name": "我的备注", "unique_id": "wzlovegsy", "short_id": None},
                ], "sync": None, "active_sync": None,
            },
            response.json(),
        )
        self.assertIn("data-target-multiselect", tasks_page.text)
        self.assertIn('id="target-hidden-fields"', tasks_page.text)
        self.assertIn('id="target-picker-dialog"', tasks_page.text)
        self.assertIn("批量选择续火好友", tasks_page.text)
        self.assertIn("读取好友列表", tasks_page.text)
        self.assertNotIn("secret-marker", response.text)

        avatar_url = "https://p3.douyinpic.com/friend.jpeg"
        with session_scope(self.engine) as session:
            session.add(DouyinContactAvatar(account_id=account_id, sec_uid="stable-user-id", avatar_url=avatar_url))
            other = User(username="other-avatar-owner", password_hash="hash")
            session.add(other)
            session.flush()
            other_account = AccountService(session, CookieCipher(b"c" * 32), AuditService(session)).create(
                other.id, "Other", b'[{"name":"sid","value":"test"}]'
            )
            other_id = other_account.id
            session.add(DouyinContactAvatar(account_id=other_id, sec_uid="stable-user-id", avatar_url="https://p3.douyinpic.com/private.jpeg"))
        response = self.client.get(f"/accounts/{account_id}/conversations")
        self.assertEqual(avatar_url, response.json()["items"][1]["avatar_url"])
        self.assertNotIn("avatar_url", response.json()["items"][0])
        self.assertNotIn("private.jpeg", response.text)
        self.assertEqual(404, self.client.get(f"/accounts/{other_id}/conversations").status_code)

    def test_new_task_binds_selected_stable_contact_identity(self):
        self.login()
        with session_scope(self.engine) as session:
            user = session.scalar(select(User).where(User.username == "friend"))
            user.must_change_password = False
            account = AccountService(
                session,
                CookieCipher(self.settings.cookie_key_file.read_bytes()),
                AuditService(session),
            ).create(
                user.id,
                "我的抖音",
                b'[{"name":"sessionid","value":"secret","url":"https://www.douyin.com"}]',
            )
            session.add(
                DouyinContactIdentity(
                    account_id=account.id,
                    sec_uid="stable-user-id",
                    nickname="新的昵称",
                )
            )
            account_id = account.id
        page = self.client.get("/tasks")
        csrf = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

        response = self.client.post(
            "/tasks",
            data={
                "csrf_token": csrf,
                "account_id": account_id,
                "target_name": "新的昵称",
                "target_sec_uid": "stable-user-id",
                "send_time": "09:00",
                "message_template": "今日火花",
            },
            follow_redirects=False,
        )

        self.assertEqual(303, response.status_code)
        with session_scope(self.engine) as session:
            task = session.scalar(select(SparkTask).where(SparkTask.owner_user_id == user.id))
            binding = session.get(SparkTaskTargetIdentity, task.id)
            self.assertEqual("stable-user-id", binding.sec_uid)

    def test_new_task_post_creates_one_task_per_selected_friend(self):
        self.login()
        with session_scope(self.engine) as session:
            user = session.scalar(select(User).where(User.username == "friend"))
            user.must_change_password = False
            account = AccountService(
                session,
                CookieCipher(self.settings.cookie_key_file.read_bytes()),
                AuditService(session),
            ).create(
                user.id,
                "我的抖音",
                b'[{"name":"sessionid","value":"secret","url":"https://www.douyin.com"}]',
            )
            session.add_all(
                [
                    DouyinContactIdentity(
                        account_id=account.id,
                        sec_uid="stable-a",
                        nickname="好友甲",
                    ),
                    DouyinContactIdentity(
                        account_id=account.id,
                        sec_uid="stable-b",
                        nickname="好友乙",
                    ),
                ]
            )
            account_id = account.id
            user_id = user.id
        page = self.client.get("/tasks")
        csrf = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

        response = self.client.post(
            "/tasks",
            data={
                "csrf_token": csrf,
                "account_id": account_id,
                "target_name": ["好友甲", "好友乙"],
                "target_sec_uid": ["stable-a", "stable-b"],
                "send_time": "09:00",
                "message_template": "续火花",
                "message_kind": "douyin_sticker",
            },
            follow_redirects=False,
        )

        self.assertEqual(303, response.status_code)
        with session_scope(self.engine) as session:
            tasks = session.scalars(
                select(SparkTask)
                .where(SparkTask.owner_user_id == user_id)
                .order_by(SparkTask.created_at, SparkTask.id)
            ).all()
            self.assertEqual({"好友甲", "好友乙"}, {task.target_name for task in tasks})
            self.assertTrue(all(task.message_kind == "douyin_sticker" for task in tasks))
            bindings = {
                session.get(SparkTaskTargetIdentity, task.id).sec_uid for task in tasks
            }
            self.assertEqual({"stable-a", "stable-b"}, bindings)

    def test_new_task_post_allows_same_name_for_distinct_stable_friends(self):
        self.login()
        with session_scope(self.engine) as session:
            user = session.scalar(select(User).where(User.username == "friend"))
            user.must_change_password = False
            account = AccountService(
                session,
                CookieCipher(self.settings.cookie_key_file.read_bytes()),
                AuditService(session),
            ).create(
                user.id,
                "我的抖音",
                b'[{"name":"sessionid","value":"secret","url":"https://www.douyin.com"}]',
            )
            session.add_all(
                [
                    DouyinContactIdentity(
                        account_id=account.id,
                        sec_uid="same-name-a",
                        nickname="同名好友",
                    ),
                    DouyinContactIdentity(
                        account_id=account.id,
                        sec_uid="same-name-b",
                        nickname="同名好友",
                    ),
                ]
            )
            account_id = account.id
            user_id = user.id
        page = self.client.get("/tasks")
        csrf = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

        response = self.client.post(
            "/tasks",
            data={
                "csrf_token": csrf,
                "account_id": account_id,
                "target_name": ["同名好友", "同名好友"],
                "target_sec_uid": ["same-name-a", "same-name-b"],
                "send_time": "09:00",
                "message_template": "续火花",
                "message_kind": "douyin_sticker",
            },
            follow_redirects=False,
        )

        self.assertEqual(303, response.status_code)
        with session_scope(self.engine) as session:
            tasks = session.scalars(
                select(SparkTask).where(SparkTask.owner_user_id == user_id)
            ).all()
            self.assertEqual(2, len(tasks))
            self.assertEqual(2, len({task.target_key for task in tasks}))

    def test_multi_target_post_rejects_invalid_identity_without_partial_creation(self):
        self.login()
        with session_scope(self.engine) as session:
            user = session.scalar(select(User).where(User.username == "friend"))
            user.must_change_password = False
            account = AccountService(
                session,
                CookieCipher(self.settings.cookie_key_file.read_bytes()),
                AuditService(session),
            ).create(
                user.id,
                "我的抖音",
                b'[{"name":"sessionid","value":"secret","url":"https://www.douyin.com"}]',
            )
            session.add(
                DouyinContactIdentity(
                    account_id=account.id,
                    sec_uid="stable-valid",
                    nickname="有效好友",
                )
            )
            account_id = account.id
            user_id = user.id
        page = self.client.get("/tasks")
        csrf = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

        response = self.client.post(
            "/tasks",
            data={
                "csrf_token": csrf,
                "account_id": account_id,
                "target_name": ["有效好友", "无效好友"],
                "target_sec_uid": ["stable-valid", "stable-missing"],
                "send_time": "09:00",
                "message_template": "今日火花",
            },
            follow_redirects=False,
        )

        self.assertEqual(400, response.status_code)
        with session_scope(self.engine) as session:
            count = len(
                session.scalars(
                    select(SparkTask).where(SparkTask.owner_user_id == user_id)
                ).all()
            )
            self.assertEqual(0, count)

    def test_multi_target_post_checks_quota_for_all_new_tasks(self):
        self.login()
        with session_scope(self.engine) as session:
            user = session.scalar(select(User).where(User.username == "friend"))
            user.must_change_password = False
            subscription = session.get(UserSubscription, user.id)
            subscription.max_tasks = 2
            account = AccountService(
                session,
                CookieCipher(self.settings.cookie_key_file.read_bytes()),
                AuditService(session),
            ).create(
                user.id,
                "我的抖音",
                b'[{"name":"sessionid","value":"secret","url":"https://www.douyin.com"}]',
            )
            session.add(
                SparkTask(
                    owner_user_id=user.id,
                    douyin_account_id=account.id,
                    target_name="已有好友",
                    send_time="08:00",
                    message_template="今日火花",
                    enabled=True,
                )
            )
            account_id = account.id
            user_id = user.id
        page = self.client.get("/tasks")
        csrf = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

        response = self.client.post(
            "/tasks",
            data={
                "csrf_token": csrf,
                "account_id": account_id,
                "target_name": ["好友甲", "好友乙"],
                "target_sec_uid": ["", ""],
                "send_time": "09:00",
                "message_template": "今日火花",
            },
            follow_redirects=False,
        )

        self.assertEqual(303, response.status_code)
        self.assertEqual("/subscription?required=1", response.headers["location"])
        with session_scope(self.engine) as session:
            tasks = session.scalars(
                select(SparkTask).where(SparkTask.owner_user_id == user_id)
            ).all()
            self.assertEqual(["已有好友"], [task.target_name for task in tasks])

    def test_new_task_can_select_native_spark_sticker(self):
        self.login()
        with session_scope(self.engine) as session:
            user = session.scalar(select(User).where(User.username == "friend"))
            user.must_change_password = False
            account = AccountService(
                session,
                CookieCipher(self.settings.cookie_key_file.read_bytes()),
                AuditService(session),
            ).create(
                user.id,
                "我的抖音",
                b'[{"name":"sessionid","value":"secret","url":"https://www.douyin.com"}]',
            )
            account_id = account.id
        page = self.client.get("/tasks")
        self.assertIn('name="message_kind"', page.text)
        self.assertIn("抖音原生表情", page.text)
        csrf = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

        response = self.client.post(
            "/tasks",
            data={
                "csrf_token": csrf,
                "account_id": account_id,
                "target_name": "新的好友",
                "send_time": "09:00",
                "message_template": "续火花",
                "message_kind": "douyin_sticker",
            },
            follow_redirects=False,
        )

        self.assertEqual(303, response.status_code)
        with session_scope(self.engine) as session:
            task = session.scalar(select(SparkTask).where(SparkTask.owner_user_id == user.id))
            self.assertEqual("douyin_sticker", task.message_kind)
            self.assertEqual("续火花", task.message_template)

    def test_user_can_open_and_submit_task_editor(self):
        self.login()
        with session_scope(self.engine) as session:
            user = session.scalar(select(User).where(User.username == "friend"))
            user.must_change_password = False
            account = AccountService(
                session,
                CookieCipher(self.settings.cookie_key_file.read_bytes()),
                AuditService(session),
            ).create(
                user.id,
                "我的抖音",
                b'[{"name":"sessionid","value":"secret","url":"https://www.douyin.com"}]',
            )
            session.add(
                DouyinContactIdentity(
                    account_id=account.id,
                    sec_uid="edited-stable-id",
                    nickname="编辑后的好友",
                )
            )
            task = SparkTask(
                owner_user_id=user.id,
                douyin_account_id=account.id,
                target_name="旧好友",
                send_time="09:00",
                message_template="旧消息",
                enabled=True,
            )
            session.add(task)
            session.flush()
            task_id = task.id
            account_id = account.id

        tasks_page = self.client.get("/tasks")
        self.assertIn(f'href="/tasks/{task_id}/edit"', tasks_page.text)
        edit_page = self.client.get(f"/tasks/{task_id}/edit")
        self.assertEqual(200, edit_page.status_code)
        self.assertIn("编辑续火任务", edit_page.text)
        self.assertIn('value="09:00"', edit_page.text)
        self.assertIn("旧消息", edit_page.text)
        csrf = edit_page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

        response = self.client.post(
            f"/tasks/{task_id}/edit",
            data={
                "csrf_token": csrf,
                "account_id": account_id,
                "target_name": "编辑后的好友",
                "target_sec_uid": "edited-stable-id",
                "send_time": "21:30",
                "message_template": "编辑后的消息",
            },
            follow_redirects=False,
        )

        self.assertEqual(303, response.status_code)
        self.assertEqual("/tasks?notice=task-updated", response.headers["location"])
        with session_scope(self.engine) as session:
            task = session.get(SparkTask, task_id)
            binding = session.get(SparkTaskTargetIdentity, task_id)
            self.assertEqual("编辑后的好友", task.target_name)
            self.assertEqual("21:30", task.send_time)
            self.assertEqual("编辑后的消息", task.message_template)
            self.assertEqual("edited-stable-id", binding.sec_uid)

    def test_run_history_uses_chinese_status_and_shanghai_time(self):
        with session_scope(self.engine) as session:
            user = session.scalar(select(User).where(User.username == "friend"))
            user.must_change_password = False
            account = DouyinAccount(
                owner_user_id=user.id,
                display_name="我的抖音",
                encrypted_cookies=b"encrypted",
                cookie_nonce=b"nonce",
            )
            session.add(account)
            session.flush()
            task = SparkTask(
                owner_user_id=user.id,
                douyin_account_id=account.id,
                target_name="繁花",
                send_time="16:36",
                message_template="今日火花",
                enabled=True,
            )
            session.add(task)
            session.flush()
            session.add(
                TaskRun(
                    task_id=task.id,
                    scheduled_for=datetime(2026, 8, 26, 8, 36, tzinfo=timezone.utc),
                    status="success",
                    stage="submitted",
                    error_code="delivery_confirmation_unavailable",
                    error_summary="消息已提交，页面未能二次确认",
                    started_at=datetime(2026, 8, 26, 8, 36, 2, tzinfo=timezone.utc),
                    finished_at=datetime(2026, 8, 26, 8, 36, 8, tzinfo=timezone.utc),
                )
            )
            session.add(
                TaskRun(
                    task_id=task.id,
                    scheduled_for=datetime(2026, 8, 25, 8, 36, tzinfo=timezone.utc),
                    status="failed",
                    stage="worker_error",
                    error_code="unexpected_error",
                    error_summary="任务执行发生意外异常，Worker 已继续运行",
                )
            )
        self.login()

        response = self.client.get("/runs")

        self.assertEqual(200, response.status_code)
        self.assertIn("16:36", response.text)
        self.assertIn("成功", response.text)
        self.assertIn("已提交发送", response.text)
        self.assertIn("消息已提交，页面未能二次确认", response.text)
        self.assertIn('class="run-note info"', response.text)
        self.assertIn("失败", response.text)
        self.assertIn("执行异常", response.text)
        self.assertIn("繁花", response.text)
        self.assertIn('class="records-list"', response.text)
        self.assertEqual(2, response.text.count('<details class="record-item"'))
        self.assertIn("6.0 秒", response.text)

    def test_run_history_paginates_filters_and_isolates_customers(self):
        now = datetime.now(timezone.utc)
        with session_scope(self.engine) as session:
            user = session.scalar(select(User).where(User.username == "friend"))
            user.must_change_password = False
            other = User(username="other", password_hash="unused")
            session.add(other)
            session.flush()
            tasks = [SparkTask(owner_user_id=owner, target_name=name, send_time="09:00", message_template="hello")
                     for owner, name in [(user.id, "好友%_甲"), (other.id, "其他客户的好友")]]
            session.add_all(tasks)
            session.flush()
            for index in range(27):
                session.add(TaskRun(
                    id=f"own-run-{index:02}", task_id=tasks[0].id,
                    scheduled_for=now - timedelta(minutes=index),
                    status="failed" if index == 0 else "success",
                ))
            session.add(TaskRun(task_id=tasks[1].id, scheduled_for=now, status="failed"))
        self.login()
        first = self.client.get("/runs")
        second = self.client.get("/runs?page=2")
        self.assertEqual(200, first.status_code)
        self.assertEqual(12, first.text.count('<details class="record-item"'))
        self.assertIn('data-run-id="own-run-00"', first.text)
        self.assertNotIn('data-run-id="own-run-12"', first.text)
        self.assertIn('data-run-id="own-run-12"', second.text)
        self.assertNotIn('data-run-id="own-run-00"', second.text)
        self.assertIn("共 27 条", first.text)
        self.assertNotIn("其他客户的好友", first.text)
        filtered = self.client.get("/runs", params={"q": "%_", "status": "attention"})
        self.assertEqual(1, filtered.text.count('<details class="record-item"'))
        self.assertIn('data-run-id="own-run-00"', filtered.text)
        self.assertNotIn("其他客户的好友", filtered.text)
        empty = self.client.get("/runs", params={"q": "absent", "page": 99})
        self.assertIn("没有匹配的执行记录", empty.text)
        self.assertIn("第 1 / 1 页", empty.text)
        last = self.client.get("/runs?page=9999")
        self.assertEqual(3, last.text.count('<details class="record-item"'))
        invalid = self.client.get("/runs?page=-1&page_size=1000000&status=invalid")
        self.assertEqual(12, invalid.text.count('<details class="record-item"'))
        larger = self.client.get("/runs?page_size=24")
        self.assertEqual(24, larger.text.count('<details class="record-item"'))


if __name__ == "__main__":
    unittest.main()
