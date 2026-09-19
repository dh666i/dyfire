import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select
from spark_console.db import create_schema
from spark_console.models import TaskRun, TaskHealthRetry, SparkTask, DouyinAccount, UserSubscription
from spark_console.executor import ExecutionResult
from spark_console.worker import Worker
from spark_console.scheduler import claim_next_due_task
from spark_console.services.account_health import record, snapshot
from tests.console import test_scheduler_worker as fixtures
from sqlalchemy import create_engine
import pytest

@pytest.fixture
def case(tmp_path):
    now = datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc)
    settings = fixtures.WorkerCredentialTests._settings(tmp_path)
    engine = create_engine(settings.database_url)
    create_schema(engine)
    account_id, tasks = fixtures.WorkerCredentialTests._seed_health_case(engine, now)
    yield now, settings, engine, account_id, tasks[0]
    engine.dispose()

class Executor:
    def __init__(self, *, fail=True, submitted=False, health="temporary"):
        self.calls = 0
        self.fail, self.submitted, self.health = fail, submitted, health
    async def execute(self, cookies, target_name, message, *, on_submitted=None, **kwargs):
        self.calls += 1
        if self.submitted and on_submitted:
            on_submitted()
        if self.fail:
            return ExecutionResult(False, "authenticating", "account_health_pending",
                "temporary test", health_state=self.health, health_reason="test")
        return ExecutionResult(True, "complete")

def attempt(case, executor, offset=0):
    now, settings, engine, _, _ = case
    at=now+timedelta(seconds=offset)
    return asyncio.run(Worker(settings,engine,executor=executor,
        started_at=at-timedelta(seconds=1)).run_once(at))

def recover(case, seconds=40):
    now, _, engine, aid, _=case
    with Session(engine) as db:
        a=db.get(DouyinAccount,aid)
        record(db,aid,snapshot(a),"valid","复核正常",now=now+timedelta(seconds=seconds),recheck=True)
        db.commit()

def test_recovery_retries_same_run_without_changing_next_day(case):
    first=attempt(case,Executor())
    assert first.status=="retrying" and first.submitted_at is None
    now,_,engine,aid,tid=case
    with Session(engine) as db:
        following=db.get(SparkTask,tid).next_run_at
        assert claim_next_due_task(db,now+timedelta(seconds=35),"other") is None
    recover(case)
    result=attempt(case,Executor(fail=False),190)
    assert result.id==first.id and result.status=="success" and result.attempt==2
    with Session(engine) as db:
        assert db.get(SparkTask,tid).next_run_at==following
        assert len(db.scalars(select(TaskRun)).all())==1
        assert claim_next_due_task(db,now+timedelta(seconds=65),"other") is None

def test_chat_list_flake_retries_inside_window(case, monkeypatch):
    from spark_console import health_retry
    monkeypatch.setattr(health_retry, "WINDOW", timedelta(minutes=20))
    class Flake(Executor):
        async def execute(self, cookies, target_name, message, *, on_submitted=None, **kwargs):
            self.calls += 1
            return ExecutionResult(False, "authenticating", "account_health_pending",
                "已等待页面就绪 45 秒；未检测到已登录会话列表；HTTP 200，业务码缺失或格式异常，未取得有效登录信息；页面 /chat",
                health_state="temporary", health_reason="test")
    first=attempt(case,Flake())
    recover(case,170)
    second=attempt(case,Flake(),180)
    recover(case,650)
    third=attempt(case,Flake(),660)
    assert third.id==first.id and third.attempt==3 and third.status=="retrying"
    recover(case,1130)
    fourth=attempt(case,Executor(fail=False),1140)
    assert fourth.id==first.id and fourth.status=="success"


def test_chat_flake_waits_out_backoff_inside_window(case, monkeypatch):
    from spark_console import health_retry
    monkeypatch.setattr(health_retry, "WINDOW", timedelta(minutes=20))
    class Flake(Executor):
        async def execute(self, cookies, target_name, message, *, on_submitted=None, **kwargs):
            self.calls += 1
            return ExecutionResult(False, "authenticating", "account_health_pending",
                "已等待页面就绪 45 秒；未检测到已登录会话列表；HTTP 200，业务码缺失或格式异常，未取得有效登录信息；页面 /chat",
                health_state="temporary", health_reason="test")
    first=attempt(case,Flake())
    recover(case,170)
    attempt(case,Flake(),180)
    recover(case,1130)
    third=attempt(case,Flake(),1140)
    assert third.id==first.id and third.status=="retrying"
    recover(case,1310)
    poked=attempt(case,Executor(fail=False),1320)
    assert poked is None or poked.status=="retrying"
    now,_,engine,_,_=case
    with Session(engine) as db:
        assert db.get(TaskRun, first.id).status=="retrying"
        assert db.get(TaskRun, first.id).error_code!="health_retry_exhausted"
    recover(case,1610)
    fourth=attempt(case,Executor(fail=False),1620)
    assert fourth.id==first.id and fourth.status=="success"


def test_two_retries_max(case):
    first=attempt(case,Executor())
    for seconds in (180,660):
        recover(case,seconds-10)
        result=attempt(case,Executor(),seconds)
    assert result.id==first.id and result.attempt==3 and result.status=="failed"
    assert result.error_code=="health_retry_exhausted"
    recover(case,210)
    assert attempt(case,Executor(fail=False),240) is None

@pytest.mark.parametrize("reason",["expiry","edited","paused","membership","verification","invalid"])
def test_ineligible_recovery_never_executes(case,reason):
    first=attempt(case,Executor())
    now,_,engine,aid,tid=case
    recover(case)
    with Session(engine) as db:
        task=db.get(SparkTask,tid)
        if reason=="edited": task.message_template="changed"
        if reason=="paused": task.enabled=False
        if reason=="membership": db.get(UserSubscription,task.owner_user_id).valid_until=now+timedelta(seconds=5)
        if reason in ("verification","invalid"): db.get(DouyinAccount,aid).validation_state=reason
        db.commit()
    executor=Executor(fail=False)
    assert attempt(case,executor,601 if reason=="expiry" else 190) is None
    assert executor.calls==0
    with Session(engine) as db:
        run=db.get(TaskRun,first.id)
        assert run.status=="failed" and run.error_code.startswith("health_retry_")

@pytest.mark.parametrize("health",["temporary","verification","logged_out"])
def test_submission_boundary_wins_over_health_error(case,health):
    result=attempt(case,Executor(submitted=True,health=health))
    assert result.status=="submitted_unconfirmed"
    with Session(case[2]) as db:
        assert db.get(TaskHealthRetry,result.id) is None

def test_explicit_login_failure_does_not_queue(case):
    result=attempt(case,Executor(health="logged_out"))
    assert result.status=="failed"
    with Session(case[2]) as db:
        assert db.get(TaskHealthRetry,result.id) is None

def test_old_failures_are_not_replayed(case):
    first=attempt(case,Executor(health="verification"))
    recover(case)
    assert attempt(case,Executor(fail=False),60) is None

def test_claim_is_not_duplicated_and_expired_crash_is_bounded(case):
    first=attempt(case,Executor())
    recover(case)
    now,_,engine,_,_=case
    with Session(engine) as db:
        run=claim_next_due_task(db,now+timedelta(seconds=190),"a")
        assert run.id==first.id
        db.commit()
    with Session(engine) as db:
        assert claim_next_due_task(db,now+timedelta(seconds=191),"b") is None
        db.commit()
    with Session(engine) as db:
        assert claim_next_due_task(db,now+timedelta(minutes=31),"b") is None
        db.commit()
        assert db.get(TaskRun,first.id).error_code=="health_retry_expired"

def test_submit_checkpoint_enforces_expiration(case):
    attempt(case,Executor())
    recover(case)
    now,settings,engine,_,_=case
    class ExpiringExecutor(Executor):
        async def execute(self,cookies,target_name,message,*,on_submitted=None,**kwargs):
            clock[0]=now+timedelta(seconds=601)
            with pytest.raises(RuntimeError):
                on_submitted()
            return ExecutionResult(False,"sending","checkpoint_failed",retryable=True)
    clock=[now+timedelta(seconds=590)]
    result=asyncio.run(Worker(settings,engine,executor=ExpiringExecutor(),
        started_at=now,clock=lambda:clock[0]).run_once(clock[0]))
    assert result.submitted_at is None and result.status=="failed"
    assert result.error_code=="health_retry_expired"
def test_monitor_counts_waiting_health_retry(case):
    from spark_console.ops_monitor import _current_retries
    attempt(case, Executor())
    with Session(case[2]) as db:
        assert _current_retries(db, case[0] + timedelta(seconds=15)) == 1
        assert _current_retries(db, case[0] + timedelta(seconds=601)) == 0


def test_generic_retry_keeps_original_deadline(case):
    now, _, engine, _, tid = case
    from spark_console.health_retry import original_schedule
    with Session(engine) as db:
        prior = TaskRun(task_id=tid, scheduled_for=now, status="retrying",
                        retry_after=now+timedelta(minutes=1), attempt=1)
        current = TaskRun(task_id=tid, scheduled_for=now+timedelta(minutes=1),
                          status="running", attempt=2)
        db.add_all([prior, current])
        db.flush()
        assert original_schedule(db, current) == now


def test_submitted_retry_crash_never_reclaims(case):
    first = attempt(case, Executor())
    recover(case)
    now, _, engine, _, _ = case
    with Session(engine) as db:
        run = claim_next_due_task(db, now+timedelta(seconds=190), "a")
        run.submitted_at = now+timedelta(seconds=65)
        run.stage = "submitted"
        db.commit()
    with Session(engine) as db:
        assert claim_next_due_task(db, now+timedelta(minutes=31), "b") is None
        assert db.get(TaskRun, first.id).status == "submitted_unconfirmed"

def add_four_due_tasks(case):
    now, _, engine, aid, tid = case
    with Session(engine) as db:
        first = db.get(SparkTask, tid)
        for n in range(4):
            db.add(SparkTask(owner_user_id=first.owner_user_id, douyin_account_id=aid,
                target_name=f"同时好友{n}", send_time=first.send_time, message_template="test",
                enabled=True, next_run_at=first.next_run_at))
        db.commit()


def test_five_due_tasks_survive_first_login_failure(case):
    add_four_due_tasks(case)
    first = attempt(case, Executor())
    assert attempt(case, Executor(), 35) is None
    with Session(case[2]) as db:
        rows = db.scalars(select(TaskRun)).all()
        assert len(rows) == 5
        assert sorted(r.attempt for r in rows) == [0, 0, 0, 0, 1]
        assert all(r.status == "retrying" for r in rows)
    recover(case)
    executor = Executor(fail=False, submitted=True)
    done = [attempt(case, executor, 70+n*35) for n in range(5)]
    assert len({r.task_id for r in done}) == 5
    assert all(r.status == "success" for r in done)
    assert executor.calls == 5
    assert attempt(case, executor, 250) is None


def test_pending_siblings_expire_with_visible_records(case):
    add_four_due_tasks(case)
    attempt(case, Executor())
    assert attempt(case, Executor(), 35) is None
    assert attempt(case, Executor(fail=False), 601) is None
    with Session(case[2]) as db:
        rows = db.scalars(select(TaskRun)).all()
        assert len(rows) == 5
        assert sum(r.status == "skipped" and r.attempt == 0 for r in rows) == 4
        assert all(r.error_code == "health_retry_expired" for r in rows)


def test_recovery_before_scheduler_preserves_siblings_and_deadline(case):
    add_four_due_tasks(case)
    attempt(case, Executor())
    recover(case, 450)
    results = [attempt(case, Executor(fail=False), 460+n*30) for n in range(5)]
    assert all(r and r.status == "success" for r in results), [(r.status, r.error_code) if r else None for r in results]
    assert len({r.task_id for r in results}) == 5


def test_attempt_history_keeps_original_reasons_and_times(case):
    from spark_console.models import TaskRunAttempt
    first = attempt(case, Executor())
    recover(case)
    second = attempt(case, Executor(), 190)
    recover(case, 640)
    third = attempt(case, Executor(fail=False, submitted=True), 670)
    with Session(case[2]) as db:
        history = db.scalars(select(TaskRunAttempt).where(
            TaskRunAttempt.run_id == first.id).order_by(TaskRunAttempt.number)).all()
        assert [h.number for h in history] == [1, 2, 3]
        assert [h.status for h in history] == ["failed", "failed", "success"]
        assert history[0].reason == "temporary test"
        assert history[1].started_at > history[0].started_at
        assert history[2].submitted_at is not None
        assert third.id == second.id == first.id

def test_relogin_cancels_waiting_siblings(case):
    from spark_console.services.account_health import recovered
    add_four_due_tasks(case)
    attempt(case, Executor())
    assert attempt(case, Executor(), 35) is None
    with Session(case[2]) as db:
        recovered(db, db.get(DouyinAccount, case[3]))
        db.commit()
        rows = db.scalars(select(TaskRun)).all()
        pending = [r for r in rows if r.error_code == "health_retry_cancelled"]
        assert len(pending) == 5
        assert all(r.status in ("failed", "skipped") for r in pending)


def test_queued_generic_retry_cannot_reset_deadline_or_attempts(case):
    from spark_console.health_retry import queue_blocked_tasks
    now, _, engine, aid, tid = case
    with Session(engine) as db:
        task = db.get(SparkTask, tid)
        task.next_run_at = now
        db.get(DouyinAccount, aid).validation_state = "temporary"
        prior = TaskRun(task_id=tid, scheduled_for=now-timedelta(minutes=6),
                        status="retrying", retry_after=now, attempt=2)
        db.add(prior)
        db.flush()
        queue_blocked_tasks(db, now)
        run = db.scalar(select(TaskRun).where(TaskRun.scheduled_for == now))
        pending = db.get(TaskHealthRetry, run.id)
        assert run.attempt == 2
        from spark_console.health_retry import aware
        assert aware(pending.expires_at) == now+timedelta(minutes=4)


def test_retry_wait_backs_off_two_then_five_minutes():
    from spark_console.health_retry import retry_wait
    assert retry_wait(1) == timedelta(minutes=2)
    assert retry_wait(2) == timedelta(minutes=5)
