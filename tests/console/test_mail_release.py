import unittest
import json,time
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from spark_console.db import session_scope
from spark_console.models import User, MailSettings, MailPreference, MailDelivery, DouyinAccount
from spark_console.crypto import CookieCipher
from spark_console.services import mail
from spark_console.mail_outbox import open_body
from tests.console.test_web_fixture import UserFixture
from spark_console.models import SparkTask


class MailReleaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture=UserFixture();self.fixture.setUp();self.client=self.fixture.client
        (self.fixture.settings.data_dir/'mail-worker-health.json').write_text(json.dumps({'at':time.time(),'state':'ready'}))
        with session_scope(self.fixture.engine) as db:
            user=db.scalar(select(User).where(User.username=='friend'));user.must_change_password=False;user.role='admin';self.uid=user.id
        self.fixture.login()
        with session_scope(self.fixture.engine) as db:
            from spark_console.models import WebSession
            self.csrf=db.scalar(select(WebSession).where(WebSession.user_id==self.uid)).csrf_token
        self.cipher=CookieCipher(self.fixture.settings.cookie_key_file.read_bytes())
        self.config={'csrf_token':self.csrf,'enabled':True,'host':'smtp.example.com','port':465,'mode':'ssl','username':'sender@example.com','password':'authorization-secret','sender':'sender@example.com','sender_name':'火花守护','events':list(mail.EVENTS)}

    def tearDown(self):self.fixture.tearDown()

    def test_permissions_encrypted_config_and_draft_test(self):
        self.assertEqual(403,self.client.post('/admin/settings/mail/save',json={}).status_code)
        self.assertEqual(200,self.client.post('/admin/settings/mail/save',json=self.config).status_code)
        with session_scope(self.fixture.engine) as db:
            row=db.get(MailSettings,1);self.assertNotIn(b'authorization-secret',row.ciphertext)
        page=self.client.get('/admin/settings/mail');self.assertEqual(200,page.status_code);self.assertNotIn('authorization-secret',page.text)
        draft={**self.config,'host':'new.example.com','password':''}
        self.assertEqual(400,self.client.post('/admin/settings/mail/save',json=draft).status_code)
        with patch.object(mail,'connect',return_value=MagicMock()) as connection:
            self.assertEqual(200,self.client.post('/admin/settings/mail/test',json={**self.config,'sender_name':'draft'}).status_code)
            connection.assert_called_once()
        with session_scope(self.fixture.engine) as db:
            self.assertEqual('火花守护',mail.load_config(db,self.cipher)['sender_name'])
            db.get(User,self.uid).role='user'
        self.assertEqual(404,self.client.get('/admin/settings/mail').status_code)

    def test_verification_and_attempt_limit(self):
        self.client.post('/admin/settings/mail/save',json=self.config)
        self.assertEqual(200,self.client.post('/notifications/save',json={'csrf_token':self.csrf,'email':'receiver@example.com','enabled':True}).status_code)
        self.assertEqual(200,self.client.post('/notifications/verify-send',json={'csrf_token':self.csrf}).status_code)
        self.assertEqual(400,self.client.post('/notifications/verify-send',json={'csrf_token':self.csrf}).status_code)
        with session_scope(self.fixture.engine) as db:
            self.assertFalse(db.get(MailPreference,self.uid).verified)
        for _ in range(5):self.assertEqual(400,self.client.post('/notifications/verify',json={'csrf_token':self.csrf,'code':'invalid'}).status_code)
        with session_scope(self.fixture.engine) as db:self.assertEqual(5,db.get(MailPreference,self.uid).attempts)

    def test_durable_dedup_and_unknown_send_not_retried(self):
        self.client.post('/admin/settings/mail/save',json=self.config)
        with session_scope(self.fixture.engine) as db:
            db.add(MailPreference(user_id=self.uid,email='receiver@example.com',enabled=True,verified=True))
            db.add(DouyinAccount(owner_user_id=self.uid,display_name='测试',encrypted_cookies=b'x',cookie_nonce=b'x',validation_state='verification'))
        for _ in range(2):
            with session_scope(self.fixture.engine) as db:mail.collect(db,mail.load_config(db,self.cipher),datetime.now(timezone.utc),self.cipher)
        with session_scope(self.fixture.engine) as db:self.assertEqual(1,len(db.scalars(select(MailDelivery)).all()))
        smtp=MagicMock();smtp.send_message.side_effect=TimeoutError()
        with patch.object(mail,'connect',return_value=smtp):
            mail.deliver_one(self.fixture.engine,self.cipher);mail.deliver_one(self.fixture.engine,self.cipher)
        smtp.send_message.assert_called_once()
        with session_scope(self.fixture.engine) as db:
            self.assertEqual('uncertain',db.scalar(select(MailDelivery)).status)
            db.scalar(select(DouyinAccount)).validation_state='valid'
        with session_scope(self.fixture.engine) as db:mail.collect(db,mail.load_config(db,self.cipher),datetime.now(timezone.utc),self.cipher)
        smtp=MagicMock()
        with patch.object(mail,'connect',return_value=smtp):mail.deliver_one(self.fixture.engine,self.cipher)
        with session_scope(self.fixture.engine) as db:self.assertEqual('accepted',db.scalar(select(MailDelivery).where(MailDelivery.event=='account_recovered')).status)

    def test_template_preview_escapes_html_and_batch_ownership(self):
        response=self.client.post('/admin/settings/mail/preview',json={'csrf_token':self.csrf,'event':'expiry','body':'<script>alert(1)</script> {username}'})
        self.assertNotIn('<script>',response.json()['html']);self.assertIn('&lt;script&gt;',response.json()['html'])
        response=self.client.post('/tasks/batch',json={'csrf_token':self.csrf,'ids':['other-users-task'],'action':'pause'})
        self.assertEqual(404,response.status_code)
        for path in ('/notifications','/admin/settings/mail','/admin/operations','/dashboard','/subscription'):
            self.assertEqual(200,self.client.get(path).status_code,path)

    def test_batch_pause_enable_time_and_running_guard(self):
        with session_scope(self.fixture.engine) as db:
            account=DouyinAccount(owner_user_id=self.uid,display_name='账号',encrypted_cookies=b'x',cookie_nonce=b'x',validation_state='valid')
            db.add(account);db.flush()
            task=SparkTask(owner_user_id=self.uid,douyin_account_id=account.id,target_name='好友',send_time='10:00',message_template='你好',message_kind='text',enabled=True)
            db.add(task);db.flush();identifier=task.id
        for action in ('pause','enable','time'):
            response=self.client.post('/tasks/batch',json={'csrf_token':self.csrf,'ids':[identifier],'action':action,'time':'11:00'})
            self.assertEqual(200,response.status_code);self.assertTrue(response.json()['results'][0]['ok'],response.text)
        with session_scope(self.fixture.engine) as db:
            from spark_console.models import TaskRun
            task=db.get(SparkTask,identifier);self.assertEqual('11:00',task.send_time);self.assertTrue(task.enabled)
            self.assertGreater(mail.aware(task.next_run_at),datetime.now(timezone.utc))
            db.add(TaskRun(task_id=identifier,scheduled_for=datetime.now(timezone.utc),status='running'))
        response=self.client.post('/tasks/batch',json={'csrf_token':self.csrf,'ids':[identifier],'action':'pause'})
        self.assertFalse(response.json()['results'][0]['ok'])
