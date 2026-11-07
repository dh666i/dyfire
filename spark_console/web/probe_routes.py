import json
from fastapi import APIRouter,Request,HTTPException
from sqlalchemy import select
from spark_console.db import session_scope
from spark_console.web.contracts import ProbeResponse
from spark_console.models import TaskProbe,TaskRun
from spark_console.services import probes,NotFound,ValidationError,Conflict


def build_probe_router(engine,auth):
    router=APIRouter()
    @router.post('/api/tasks/{identifier}/self-check')
    async def task_probe(request:Request,identifier:str):
        return await enqueue(request,identifier)

    @router.post('/api/runs/{identifier}/reconcile')
    async def reconcile(request:Request,identifier:str):
        with session_scope(engine) as db:
            user,_=auth.current(request,db)
            run=db.get(TaskRun,identifier)
            if run is None:raise HTTPException(404,'记录不存在')
            task_id=run.task_id
        return await enqueue(request,task_id,identifier)

    async def enqueue(request,identifier,run_id=None):
        try:values=await request.json()
        except (ValueError,TypeError):raise HTTPException(400,'请求格式不正确') from None
        if not isinstance(values,dict):raise HTTPException(400,'请求格式不正确')
        with session_scope(engine) as db:
            user,session=auth.current(request,db);auth.csrf(session,values.get('csrf_token'))
            try:row=probes.queue(db,user,identifier,run_id=run_id)
            except NotFound as error:raise HTTPException(404,str(error)) from error
            except (ValidationError,Conflict) as error:raise HTTPException(409,str(error)) from error
            return probes.view(row)

    @router.get('/api/task-probes/{identifier}',response_model=ProbeResponse)
    def status(request:Request,identifier:str):
        with session_scope(engine) as db:
            user,_=auth.current(request,db);row=db.get(TaskProbe,identifier)
            if row is None or (user.role!='admin' and user.id!=row.owner_id):raise HTTPException(404,'自检不存在')
            return probes.view(row)

    @router.get('/api/tasks/{identifier}/checks')
    def history(request:Request,identifier:str):
        from spark_console.models import SparkTask
        with session_scope(engine) as db:
            user,_=auth.current(request,db);task=db.get(SparkTask,identifier)
            if task is None or (user.role!='admin' and user.id!=task.owner_user_id):raise HTTPException(404,'任务不存在')
            rows=db.scalars(select(TaskProbe).where(TaskProbe.task_id==identifier).order_by(TaskProbe.created_at.desc()).limit(10))
            return {'items':[probes.view(row) for row in rows]}
    return router
