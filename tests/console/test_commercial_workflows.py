import unittest
from datetime import timedelta
from sqlalchemy import select,func
from spark_console.db import session_scope
from spark_console.models import SparkTask,TaskRun,User,DouyinAccount,AuditEvent,WebSession,utc_now
from spark_console.services.tasks import TaskService
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.web.inbox import entries
from tests.console import test_directory

class CommercialWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.f=test_directory.DirectoryTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.client=self.f.client
    def test_preview_does_not_create_and_rejects_wrong_owner(self):
        payload={'csrf_token':self.f.csrf,'account_id':self.f.account,'time':'12:00','targets':[{'name':'好友'}]}
        r=self.client.post('/tasks/preview',json=payload);self.assertEqual(200,r.status_code,r.text)
        self.assertEqual(['12:00'], r.json()['times'])
        with session_scope(self.f.engine) as db:self.assertEqual(0,db.scalar(select(func.count()).select_from(SparkTask)))
        payload['account_id']='not-owned';self.assertEqual(404,self.client.post('/tasks/preview',json=payload).status_code)
        payload['account_id']=self.f.account;payload['targets'][0]['sec_uid']='unknown';self.assertEqual(400,self.client.post('/tasks/preview',json=payload).status_code)

        malformed = self.client.post(
            '/tasks/preview',
            content=b'not-json',
            headers={'Content-Type': 'application/json'},
        )
        self.assertEqual(400, malformed.status_code)
        self.assertEqual('请求格式不正确', malformed.json()['detail'])

        non_object = self.client.post('/tasks/preview', json=['not', 'an', 'object'])
        self.assertEqual(400, non_object.status_code)
        self.assertEqual('请求格式不正确', non_object.json()['detail'])

        malformed_batch = self.client.post(
            '/tasks/batch',
            content=b'not-json',
            headers={'Content-Type': 'application/json'},
        )
        self.assertEqual(400, malformed_batch.status_code)
        self.assertEqual('请求格式不正确', malformed_batch.json()['detail'])
    def test_duplicate_preview_and_idempotent_pause(self):
        with session_scope(self.f.engine) as db:
            task=TaskService(db,AccountService(db,self.f.cipher,AuditService(db)),AuditService(db)).create(self.f.owner,self.f.account,'好友','12:00','续火');db.flush();tid=task.id
        p={'csrf_token':self.f.csrf,'account_id':self.f.account,'time':'12:00','targets':[{'name':'好友'}]}
        self.assertEqual(['好友'],self.client.post('/tasks/preview',json=p).json()['conflicts'])
        for _ in range(2):
            r=self.client.post('/tasks/batch',json={'csrf_token':self.f.csrf,'ids':[tid],'action':'pause'});self.assertEqual(False,r.json()['results'][0]['enabled'])
    def test_inbox_read_does_not_change_execution_and_is_owned(self):
        with session_scope(self.f.engine) as db:
            task=TaskService(db,AccountService(db,self.f.cipher,AuditService(db)),AuditService(db)).create(self.f.owner,self.f.account,'好友','12:00','续火');db.flush();tid=task.id
            for n in range(2):db.add(TaskRun(task_id=tid,status='submitted_unconfirmed',scheduled_for=utc_now()-timedelta(hours=n)))
        with session_scope(self.f.engine) as db:
            notifications=[i for i in entries(db,self.f.owner) if '发送待确认' in i['title']];self.assertEqual(1,len(notifications));key=notifications[0]['id']
        self.assertEqual(404,self.client.post('/attention/read',data={'csrf_token':self.f.csrf,'item_id':'not-owned'}).status_code)
        self.client.post('/attention/read',data={'csrf_token':self.f.csrf,'item_id':key})
        with session_scope(self.f.engine) as db:
            self.assertTrue(next(i for i in entries(db,self.f.owner) if i['id']==key)['read'])
            self.assertEqual(2,db.scalar(select(func.count()).select_from(TaskRun).where(TaskRun.status=='submitted_unconfirmed')))

    def test_spa_attention_api_keeps_read_state_owned_and_csrf_protected(self):
        with session_scope(self.f.engine) as db:
            task=TaskService(db,AccountService(db,self.f.cipher,AuditService(db)),AuditService(db)).create(self.f.owner,self.f.account,'SPA好友','12:00','续火');db.flush()
            db.add(TaskRun(task_id=task.id,status='failed',scheduled_for=utc_now()-timedelta(minutes=1)))
        value=self.client.get('/api/attention')
        self.assertEqual(200,value.status_code)
        item=next(i for i in value.json()['items'] if '执行异常' in i['title'])
        self.assertFalse(item['read'])
        self.assertEqual(403,self.client.post('/api/attention/read',json={'csrf_token':'wrong','item_id':item['id']}).status_code)
        self.assertEqual(404,self.client.post('/api/attention/read',json={'csrf_token':self.f.csrf,'item_id':'not-owned'}).status_code)
        marked=self.client.post('/api/attention/read',json={'csrf_token':self.f.csrf,'item_id':item['id']})
        self.assertEqual(200,marked.status_code)
        self.assertTrue(next(i for i in marked.json()['items'] if i['id']==item['id'])['read'])

    def test_account_center_api_lists_and_revokes_only_other_sessions(self):
        with session_scope(self.f.engine) as db:
            current=db.scalar(select(WebSession).where(WebSession.user_id==self.f.owner))
            current_id=current.id
            db.add(WebSession(user_id=self.f.owner,token_hash='f'*64,csrf_token='other',expires_at=utc_now()+timedelta(hours=1)))
        response=self.client.get('/api/account-center')
        self.assertEqual(200,response.status_code)
        self.assertEqual(2,len(response.json()['sessions']))
        self.assertTrue(next(item for item in response.json()['sessions'] if item['id']==current_id)['current'])
        self.assertEqual(403,self.client.post('/api/account-center/revoke-sessions',json={'csrf_token':'wrong'}).status_code)
        self.assertEqual(200,self.client.post('/api/account-center/revoke-sessions',json={'csrf_token':self.f.csrf}).status_code)
        after=self.client.get('/api/account-center').json()['sessions']
        self.assertEqual([current_id],[item['id'] for item in after])

    def test_subscription_api_exposes_current_entitlement(self):
        value=self.client.get('/api/subscription')
        self.assertEqual(200,value.status_code)
        self.assertTrue(value.json()['membership']['active'])
        self.assertEqual(20,value.json()['membership']['max_tasks'])

    def test_subscription_redeem_rejects_malformed_json_with_client_error(self):
        response = self.client.post(
            '/api/subscription/redeem',
            content=b'not-json',
            headers={'Content-Type': 'application/json'},
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual('请求格式无效', response.json()['detail'])

        response = self.client.post(
            '/api/subscription/redeem',
            json=['not', 'an', 'object'],
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual('请求格式无效', response.json()['detail'])
    def test_admin_diagnostics_denied_for_user(self):
        self.assertIn(self.client.get('/admin/diagnostics').status_code,(403,404))
