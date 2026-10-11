from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select, delete, func
from spark_console.db import session_scope
from spark_console.models import User, DouyinAccount, SparkTask, TaskRun, WebSession, AuditEvent, SubscriptionGrant
from spark_console.services.account_health import views as health_views
from spark_console.services.audits import AuditService
from spark_console.web.membership import membership_summary
from spark_console.services.subscriptions import pricing_plans


PRODUCT_SESSION_PAGE_SIZE = 100
PRODUCT_ACCOUNT_PAGE_SIZE = 100


def build_product_router(engine,auth,page):
    router=APIRouter()

    @router.get('/api/attention')
    def attention_json(request:Request):
        from spark_console.web.inbox import entries
        with session_scope(engine) as db:
            user,record=auth.current(request,db)
            return {'csrf':record.csrf_token,'items':entries(db,user.id)}

    @router.post('/api/attention/read')
    async def read_attention_json(request:Request):
        from spark_console.web.inbox import entries
        values=await request.json()
        with session_scope(engine) as db:
            user,record=auth.current(request,db);auth.csrf(record,values.get('csrf_token',''))
            items=entries(db,user.id);item_id=str(values.get('item_id') or '')
            if item_id!='all' and not any(i['id']==item_id for i in items):raise HTTPException(404,'通知不存在')
            for item in items:
                if not item['read'] and (item_id=='all' or item['id']==item_id):
                    AuditService(db).write(user.id,'inbox.read','notification',item['id'])
            return {'ok':True,'items':entries(db,user.id)}

    @router.get('/api/account-center')
    def account_center_json(request:Request):
        with session_scope(engine) as db:
            user,record=auth.current(request,db)
            sessions=db.scalars(select(WebSession).where(WebSession.user_id==user.id,WebSession.expires_at>datetime.now(timezone.utc)).order_by(WebSession.created_at.desc()).limit(PRODUCT_SESSION_PAGE_SIZE)).all()
            return {'csrf':record.csrf_token,'current_session_id':record.id,'sessions':[{'id':item.id,'created_at':item.created_at,'expires_at':item.expires_at,'current':item.id==record.id} for item in sessions]}

    @router.post('/api/account-center/revoke-sessions')
    async def revoke_sessions_json(request:Request):
        values=await request.json()
        with session_scope(engine) as db:
            user,record=auth.current(request,db);auth.csrf(record,values.get('csrf_token',''))
            db.execute(delete(WebSession).where(WebSession.user_id==user.id,WebSession.id!=record.id))
            AuditService(db).write(user.id,'sessions.revoked','user',user.id)
            return {'ok':True,'message':'其他登录会话已退出。'}

    @router.get('/api/subscription')
    def subscription_json(request:Request):
        with session_scope(engine) as db:
            user,record=auth.current(request,db)
            summary=membership_summary(db,user)
            grants=db.scalars(select(SubscriptionGrant).where(SubscriptionGrant.user_id==user.id).order_by(SubscriptionGrant.created_at.desc()).limit(20)).all()
            return {'csrf':record.csrf_token,'membership':{key:summary.get(key) for key in ('active','status','status_label','valid_until','days_remaining','max_accounts','max_tasks','account_count','task_count','enabled_task_count','paused_task_count','task_overage','over_limit_task_count','over_limit_enabled_tasks','task_capacity_status','task_capacity_label','task_capacity_message','pending_subscription','pending_plan','task_limit_label','can_add_account','can_add_task','free_tier','free_tier_label')},'plans':pricing_plans(),'grants':[{'id':item.id,'source':item.source,'valid_from':item.valid_from,'valid_until':item.valid_until,'created_at':item.created_at} for item in grants]}

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
            accounts=db.scalars(select(DouyinAccount).where(
                DouyinAccount.owner_user_id==user.id,
                DouyinAccount.validation_state.in_(['temporary','verification','invalid']),
            ).order_by(DouyinAccount.updated_at.desc(), DouyinAccount.id)
                .limit(PRODUCT_ACCOUNT_PAGE_SIZE)).all()
            runs=db.execute(select(TaskRun,SparkTask).join(SparkTask).where(SparkTask.owner_user_id==user.id,TaskRun.status.in_(['failed','submitted_unconfirmed']),TaskRun.scheduled_for>=datetime.now(timezone.utc)-timedelta(days=7)).order_by(TaskRun.scheduled_for.desc()).limit(50)).all()
            from spark_console.web.inbox import entries
            return page(request,'attention.html',title='待处理事项',accounts=accounts,account_health=health_views(db,accounts),runs=runs,inbox=entries(db,user.id),**context)

    @router.get('/account-center')
    def account_center(request:Request):
        with session_scope(engine) as db:
            user,record,context=auth.user_context(request,db)
            sessions=db.scalars(select(WebSession).where(WebSession.user_id==user.id,WebSession.expires_at>datetime.now(timezone.utc)).order_by(WebSession.created_at.desc()).limit(PRODUCT_SESSION_PAGE_SIZE)).all()
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
            accounts=db.scalars(select(DouyinAccount).where(DouyinAccount.owner_user_id==user_id)
                .order_by(DouyinAccount.created_at, DouyinAccount.id)
                .limit(PRODUCT_ACCOUNT_PAGE_SIZE)).all()
            tasks=db.scalars(select(SparkTask).where(SparkTask.owner_user_id==user_id).order_by(SparkTask.send_time).limit(100)).all()
            grants=db.scalars(select(SubscriptionGrant).where(SubscriptionGrant.user_id==user_id).order_by(SubscriptionGrant.created_at.desc()).limit(20)).all()
            audits=db.scalars(select(AuditEvent).where(AuditEvent.actor_user_id==user_id).order_by(AuditEvent.created_at.desc()).limit(20)).all()
            from spark_console.web.membership import membership_summary
            return page(request,'admin_customer.html',title='客户详情',admin_section='users',customer=customer,accounts=accounts,tasks=tasks,grants=grants,audits=audits,customer_membership=membership_summary(db,customer),**context)
    return router
