import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch,MagicMock
from sqlalchemy import select,func
from spark_console.db import session_scope
from spark_console.models import User,MailPreference,MailDelivery,PasswordReset,WebSession,NotificationPolicy,DouyinAccount
from spark_console.services import mail
from tests.console.test_mail_release import MailReleaseTests as Fixture


class ProductUpgradeTests(unittest.TestCase):
    def setUp(self):self.f=Fixture();self.f.setUp();self.client=self.f.client
    def tearDown(self):self.f.tearDown()

    def test_new_pages_and_customer_permissions(self):
        for path in ('/account-center','/attention','/help','/notifications','/runs?start=2026-09-01&end=2026-09-10','/admin/customers/'+self.f.uid):
            self.assertEqual(200,self.client.get(path).status_code,path)
        self.assertEqual(400,self.client.get('/runs?start=invalid').status_code)
        self.assertEqual(400,self.client.get('/admin/runs?start=invalid').status_code)
        self.assertEqual(400,self.client.get('/admin/runs?start=2026-09-10&end=2026-09-01').status_code)
        self.assertEqual(200,self.client.get('/admin/runs?start=2026-09-01&end=2026-09-10').status_code)
        self.assertEqual(403,self.client.post('/account-center/revoke-sessions',data={}).status_code)
        with session_scope(self.f.fixture.engine) as db:
            db.get(User,self.f.uid).role='user'
        self.assertEqual(404,self.client.get('/admin/customers/'+self.f.uid).status_code)

    def test_recovery_one_use_and_session_revocation(self):
        self.client.post('/admin/settings/mail/save',json=self.f.config)
        with session_scope(self.f.fixture.engine) as db:
            db.add(MailPreference(user_id=self.f.uid,email='verified@example.com',verified=True,enabled=False))
        response=self.client.post('/forgot-password',data={'username':'friend','email':'verified@example.com'})
        self.assertEqual(200,response.status_code)
        with session_scope(self.f.fixture.engine) as db:
            row=db.scalar(select(MailDelivery).where(MailDelivery.event=='reset'));code=row.body.split('：')[1].split('\n')[0]
            self.assertNotEqual(code,db.get(PasswordReset,self.f.uid).digest)
        data={'username':'friend','code':code,'password':'new-pass','confirmation':'new-pass'}
        response=self.client.post('/forgot-password/reset',data=data,follow_redirects=False)
        self.assertEqual(303,response.status_code)
        with session_scope(self.f.fixture.engine) as db:
            self.assertIsNone(db.get(PasswordReset,self.f.uid));self.assertEqual(0,db.scalar(select(func.count()).select_from(WebSession).where(WebSession.user_id==self.f.uid)))
        self.assertEqual(400,self.client.post('/forgot-password/reset',data=data).status_code)

    def test_preferences_and_quiet_queue_skip(self):
        self.client.post('/admin/settings/mail/save',json=self.f.config)
        data={'csrf_token':self.f.csrf,'events':['expiry'],'quiet_start':23,'quiet_end':8,'quiet_enabled':True,'interval_minutes':60}
        self.assertEqual(200,self.client.post('/notifications/policy',json=data).status_code)
        self.assertEqual(400,self.client.post('/notifications/policy',json={**data,'quiet_end':23}).status_code)
        with session_scope(self.f.fixture.engine) as db:
            db.merge(MailPreference(user_id=self.f.uid,email='verified@example.com',verified=True,enabled=True))
            db.add(MailDelivery(event_key='cancelled-event',user_id=self.f.uid,recipient='verified@example.com',event='account_alert',subject='x',body='x',created_at=datetime.now(timezone.utc)-timedelta(minutes=1)))
            db.add(MailDelivery(event_key='test-event',user_id=self.f.uid,recipient='verified@example.com',event='test',subject='x',body='x'))
        smtp=MagicMock()
        with patch.object(mail,'connect',return_value=smtp):mail.deliver_one(self.f.fixture.engine,self.f.cipher)
        smtp.send_message.assert_called_once()
        with session_scope(self.f.fixture.engine) as db:
            self.assertEqual('cancelled',db.scalar(select(MailDelivery).where(MailDelivery.event_key=='cancelled-event')).status)
            self.assertEqual('accepted',db.scalar(select(MailDelivery).where(MailDelivery.event_key=='test-event')).status)

    def test_quiet_backlog_does_not_starve_transactional_mail(self):
        from zoneinfo import ZoneInfo
        self.client.post('/admin/settings/mail/save',json=self.f.config)
        now=datetime.now(timezone.utc)
        hour=now.astimezone(ZoneInfo('Asia/Shanghai')).hour
        with session_scope(self.f.fixture.engine) as db:
            db.merge(MailPreference(user_id=self.f.uid,email='verified@example.com',verified=True,enabled=True))
            db.add(NotificationPolicy(user_id=self.f.uid,events='expiry',quiet_enabled=True,quiet_start=hour,quiet_end=(hour+1)%24))
            for index in range(101):
                db.add(MailDelivery(event_key=f'quiet-{index}',user_id=self.f.uid,recipient='verified@example.com',event='expiry',subject='x',body='x',created_at=now-timedelta(minutes=1)))
            db.add(MailDelivery(event_key='behind-quiet',user_id=self.f.uid,recipient='verified@example.com',event='test',subject='x',body='x',created_at=now))
        smtp=MagicMock()
        with patch.object(mail,'connect',return_value=smtp):mail.deliver_one(self.f.fixture.engine,self.f.cipher)
        smtp.send_message.assert_called_once()
        with session_scope(self.f.fixture.engine) as db:
            self.assertEqual('accepted',db.scalar(select(MailDelivery).where(MailDelivery.event_key=='behind-quiet')).status)
            self.assertEqual(101,db.scalar(select(func.count()).select_from(MailDelivery).where(MailDelivery.status=='queued')))
