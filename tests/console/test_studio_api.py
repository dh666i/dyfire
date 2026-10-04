from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

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
    assert created.json()['times']==['09:30']
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


def test_paused_tasks_do_not_consume_capacity_and_resume_checks_it(site):
    client,(owned,_)=site
    state=client.get('/api/studio').json()
    headers={'X-CSRF-Token':state['csrf']}
    created=client.post('/api/studio/tasks',json={
        'account_id':owned,'targets':[{'name':'保留任务'}],'send_time':'09:30',
    },headers=headers)
    assert created.status_code==200
    paused_id=created.json()['ids'][0]
    assert client.post(f'/api/studio/tasks/{paused_id}/enabled',json={'enabled':False},headers=headers).status_code==200

    # Three enabled tasks fit the fixture's limit even though a fourth paused
    # task remains stored and visible to the user.
    batch=client.post('/api/studio/tasks',json={
        'account_id':owned,
        'targets':[{'name':'新任务甲'},{'name':'新任务乙'},{'name':'新任务丙'}],
        'send_time':'10:00',
    },headers=headers)
    assert batch.status_code==200, batch.text
    blocked=client.post(f'/api/studio/tasks/{paused_id}/enabled',json={'enabled':True},headers=headers)
    assert blocked.status_code==409
    assert '套餐上限' in blocked.json()['detail']

    enabled_id=batch.json()['ids'][0]
    assert client.post(f'/api/studio/tasks/{enabled_id}/enabled',json={'enabled':False},headers=headers).status_code==200
    assert client.post(f'/api/studio/tasks/{paused_id}/enabled',json={'enabled':True},headers=headers).status_code==200


def test_downgrade_overage_keeps_tasks_but_blocks_create_and_resume(site):
    client,(owned,_)=site
    state=client.get('/api/studio').json()
    headers={'X-CSRF-Token':state['csrf']}
    created=client.post('/api/studio/tasks',json={
        'account_id':owned,
        'targets':[{'name':'继续运行甲'},{'name':'继续运行乙'}],
        'send_time':'09:30',
    },headers=headers)
    assert created.status_code==200, created.text
    task_ids=created.json()['ids']

    with session_scope(client.app.state.engine) as db:
        user=db.scalar(select(User).where(User.username=='fixture'))
        subscription=db.get(UserSubscription,user.id)
        subscription.max_tasks=1

    current=client.get('/api/studio').json()
    assert current['subscription']['enabled_task_count']==2
    assert current['subscription']['task_overage']==1
    assert current['subscription']['can_add_task'] is False
    assert len(current['tasks'])==2
    assert all(task['enabled'] for task in current['tasks'])

    denied=client.post('/api/studio/tasks',json={
        'account_id':owned,'targets':[{'name':'不能新增'}],'send_time':'11:00',
    },headers=headers)
    assert denied.status_code==409
    assert '套餐上限' in denied.json()['detail']

    assert client.post(f'/api/studio/tasks/{task_ids[0]}/enabled',json={'enabled':False},headers=headers).status_code==200
    restored=client.get('/api/studio').json()
    assert restored['subscription']['task_overage']==0
    assert restored['subscription']['can_add_task'] is False
    resumed=client.post(f'/api/studio/tasks/{task_ids[0]}/enabled',json={'enabled':True},headers=headers)
    assert resumed.status_code==409
    assert len(client.get('/api/studio').json()['tasks'])==2


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
    # Authenticated task routes are served by the single React console. The
    # task-specific controls are rendered after bootstrap, so this response
    # intentionally contains the shell rather than the retired Jinja page.
    assert '<div id="app"></div>' in page
    assert 'id="refresh-targets"' not in page
    assert 'id="task-target"' not in page
    result=client.post(f'/tasks/{task_id}/edit',data={'csrf_token':state['csrf'],'account_id':owned,'target_name':'Changed','send_time':'12:00','message_kind':'douyin_sticker','message_template':'续火花'},follow_redirects=False)
    assert result.status_code==303
    assert result.headers['location']=='/studio?notice=task-updated'
    assert 'href="/tasks"' not in page
    saved=client.get('/api/studio').json()['tasks'][0]
    assert saved['name']=='Friend' and saved['time']=='12:00' and saved['enabled'] is False


def test_batch_create_staggers_and_schedule_avoids_busy_hour(site):
    client,(owned,foreign)=site
    state=client.get('/api/studio').json()
    headers={'X-CSRF-Token':state['csrf']}
    from spark_console.db import session_scope
    from spark_console.models import SparkTask, DouyinAccount
    engine=client.app.state.engine
    with session_scope(engine) as db:
        other=db.get(DouyinAccount, foreign)
        for minute in range(3):
            db.add(SparkTask(owner_user_id=other.owner_user_id, douyin_account_id=other.id,
                target_name=f'Busy{minute}', send_time=f'09:{30+minute:02d}',
                message_template='续火花', enabled=True,
                next_run_at=datetime.now(timezone.utc)+timedelta(days=1)))
    preview=client.post('/tasks/preview',json={
        'csrf_token':state['csrf'],'account_id':owned,'time':'09:30',
        'targets':[{'name':'甲'},{'name':'乙'},{'name':'丙'}],
    })
    assert preview.status_code==200, preview.text
    body=preview.json()
    assert body['times']==['09:30','09:37','09:42']
    assert body['suggested']
    assert body['suggested'][0]['start']!='09:30'
    created=client.post('/api/studio/tasks',json={
        'account_id':owned,
        'targets':[{'name':'甲'},{'name':'乙'},{'name':'丙'}],
        'send_time':'09:30',
    },headers=headers)
    assert created.status_code==200, created.text
    assert created.json()['times']==body['times']
    times=sorted(item['time'] for item in client.get('/api/studio').json()['tasks'])
    assert times==['09:30','09:37','09:42']
    schedule=client.get('/api/studio/schedule',params={'account_id':owned,'count':1,'preferred':'09:30'})
    assert schedule.status_code==200
    assert schedule.json()['times'][0]=='09:47'


def test_unsubmitted_failure_can_retry_once(site):
    from spark_console.db import session_scope
    from spark_console.models import SparkTask, TaskRun
    client,(owned,_)=site
    state=client.get('/api/studio').json()
    headers={'X-CSRF-Token':state['csrf']}
    task_id=client.post('/api/studio/tasks',json={
        'account_id':owned,'targets':[{'name':'补发好友'}],'send_time':'18:00',
    },headers=headers).json()['ids'][0]
    now=datetime.now(timezone.utc)
    with session_scope(client.app.state.engine) as db:
        run=TaskRun(task_id=task_id, scheduled_for=now-timedelta(hours=2),
                    status='failed', stage='authenticating',
                    error_code='health_retry_exhausted', submitted_at=None)
        db.add(run)
        db.flush()
        run_id=run.id
        submitted=TaskRun(task_id=task_id, scheduled_for=now-timedelta(hours=3),
                          status='submitted_unconfirmed', stage='submitted',
                          submitted_at=now-timedelta(hours=3))
        db.add(submitted)
        db.flush()
        submitted_id=submitted.id
    page=client.get('/runs')
    assert '<div id="app"></div>' in page.text
    # Retry is an API-backed action in the React execution log. Keep the
    # behavior assertion below instead of coupling the test to rendered HTML.
    first=client.post(f'/runs/{run_id}/retry', data={'csrf_token':state['csrf']}, follow_redirects=False)
    assert first.status_code==303
    assert 'retry-queued' in first.headers['location']
    with session_scope(client.app.state.engine) as db:
        task=db.get(SparkTask, task_id)
        queued=task.next_run_at
        if queued.tzinfo is None:
            queued=queued.replace(tzinfo=timezone.utc)
        assert queued <= datetime.now(timezone.utc)+timedelta(seconds=3)
    second=client.post(f'/runs/{run_id}/retry', data={'csrf_token':state['csrf']}, follow_redirects=False)
    assert second.status_code==303
    assert 'retry-busy' in second.headers['location']
    blocked=client.post(f'/runs/{submitted_id}/retry', data={'csrf_token':state['csrf']}, follow_redirects=False)
    assert blocked.status_code==303
    assert 'retry-rejected' in blocked.headers['location']


def test_preview_create_agree_and_return_new_items_without_refresh(site, monkeypatch):
    from spark_console.schedule_slots import SHANGHAI
    client,(owned,_)=site
    monkeypatch.setattr('spark_console.schedule_slots._local_now', lambda now=None: datetime(2026,10,4,10,0,tzinfo=SHANGHAI))
    state=client.get('/api/studio').json()
    targets=[{'name':'预览好友甲'},{'name':'预览好友乙'}]
    preview=client.post('/tasks/preview',json={
        'csrf_token':state['csrf'],'account_id':owned,'time':'09:30',
        'targets':targets,'mode':'auto',
    })
    assert preview.status_code==200,preview.text
    planned=preview.json()
    assert planned['status']=='ready' and planned['count']==2
    assert planned['targets']==[{'name':'预览好友甲','sec_uid':''},{'name':'预览好友乙','sec_uid':''}]
    created=client.post('/api/studio/tasks',json={
        'account_id':owned,'targets':planned['targets'],'send_time':planned['times'][0],
        'schedule_mode':'auto','expected_times':planned['times'],
        'expected_next_runs':[entry['next_run_at'] for entry in planned['entries']],
    },headers={'X-CSRF-Token':state['csrf']})
    assert created.status_code==200,created.text
    body=created.json()
    assert body['times']==planned['times']
    assert [entry['next_run_at'] for entry in body['entries']]==[entry['next_run_at'] for entry in planned['entries']]
    assert [item['name'] for item in body['items']]==['预览好友甲','预览好友乙']
    assert [item['time'] for item in body['items']]==planned['times']
    assert body['count']==2 and all(item['enabled'] for item in body['items'])


def test_automatic_preview_does_not_require_a_user_time(site):
    client, (owned, _) = site
    state = client.get('/api/studio').json()
    preview = client.post('/tasks/preview', json={
        'csrf_token': state['csrf'],
        'account_id': owned,
        'targets': [{'name': '自动安排好友'}],
        'mode': 'auto',
        'time': '',
    })
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body['mode'] == 'auto'
    assert body['status'] == 'ready'
    assert len(body['times']) == 1


def test_changed_load_rejects_preview_atomically_and_returns_updated_plan(site):
    from spark_console.models import SparkTask
    client,(owned,foreign)=site
    state=client.get('/api/studio').json()
    targets=[{'name':'先核对甲'},{'name':'先核对乙'}]
    planned=client.post('/tasks/preview',json={
        'csrf_token':state['csrf'],'account_id':owned,'time':'10:00','targets':targets,
    }).json()
    assert planned['times']==['10:00','10:05']
    with session_scope(client.app.state.engine) as db:
        account=db.get(DouyinAccount,foreign)
        db.add(SparkTask(owner_user_id=account.owner_user_id,douyin_account_id=foreign,
            target_name='刚新增的全站负载',send_time='10:05',message_template='续火',enabled=True,
            next_run_at=datetime.now(timezone.utc)+timedelta(days=1)))
    rejected=client.post('/api/studio/tasks',json={
        'account_id':owned,'targets':targets,'send_time':'10:00',
        'expected_times':planned['times'],
    },headers={'X-CSRF-Token':state['csrf']})
    assert rejected.status_code==409,rejected.text
    detail=rejected.json()['detail']
    assert detail['code']=='schedule_changed'
    assert detail['plan']['times']==['10:00','10:10']
    assert client.get('/api/studio').json()['tasks']==[]


def test_paused_global_tasks_do_not_affect_automatic_suggestion(site, monkeypatch):
    from spark_console.models import SparkTask
    from spark_console.schedule_slots import SHANGHAI
    client,(owned,foreign)=site
    monkeypatch.setattr('spark_console.schedule_slots._local_now',lambda now=None: datetime(2026,10,4,9,25,tzinfo=SHANGHAI))
    with session_scope(client.app.state.engine) as db:
        account=db.get(DouyinAccount,foreign)
        db.add(SparkTask(owner_user_id=account.owner_user_id,douyin_account_id=foreign,
            target_name='暂停负载',send_time='09:30',message_template='续火',enabled=False,
            next_run_at=datetime.now(timezone.utc)+timedelta(days=1)))
    planned=client.get('/api/studio/schedule',params={'account_id':owned,'mode':'auto'}).json()
    assert planned['times']==['09:30'] and planned['nearby_load']==0


def test_preview_normalizes_aliases_before_planning_and_creation(site):
    from spark_console.models import DouyinContactIdentity
    client,(owned,_)=site
    state=client.get('/api/studio').json()
    with session_scope(client.app.state.engine) as db:
        db.add(DouyinContactIdentity(account_id=owned,sec_uid='same-friend',nickname='同一好友'))
    planned=client.post('/tasks/preview',json={
        'csrf_token':state['csrf'],'account_id':owned,'time':'12:00',
        'targets':[{'name':'同一好友'},{'name':'同一好友','sec_uid':'same-friend'},
                   {'name':'另一个昵称','sec_uid':'same-friend'}],
    }).json()
    assert planned['count']==1 and planned['times']==['12:00']
    assert planned['targets']==[{'name':'同一好友','sec_uid':'same-friend'}]
    created=client.post('/api/studio/tasks',json={
        'account_id':owned,'targets':planned['targets'],'send_time':'12:00',
        'expected_times':planned['times'],
    },headers={'X-CSRF-Token':state['csrf']})
    assert created.status_code==200,created.text
    assert created.json()['count']==1


def test_concurrent_confirmations_do_not_create_overlapping_account_times(site):
    from concurrent.futures import ThreadPoolExecutor
    client,(owned,_)=site
    state=client.get('/api/studio').json()
    headers={'X-CSRF-Token':state['csrf']}
    def submit(name):
        return client.post('/api/studio/tasks',json={
            'account_id':owned,'targets':[{'name':name}],'send_time':'12:00',
            'expected_times':['12:00'],
        },headers=headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(submit,['并发甲','并发乙']))
    assert sorted(response.status_code for response in responses)==[200,409]
    rejected=next(response for response in responses if response.status_code==409)
    assert rejected.json()['detail']['code']=='schedule_changed'
    assert rejected.json()['detail']['plan']['times']==['12:05']
    assert len(client.get('/api/studio').json()['tasks'])==1


def test_first_run_date_changed_requires_reconfirmation_and_no_partial_tasks(site):
    client,(owned,_)=site
    state=client.get('/api/studio').json()
    rejected=client.post('/api/studio/tasks',json={
        'account_id':owned,'targets':[{'name':'日期已变'}],'send_time':'12:00',
        'expected_times':['12:00'],'expected_next_runs':['2020-01-01T04:00:00+00:00'],
    },headers={'X-CSRF-Token':state['csrf']})
    assert rejected.status_code==409
    assert rejected.json()['detail']['code']=='schedule_changed'
    assert client.get('/api/studio').json()['tasks']==[]
