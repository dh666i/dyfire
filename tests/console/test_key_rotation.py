import json
import sqlite3

import pytest
from sqlalchemy import select

from spark_console.db import session_scope
from spark_console.key_rotation import rotate_copy
from spark_console.crypto import CookieCipher
from spark_console.mail_outbox import seal_body, _context
from spark_console.models import MailDelivery, MailSettings, InviteCode, InviteCodeSecret, RedemptionCode, MailPreference, utc_now
from tests.console.test_task_probes import fixture


def test_rotation_copy_preserves_login_codes_mail_and_original_database(fixture,tmp_path):
    engine,old,(user_id,account_id,_)=fixture;new=CookieCipher(b'n'*32)
    with session_scope(engine) as db:
        sealed=old.encrypt(b'{"host":"smtp.fixture","enabled":false}')
        db.add(MailSettings(id=1,ciphertext=sealed.ciphertext,nonce=sealed.nonce))
        invite=InviteCode(code_hash='i'*64,expires_at=utc_now());db.add(invite);db.flush()
        sealed=old.encrypt(b'FIXTURE-INVITE');db.add(InviteCodeSecret(invite_id=invite.id,ciphertext=sealed.ciphertext,nonce=sealed.nonce))
        code=RedemptionCode(code_hash='r'*64,duration_days=30,max_accounts=1,max_tasks=10)
        db.add(code);db.flush();ctx=(code.id+':'+code.code_hash).encode()
        sealed=old.encrypt_for(b'FIXTURE-REDEMPTION','redemption-code',ctx);code.code_ciphertext=sealed.ciphertext;code.code_nonce=sealed.nonce
        body=MailDelivery(user_id=user_id,event_key='rotation-test',event='expiry',subject='fixture',recipient='fixture@example.com',body='only in encrypted storage')
        db.add(seal_body(body,old));db.flush();mail_id=body.id
        db.add(MailPreference(user_id=user_id,email='fixture@example.com',code_hash=old.digest('mail','123456')))
    source=engine.url.database;output=tmp_path/'rotation'
    with sqlite3.connect(source) as db:
        original=db.execute('select encrypted_cookies,cookie_nonce from douyin_accounts where id=?',(account_id,)).fetchone()
    report=rotate_copy(source,b'c'*32,b'n'*32,output)
    assert report['source_changed'] is False and report['installed'] is False
    assert '123456' not in json.dumps(report)
    with sqlite3.connect(source) as db:
        assert db.execute('select encrypted_cookies,cookie_nonce from douyin_accounts where id=?',(account_id,)).fetchone()==original
    with sqlite3.connect(output/'spark.db') as db:
        db.row_factory=sqlite3.Row
        account=db.execute('select * from douyin_accounts').fetchone()
        assert new.decrypt(account['encrypted_cookies'],account['cookie_nonce'])==b'fixture-credentials'
        code=db.execute('select * from redemption_codes').fetchone()
        assert new.decrypt_for(code['code_ciphertext'],code['code_nonce'],'redemption-code',(code['id']+':'+code['code_hash']).encode())==b'FIXTURE-REDEMPTION'
        body=db.execute('select * from mail_deliveries where id=?',(mail_id,)).fetchone()
        from types import SimpleNamespace
        assert new.decrypt_for(body['body_ciphertext'],body['body_nonce'],'mail-body',_context(SimpleNamespace(**dict(body))))==b'only in encrypted storage'
        assert db.execute('select code_hash from mail_preferences').fetchone()[0] is None
        assert db.execute('pragma integrity_check').fetchone()[0]=='ok'


def test_wrong_rotation_key_does_not_leave_a_partial_output(fixture,tmp_path):
    engine,_,_=fixture;destination=tmp_path/'failed-rotation'
    with pytest.raises(Exception):rotate_copy(engine.url.database,b'x'*32,b'n'*32,destination)
    assert not destination.exists()


def test_rotation_never_overwrites_existing_output(fixture,tmp_path):
    engine,_,_=fixture;destination=tmp_path/'existing';destination.mkdir();(destination/'keep').write_text('keep')
    with pytest.raises(FileExistsError):rotate_copy(engine.url.database,b'c'*32,b'n'*32,destination)
    assert (destination/'keep').read_text()=='keep'
