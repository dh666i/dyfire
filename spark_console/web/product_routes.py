from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select, delete, func
from spark_console.db import session_scope
from spark_console.models import User, DouyinAccount, SparkTask, TaskRun, WebSession, AuditEvent, SubscriptionGrant
from spark_console.services.account_health import views as health_views


def build_product_router(engine,auth,page):
    router=APIRouter()

    @router.post('/attention/read')
    def read_attention(request:Request,csrf_token:str=Form(''),item_id:str=Form('')):
        from spark_console.web.inbox import entries
        from spark_console.services.audits import AuditService
        with session_scope(engine) as db:
            user,record=auth.current(request,db);auth.csrf(record,csrf_token)
            items=entries(db,user.id)
            if item_id!='all' and not any(i['id']==item_id for i in items):raise HTTPException(404,'通知不存在')
            for item in items:
                if not item['read'] and (item_id=='all' or item['id']==item_id):AuditService(db).write(user.id,'inbox.read','notification',item['id'])
        return RedirectResponse('/attention',status_code=303)

    @router.get('/attention')
    def attention(request:Request):
        with session_scope(engine) as db:
            user,_,context=auth.user_context(request,db)
            accounts=db.scalars(select(DouyinAccount).where(DouyinAccount.owner_user_id==user.id,DouyinAccount.validation_state.in_(['temporary','verification','invalid']))).all()
            runs=db.execute(select(TaskRun,SparkTask).join(SparkTask).where(SparkTask.owner_user_id==user.id,TaskRun.status.in_(['failed','submitted_unconfirmed']),TaskRun.scheduled_for>=datetime.now(timezone.utc)-timedelta(days=7)).order_by(TaskRun.scheduled_for.desc()).limit(50)).all()
            from spark_console.web.inbox import entries
            return page(request,'attention.html',title='待处理事项',accounts=accounts,account_health=health_views(db,accounts),runs=runs,inbox=entries(db,user.id),**context)

    @router.get('/account-center')
    def account_center(request:Request):
        with session_scope(engine) as db:
            user,record,context=auth.user_context(request,db)
            sessions=db.scalars(select(WebSession).where(WebSession.user_id==user.id,WebSession.expires_at>datetime.now(timezone.utc)).order_by(WebSession.created_at.desc())).all()
            return page(request,'account_center.html',title='账户中心',sessions=sessions,current_session=record.id,**context)

    @router.post('/account-center/revoke-sessions')
    def revoke(request:Request,csrf_token:str=Form('')):
        with session_scope(engine) as db:
            user,record=auth.current(request,db);auth.csrf(record,csrf_token)
            db.execute(delete(WebSession).where(WebSession.user_id==user.id,WebSession.id!=record.id))
            from spark_console.services.audits import AuditService
            AuditService(db).write(user.id,'sessions.revoked','user',user.id)
        return RedirectResponse('/account-center?revoked=1',status_code=303)

    @router.get('/help')
    def help_page(request:Request):
        with session_scope(engine) as db:
            _,_,context=auth.user_context(request,db)
            return page(request,'help.html',title='使用帮助',**context)

    @router.get('/admin/customers/{user_id}')
    def customer(request:Request,user_id:str):
        with session_scope(engine) as db:
            _,_,context=auth.admin_context(request,db)
            customer=db.get(User,user_id)
            if not customer:raise HTTPException(404)
            accounts=db.scalars(select(DouyinAccount).where(DouyinAccount.owner_user_id==user_id)).all()
            tasks=db.scalars(select(SparkTask).where(SparkTask.owner_user_id==user_id).order_by(SparkTask.send_time).limit(100)).all()
            grants=db.scalars(select(SubscriptionGrant).where(SubscriptionGrant.user_id==user_id).order_by(SubscriptionGrant.created_at.desc()).limit(20)).all()
            audits=db.scalars(select(AuditEvent).where(AuditEvent.actor_user_id==user_id).order_by(AuditEvent.created_at.desc()).limit(20)).all()
            from spark_console.web.membership import membership_summary
            return page(request,'admin_customer.html',title='客户详情',admin_section='users',customer=customer,accounts=accounts,tasks=tasks,grants=grants,audits=audits,customer_membership=membership_summary(db,customer),**context)
    return router
