from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import re
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from spark_console.db import session_scope
from spark_console.models import SparkTask, SparkTaskTargetIdentity, TaskRun, DouyinAccount, DouyinContactIdentity
from spark_console.services import ValidationError, Conflict, NotFound
from spark_console.services.tasks import TaskService
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.services.subscriptions import SubscriptionService


def build_reliability_router(engine,auth,cipher):
    router=APIRouter()
    @router.get('/admin/diagnostics')
    def diagnostics(request:Request):
        from pathlib import Path
        import json,os
        from spark_console.models import DouyinLoginSession,ContactDirectoryJob
        with session_scope(engine) as db:
            auth.admin_context(request,db)
            rows=db.execute(select(TaskRun,SparkTask).join(SparkTask).order_by(TaskRun.scheduled_for.desc()).limit(30)).all()
            def seconds(a,b):return round(max(0,(b-a).total_seconds()),1) if a and b else None
            items=[{
                'id': r.id,
                'name': t.target_name,
                'status': r.status,
                'stage': r.stage,
                'attempt': r.attempt,
                'error_code': r.error_code,
                'error_summary': r.error_summary,
                'queue': seconds(r.scheduled_for, r.started_at),
                'before_submit': seconds(r.started_at, r.submitted_at),
                'confirmation': seconds(r.submitted_at, r.finished_at),
                'total': seconds(r.started_at, r.finished_at),
            } for r,t in rows]
            backup={'status':'unconfigured','message':'尚未配置备份任务'}
            try:
                historical=json.loads((Path(os.environ.get('DYFIRE_UPDATE_DIR','/run/dyfire-updates'))/'backup-status.json').read_text())
                if isinstance(historical,dict):backup=historical
            except (OSError,ValueError):pass
            schedule={'enabled':None}
            try:
                configured=json.loads((Path(os.environ.get('DYFIRE_UPDATE_DIR','/run/dyfire-updates'))/'backup-schedule.json').read_text())
                if isinstance(configured,dict) and isinstance(configured.get('enabled'),bool):schedule=configured
            except (OSError,ValueError):pass
            backup['schedule_enabled']=schedule['enabled']
            return JSONResponse({'items':items,'backup':backup},headers={'Cache-Control':'no-store'})
    @router.post('/tasks/preview')
    async def preview(request:Request):
        data=await request.json()
        if not isinstance(data,dict):raise HTTPException(400,'请求格式不正确')
        with session_scope(engine) as db:
            user,record=auth.current(request,db);auth.csrf(record,data.get('csrf_token',''))
            service=TaskService(db,AccountService(db,cipher,AuditService(db)),AuditService(db))
            try:
                account=service.accounts.get_owned(user.id,str(data.get('account_id','')))
                raw=data.get('targets',[])
                if not isinstance(raw,list) or len(raw)>100 or any(not isinstance(t,dict) for t in raw):raise ValidationError('请选择 1–100 位好友')
                targets=service.normalize_targets((t.get('name'),t.get('sec_uid')) for t in raw)
                send_time=str(data.get('time',''))
                if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',send_time):raise ValidationError('请选择发送时间')
                if user.role!='admin':SubscriptionService(db,AuditService(db)).require_task_capacity(user.id,len(targets))
                for name,uid in targets:
                    if uid and not db.get(DouyinContactIdentity,(account.id,uid)):raise ValidationError('所选好友不属于当前账号')
                from spark_console.schedule_slots import plan as plan_slots
                planned=plan_slots(db,account.id,len(targets),send_time)
                times=list(planned['times'])
                existing=list(db.execute(
                    select(SparkTask.target_name, SparkTaskTargetIdentity.sec_uid)
                    .outerjoin(SparkTaskTargetIdentity, SparkTaskTargetIdentity.task_id == SparkTask.id)
                    .where(SparkTask.douyin_account_id == account.id)
                ).all())
                conflicts=[name for name,uid in targets if service._has_target_conflict(existing,name,uid)]
                return JSONResponse({'account':account.display_name,'count':len(targets),'time':send_time,'times':times,'suggested':planned['suggested'],'load':planned['load'],'gap_minutes':planned['gap_minutes'],'conflicts':conflicts,'blocked':account.validation_state in ('invalid','verification','temporary'),'manual_count':sum(not uid for _,uid in targets)},headers={'Cache-Control':'no-store'})
            except NotFound:raise HTTPException(404,'账号不存在')
            except (ValidationError,Conflict) as error:raise HTTPException(400,str(error))

    @router.post('/tasks/batch')
    async def batch(request:Request):
        data=await request.json()
        if not isinstance(data,dict):raise HTTPException(400,'请求格式不正确')
        with session_scope(engine) as db:
            if engine.dialect.name=='sqlite':db.execute(text('BEGIN IMMEDIATE'))
            user,record=auth.current(request,db);auth.csrf(record,data.get('csrf_token',''))
            identifiers=data.get('ids',[])
            if not isinstance(identifiers,list) or not identifiers or len(identifiers)>100 or any(not isinstance(i,str) for i in identifiers): raise HTTPException(400,'请选择 1–100 个任务')
            identifiers=list(dict.fromkeys(identifiers))
            tasks=db.scalars(select(SparkTask).where(SparkTask.id.in_(identifiers),SparkTask.owner_user_id==user.id)).all()
            if len(tasks)!=len(identifiers): raise HTTPException(404,'部分任务不存在')
            action=data.get('action')
            if action not in ('pause','enable','time'): raise HTTPException(400,'不支持的批量操作')
            send_time=str(data.get('time',''))
            if action=='time' and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',send_time): raise HTTPException(400,'时间格式应为 HH:MM')
            service=TaskService(db,AccountService(db,cipher,AuditService(db)),AuditService(db))
            results=[]
            for task in tasks:
                identifier,name=task.id,task.target_name
                try:
                    with db.begin_nested():
                        running=db.scalar(select(TaskRun.id).where(TaskRun.task_id==task.id,TaskRun.status=='running').limit(1))
                        if running: raise Conflict('任务正在执行，请稍后再修改')
                        if action=='enable' and user.role!='admin': SubscriptionService(db,AuditService(db)).require_active(user.id)
                        if action=='time':
                            binding=db.get(SparkTaskTargetIdentity,task.id)
                            service.update_owned(user.id,task.id,task.douyin_account_id,task.target_name,send_time,task.message_template,binding.sec_uid if binding else None,task.message_kind)
                        else:
                            was_enabled=task.enabled
                            service.set_enabled_owned(user.id,task.id,action=='enable')
                            if action=='enable' and not was_enabled:
                                now=datetime.now(ZoneInfo('Asia/Shanghai'));hour,minute=map(int,task.send_time.split(':'))
                                next_at=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
                                if next_at<=now: next_at+=timedelta(days=1)
                                task.next_run_at=next_at.astimezone(timezone.utc)
                    results.append({'id':identifier,'name':name,'ok':True,'enabled':task.enabled,'time':task.send_time,'next_run_at':task.next_run_at.replace(tzinfo=timezone.utc).isoformat() if task.next_run_at else None})
                except (ValidationError,Conflict,NotFound) as error:
                    results.append({'id':identifier,'name':name,'ok':False,'error':str(error)})
            return JSONResponse({'results':results})
    return router
