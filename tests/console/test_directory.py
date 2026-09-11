import asyncio
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from spark_console.db import session_scope
from spark_console.crypto import CookieCipher
from spark_console.models import (User, DouyinAccount, DouyinAccountProfile, ContactDirectoryJob,
    DouyinContactIdentity, SparkTask, SparkTaskTargetIdentity, utc_now)
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.services.directory import save_profile, save_contacts, process_directory_job, task_avatar_map
from spark_console.web.task_groups import list_task_groups, list_group_tasks
from spark_console.directory_browser import collect_directory, ROWS_SCRIPT, read_self_profile
from tests.console import test_web_user


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.fx = test_web_user.UserWebTests()
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)
        self.engine, self.client = self.fx.engine, self.fx.client
        self.cipher = CookieCipher(b'c' * 32)
        with session_scope(self.engine) as db:
            user = db.scalar(select(User).where(User.username == 'friend'))
            user.must_change_password = False
            self.owner = user.id
            account = AccountService(db, self.cipher, AuditService(db)).create(user.id, '抖音账号', b'[{"name":"sid","value":"test"}]')
            self.account = account.id
        self.fx.login()
        html = self.client.get('/accounts').text
        self.csrf = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    def post(self, account=None, **values):
        return self.client.post(f'/accounts/{account or self.account}/directory-jobs', data={'csrf_token': self.csrf, **values})

    def test_csrf_ownership_idempotency_and_pending_resume(self):
        self.assertEqual(422, self.client.post(f'/accounts/{self.account}/directory-jobs').status_code)
        self.assertEqual(403, self.post(csrf_token='wrong').status_code)
        with session_scope(self.engine) as db:
            user = User(username='other', password_hash='unused', role='user')
            db.add(user); db.flush()
            account = AccountService(db, self.cipher, AuditService(db)).create(user.id, 'Other', b'[{"name":"sid","value":"test"}]')
            other = account.id
        self.assertEqual(404, self.post(account=other).status_code)
        first = self.post()
        self.assertEqual(202, first.status_code, first.text)
        job = first.json()
        self.assertEqual(20, job['limit'])
        self.assertEqual(job['id'], self.post().json()['id'])
        self.assertEqual(409, self.post(query='new').status_code)
        self.assertEqual(404, self.client.get(f'/accounts/{other}/directory-jobs/{job["id"]}').status_code)
        self.assertEqual(job['id'], self.client.get(f'/accounts/{self.account}/conversations').json()['active_sync']['id'])

    def test_validation_expired_jobs_and_throttle(self):
        self.assertEqual(422, self.post(limit=21).status_code)
        self.assertEqual(422, self.post(query='x'*101).status_code)
        with session_scope(self.engine) as db:
            db.add(ContactDirectoryJob(account_id=self.account, owner_user_id=self.owner, status='running', created_at=utc_now()-timedelta(minutes=6)))
        self.assertEqual(202, self.post().status_code)
        with session_scope(self.engine) as db:
            for job in db.scalars(select(ContactDirectoryJob)):
                job.status = 'succeeded'; job.finished_at = utc_now()
            for _ in range(3):
                db.add(ContactDirectoryJob(account_id=self.account, owner_user_id=self.owner, status='succeeded', finished_at=utc_now()))
        self.assertEqual(429, self.post().status_code)

    def test_profile_auto_name_and_custom_remark_preserved(self):
        with session_scope(self.engine) as db:
            account = db.get(DouyinAccount, self.account)
            save_profile(db, account, {'nickname':'真实昵称', 'avatar_url':'https://p3.douyinpic.com/a.jpg'})
            db.flush()
            self.assertEqual('真实昵称', account.display_name)
            AccountService(db, self.cipher, AuditService(db)).rename_owned(self.owner, self.account, '工作号')
            save_profile(db, account, {'nickname':'新昵称'})
            self.assertEqual('工作号', account.display_name)
            self.assertEqual('新昵称', db.get(DouyinAccountProfile, self.account).nickname)
        page = self.client.get('/accounts')
        self.assertIn('新昵称', page.text)
        self.assertIn('备注：工作号', page.text)

    def test_sync_persists_aliases_without_changing_credentials(self):
        job = self.post().json()
        with session_scope(self.engine) as db:
            original = db.get(DouyinAccount, self.account).encrypted_cookies
        reader = AsyncMock(return_value={'items':[{'name':'同学', 'sec_uid':'uid-a', 'nickname':'小张', 'remark_name':'同学', 'unique_id':'zhang123', 'avatar_url':'https://p3.douyinpic.com/a.jpg'}], 'profile':{'nickname':'本人'}, 'has_more':True})
        asyncio.run(process_directory_job(self.engine, self.cipher, reader))
        response = self.client.get(f'/accounts/{self.account}/conversations').json()
        self.assertEqual('succeeded', response['sync']['status'])
        self.assertEqual('小张', response['items'][0]['nickname'])
        self.assertEqual('zhang123', response['items'][0]['unique_id'])
        with session_scope(self.engine) as db:
            self.assertEqual(original, db.get(DouyinAccount, self.account).encrypted_cookies)
            self.assertEqual('succeeded', db.get(ContactDirectoryJob, job['id']).status)

    def test_inconclusive_search_does_not_block_healthy_account(self):
        from core.session_health import SessionEvidence, SessionUnavailable
        with session_scope(self.engine) as db:
            db.get(DouyinAccount, self.account).validation_state='valid'
        self.post(query='同学')
        reader=AsyncMock(side_effect=SessionUnavailable(SessionEvidence('temporary','页面未就绪')))
        asyncio.run(process_directory_job(self.engine,self.cipher,reader))
        with session_scope(self.engine) as db:
            self.assertEqual('valid',db.get(DouyinAccount,self.account).validation_state)
            job=db.scalar(select(ContactDirectoryJob))
            self.assertEqual('failed',job.status)

    def test_relogin_race_does_not_persist_old_sync(self):
        job = self.post().json()
        async def reader(*args):
            with session_scope(self.engine) as db:
                db.get(DouyinAccount, self.account).encrypted_cookies = b'replaced'
            return {'items':[], 'profile':{'nickname':'过时昵称'}}
        asyncio.run(process_directory_job(self.engine, self.cipher, reader))
        with session_scope(self.engine) as db:
            self.assertEqual('failed', db.get(ContactDirectoryJob, job['id']).status)
            self.assertIsNone(db.get(DouyinAccountProfile, self.account))

    def test_timeout_and_corrupt_credential_fail_cleanly(self):
        job = self.post().json()
        asyncio.run(process_directory_job(self.engine, self.cipher, AsyncMock(side_effect=TimeoutError())))
        with session_scope(self.engine) as db:
            self.assertIn('超时', db.get(ContactDirectoryJob, job['id']).error)
            db.get(DouyinAccount, self.account).encrypted_cookies = b'corrupt'
        next_job = self.post().json()
        asyncio.run(process_directory_job(self.engine, self.cipher, AsyncMock()))
        with session_scope(self.engine) as db:
            self.assertEqual('failed', db.get(ContactDirectoryJob, next_job['id']).status)

    def test_groups_paginate_accounts_and_lazy_children_with_filter(self):
        with session_scope(self.engine) as db:
            save_profile(db, db.get(DouyinAccount, self.account), {'nickname':'真实昵称'})
            for i in range(45):
                db.add(SparkTask(owner_user_id=self.owner, douyin_account_id=self.account, target_name=f'Friend {i}', send_time='12:00', message_template='test', enabled=i%2 == 0))
            for i in range(21):
                a = AccountService(db, self.cipher, AuditService(db)).create(self.owner, '同名账号', b'[{"name":"sid","value":"test"}]')
                db.add(SparkTask(owner_user_id=self.owner, douyin_account_id=a.id, target_name='Friend', send_time='12:00', message_template='test'))
            db.flush()
            groups, pagination = list_task_groups(db)
            self.assertEqual(22, pagination['total'])
            self.assertEqual(20, len(groups))
            rows, pagination = list_group_tasks(db, self.owner, self.account, page=2)
            self.assertEqual(45, pagination['total']); self.assertEqual(20, len(rows))
            groups, pagination = list_task_groups(db, q='真实昵称', status='disabled')
            self.assertEqual(1, pagination['total']); self.assertEqual(22, groups[0]['total'])
        path = f'/admin/tasks/groups/{self.owner}/{self.account}'
        self.assertEqual(404, self.client.get(path).status_code)
        with session_scope(self.engine) as db:
            db.get(User, self.owner).role = 'admin'
        page = self.client.get('/admin/tasks?q=真实昵称')
        self.assertIn('data-task-group', page.text)
        self.assertNotIn('Friend 0', page.text)
        partial = self.client.get(path)
        self.assertEqual(200, partial.status_code)
        self.assertEqual(20, partial.text.count('data-bulk-item="tasks"'))

    def test_same_name_avatar_uses_account_and_stable_identity(self):
        with session_scope(self.engine) as db:
            save_contacts(db, self.account, [{'name':'同名','sec_uid':'a','avatar_url':'https://p3.douyinpic.com/a.jpg'}, {'name':'同名','sec_uid':'b','avatar_url':'https://p3.douyinpic.com/b.jpg'}])
            task = SparkTask(owner_user_id=self.owner, douyin_account_id=self.account, target_name='同名', send_time='12:00', message_template='test')
            db.add(task); db.flush()
            self.assertNotIn(task.id, task_avatar_map(db, [task]), 'ambiguous legacy avatar must not guess')
            db.add(SparkTaskTargetIdentity(task_id=task.id, sec_uid='b')); db.flush()
            self.assertEqual('https://p3.douyinpic.com/b.jpg', task_avatar_map(db, [task])[task.id])


class DirectoryBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_accumulates_twenty_across_virtual_scroll(self):
        page = AsyncMock()
        chunks = iter([[{'name':str(i)} for i in range(14)], [{'name':str(i)} for i in range(10,24)]])
        page.evaluate.side_effect = lambda script: next(chunks) if script == ROWS_SCRIPT else True
        with patch('spark_console.directory_browser.asyncio.sleep', new=AsyncMock()):
            result = await collect_directory(page, 20)
        self.assertEqual([str(i) for i in range(20)], [row['name'] for row in result])

    async def test_profile_endpoint_failure_is_optional(self):
        context = AsyncMock()
        context.request.get.side_effect = TimeoutError()
        self.assertEqual({}, await read_self_profile(context))
