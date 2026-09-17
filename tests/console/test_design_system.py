import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from spark_console.db import session_scope
from spark_console.models import User
from tests.console import test_directory


class DesignSystemTests(TestCase):
    def setUp(self):
        self.fixture = test_directory.DirectoryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client = self.fixture.client

    def test_user_pages_share_design_and_keep_workflow_controls(self):
        for route in ['/dashboard', '/accounts', '/runs', '/subscription',
                      '/attention', '/notifications', '/account-center', '/help']:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200, response.text[:200])
                self.assertIn('/static/design_system.css?v=0.2.4', response.text)
                self.assertIn('/static/design_system.js?v=0.2.4', response.text)
                self.assertIn('action="/logout"', response.text)
                self.assertIn('name="csrf_token"', response.text)
        accounts = self.client.get('/accounts').text
        self.assertIn('/rename', accounts)
        self.assertIn('/delete', accounts)
        self.assertIn('data-scan-account-id', accounts)

    def test_admin_pages_and_disabled_backup_are_distinct_from_historical_success(self):
        with session_scope(self.fixture.engine) as db:
            db.get(User, self.fixture.owner).role = 'admin'
        for route in ['/admin/overview', '/admin/today', '/admin/users', '/admin/tasks', '/admin/runs',
                      '/admin/subscriptions', '/admin/codes', '/admin/audit',
                      '/admin/operations', '/admin/settings', '/admin/settings/mail']:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200, response.text[:200])
                self.assertIn('/static/design_system.css?v=0.2.4', response.text)
        with TemporaryDirectory() as directory, patch.dict(os.environ, DYFIRE_UPDATE_DIR=directory):
            Path(directory, 'backup-status.json').write_text(json.dumps({'status':'success','at':'2026-09-12T00:00:00Z'}))
            Path(directory, 'backup-schedule.json').write_text(json.dumps({'enabled':False}))
            backup = self.client.get('/admin/diagnostics').json()['backup']
            self.assertEqual(backup['status'], 'success')
            self.assertIs(backup['schedule_enabled'], False)
            Path(directory, 'backup-schedule.json').unlink()
            self.assertIsNone(self.client.get('/admin/diagnostics').json()['backup']['schedule_enabled'])

    def test_ordinary_user_cannot_read_admin_diagnostics(self):
        self.assertIn(self.client.get('/admin/diagnostics').status_code, (403,404))

    def test_legacy_task_list_redirects_to_studio(self):
        response=self.client.get('/tasks?account=example',follow_redirects=False)
        self.assertEqual(response.status_code,303)
        self.assertEqual(response.headers['location'],'/studio?account=example')

    def test_login_lands_on_account_first_overview(self):
        response=self.fixture.fx.login()
        self.assertEqual(response.headers['location'],'/dashboard')
        self.assertEqual(self.client.get('/',follow_redirects=False).headers['location'],'/dashboard')
        html=self.client.get('/dashboard').text
        self.assertLess(html.index('overview-accounts-title'),html.index('今日运行总览'))
        self.assertIn('data-state=',html)
        self.assertIn('管理账号',html)
