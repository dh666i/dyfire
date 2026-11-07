import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from spark_console.config import Settings
from spark_console.db import create_schema, session_scope
from spark_console.models import (DouyinAccount, PaymentRecord, RedemptionCode, SparkTask, TaskProbe, User)
from spark_console.security import PasswordService
from spark_console.services.subscriptions import _digest
from spark_console.web.app import create_app


class CompleteOptimizationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.settings=Settings(data_dir=root,database_url=f"sqlite:///{root/'test.db'}",
            cookie_key_file=root/'cookie.key',session_key_file=root/'session.key')
        self.settings.cookie_key_file.write_bytes(b'c'*32);self.settings.session_key_file.write_bytes(b's'*32)
        self.engine=create_engine(self.settings.database_url,connect_args={'check_same_thread':False});create_schema(self.engine)
        digest=PasswordService().hash('Permanent-123!')
        with session_scope(self.engine) as db:
            self.admin=User(username='admin',password_hash=digest,role='admin',must_change_password=False)
            self.user=User(username='customer',password_hash=digest,role='user',must_change_password=False)
            self.other=User(username='other',password_hash=digest,role='user',must_change_password=False)
            db.add_all([self.admin,self.user,self.other]);db.flush()
            self.admin_id,self.user_id,self.other_id=self.admin.id,self.user.id,self.other.id
            account=DouyinAccount(owner_user_id=self.user.id,display_name='测试账号',encrypted_cookies=b'cipher',cookie_nonce=b'nonce')
            db.add(account);db.flush();self.account_id=account.id
            task=SparkTask(owner_user_id=self.user.id,douyin_account_id=account.id,target_name='好友',send_time='09:30',message_template='续火花',message_kind='douyin_sticker')
            db.add(task);db.flush();self.task_id=task.id
            code=RedemptionCode(code_hash=_digest('PREVIEW-10'),duration_days=30,max_accounts=1,max_tasks=10,created_by_user_id=self.admin.id)
            db.add(code)
        app=create_app(self.settings,self.engine)
        self.admin_client=TestClient(app,base_url='https://testserver');self.user_client=TestClient(app,base_url='https://testserver');self.other_client=TestClient(app,base_url='https://testserver')
        self._login(self.admin_client,'admin');self._login(self.user_client,'customer');self._login(self.other_client,'other')
        self.admin_csrf=self.admin_client.get('/api/app/bootstrap').json()['csrf']
        self.user_csrf=self.user_client.get('/api/app/bootstrap').json()['csrf']

    def tearDown(self):
        self.admin_client.close();self.user_client.close();self.other_client.close();self.engine.dispose();self.temp.cleanup()

    @staticmethod
    def _login(client,username):
        response=client.post('/login',data={'username':username,'password':'Permanent-123!'},follow_redirects=False)
        if response.status_code!=303:raise AssertionError(response.text)

    def test_announcement_scope_expiry_read_and_admin_only_management(self):
        response=self.admin_client.post('/api/admin/announcements',json={'csrf_token':self.admin_csrf,
            'title':'计划维护','body':'周日 02:00 短暂维护','user_id':self.user_id,'days':3})
        self.assertEqual(200,response.status_code,response.text)
        item_id=response.json()['id']
        self.assertEqual(1,len(self.user_client.get('/api/announcements').json()['items']))
        self.assertEqual([],self.other_client.get('/api/announcements').json()['items'])
        self.assertEqual(404,self.user_client.get('/api/admin/announcements').status_code)
        self.assertEqual(200,self.user_client.post(f'/api/announcements/{item_id}/read',json={'csrf_token':self.user_csrf}).status_code)
        self.assertTrue(self.user_client.get('/api/announcements').json()['items'][0]['read'])

    def test_payment_request_is_idempotent_and_rejects_payload_reuse(self):
        request_id='03a16a45-883d-4c47-bf3c-f7a52ba569af'
        payload={'csrf_token':self.admin_csrf,'request_id':request_id,'user_id':self.user_id,'source':'paid','amount':'19.90','reference':'offline-1'}
        first=self.admin_client.post('/api/admin/payments',json=payload)
        again=self.admin_client.post('/api/admin/payments',json=payload)
        self.assertEqual(200,first.status_code,first.text);self.assertEqual(first.json()['id'],again.json()['id'])
        changed=self.admin_client.post('/api/admin/payments',json={**payload,'amount':'29.90'})
        self.assertEqual(400,changed.status_code)
        deleted=self.admin_client.post(f'/admin/users/{self.user_id}/delete',data={'csrf_token':self.admin_csrf,'confirmation':'customer'},follow_redirects=False)
        self.assertEqual(400,deleted.status_code)
        with session_scope(self.engine) as db:
            self.assertEqual(1,len(list(db.scalars(select(PaymentRecord)))))
            self.assertIsNotNone(db.get(User,self.user_id))

    def test_business_report_does_not_invent_cost_values(self):
        value=self.admin_client.get('/api/admin/costs').json()
        self.assertFalse(value['cost_inputs_complete'])
        self.assertIsNone(value['estimated_monthly_cost_yuan'])
        self.assertTrue(value['income_from_recorded_payments_only'])

    def test_self_check_is_owner_scoped_and_queues_without_sending(self):
        payload={'csrf_token':self.user_csrf}
        response=self.user_client.post(f'/api/tasks/{self.task_id}/self-check',json=payload)
        self.assertEqual(200,response.status_code,response.text)
        self.assertEqual('queued',response.json()['status'])
        self.assertEqual(404,self.other_client.post(f'/api/tasks/{self.task_id}/self-check',json={'csrf_token':self.other_client.get('/api/app/bootstrap').json()['csrf']}).status_code)
        with session_scope(self.engine) as db:
            self.assertEqual(1,len(list(db.scalars(select(TaskProbe)))))

    def test_subscription_preview_is_read_only_and_describes_limits(self):
        response=self.user_client.post('/subscription/preview',data={'csrf_token':self.user_csrf,'code':'PREVIEW-10'})
        self.assertEqual(200,response.status_code,response.text)
        self.assertEqual(10,response.json()['preview']['max_tasks'])
        self.assertFalse(response.json()['preview']['code_consumed'])
        with session_scope(self.engine) as db:
            row=db.scalar(select(RedemptionCode).where(RedemptionCode.code_hash==_digest('PREVIEW-10')))
            self.assertIsNone(row.redeemed_at)


if __name__=='__main__':unittest.main()
