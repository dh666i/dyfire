from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import re
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from spark_console.db import session_scope
from spark_console.models import SparkTask, SparkTaskTargetIdentity, TaskRun
from spark_console.services import ValidationError, Conflict, NotFound
from spark_console.services.tasks import TaskService
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.services.subscriptions import SubscriptionService


def build_reliability_router(engine,auth,cipher):
    router=APIRouter()
    @router.post('/tasks/batch')
    async def batch(request:Request):
        data=await request.json()
        with session_scope(engine) as db:
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
                    results.append({'id':identifier,'name':name,'ok':True})
                except (ValidationError,Conflict,NotFound) as error:
                    results.append({'id':identifier,'name':name,'ok':False,'error':str(error)})
            return JSONResponse({'results':results})
    return router
