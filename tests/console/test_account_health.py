import asyncio
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from spark_console.db import session_scope
from spark_console.models import AccountHealth, AccountOperation, DouyinAccount, SparkTask, TaskRun, User, utc_now
from spark_console.services.account_health import acquire, release, record, snapshot, recovered
from spark_console.health_worker import process_health_check
from spark_console.scheduler import claim_next_due_task
from spark_console.services.scan_sessions import ScanSessionService
from core.session_health import SessionEvidence, inspect_session, read_login_api, wait_for_session
from tests.console import test_directory


class AccountHealthTests(unittest.TestCase):
    def setUp(self):
        fixture = test_directory.DirectoryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.engine, self.client, self.cipher = fixture.engine, fixture.client, fixture.cipher
        self.owner, self.account, self.csrf = fixture.owner, fixture.account, fixture.csrf
        self.now = utc_now()
        with session_scope(self.engine) as db:
            account = db.get(DouyinAccount, self.account)
            account.validation_state = 'valid'
            self.expected = snapshot(account)
            a = SparkTask(owner_user_id=self.owner, douyin_account_id=self.account, target_name='A', send_time='10:00', message_template='test', enabled=True, next_run_at=self.now-timedelta(minutes=1))
            b = SparkTask(owner_user_id=self.owner, douyin_account_id=self.account, target_name='B', send_time='10:00', message_template='test', enabled=False, next_run_at=self.now-timedelta(minutes=1))
            db.add_all([a,b]); db.flush(); self.tasks = a.id,b.id

    def anomaly(self, state='temporary'):
        with session_scope(self.engine) as db:
            record(db, self.account, self.expected, state, '测试登录信号', now=self.now)

    def test_repeated_negative_required_and_tasks_preserved(self):
        self.anomaly('logged_out')
        with session_scope(self.engine) as db:
            self.assertEqual('temporary', db.get(DouyinAccount,self.account).validation_state)
            self.assertEqual(self.now+timedelta(seconds=30), db.get(AccountHealth,self.account).next_check_at.replace(tzinfo=self.now.tzinfo))
            self.assertIsNone(claim_next_due_task(db, self.now, 'test'))
            record(db,self.account,self.expected,'logged_out','登录界面再次确认',now=self.now+timedelta(seconds=31),recheck=True)
            self.assertEqual('invalid',db.get(DouyinAccount,self.account).validation_state)
            self.assertIsNone(db.get(AccountHealth,self.account).next_check_at)
            self.assertTrue(db.get(SparkTask,self.tasks[0]).enabled)
            self.assertFalse(db.get(SparkTask,self.tasks[1]).enabled)

    def test_two_uncertain_rechecks_stop_without_claiming_logout(self):
        self.anomaly()
        with session_scope(self.engine) as db:
            record(db,self.account,self.expected,'temporary','网络超时',now=self.now+timedelta(seconds=31),recheck=True)
            self.assertEqual(self.now+timedelta(seconds=151),db.get(AccountHealth,self.account).next_check_at.replace(tzinfo=self.now.tzinfo))
            record(db,self.account,self.expected,'temporary','网络超时',now=self.now+timedelta(seconds=152),recheck=True)
            self.assertIsNone(db.get(AccountHealth,self.account).next_check_at)
            self.assertEqual('temporary',db.get(DouyinAccount,self.account).validation_state)

    def test_verification_stops_checks_and_cannot_be_retried_by_button(self):
        self.anomaly('verification')
        checker=AsyncMock()
        self.assertFalse(asyncio.run(process_health_check(self.engine,self.cipher,checker)))
        checker.assert_not_called()
        response=self.client.post(f'/accounts/{self.account}/health-check',data={'csrf_token':self.csrf})
        self.assertEqual(409,response.status_code)
        bootstrap = self.client.get('/api/app/bootstrap')
        self.assertEqual(200, bootstrap.status_code)
        account_row = next(item for item in bootstrap.json()['accounts'] if item['id'] == self.account)
        self.assertEqual('verification', account_row['validation_state'])
        self.assertIn('验证', account_row['health']['label'])

    def test_recovery_preserves_recent_and_does_not_enable_manual_pauses(self):
        self.anomaly()
        with session_scope(self.engine) as db:
            db.add(TaskRun(task_id=self.tasks[0],scheduled_for=self.now-timedelta(days=1),status='submitted_unconfirmed',stage='submitted',submitted_at=self.now-timedelta(days=1)))
            record(db,self.account,self.expected,'valid','接口有效',now=self.now,recheck=True)
            db.flush()
            self.assertGreater(db.get(SparkTask,self.tasks[0]).next_run_at.replace(tzinfo=self.now.tzinfo),self.now)
            self.assertFalse(db.get(SparkTask,self.tasks[1]).enabled)
            self.assertEqual('submitted_unconfirmed',db.scalar(select(TaskRun)).status)
            resumed = claim_next_due_task(db,self.now,'test')
            self.assertIsNotNone(resumed)
            self.assertEqual(self.tasks[0], resumed.task_id)

    def test_operation_lease_exclusive_and_login_waits_for_send(self):
        with session_scope(self.engine) as db:
            self.assertTrue(acquire(db,self.account,'send-a','send',self.now,60))
            self.assertFalse(acquire(db,self.account,'directory-b','directory',self.now,60))
            self.assertIsNone(claim_next_due_task(db,self.now,'test'))
            scan=ScanSessionService(db).start(self.owner,self.account)
            self.assertIsNone(ScanSessionService(db).claim_next())
            release(db,self.account,'wrong-token')
            self.assertIsNotNone(db.get(AccountOperation,self.account))
            release(db,self.account,'send-a')
            self.assertEqual(scan.id,ScanSessionService(db).claim_next().id)
            self.assertEqual('login',db.get(AccountOperation,self.account).kind)
            self.assertIsNone(claim_next_due_task(db,self.now,'test'))

    def test_expired_operation_can_be_recovered(self):
        with session_scope(self.engine) as db:
            self.assertTrue(acquire(db,self.account,'old','send',self.now-timedelta(seconds=70),60))
            self.assertTrue(acquire(db,self.account,'new','health',self.now,60))
            release(db,self.account,'old')
            self.assertEqual('new',db.get(AccountOperation,self.account).token)

    def test_readonly_checker_refresh_cas_and_no_send(self):
        self.anomaly()
        with session_scope(self.engine) as db:
            db.get(AccountHealth,self.account).next_check_at=self.now-timedelta(seconds=1)
        async def checker(raw,version):
            with session_scope(self.engine) as db:
                account=db.get(DouyinAccount,self.account)
                account.encrypted_cookies=b'new-session'
                account.validation_state='valid'
            return SessionEvidence('logged_out','旧会话失效')
        asyncio.run(process_health_check(self.engine,self.cipher,checker))
        with session_scope(self.engine) as db:
            self.assertEqual('valid',db.get(DouyinAccount,self.account).validation_state)
            self.assertIsNone(db.get(AccountOperation,self.account))
            self.assertEqual(0,len(db.scalars(select(TaskRun)).all()))

    def test_manual_check_owner_csrf_and_cooldown(self):
        path=f'/accounts/{self.account}/health-check'
        self.assertEqual(403,self.client.post(path,data={'csrf_token':'bad'}).status_code)
        self.assertEqual(404,self.client.post('/accounts/not-owned/health-check',data={'csrf_token':self.csrf}).status_code)
        self.assertEqual(202,self.client.post(path,data={'csrf_token':self.csrf}).status_code)
        self.assertEqual(429,self.client.post(path,data={'csrf_token':self.csrf}).status_code)
        response=self.client.get('/accounts/health-state')
        self.assertEqual('no-store',response.headers['cache-control'])
        self.assertEqual('temporary',response.json()[self.account]['state'])
        self.assertTrue(response.json()[self.account]['next_check_at'].endswith('+00:00'))

    def test_worker_recheck_budget_is_durable_and_bounded(self):
        self.anomaly()
        checker = AsyncMock(return_value=SessionEvidence('temporary','页面超时'))
        for attempt in range(2):
            with session_scope(self.engine) as db:
                db.get(AccountHealth,self.account).next_check_at=utc_now()-timedelta(seconds=1)
            self.assertTrue(asyncio.run(process_health_check(self.engine,self.cipher,checker)))
            with session_scope(self.engine) as db:
                self.assertEqual(attempt+1,db.get(AccountHealth,self.account).attempts)
        self.assertFalse(asyncio.run(process_health_check(self.engine,self.cipher,checker)))
        self.assertEqual(2,checker.await_count)

    def test_explicit_relogin_preserves_task_choices_and_skips_backlog(self):
        self.anomaly()
        with session_scope(self.engine) as db:
            account=db.get(DouyinAccount,self.account)
            account.validation_state='valid'
            recovered(db,account)
            db.flush()
            self.assertIsNone(db.get(AccountHealth,self.account).next_check_at)
            self.assertTrue(db.get(SparkTask,self.tasks[0]).enabled)
            self.assertFalse(db.get(SparkTask,self.tasks[1]).enabled)
            self.assertGreater(db.get(SparkTask,self.tasks[0]).next_run_at.replace(tzinfo=self.now.tzinfo),self.now)


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_diagnostics_distinguish_failures_without_leaking_body(self):
        for status, body, expected, state in (
            (401, ValueError('secret-cookie'), 'HTTP 401', 'logged_out'),
            (403, {}, '请求受限', 'unknown'),
            (429, {}, 'HTTP 429', 'unknown'),
            (502, {}, '请求未成功', 'unknown'),
            (200, ValueError('secret-cookie'), 'JSON', 'unknown'),
            (200, [], '结构异常', 'unknown'),
            (200, {'data': {'error_code': 123, 'description': 'secret-cookie'}}, '业务码 123', 'unknown'),
        ):
            with self.subTest(status=status, expected=expected):
                context = AsyncMock()
                response = context.request.get.return_value
                response.status = status
                if isinstance(body, Exception):
                    response.json.side_effect = body
                else:
                    response.json.return_value = body
                actual, detail = await read_login_api(context)
                self.assertEqual(state, actual)
                self.assertIn(expected, detail)
                self.assertNotIn('secret-cookie', detail)
        for error, expected in ((TimeoutError('secret'), '请求超时'), (ConnectionError('secret'), '网络或请求异常')):
            context = AsyncMock()
            context.request.get.side_effect = error
            state, detail = await read_login_api(context)
            self.assertEqual('unknown', state)
            self.assertIn(expected, detail)
            self.assertNotIn('secret', detail)

    async def test_waits_for_delayed_chat_without_repeated_api_requests(self):
        from tests.console.test_credentials import _FakeLoginPage, _FakeTextMatches
        page = _FakeLoginPage()
        ready = False
        page.locator = lambda selector: _FakeTextMatches(ready and selector == '.conversationConversationItemwrapper')
        context = AsyncMock()
        context.request.get.return_value.status = 200
        context.request.get.return_value.json.return_value = {}
        async def hydrate():
            nonlocal ready
            await asyncio.sleep(.02)
            ready = True
        task = asyncio.create_task(hydrate())
        try:
            evidence = await wait_for_session(page, context, timeout=.2, interval=.005)
        finally:
            await task
        self.assertEqual('valid', evidence.state)
        self.assertEqual(1, context.request.get.await_count)

    async def test_transient_login_screen_can_recover_during_grace_period(self):
        from tests.console.test_credentials import _FakeLoginPage, _FakeTextMatches
        page = _FakeLoginPage(login_text='扫码登录')
        ready = False
        page.locator = lambda selector: _FakeTextMatches(ready and selector == '.conversationConversationItemwrapper')
        async def hydrate():
            nonlocal ready
            await asyncio.sleep(.02)
            page.login_text = None
            ready = True
        task = asyncio.create_task(hydrate())
        with patch('core.session_health.read_login_api', AsyncMock(return_value=('unknown', 'HTTP 403'))):
            evidence = await wait_for_session(page, AsyncMock(), timeout=.2, interval=.005)
        await task
        self.assertEqual('valid', evidence.state)

    async def test_verification_stops_wait_without_api_and_unknown_is_bounded(self):
        from tests.console.test_credentials import _FakeLoginPage
        context = AsyncMock()
        evidence = await wait_for_session(_FakeLoginPage(login_text='验证登录密码'), context, timeout=.2)
        self.assertEqual('verification', evidence.state)
        context.request.get.assert_not_called()
        with patch('core.session_health.read_login_api', AsyncMock(return_value=('unknown', 'HTTP 403，请求受限'))) as api:
            evidence = await wait_for_session(_FakeLoginPage(), context, timeout=.03, interval=.005)
        self.assertEqual('temporary', evidence.state)
        self.assertIn('HTTP 403', evidence.reason)
        self.assertIn('已等待页面就绪', evidence.reason)
        self.assertEqual(2, api.await_count)

    async def test_final_api_probe_can_recover_and_cancellation_propagates(self):
        from tests.console.test_credentials import _FakeLoginPage
        with patch('core.session_health.read_login_api', AsyncMock(side_effect=[('unknown', 'HTTP 403'), ('valid', 'HTTP 200')])):
            evidence = await wait_for_session(_FakeLoginPage(), AsyncMock(), timeout=.02, interval=.005)
        self.assertEqual('valid', evidence.state)
        with patch('core.session_health.read_login_api', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await wait_for_session(_FakeLoginPage(), AsyncMock(), timeout=.1)

    async def check(self, *, url='https://www.douyin.com/chat', text='', api='unknown', chat=False):
        from tests.console.test_credentials import _FakeLoginPage, _FakeTextMatches
        page=_FakeLoginPage(url=url,login_text=text)
        page.locator=lambda selector:_FakeTextMatches(chat and selector=='.conversationConversationItemwrapper')
        context=AsyncMock()
        response=AsyncMock(); response.status=200
        response.json.return_value={'message':'success','data':{'error_code':0,'user_id':'self'}} if api=='valid' else {'message':'not logged in','data':{}} if api=='logged_out' else {}
        context.request.get.return_value=response
        return await inspect_session(page,context)

    async def test_redirect_is_not_logout(self):
        self.assertEqual('temporary',(await self.check(url='https://www.douyin.com/')).state)

    async def test_conflicting_signals_need_recheck(self):
        self.assertEqual('temporary',(await self.check(text='扫码登录',api='valid')).state)
        self.assertEqual('temporary',(await self.check(chat=True,api='logged_out')).state)

    async def test_verification_wins_over_authenticated_api(self):
        self.assertEqual('verification',(await self.check(text='验证登录密码',api='valid')).state)

    async def test_positive_and_negative_evidence(self):
        self.assertEqual('valid',(await self.check(chat=True)).state)
        self.assertEqual('valid',(await self.check(api='valid')).state)
        self.assertEqual('logged_out',(await self.check(text='扫码登录')).state)

    async def test_chat_shell_with_malformed_business_probe_is_not_account_failure(self):
        evidence = await self.check(
            chat=False,
            api='unknown',
        )
        self.assertEqual('temporary', evidence.state)

        from tests.console.test_credentials import _FakeLoginPage, _FakeTextMatches
        page = _FakeLoginPage()
        # The shell is open but the virtualized conversation list is still
        # empty. This is the intermediate state that used to poison account
        # health and block the task queue.
        page.locator = lambda selector: _FakeTextMatches(False)
        context = AsyncMock()
        response = AsyncMock()
        response.status = 200
        response.json.return_value = {
            'message': 'success',
            'data': {'error_code': '0'},
        }
        context.request.get.return_value = response
        evidence = await inspect_session(page, context)
        self.assertEqual('valid', evidence.state)
        self.assertIn('等待会话列表水合', evidence.reason)
