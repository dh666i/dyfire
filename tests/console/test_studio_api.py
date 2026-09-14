from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from spark_console.config import Settings
from spark_console.db import create_schema, session_scope
from spark_console.models import User, DouyinAccount, UserSubscription
from spark_console.security import PasswordService, SessionService
from spark_console.web.app import create_app


@pytest.fixture
def site(tmp_path):
    cookie = tmp_path/'cookie.key'; cookie.write_bytes(b'c'*32)
    key = tmp_path/'session.key'; key.write_bytes(b's'*32)
    settings=Settings(tmp_path,f'sqlite:///{tmp_path}/test.db',cookie,key)
    engine=create_engine(settings.database_url,connect_args={'check_same_thread':False})
    create_schema(engine)
    with session_scope(engine) as db:
        user=User(username='fixture',password_hash=PasswordService().hash('test-only'),role='user',must_change_password=False)
        other=User(username='other',password_hash='unused',role='user',must_change_password=False)
        db.add_all([user,other]);db.flush()
        now=datetime.now(timezone.utc)
        db.add(UserSubscription(user_id=user.id,valid_from=now-timedelta(days=1),valid_until=now+timedelta(days=1),max_accounts=2,max_tasks=3))
        owned=DouyinAccount(owner_user_id=user.id,display_name='Owned',encrypted_cookies=b'fixture',cookie_nonce=b'fixture',validation_state='valid')
        foreign=DouyinAccount(owner_user_id=other.id,display_name='Foreign',encrypted_cookies=b'fixture',cookie_nonce=b'fixture',validation_state='valid')
        db.add_all([owned,foreign]);db.flush()
        ids=(owned.id,foreign.id)
        token,record=SessionService(key.read_bytes()).create_record(user.id);db.add(record)
    with TestClient(create_app(settings,engine),base_url='https://testserver') as client:
        client.cookies.set('spark_session',token)
        yield client,ids
    engine.dispose()


def test_create_owned_and_idempotent_enabled(site):
    client,(owned,foreign)=site
    data=client.get('/api/studio').json()
    assert [a['id'] for a in data['accounts']]==[owned]
    headers={'X-CSRF-Token':data['csrf']}
    payload={'account_id':owned,'targets':[{'name':'Friend'}],'send_time':'09:30'}
    assert client.post('/api/studio/tasks',json=payload).status_code==403
    assert client.post('/api/studio/tasks',json={**payload,'account_id':foreign},headers=headers).status_code==404
    created=client.post('/api/studio/tasks',json=payload,headers=headers)
    assert created.status_code==200,created.text
    assert client.post('/api/studio/tasks',json=payload,headers=headers).status_code==409
    task_id=created.json()['ids'][0]
    for _ in range(2):
        assert client.post(f'/api/studio/tasks/{task_id}/enabled',json={'enabled':False},headers=headers).status_code==200
    assert client.get('/api/studio').json()['tasks'][0]['enabled'] is False
    assert client.get('/api/admin/updates').status_code==404
    runs=client.get('/api/studio/runs').json()
    assert runs['total']==0 and runs['items']==[]
    assert client.get('/api/studio/runs?page=0').status_code==422
    assert client.get('/api/studio/runs?status=bogus').status_code==400


def test_expired_auth_returns_json(site):
    client,_=site
    client.cookies.clear()
    response=client.get('/api/studio')
    assert response.status_code==401
    assert response.headers['content-type'].startswith('application/json')

def test_existing_recipient_is_fixed_and_cannot_be_created_again(site):
    client,(owned,_)=site
    state=client.get('/api/studio').json()
    headers={'X-CSRF-Token':state['csrf']}
    payload={'account_id':owned,'targets':[{'name':'Friend'}],'send_time':'09:30'}
    task_id=client.post('/api/studio/tasks',json=payload,headers=headers).json()['ids'][0]
    client.post(f'/api/studio/tasks/{task_id}/enabled',json={'enabled':False},headers=headers)
    duplicate=client.post('/api/studio/tasks',json={**payload,'send_time':'11:00'},headers=headers)
    assert duplicate.status_code==409
    assert len(client.get('/api/studio').json()['tasks'])==1
    page=client.get(f'/tasks/{task_id}/edit').text
    assert 'id="refresh-targets"' not in page
    assert 'id="task-target"' not in page
    result=client.post(f'/tasks/{task_id}/edit',data={'csrf_token':state['csrf'],'account_id':owned,'target_name':'Changed','send_time':'12:00','message_kind':'douyin_sticker','message_template':'续火花'},follow_redirects=False)
    assert result.status_code==303
    saved=client.get('/api/studio').json()['tasks'][0]
    assert saved['name']=='Friend' and saved['time']=='12:00' and saved['enabled'] is False
