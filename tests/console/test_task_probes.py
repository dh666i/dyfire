"""Exercise the asynchronous probe processor, not just queue endpoints."""
import asyncio
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import create_engine_for, create_schema, session_scope
from spark_console.models import AccountOperation, DouyinAccount, DouyinAccountIdentity, SparkTask, SparkTaskTargetIdentity, TaskProbe, TaskRun, User, utc_now
from spark_console.services import Conflict, ValidationError
from spark_console.services import probes
from spark_console.services.account_health import acquire


@pytest.fixture
def fixture(tmp_path):
    settings=Settings(tmp_path,f'sqlite:///{tmp_path / "probe.db"}',tmp_path/'cookie',tmp_path/'session')
    engine=create_engine_for(settings);create_schema(engine)
    cipher=CookieCipher(b'c'*32);sealed=cipher.encrypt(b'fixture-credentials')
    with session_scope(engine) as db:
        user=User(username='probe-owner',password_hash='unused',must_change_password=False)
        db.add(user);db.flush()
        account=DouyinAccount(owner_user_id=user.id,display_name='fixture',encrypted_cookies=sealed.ciphertext,cookie_nonce=sealed.nonce,cookie_version=1)
        db.add(account);db.flush()
        db.add(DouyinAccountIdentity(account_id=account.id,douyin_user_id='123456'))
        task=SparkTask(owner_user_id=user.id,douyin_account_id=account.id,target_name='fixture-friend',send_time='09:30',message_template='fixture',next_run_at=utc_now()+timedelta(days=1))
        db.add(task);db.flush();db.add(SparkTaskTargetIdentity(task_id=task.id,sec_uid='stable-friend'))
        ids=user.id,account.id,task.id
    yield engine,cipher,ids
    engine.dispose()


def enqueue(fixture,run_id=None):
    engine,cipher,(user_id,_,task_id)=fixture
    with session_scope(engine) as db:
        return probes.queue(db,db.get(User,user_id),task_id,run_id=run_id).id


def test_processor_checks_without_send_and_erases_raw_bytes(fixture):
    engine,cipher,(_,account_id,_)=fixture;identifier=enqueue(fixture);raw_refs=[]
    async def reader(raw,*args,**kwargs):
        raw_refs.append(raw)
        assert args[1:] == ('fixture-friend','stable-friend','text')
        with session_scope(engine) as db:
            lease=db.get(AccountOperation,account_id)
            assert lease.token==identifier and lease.kind=='probe'
        return {'summary':'read only','checks':{'identity':True,'stable_target':True}}
    assert asyncio.run(probes.process(engine,cipher,reader))
    assert raw_refs[0]==bytearray()
    with session_scope(engine) as db:
        assert db.get(TaskProbe,identifier).status=='succeeded'
        assert db.get(AccountOperation,account_id) is None
        assert list(db.scalars(select(TaskRun)))==[]


def test_decrypt_failure_finishes_instead_of_poisoning_queue(fixture):
    engine,_,(_,account_id,_)=fixture;identifier=enqueue(fixture)
    with session_scope(engine) as db:db.get(DouyinAccount,account_id).encrypted_cookies=b'broken'
    async def reader(*args,**kwargs):raise AssertionError('must not launch browser')
    assert asyncio.run(probes.process(engine,CookieCipher(b'c'*32),reader))
    with session_scope(engine) as db:
        row=db.get(TaskProbe,identifier)
        assert row.status=='failed' and json.loads(row.result_json)['error_code']=='credential_unavailable'
        assert db.get(AccountOperation,account_id) is None


def test_reconciliation_requires_original_receipt_identity_and_unchanged_task(fixture):
    engine,cipher,(_,_,task_id)=fixture
    with session_scope(engine) as db:
        run=TaskRun(task_id=task_id,scheduled_for=utc_now(),status='submitted_unconfirmed',submitted_at=utc_now())
        db.add(run);db.flush();run_id=run.id
        run.receipt_json=json.dumps({'run_id':run.id,'task_id':task_id,'message_id':'platform-unique-123'})
    identifier=enqueue(fixture,run_id)
    async def reader(raw,*args,**kwargs):
        assert kwargs['message_id']=='platform-unique-123'
        with session_scope(engine) as db:db.get(SparkTask,task_id).send_time='11:45'
        return {'delivery':'message_id_found','checks':{'stable_target':True,'identity':True}}
    asyncio.run(probes.process(engine,cipher,reader))
    with session_scope(engine) as db:
        assert db.get(TaskRun,run_id).status=='submitted_unconfirmed'
        assert json.loads(db.get(TaskProbe,identifier).result_json)['error_code']=='context_changed'


def test_no_receipt_never_turns_uncertainty_into_success(fixture):
    engine,cipher,(_,_,task_id)=fixture
    with session_scope(engine) as db:
        run=TaskRun(task_id=task_id,scheduled_for=utc_now(),status='submitted_unconfirmed',submitted_at=utc_now())
        db.add(run);db.flush();run_id=run.id
    enqueue(fixture,run_id)
    async def reader(*args,**kwargs):
        assert kwargs['message_id'] is None
        return {'delivery':'message_id_found','checks':{'identity':True,'stable_target':True}}
    asyncio.run(probes.process(engine,cipher,reader))
    with session_scope(engine) as db:assert db.get(TaskRun,run_id).status=='submitted_unconfirmed'


def test_successful_readonly_reconciliation_does_not_create_another_run(fixture):
    engine,cipher,(_,_,task_id)=fixture
    with session_scope(engine) as db:
        run=TaskRun(task_id=task_id,scheduled_for=utc_now(),status='submitted_unconfirmed',submitted_at=utc_now())
        db.add(run);db.flush();run_id=run.id
        run.receipt_json=json.dumps({'run_id':run.id,'task_id':task_id,'message_id':'platform-unique-123'})
    enqueue(fixture,run_id)
    async def reader(*args,**kwargs):return {'delivery':'message_id_found','checks':{'identity':True,'stable_target':True}}
    asyncio.run(probes.process(engine,cipher,reader))
    with session_scope(engine) as db:
        assert db.get(TaskRun,run_id).status=='success'
        assert len(list(db.scalars(select(TaskRun))))==1


def test_busy_lease_defers_probe_and_bad_run_is_validated_before_cooldown(fixture):
    engine,cipher,(user_id,account_id,task_id)=fixture;identifier=enqueue(fixture)
    with session_scope(engine) as db:
        acquire(db,account_id,'sending-token','send')
        with pytest.raises(ValidationError):probes.queue(db,db.get(User,user_id),task_id,run_id='unknown')
    async def reader(*args,**kwargs):raise AssertionError('busy account cannot be opened')
    assert not asyncio.run(probes.process(engine,cipher,reader))
    with session_scope(engine) as db:assert db.get(TaskProbe,identifier).status=='queued'
