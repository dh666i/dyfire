"""Kill a real worker process against an isolated DB; no network or messages."""
import asyncio
from datetime import timedelta
import multiprocessing
from pathlib import Path

import pytest
from sqlalchemy import select

from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import create_engine_for, create_schema, session_scope
from spark_console.models import DouyinAccount, SparkTask, TaskRun, utc_now
from spark_console.scheduler import claim_next_due_task, RUN_LEASE
from spark_console.worker import Worker


def settings_for(root):
    root=Path(root)
    return Settings(root,f'sqlite:///{root / "crash.db"}',root/'cookie.key',root/'session.key')


def child(root,submitted,ready):
    class BlockingExecutor:
        async def execute(self,*args,on_submitted=None,**kwargs):
            if submitted:on_submitted()
            ready.set()
            await asyncio.Event().wait()
    settings=settings_for(root);engine=create_engine_for(settings)
    asyncio.run(Worker(settings,engine,executor=BlockingExecutor(),started_at=utc_now()-timedelta(minutes=1)).run_once())


@pytest.mark.parametrize('submitted',[False,True])
def test_worker_process_death_obeys_durable_submission_boundary(tmp_path,submitted):
    from spark_console.models import User
    settings=settings_for(tmp_path);settings.cookie_key_file.write_bytes(b'c'*32);settings.session_key_file.write_bytes(b's'*32)
    engine=create_engine_for(settings);create_schema(engine)
    raw=b'[{"name":"sid","value":"fixture","domain":".douyin.com","path":"/"}]'
    sealed=CookieCipher(b'c'*32).encrypt(raw)
    with session_scope(engine) as db:
        user=User(username='crash-fixture',password_hash='unused',role='admin');db.add(user);db.flush()
        account=DouyinAccount(owner_user_id=user.id,display_name='fixture',encrypted_cookies=sealed.ciphertext,cookie_nonce=sealed.nonce,validation_state='valid')
        db.add(account);db.flush()
        db.add(SparkTask(owner_user_id=user.id,douyin_account_id=account.id,target_name='fixture',send_time='09:30',message_template='never sent',next_run_at=utc_now()))
    context=multiprocessing.get_context('spawn');ready=context.Event();process=context.Process(target=child,args=(str(tmp_path),submitted,ready))
    process.start()
    try:
        assert ready.wait(15),'isolated worker did not reach requested boundary'
        with session_scope(engine) as db:
            original=db.scalar(select(TaskRun));identifier=original.id
            assert (original.submitted_at is not None)==submitted
        process.terminate();process.join(10);assert not process.is_alive()
        # Advance only this isolated scheduler's clock past the lease.
        with session_scope(engine) as db:
            replacement=claim_next_due_task(db,utc_now()+RUN_LEASE+timedelta(seconds=1),'replacement-worker')
            if submitted:
                assert replacement is None
                assert db.get(TaskRun,identifier).status=='submitted_unconfirmed'
            else:
                assert replacement.id==identifier and replacement.attempt==2
                assert replacement.submitted_at is None
            assert len(list(db.scalars(select(TaskRun))))==1
    finally:
        if process.is_alive():process.terminate();process.join(10)
        engine.dispose()
