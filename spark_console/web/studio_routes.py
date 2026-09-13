from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func

from spark_console.db import session_scope
from spark_console.models import DouyinAccount, SparkTask, TaskRun
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.accounts import AccountService
from spark_console.services.account_health import views
from spark_console.services.audits import AuditService
from spark_console.services.directory import task_avatar_map
from spark_console.services.subscriptions import SubscriptionService
from spark_console.services.tasks import TaskService
from spark_console.web.membership import membership_summary


class Target(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    sec_uid: str = Field(default='', max_length=256)


class NewTasks(BaseModel):
    account_id: str
    targets: list[Target] = Field(min_length=1, max_length=100)
    send_time: str
    message_kind: str = 'douyin_sticker'
    message_template: str = '续火花'


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
            accounts = db.scalars(select(DouyinAccount).where(DouyinAccount.owner_user_id == user.id)).all()
            tasks = db.scalars(select(SparkTask).where(SparkTask.owner_user_id == user.id)).all()
            health = views(db, accounts)
            avatars = task_avatar_map(db, tasks)
            today = datetime.now(ZoneInfo('Asia/Shanghai')).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
            runs = db.scalars(select(TaskRun).join(SparkTask).where(SparkTask.owner_user_id == user.id, TaskRun.scheduled_for >= today).order_by(TaskRun.scheduled_for.desc())).all()
            latest = {}
            for run in runs:
                latest.setdefault(run.task_id, run.status)
            membership = membership_summary(db, user)
            return {'user': {'id': user.id, 'name': user.username, 'role': user.role}, 'csrf': record.csrf_token,
                    'accounts': [{'id': a.id, 'name': a.display_name, 'health': health[a.id]} for a in accounts],
                    'tasks': [{'id': t.id, 'name': t.target_name, 'accountId': t.douyin_account_id,
                               'time': t.send_time, 'kind': '续火花表情' if t.message_kind == 'douyin_sticker' else '文字消息',
                               'message': t.message_template, 'enabled': t.enabled, 'avatar': avatars.get(t.id),
                               'confirmed': latest.get(t.id) == 'success', 'status': latest.get(t.id, 'scheduled')} for t in tasks],
                    'subscription': {k: membership[k] for k in ('active','status_label','days_remaining','max_accounts','max_tasks','can_add_task')}}

    @router.post('/api/studio/tasks')
    def create(payload: NewTasks, request: Request):
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            auth.csrf(record, request.headers.get('X-CSRF-Token', ''))
            audit = AuditService(db)
            service = TaskService(db, AccountService(db, cipher, audit), audit)
            try:
                targets = service.normalize_targets((t.name,t.sec_uid) for t in payload.targets)
                if user.role != 'admin':
                    SubscriptionService(db,audit).require_task_capacity(user.id,len(targets))
                tasks = service.create_many(user.id,payload.account_id,targets,payload.send_time,payload.message_template,payload.message_kind)
                return {'ids': [t.id for t in tasks]}
            except NotFound:
                raise HTTPException(404, '账号不存在')
            except ValidationError as error:
                raise HTTPException(400, str(error))
            except Conflict as error:
                raise HTTPException(409, str(error))

    @router.post('/api/studio/tasks/{task_id}/enabled')
    def set_enabled(task_id: str, payload: Enabled, request: Request):
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            auth.csrf(record, request.headers.get('X-CSRF-Token',''))
            audit = AuditService(db)
            try:
                if payload.enabled and user.role!='admin':SubscriptionService(db,audit).require_active(user.id)
                owned=db.scalar(select(SparkTask).where(SparkTask.id==task_id,SparkTask.owner_user_id==user.id))
                if not owned:raise NotFound('任务不存在')
                if db.scalar(select(TaskRun.id).where(TaskRun.task_id==task_id,TaskRun.status=='running').limit(1)):raise Conflict('任务正在执行，请稍后修改')
                TaskService(db,AccountService(db,cipher,audit),audit).set_enabled_owned(user.id,task_id,payload.enabled)
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
            return {'total':total,'page':page,'pages':pages,'items':[{'id':run.id,'name':task.target_name,
                'time':shanghai_time(run.scheduled_for),'status':run.status,'label':run_view_status(run)['label'],
                'stage':RUN_STAGE_LABELS.get(run.stage,run.stage),'guidance':run_guidance(run),
                'error':run.error_summary,'kind':'续火花表情' if task.message_kind=='douyin_sticker' else '文字消息'} for run,task in rows]}

    return router
