import unittest
from datetime import timedelta
from sqlalchemy import select,func
from spark_console.db import session_scope
from spark_console.models import SparkTask,TaskRun,User,DouyinAccount,AuditEvent,utc_now
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
        with session_scope(self.f.engine) as db:self.assertEqual(0,db.scalar(select(func.count()).select_from(SparkTask)))
        payload['account_id']='not-owned';self.assertEqual(404,self.client.post('/tasks/preview',json=payload).status_code)
        payload['account_id']=self.f.account;payload['targets'][0]['sec_uid']='unknown';self.assertEqual(400,self.client.post('/tasks/preview',json=payload).status_code)
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
    def test_admin_diagnostics_denied_for_user(self):
        self.assertIn(self.client.get('/admin/diagnostics').status_code,(403,404))
