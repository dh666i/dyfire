from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, text

from spark_console.db import session_scope
from spark_console.models import DouyinAccount, SparkTask, TaskRun, SparkTaskTargetIdentity
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.accounts import AccountService
from spark_console.services.account_health import views
from spark_console.services.audits import AuditService
from spark_console.services.directory import task_avatar_map
from spark_console.services.subscriptions import SubscriptionService
from spark_console.services.tasks import TaskService, ScheduleChanged
from spark_console.web.membership import membership_summary


STUDIO_BOOTSTRAP_PAGE_SIZE = 100


class Target(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    sec_uid: str = Field(default='', max_length=256)


class NewTasks(BaseModel):
    account_id: str
    targets: list[Target] = Field(min_length=1, max_length=100)
    send_time: str
    message_kind: str = 'douyin_sticker'
    message_template: str = '续火花'
    schedule_mode: Literal['auto', 'manual'] = 'manual'
    expected_times: list[str] | None = Field(default=None, min_length=1, max_length=100)
    expected_next_runs: list[str] | None = Field(default=None, min_length=1, max_length=100)


class Enabled(BaseModel):
    enabled: bool


def build_studio_router(engine, auth, cipher):
    router = APIRouter()

    @router.get('/studio')
    def studio():
        path = Path(__file__).resolve().parents[2] / 'frontend' / 'dist' / 'index.html'
        if not path.exists():
            raise HTTPException(503, '前端尚未构建')
        return FileResponse(path, headers={'Cache-Control': 'no-cache'})

    @router.get('/api/studio')
    def bootstrap(request: Request):
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            accounts = db.scalars(
                select(DouyinAccount)
                .where(DouyinAccount.owner_user_id == user.id)
                .order_by(DouyinAccount.created_at.desc(), DouyinAccount.id)
                .limit(STUDIO_BOOTSTRAP_PAGE_SIZE)
            ).all()
            tasks = db.scalars(
                select(SparkTask)
                .where(SparkTask.owner_user_id == user.id)
                .order_by(SparkTask.send_time, SparkTask.target_name, SparkTask.id)
                .limit(STUDIO_BOOTSTRAP_PAGE_SIZE)
            ).all()
            account_total = int(
                db.scalar(
                    select(func.count(DouyinAccount.id)).where(
                        DouyinAccount.owner_user_id == user.id
                    )
                )
                or 0
            )
            task_total = int(
                db.scalar(
                    select(func.count(SparkTask.id)).where(
                        SparkTask.owner_user_id == user.id
                    )
                )
                or 0
            )
            task_ids = [task.id for task in tasks]
            identities = {
                identity.task_id: identity
                for identity in db.scalars(
                    select(SparkTaskTargetIdentity).where(
                        SparkTaskTargetIdentity.task_id.in_(task_ids)
                    )
                ).all()
            } if task_ids else {}
            health = views(db, accounts)
            avatars = task_avatar_map(db, tasks)
            today = datetime.now(ZoneInfo('Asia/Shanghai')).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
            # Keep the lightweight bootstrap bounded.  Complete history is
            # served by /api/studio/runs, which is paginated; returning every
            # run since midnight made this response grow with the user's task
            # count and could exhaust the web process for large accounts.
            runs = db.scalars(
                select(TaskRun)
                .join(SparkTask)
                .where(
                    SparkTask.owner_user_id == user.id,
                    TaskRun.scheduled_for >= today,
                )
                .order_by(TaskRun.scheduled_for.desc(), TaskRun.id.desc())
                .limit(200)
            ).all()
            latest = {}
            for run in runs:
                latest.setdefault(run.task_id, run.status)
            membership = membership_summary(db, user)
            return {'user': {'id': user.id, 'name': user.username, 'role': user.role}, 'csrf': record.csrf_token,
                    'resource_limits': {'page_size': STUDIO_BOOTSTRAP_PAGE_SIZE, 'accounts_total': account_total,
                                        'tasks_total': task_total, 'accounts_truncated': account_total > len(accounts),
                                        'tasks_truncated': task_total > len(tasks)},
                    'accounts': [{'id': a.id, 'name': a.display_name, 'health': health[a.id]} for a in accounts],
                    'tasks': [{'id': t.id, 'name': t.target_name, 'accountId': t.douyin_account_id, 'sec_uid': (identity.sec_uid if (identity := identities.get(t.id)) else ''),
                               'time': t.send_time, 'kind': '续火花表情' if t.message_kind == 'douyin_sticker' else '文字消息',
                               'message': t.message_template, 'enabled': t.enabled, 'avatar': avatars.get(t.id),
                               'confirmed': latest.get(t.id) == 'success', 'status': latest.get(t.id, 'scheduled')} for t in tasks],
                    'subscription': {k: membership[k] for k in (
                        'active','status_label','days_remaining','max_accounts','max_tasks',
                        'can_add_task','task_count','enabled_task_count','paused_task_count',
                        'task_overage','pending_max_tasks','pending_valid_from',
                    )}}

    @router.get('/api/studio/schedule')
    def schedule(request: Request, account_id: str = '', count: int = Query(1, ge=1, le=100), preferred: str = Query('09:30', max_length=5), mode: Literal['auto', 'manual'] = 'manual'):
        import re
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', preferred):
            raise HTTPException(400, '请选择发送时间')
        from spark_console.schedule_slots import plan as plan_slots
        with session_scope(engine) as db:
            user, _ = auth.current(request, db)
            owned = None
            if account_id:
                owned = db.scalar(select(DouyinAccount).where(DouyinAccount.id == account_id, DouyinAccount.owner_user_id == user.id))
                if owned is None:
                    raise HTTPException(404, '账号不存在')
            planned = plan_slots(db, owned.id if owned else None, count, preferred, mode=mode)
            return planned

    @router.post('/api/studio/tasks')
    def create(payload: NewTasks, request: Request):
        with session_scope(engine) as db:
            if engine.dialect.name == 'sqlite':
                # Serialize the preview check and allocation with other writers.
                # The lock is held only for this small bounded DB transaction.
                db.execute(text('BEGIN IMMEDIATE'))
            user, record = auth.current(request, db)
            auth.csrf(record, request.headers.get('X-CSRF-Token', ''))
            audit = AuditService(db)
            service = TaskService(db, AccountService(db, cipher, audit), audit)
            try:
                targets = service.normalize_targets((t.name,t.sec_uid) for t in payload.targets)
                if user.role != 'admin':
                    SubscriptionService(db,audit).require_task_capacity(user.id,len(targets))
                tasks = service.create_many(
                    user.id,payload.account_id,targets,payload.send_time,payload.message_template,payload.message_kind,
                    schedule_mode=payload.schedule_mode, expected_times=payload.expected_times,
                    expected_next_runs=payload.expected_next_runs,
                )
                avatars = task_avatar_map(db, tasks)
                today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
                entries = [
                    {'index': index, 'time': task.send_time,
                     'next_run_at': task.next_run_at.isoformat(),
                     'next_run_local': task.next_run_at.astimezone(ZoneInfo('Asia/Shanghai')).isoformat(),
                     'day_offset': (task.next_run_at.astimezone(ZoneInfo('Asia/Shanghai')).date() - today).days}
                    for index, task in enumerate(tasks)
                ]
                return {
                    'ids': [t.id for t in tasks], 'times': [t.send_time for t in tasks],
                    'count': len(tasks), 'entries': entries,
                    'items': [{'id': t.id, 'name': t.target_name, 'accountId': t.douyin_account_id,
                               'sec_uid': stable_uid, 'time': t.send_time,
                               'kind': '续火花表情' if t.message_kind == 'douyin_sticker' else '文字消息',
                               'message': t.message_template, 'enabled': t.enabled, 'avatar': avatars.get(t.id),
                               'confirmed': False, 'status': 'scheduled'}
                              for t, (_name, stable_uid) in zip(tasks, targets, strict=True)],
                }
            except NotFound:
                raise HTTPException(404, '账号不存在')
            except ValidationError as error:
                raise HTTPException(400, str(error))
            except ScheduleChanged as error:
                raise HTTPException(409, {'code': 'schedule_changed', 'message': str(error), 'plan': error.planned})
            except Conflict as error:
                raise HTTPException(409, str(error))

    @router.post('/api/studio/tasks/{task_id}/enabled')
    def set_enabled(task_id: str, payload: Enabled, request: Request):
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            auth.csrf(record, request.headers.get('X-CSRF-Token',''))
            audit = AuditService(db)
            try:
                owned=db.scalar(select(SparkTask).where(SparkTask.id==task_id,SparkTask.owner_user_id==user.id))
                if not owned:raise NotFound('任务不存在')
                if db.scalar(select(TaskRun.id).where(TaskRun.task_id==task_id,TaskRun.status=='running').limit(1)):raise Conflict('任务正在执行，请稍后修改')
                TaskService(db,AccountService(db,cipher,audit),audit).set_enabled_owned(
                    user.id,
                    task_id,
                    payload.enabled,
                    enforce_capacity=user.role != 'admin',
                )
            except NotFound:
                raise HTTPException(404, '任务不存在')
            except ValidationError as error:
                raise HTTPException(400, str(error))
            except Conflict as error:
                raise HTTPException(409, str(error))
        return {'saved': True}

    @router.get('/api/studio/runs')
    def runs(request: Request, page: int = Query(1,ge=1), q: str = Query('',max_length=100), status: str = ''):
        from spark_console.web.app import run_view_status, run_guidance, shanghai_time, RUN_STAGE_LABELS
        from spark_console.run_attempts import for_runs as attempts_for_runs
        from spark_console.run_events import for_runs as events_for_runs
        with session_scope(engine) as db:
            user,_ = auth.current(request,db)
            criteria=[SparkTask.owner_user_id==user.id]
            if q: criteria.append(SparkTask.target_name.contains(q,autoescape=True))
            if status:
                if status not in ('success','failed','submitted_unconfirmed','running','skipped'):
                    raise HTTPException(400,'记录状态无效')
                criteria.append(TaskRun.status==status)
            total=db.scalar(select(func.count()).select_from(TaskRun).join(SparkTask).where(*criteria)) or 0
            pages=max(1,(total+11)//12);page=min(page,pages)
            rows=db.execute(select(TaskRun,SparkTask).join(SparkTask).where(*criteria).order_by(TaskRun.scheduled_for.desc(),TaskRun.id.desc()).offset((page-1)*12).limit(12)).all()
            attempts = attempts_for_runs(db, [run.id for run, _task in rows])
            events = events_for_runs(db, [run.id for run, _task in rows])
            def timeline_for(run):
                return [{'sequence': event.sequence, 'event': event.event,
                    'stage': RUN_STAGE_LABELS.get(event.stage, event.stage),
                    'occurred_at': shanghai_time(event.occurred_at),
                    'detail': event.detail} for event in events.get(run.id, [])]
            return {'total':total,'page':page,'pages':pages,'items':[{'id':run.id,'name':task.target_name,
                'time':shanghai_time(run.scheduled_for),'status':run.status,'label':run_view_status(run)['label'],
                'stage':RUN_STAGE_LABELS.get(run.stage,run.stage),'guidance':run_guidance(run),
                'error':run.error_summary,'kind':'续火花表情' if task.message_kind=='douyin_sticker' else '文字消息',
                'attempts':[{'number': attempt.number, 'status': attempt.status,
                    'stage': RUN_STAGE_LABELS.get(attempt.stage, attempt.stage), 'reason': attempt.reason,
                    'started_at': shanghai_time(attempt.started_at),
                    'finished_at': shanghai_time(attempt.finished_at) if attempt.finished_at else None}
                    for attempt in attempts.get(run.id, [])],
                'timeline': timeline_for(run)} for run,task in rows]}

    return router
