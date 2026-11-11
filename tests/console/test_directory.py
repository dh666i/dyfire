import asyncio
import json
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from sqlalchemy import delete, select
from spark_console.db import session_scope
from spark_console.crypto import CookieCipher
from spark_console.models import (User, DouyinAccount, DouyinAccountProfile, ContactDirectoryJob,
    DouyinContactIdentity, DouyinConversation, SparkTask, SparkTaskTargetIdentity, utc_now)
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.services.directory import save_profile, save_contacts, process_directory_job, task_avatar_map
from spark_console.web.task_groups import list_task_groups, list_group_tasks
from spark_console.directory_browser import collect_directory, ROWS_SCRIPT, STREAK_ROWS_SCRIPT, read_self_profile, _conversation_identity_map, _merge_streak_observations
from core.streak_observation import parse_streak_rows
from tests.console.test_web_fixture import UserFixture


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.fx = UserFixture()
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
        # Authenticated product pages are served by the React shell; the
        # mutation token is exposed by the JSON bootstrap endpoint.
        self.csrf = self.client.get('/api/studio').json()['csrf']

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
        self.assertEqual(422, self.post(query='new').status_code)
        self.assertEqual(404, self.client.get(f'/accounts/{other}/directory-jobs/{job["id"]}').status_code)
        self.assertEqual(job['id'], self.client.get(f'/accounts/{self.account}/conversations').json()['active_sync']['id'])

    def test_validation_expired_jobs_and_throttle(self):
        self.assertEqual(422, self.post(query='郭琪').status_code)
        with session_scope(self.engine) as db:
            self.assertIsNone(db.scalar(select(ContactDirectoryJob)))
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
        bootstrap = self.client.get('/api/app/bootstrap')
        self.assertEqual(200, bootstrap.status_code)
        account_row = next(item for item in bootstrap.json()['accounts'] if item['id'] == self.account)
        self.assertEqual('工作号', account_row['name'])
        self.assertEqual('新昵称', account_row['profile']['nickname'])

    def test_sync_persists_aliases_without_changing_credentials(self):
        job = self.post().json()
        observed_at = utc_now().isoformat()
        with session_scope(self.engine) as db:
            task = SparkTask(owner_user_id=self.owner, douyin_account_id=self.account,
                             target_name='同学', send_time='12:00', message_template='test')
            db.add(task)
            db.flush()
            db.add(SparkTaskTargetIdentity(task_id=task.id, sec_uid='uid-a'))
        with session_scope(self.engine) as db:
            original = db.get(DouyinAccount, self.account).encrypted_cookies
        reader = AsyncMock(return_value={'items':[{'name':'同学', 'sec_uid':'uid-a', 'nickname':'小张', 'remark_name':'同学', 'unique_id':'zhang123', 'avatar_url':'https://p3.douyinpic.com/a.jpg'}], 'profile':{'nickname':'本人'}, 'streaks':[{'sec_uid':'uid-a', 'days':374, 'state':'recovering', 'recover_current':1, 'recover_total':3, 'source':'douyin_im', 'platform_text':'重燃中 1/3', 'observed_at':observed_at}], 'has_more':True})
        asyncio.run(process_directory_job(self.engine, self.cipher, reader))
        response = self.client.get(f'/accounts/{self.account}/conversations').json()
        self.assertEqual('succeeded', response['sync']['status'])
        self.assertEqual('小张', response['items'][0]['nickname'])
        self.assertEqual('zhang123', response['items'][0]['unique_id'])
        self.assertEqual(374, response['items'][0]['spark']['days'])
        self.assertEqual('recovering', response['items'][0]['spark']['state'])
        self.assertEqual('重燃中 1/3', response['items'][0]['spark']['platform_text'])
        streaks = self.client.get('/api/streaks', params={'account_id': self.account})
        self.assertEqual(200, streaks.status_code, streaks.text)
        self.assertEqual(374, streaks.json()['items'][0]['days'])
        history = self.client.get('/api/streaks/history', params={'account_id': self.account, 'sec_uid': 'uid-a'})
        self.assertEqual(200, history.status_code, history.text)
        self.assertEqual('recovering', history.json()['current']['state'])
        bootstrap = self.client.get('/api/app/bootstrap').json()
        task_row = next(item for item in bootstrap['tasks'] if item['name'] == '同学')
        self.assertEqual(374, task_row['spark']['days'])
        self.assertEqual('recovering', task_row['spark']['state'])
        with session_scope(self.engine) as db:
            self.assertEqual(original, db.get(DouyinAccount, self.account).encrypted_cookies)
            self.assertEqual('succeeded', db.get(ContactDirectoryJob, job['id']).status)

    def test_conversations_response_is_bounded_and_signals_more(self):
        """Legacy picker responses must not grow with a large directory."""
        with session_scope(self.engine) as db:
            db.add_all(
                DouyinContactIdentity(
                    account_id=self.account,
                    sec_uid=f"sec-{index}",
                    nickname=f"好友-{index:03d}",
                )
                for index in range(125)
            )
            db.add_all(
                DouyinConversation(
                    account_id=self.account,
                    display_name=f"会话-{index:03d}",
                )
                for index in range(125)
            )
        response = self.client.get(f"/accounts/{self.account}/conversations")
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(100, body["limit"])
        self.assertEqual(100, len(body["items"]))
        self.assertTrue(body["has_more"])
        self.assertEqual(250, body["total"])

        second = self.client.get(
            f"/accounts/{self.account}/conversations?offset=100&limit=100"
        )
        self.assertEqual(200, second.status_code)
        second_body = second.json()
        self.assertEqual(100, second_body["offset"])
        self.assertEqual(100, len(second_body["items"]))
        self.assertTrue(second_body["has_more"])
        self.assertTrue(
            {item["name"] for item in body["items"]}.isdisjoint(
                {item["name"] for item in second_body["items"]}
            )
        )

        third = self.client.get(
            f"/accounts/{self.account}/conversations?offset=200&limit=100"
        )
        self.assertEqual(200, third.status_code)
        third_body = third.json()
        self.assertEqual(50, len(third_body["items"]))
        self.assertFalse(third_body["has_more"])
        self.assertEqual(250, third_body["next_offset"])

    def test_missing_sync_history_keeps_cached_directory_sync_available(self):
        """Retained contacts must remain discoverable after job cleanup."""
        for count, next_limit in ((0, 20), (8, 20), (18, 20), (20, 40),
                                  (40, 60), (41, 60), (100, 100), (125, 100)):
            with self.subTest(cached_contacts=count):
                with session_scope(self.engine) as db:
                    db.execute(delete(ContactDirectoryJob))
                    db.execute(delete(DouyinContactIdentity))
                    db.add_all(
                        DouyinContactIdentity(account_id=self.account,
                                              sec_uid=f"sec-{index}",
                                              nickname=f"好友-{index:03d}")
                        for index in range(count)
                    )
                response = self.client.get(f"/accounts/{self.account}/conversations")
                self.assertEqual("no-store", response.headers["cache-control"])
                body = response.json()
                self.assertEqual(count, body["total"])
                self.assertIsNone(body["sync"])
                self.assertIsNone(body["sync_has_more"])
                self.assertEqual(0, body["sync_limit"])
                self.assertTrue(body["can_sync_more"])
                self.assertEqual(next_limit, body["sync_next_limit"])
                # In particular, counts such as 41 may never be passed as
                # the job limit: the creation endpoint rejects them.
                created = self.post(limit=body["sync_next_limit"])
                self.assertEqual(202, created.status_code, created.text)

    def test_successful_sync_controls_next_bound_and_exhaustion(self):
        for requested_limit, has_more, next_limit in ((20, True, 40),
                                                     (40, True, 60),
                                                     (20, False, None),
                                                     (100, True, None),
                                                     (100, False, None)):
            with self.subTest(limit=requested_limit, has_more=has_more):
                with session_scope(self.engine) as db:
                    db.execute(delete(ContactDirectoryJob))
                    db.add(ContactDirectoryJob(
                        account_id=self.account, owner_user_id=self.owner,
                        limit=requested_limit, status="succeeded",
                        finished_at=utc_now(),
                        result_json=json.dumps({"items": [], "has_more": has_more}),
                    ))
                body = self.client.get(f"/accounts/{self.account}/conversations").json()
                self.assertIs(has_more, body["sync_has_more"])
                self.assertEqual(requested_limit, body["sync_limit"])
                self.assertEqual(next_limit is not None, body["can_sync_more"])
                self.assertEqual(next_limit, body["sync_next_limit"])

    def test_successful_legacy_result_without_more_flag_is_unknown(self):
        with session_scope(self.engine) as db:
            db.add(ContactDirectoryJob(
                account_id=self.account, owner_user_id=self.owner,
                limit=20, status="succeeded", finished_at=utc_now(),
                result_json=json.dumps({"items": []}),
            ))
        body = self.client.get(f"/accounts/{self.account}/conversations").json()
        self.assertIsNone(body["sync_has_more"])
        self.assertTrue(body["can_sync_more"])
        self.assertEqual(40, body["sync_next_limit"])

    def test_active_directory_sync_is_returned_for_resume(self):
        for status in ("queued", "running"):
            with self.subTest(status=status):
                with session_scope(self.engine) as db:
                    db.execute(delete(ContactDirectoryJob))
                    job = ContactDirectoryJob(
                        account_id=self.account, owner_user_id=self.owner,
                        limit=40, status=status,
                    )
                    db.add(job)
                    db.flush()
                    job_id = job.id
                body = self.client.get(f"/accounts/{self.account}/conversations").json()
                self.assertEqual(job_id, body["active_sync"]["id"])
                self.assertEqual(status, body["active_sync"]["status"])
                self.assertEqual(40, body["active_sync"]["limit"])
                self.assertFalse(body["can_sync_more"])
                self.assertIsNone(body["sync_next_limit"])

    def test_expired_active_job_does_not_block_more_sync(self):
        with session_scope(self.engine) as db:
            db.add_all(DouyinContactIdentity(account_id=self.account,
                                           sec_uid=f"sec-{index}",
                                           nickname=f"好友-{index:03d}")
                       for index in range(41))
            job = ContactDirectoryJob(
                account_id=self.account, owner_user_id=self.owner,
                limit=40, status="running",
                created_at=utc_now() - timedelta(minutes=6),
            )
            db.add(job)
            db.flush()
            job_id = job.id
        body = self.client.get(f"/accounts/{self.account}/conversations").json()
        self.assertEqual(job_id, body["active_sync"]["id"])
        self.assertEqual("failed", body["active_sync"]["status"])
        self.assertTrue(body["can_sync_more"])
        self.assertEqual(60, body["sync_next_limit"])
        with session_scope(self.engine) as db:
            self.assertEqual("running", db.get(ContactDirectoryJob, job_id).status)

    def test_inconclusive_search_does_not_block_healthy_account(self):
        from core.session_health import SessionEvidence, SessionUnavailable
        with session_scope(self.engine) as db:
            db.get(DouyinAccount, self.account).validation_state='valid'
        self.post()
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
        page = self.client.get('/api/admin/bootstrap?q=真实昵称')
        self.assertEqual(200, page.status_code)
        self.assertEqual([], page.json()['groups'])
        group_page = self.client.get('/api/admin/task-groups?q=真实昵称&status=disabled')
        self.assertEqual(200, group_page.status_code)
        groups = group_page.json()['items']
        self.assertEqual(1, len(groups))
        self.assertEqual('真实昵称', groups[0]['nickname'])
        self.assertNotIn('Friend 0', page.text)
        partial = self.client.get(path)
        self.assertEqual(200, partial.status_code)
        self.assertIn('<div id="app"></div>', partial.text)
        self.assertNotIn('data-bulk-item="tasks"', partial.text)

    def test_same_name_avatar_uses_account_and_stable_identity(self):
        with session_scope(self.engine) as db:
            save_contacts(db, self.account, [{'name':'同名','sec_uid':'a','avatar_url':'https://p3.douyinpic.com/a.jpg'}, {'name':'同名','sec_uid':'b','avatar_url':'https://p3.douyinpic.com/b.jpg'}])
            task = SparkTask(owner_user_id=self.owner, douyin_account_id=self.account, target_name='同名', send_time='12:00', message_template='test')
            db.add(task); db.flush()
            self.assertNotIn(task.id, task_avatar_map(db, [task]), 'ambiguous legacy avatar must not guess')
            db.add(SparkTaskTargetIdentity(task_id=task.id, sec_uid='b')); db.flush()
            self.assertEqual('https://p3.douyinpic.com/b.jpg', task_avatar_map(db, [task])[task.id])


class DirectoryBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_delayed_remark_search_result(self):
        from unittest.mock import MagicMock
        page=AsyncMock();field=AsyncMock()
        page.locator=MagicMock(return_value=MagicMock(first=field))
        field.count.return_value=1
        page.evaluate.side_effect=[[],[],[{'name':'原昵称','remark_name':'郭琪','sec_uid':'friend'}]]
        with patch('spark_console.directory_browser.asyncio.sleep',new=AsyncMock()):
            rows=await collect_directory(page,20,'郭琪')
        self.assertEqual('friend',rows[0]['sec_uid'])
        self.assertEqual(3,page.evaluate.await_count)

    async def test_unreadable_search_is_not_successful_empty_result(self):
        from unittest.mock import MagicMock
        page=AsyncMock();field=AsyncMock()
        page.locator=MagicMock(return_value=MagicMock(first=field));field.count.return_value=1
        page.evaluate.return_value=[]
        with patch('spark_console.directory_browser.asyncio.sleep',new=AsyncMock()):
            with self.assertRaisesRegex(ValueError,'无法确认好友不存在'):
                await collect_directory(page,20,'郭琪')

    async def test_accumulates_twenty_across_virtual_scroll(self):
        page = AsyncMock()
        chunks = iter([[{'name':str(i)} for i in range(14)], [{'name':str(i)} for i in range(10,24)]])
        page.evaluate.side_effect = lambda script: next(chunks) if script == ROWS_SCRIPT else [] if script == STREAK_ROWS_SCRIPT else True
        with patch('spark_console.directory_browser.asyncio.sleep', new=AsyncMock()):
            result = await collect_directory(page, 20)
        self.assertEqual([str(i) for i in range(20)], [row['name'] for row in result])

    async def test_collects_streak_badges_from_each_virtual_window_and_parses_stable_ids(self):
        from datetime import datetime, timezone
        page = AsyncMock()
        chunks = iter([
            [{'name': '小张', 'sec_uid': 'sec-a'}],
            [{'name': '小王', 'sec_uid': 'sec-b'}],
            [],
        ])
        samples = iter([
            [{'identity': {'sec_uids': ['sec-a'], 'uids': [], 'conversation_ids': []},
              'signals': [{'kind': 'badge', 'text': '120'}]}],
            [{'identity': {'sec_uids': ['sec-b'], 'uids': [], 'conversation_ids': []},
              'signals': [{'kind': 'badge', 'text': '42'}]}],
            [],
        ])
        scrolls = iter([True, False, False])
        page.evaluate.side_effect = lambda script: next(chunks) if script == ROWS_SCRIPT else next(samples) if script == STREAK_ROWS_SCRIPT else next(scrolls)
        output = []
        with patch('spark_console.directory_browser.asyncio.sleep', new=AsyncMock()):
            rows = await collect_directory(page, 20, streak_samples=output)
        self.assertEqual(['sec-a', 'sec-b'], [row['sec_uid'] for row in rows])
        parsed = parse_streak_rows(output, observed_at=datetime(2026, 11, 11, tzinfo=timezone.utc))
        self.assertEqual({'sec-a': 120, 'sec-b': 42}, {item['sec_uid']: item['days'] for item in parsed})
        self.assertEqual(3, sum(call.args[0] == STREAK_ROWS_SCRIPT for call in page.evaluate.await_args_list))

    def test_conversation_identity_mapping_requires_trusted_self_and_unambiguous_peer(self):
        samples = [
            {'identity': {'conversation_ids': ['0:1:100:200']}},
            {'identity': {'conversation_ids': ['0:1:100:201']}},
            {'identity': {'conversation_ids': ['0:1:100:202', '0:1:100:203']}},
        ]
        identities = _conversation_identity_map(samples, '100', {'200': 'sec-a', '201': 'sec-b', '202': 'sec-c', '203': 'sec-d'})
        self.assertEqual({'0:1:100:200': 'sec-a', '0:1:100:201': 'sec-b'},
                         {key: value for key, value in identities.items() if key.rsplit(':', 1)[-1] in ('200', '201')})
        ambiguous_sample = {'identity': {'conversation_ids': ['0:1:100:202', '0:1:100:203']},
                            'signals': [{'kind': 'badge', 'text': '392'}]}
        self.assertEqual([], parse_streak_rows([ambiguous_sample], conversation_to_sec_uid=identities))
        self.assertEqual({}, _conversation_identity_map(samples, None, {'200': 'sec-a'}))

    def test_conflicting_streak_sources_are_discarded(self):
        dom = [{'sec_uid': 'sec-a', 'days': 120, 'state': 'active', 'recover_current': None,
                'recover_total': None, 'platform_text': '120', 'source': 'douyin_dom'}]
        same = [{'sec_uid': 'sec-a', 'days': 120, 'state': 'active', 'recover_current': None,
                 'recover_total': None, 'platform_text': '120', 'source': 'douyin_im'}]
        more_complete = [{'sec_uid': 'sec-a', 'days': 120, 'state': 'active', 'recover_current': None,
                          'recover_total': None, 'platform_text': '120', 'source': 'douyin_im'}]
        partial_dom = [{'sec_uid': 'sec-a', 'days': 120, 'state': 'unknown', 'recover_current': None,
                        'recover_total': None, 'platform_text': '120', 'source': 'douyin_dom'}]
        conflict = [{'sec_uid': 'sec-a', 'days': 121, 'state': 'active', 'recover_current': None,
                     'recover_total': None, 'platform_text': '121', 'source': 'douyin_im'}]
        self.assertEqual('douyin_dom', _merge_streak_observations(dom, same)[0]['source'])
        self.assertEqual('douyin_im', _merge_streak_observations(partial_dom, more_complete)[0]['source'])
        self.assertEqual([], _merge_streak_observations(dom, conflict))

    async def test_profile_endpoint_failure_is_optional(self):
        context = AsyncMock()
        context.request.get.side_effect = TimeoutError()
        self.assertEqual({}, await read_self_profile(context))
