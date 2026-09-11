import json
import shutil
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from spark_console.db import session_scope
from spark_console.models import User, DouyinContactIdentity, SparkTask
from tests.console import test_web_user

ROOT = Path(__file__).resolve().parents[2]


class HenngUITests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node") and (ROOT / ".artifacts/ui-check/node_modules/jsdom").is_dir(), "DOM test dependency required")
    def test_experience_submission_and_view_state(self):
        result = subprocess.run([shutil.which("node"), str(ROOT / "tests/console/experience_js_harness.js")],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, result.returncode, result.stderr)

    @unittest.skipUnless(shutil.which("node") and (ROOT / ".artifacts/ui-check/node_modules/jsdom").is_dir(), "DOM test dependency required")
    def test_custom_select_preserves_form_behavior(self):
        result = subprocess.run([shutil.which("node"), str(ROOT / "tests/console/ui_select_js_harness.js")],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, result.returncode, result.stderr)

    @unittest.skipUnless(shutil.which("node") and (ROOT / ".artifacts/ui-check/node_modules/css-tree").is_dir(), "CSS parser dependency required")
    def test_studio_styles_and_theme_contrast(self):
        result = subprocess.run([shutil.which("node"), str(ROOT / "tests/console/studio_css_js_harness.js")],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, result.returncode, result.stderr)

    @unittest.skipUnless(shutil.which("node") and (ROOT / ".artifacts/ui-check/node_modules/jsdom").is_dir(), "DOM test dependency required")
    def test_restored_pages_and_friend_picker_without_browser(self):
        fixture = test_web_user.UserWebTests()
        fixture.setUp()
        try:
            with session_scope(fixture.engine) as db:
                user = db.scalar(select(User).where(User.username == "friend"))
                user.must_change_password = False
                user.role = "admin"
            fixture.login()
            from spark_console.crypto import CookieCipher
            from spark_console.services.accounts import AccountService
            from spark_console.services.audits import AuditService
            with session_scope(fixture.engine) as db:
                user = db.scalar(select(User).where(User.username == "friend"))
                account = AccountService(db, CookieCipher(b"c" * 32), AuditService(db)).create(user.id, "Test account", b'[{"name":"sid","value":"test"}]')
                db.add(DouyinContactIdentity(account_id=account.id, sec_uid="stable-a", nickname="Friend A"))
                tasks = []
                for i in range(13):
                    task = SparkTask(owner_user_id=user.id, douyin_account_id=account.id,
                                     target_name=f"Friend {i:02}", send_time=f"09:{i:02}",
                                     message_template="Test", message_kind="text", enabled=True,
                                     next_run_at=datetime.now(timezone.utc) + timedelta(days=1))
                    db.add(task)
                    tasks.append(task)
                db.flush()
                edit_route = f"/tasks/{tasks[0].id}/edit"
            routes = ["/login", "/register", "/dashboard", "/accounts", "/tasks", edit_route,
                      "/subscription", "/change-password", "/runs", "/admin/overview", "/admin/users",
                      "/admin/tasks", "/admin/subscriptions", "/admin/codes", "/admin/runs", "/admin/audit", "/admin/settings", "/admin/operations"]
            pages = {}
            for route in routes:
                response = fixture.client.get(route)
                self.assertEqual(200, response.status_code, route)
                pages[route] = response.text
            result = subprocess.run([shutil.which("node"), str(ROOT / "tests/console/restored_ui_js_harness.js")],
                                    cwd=ROOT, input=json.dumps(pages), capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn('"ok":true', result.stdout)
        finally:
            fixture.tearDown()
