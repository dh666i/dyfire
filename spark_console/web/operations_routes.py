from datetime import timezone
from fastapi import APIRouter,Request,HTTPException
from sqlalchemy import select,func
from spark_console.db import session_scope
from spark_console.models import Announcement,AnnouncementRead,PaymentRecord,User,utc_now
from spark_console.services import ValidationError,NotFound,Conflict
from spark_console.services import operations
from spark_console.services.audits import AuditService


def build_operations_router(engine,auth):
    router=APIRouter()

    async def body(request):
        try:value=await request.json()
        except (ValueError,TypeError):raise HTTPException(400,'请求格式不正确') from None
        if not isinstance(value,dict):raise HTTPException(400,'请求格式不正确')
        return value

    @router.get('/api/announcements')
    def customer_announcements(request:Request):
        with session_scope(engine) as db:
            user,_=auth.current(request,db)
            return {'items':operations.announcements(db,user.id)}

    @router.post('/api/announcements/{identifier}/read')
    async def read_announcement(request:Request,identifier:str):
        values=await body(request)
        with session_scope(engine) as db:
            user,session=auth.current(request,db);auth.csrf(session,values.get('csrf_token'))
            if not any(row['id']==identifier for row in operations.announcements(db,user.id)):
                raise HTTPException(404,'公告不存在或已过期')
            db.merge(AnnouncementRead(announcement_id=identifier,user_id=user.id))
            return {'ok':True}

    @router.delete('/api/admin/announcements/{identifier}')
    async def withdraw_announcement(request:Request,identifier:str):
        values=await body(request)
        with session_scope(engine) as db:
            user,session,_=auth.admin_context(request,db);auth.csrf(session,values.get('csrf_token'))
            row=db.get(Announcement,identifier)
            if row is None:raise HTTPException(404,'公告不存在')
            row.withdrawn_at=utc_now();AuditService(db).write(user.id,'announcement.withdrawn','announcement',row.id)
            return {'ok':True}

    def handler(section):
        async def manage(request:Request):
            values=await body(request) if request.method=='POST' else {}
            with session_scope(engine) as db:
                actor,session,_=auth.admin_context(request,db)
                if request.method=='POST':
                    auth.csrf(session,values.get('csrf_token'))
                    try:
                        if section=='announcements':return {'ok':True,'id':operations.publish(db,actor,values).id}
                        if section=='payments':return {'ok':True,'id':operations.record_payment(db,actor,values).id}
                        if section=='costs':return {'ok':True,'values':operations.save_config(db,actor,values)}
                    except NotFound as error:raise HTTPException(404,str(error)) from error
                    except (ValidationError,Conflict) as error:raise HTTPException(400,str(error)) from error
                if section=='announcements':return {'items':operations.announcements(db,actor.id,admin=True)}
                if section=='costs':return operations.business_report(db)
                query=select(PaymentRecord,User.username).join(User,PaymentRecord.user_id==User.id)
                target=request.query_params.get('user_id')
                if target:query=query.where(PaymentRecord.user_id==target)
                try:page=max(1,int(request.query_params.get('page','1')))
                except ValueError:raise HTTPException(400,'页码不正确') from None
                total=db.scalar(select(func.count()).select_from(query.subquery())) or 0
                rows=db.execute(query.order_by(PaymentRecord.created_at.desc(),PaymentRecord.id).offset((page-1)*30).limit(30)).all()
                return {'items':[{'id':r.id,'user_id':r.user_id,'username':name,'source':r.source,'amount_yuan':f'{r.amount_cents/100:.2f}',
                    'reference':r.reference,'note':r.note,'grant_id':r.grant_id,'created_at':r.created_at.replace(tzinfo=timezone.utc).isoformat()} for r,name in rows],
                    'pagination':{'page':page,'total':total,'pages':max(1,(total+29)//30),'has_next':page*30<total,'has_previous':page>1}}
        return manage

    for section in ('announcements','payments','costs'):
        router.add_api_route('/api/admin/'+section,handler(section),methods=['GET','POST'],name='manage_'+section)
    return router
