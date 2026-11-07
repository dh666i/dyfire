import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from spark_console.db import session_scope
from spark_console.mail_outbox import seal_body, open_body
from spark_console.mail_worker import read_health
from spark_console.models import MailDelivery, MailPreference, PasswordReset, RedemptionCode
from spark_console.security_maintenance import maintain
from spark_console.services import mail
from spark_console.executor import DouyinExecutor
from tests.console import test_mail_release as mail_tests


@pytest.fixture
def configured():
    case = mail_tests.MailReleaseTests()
    case.setUp()
    case.client.post('/admin/settings/mail/save', json=case.config)
    yield case
    case.tearDown()


def test_encrypted_body_binds_to_recipient_and_row(configured):
    f = configured
    with session_scope(f.fixture.engine) as db:
        row = seal_body(MailDelivery(user_id=f.uid, event='test', event_key='tamper', recipient='a@example.com', subject='test', body='123456'), f.cipher)
        assert row.body == '' and b'123456' not in row.body_ciphertext
        assert open_body(row, f.cipher) == '123456'
        row.recipient = 'other@example.com'
        db.add(row)
    with patch.object(mail, 'connect') as connect:
        mail.deliver_one(f.fixture.engine, f.cipher)
        connect.assert_not_called()
    with session_scope(f.fixture.engine) as db:
        row = db.scalar(select(MailDelivery))
        assert row.status == 'failed' and row.body_ciphertext is None


def test_cancelled_verification_erases_encrypted_payload(configured):
    f = configured
    with session_scope(f.fixture.engine) as db:
        db.add(seal_body(MailDelivery(user_id=f.uid,event='verify',event_key='cancel',recipient='a@example.com',subject='test',body='secret code'),f.cipher))
    with patch.object(mail,'connect') as connect:
        mail.deliver_one(f.fixture.engine,f.cipher)
        connect.assert_not_called()
    with session_scope(f.fixture.engine) as db:
        row=db.scalar(select(MailDelivery))
        assert row.status=='cancelled' and row.body=='' and row.body_ciphertext is None


def test_missing_or_stale_consumer_does_not_promise_delivery(configured):
    f=configured
    f.client.post('/notifications/save',json={'csrf_token':f.csrf,'email':'a@example.com'})
    path=f.fixture.settings.data_dir/'mail-worker-health.json'
    path.write_text(json.dumps({'at':time.time()-1000,'state':'ready'}))
    assert read_health(path.parent)['online'] is False
    assert not f.client.get('/api/notifications').json()['mail_ready']
    assert f.client.post('/notifications/verify-send',json={'csrf_token':f.csrf}).status_code==503
    with session_scope(f.fixture.engine) as db:
        assert db.scalar(select(MailDelivery)) is None


def test_sensitive_cleanup_is_bounded_dry_by_default_and_preserves_live_code(configured):
    f=configured; now=datetime.now(timezone.utc)
    with session_scope(f.fixture.engine) as db:
        db.add(PasswordReset(user_id=f.uid,digest='expired',expires_at=now-timedelta(minutes=1)))
        db.add(MailPreference(user_id=f.uid,email='a@example.com',code_hash='live',code_until=now+timedelta(minutes=5)))
        db.add(MailDelivery(user_id=f.uid,event='test',event_key='old',recipient='a@example.com',subject='test',body='legacy',status='accepted'))
    with session_scope(f.fixture.engine) as db:
        result=maintain(db,f.cipher,now=now)
        assert result['expired_resets']==1
        assert db.get(PasswordReset,f.uid) is not None
        maintain(db,f.cipher,apply=True,now=now)
    with session_scope(f.fixture.engine) as db:
        assert db.get(PasswordReset,f.uid) is None
        assert db.get(MailPreference,f.uid).code_hash=='live'
        assert db.scalar(select(MailDelivery)).body==''


def test_redemption_copy_uses_role_csrf_audit_and_keeps_redemption_working(configured):
    f=configured
    result=f.client.post('/api/admin/redemption-codes',json={'csrf_token':f.csrf,'quantity':1})
    assert result.status_code==200
    plaintext=result.json()['codes'][0]
    rows=f.client.get('/api/admin/redemption-codes',params={'q':plaintext}).json()['items']
    assert len(rows)==1 and rows[0]['code']==plaintext and rows[0]['can_copy']
    identifier=rows[0]['id'];url=f'/api/admin/redemption-codes/{identifier}/reveal'
    assert f.client.post(url,json={}).status_code==403
    assert f.client.post(url,json={'csrf_token':f.csrf}).json()['code']==plaintext
    with session_scope(f.fixture.engine) as db:
        row=db.get(RedemptionCode,identifier)
        assert row.code is None and plaintext.encode() not in row.code_ciphertext
        from spark_console.services.subscriptions import SubscriptionService
        from spark_console.services.audits import AuditService
        service=SubscriptionService(db,AuditService(db),cipher=f.cipher)
        service.redeem(f.uid,plaintext)
        with pytest.raises(Exception):
            service.reveal_redemption_code('unknown-user',identifier)
    from spark_console.models import User
    with session_scope(f.fixture.engine) as db:db.get(User,f.uid).role='user'
    assert f.client.get('/api/admin/redemption-codes').status_code==404


def test_idle_close_releases_runtime_but_never_active_session():
    async def check():
        executor=DouyinExecutor()
        context=MagicMock(close=AsyncMock());browser=MagicMock(close=AsyncMock());playwright=MagicMock(stop=AsyncMock())
        executor._live={'used_at':0,'context':context,'browser':browser,'playwright':playwright}
        executor._executing=True
        assert not await executor.close_idle_session(now=10000)
        executor._executing=False
        assert await executor.close_idle_session(now=10000)
        assert executor._live is None
        context.close.assert_awaited_once();browser.close.assert_awaited_once();playwright.stop.assert_awaited_once()
    asyncio.run(check())
