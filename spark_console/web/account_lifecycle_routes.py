from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, text, update

from spark_console.db import session_scope
from spark_console.models import SparkTask, TaskRun
from spark_console.services import Conflict, NotFound
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService


def build_account_lifecycle_router(engine,auth,cipher):
    router=APIRouter()

    async def change(request,account_id,unlink):
        try:values=await request.json()
        except (ValueError,TypeError):raise HTTPException(400,'请求格式无效') from None
        if not isinstance(values,dict):raise HTTPException(400,'请求格式无效')
        with session_scope(engine) as db:
            if engine.dialect.name=='sqlite':db.execute(text('BEGIN IMMEDIATE'))
            user,session=auth.current(request,db);auth.csrf(session,values.get('csrf_token'))
            service=AccountService(db,cipher,AuditService(db))
            try:
                account=service.get_owned(user.id,account_id)
                if engine.dialect.name!='sqlite':
                    from spark_console.models import DouyinAccount
                    db.execute(select(DouyinAccount.id).where(DouyinAccount.id==account_id).with_for_update())
                running=db.scalar(select(TaskRun.id).join(SparkTask,TaskRun.task_id==SparkTask.id)
                    .where(SparkTask.douyin_account_id==account_id,TaskRun.status=='running').limit(1))
                if running:raise Conflict('该账号有任务正在执行，结束后可停止托管或解绑')
                if unlink:
                    service.delete_owned(user.id,account_id)
                    return {'ok':True,'message':'已解除绑定并清除登录凭据和好友缓存，相关任务已暂停'}
                result=db.execute(update(SparkTask).where(SparkTask.douyin_account_id==account.id,SparkTask.enabled.is_(True)).values(enabled=False))
                AuditService(db).write(user.id,'account.hosting_stopped','douyin_account',account_id)
                return {'ok':True,'paused_tasks':result.rowcount,'message':'已停止该账号的全部续火任务，登录资料保留；可在任务页手动恢复'}
            except NotFound:raise HTTPException(404,'账号不存在') from None
            except Conflict as error:raise HTTPException(409,str(error)) from None

    @router.post('/api/accounts/{account_id}/stop-hosting')
    async def stop(request:Request,account_id:str):return await change(request,account_id,False)

    @router.delete('/api/accounts/{account_id}')
    async def unlink(request:Request,account_id:str):return await change(request,account_id,True)
    return router
