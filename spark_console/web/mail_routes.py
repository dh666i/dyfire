from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from spark_console.db import session_scope
from spark_console.models import MailDelivery, MailPreference, MailSettings, uuid_string, NotificationPolicy
from spark_console.services import mail
from spark_console.mail_outbox import seal_body, QUEUED_STATUSES
from spark_console.services.audits import AuditService


def build_mail_router(engine,auth,cipher,page):
    router=APIRouter()

    @router.get('/admin/settings/mail')
    def settings(request:Request):
        with session_scope(engine) as db:
            _,_,context=auth.admin_context(request,db)
            config=mail.load_config(db,cipher)
            safe={k:v for k,v in config.items() if k!='password'}
            return page(request,'admin_mail.html',title='邮件设置',admin_section='settings',
                        mail_config=safe,password_configured=bool(config['password']),events=mail.EVENTS,
                        deliveries=db.scalars(select(MailDelivery).order_by(MailDelivery.created_at.desc()).limit(30)).all(),**context)

    @router.post('/admin/settings/mail/{action}')
    async def settings_action(request:Request,action:str):
        values=await request.json()
        with session_scope(engine) as db:
            user,record,_=auth.admin_context(request,db); auth.csrf(record,values.get('csrf_token',''))
            try:
                if action=='save':
                    mail.save_config(db,cipher,values)
                    AuditService(db).write(user.id,'mail.settings.updated','mail_settings','1')
                    result={'message':'邮件设置已保存，无需重启。'}
                elif action in ('template','restore','preview'):
                    event=values.get('event')
                    if event not in mail.EVENTS: raise ValueError('未知通知类型')
                    template=mail.DEFAULTS['templates'][event] if action=='restore' else values
                    if action=='preview': return JSONResponse({'html':mail.preview(str(template.get('body',''))[:10000])})
                    mail.store_template(db,cipher,event,template['subject'],template['body'])
                    result={'message':'模板已更新','template':template}
                elif action in ('test','send-test'):
                    # Resolve draft settings without writing or holding a write lock during SMTP.
                    config=mail.save_config(db,cipher,values,persist=False)
                    mail.validate(config)
                    if action=='test':
                        def test_connection():
                            client=mail.connect(config); client.close()
                        await run_in_threadpool(test_connection)
                        result={'message':'连接和认证成功；这不代表邮件已经送达。'}
                    else:
                        from spark_console.mail_worker import read_health
                        if not read_health(request.app.state.settings.data_dir)['online']:
                            raise HTTPException(503,'邮件投递服务暂未连接，请稍后重试')
                        # Queue uses persisted configuration, avoiding secret copies in jobs.
                        persisted=mail.load_config(db,cipher)
                        if config!=persisted: raise ValueError('请先保存当前配置，再发送测试邮件')
                        if not persisted['enabled']: raise ValueError('请先启用并保存邮件服务')
                        recipient=mail.email_address(values.get('recipient',''))
                        recent=db.scalar(select(MailDelivery.id).where(MailDelivery.event=='test',MailDelivery.created_at>datetime.now(timezone.utc)-timedelta(minutes=1)))
                        if recent: raise ValueError('测试邮件每分钟最多一封，请查看发送记录')
                        db.add(seal_body(MailDelivery(event_key=uuid_string(),user_id=user.id,event='test',recipient=recipient,subject='火花守护 · 测试邮件',body='这是一封测试邮件，用于确认邮件配置。'),cipher))
                        result={'message':'测试邮件已排队，约一分钟内处理；请在发送记录查看结果。'}
                else: raise HTTPException(404)
            except ValueError as error: return JSONResponse({'message':str(error)},status_code=400)
            except (OSError,mail.smtplib.SMTPException):
                return JSONResponse({'message':'连接或认证失败，请核对主机、端口、加密方式及 SMTP 授权码。'},status_code=400)
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    @router.get('/notifications')
    def preferences(request:Request):
        with session_scope(engine) as db:
            user,_,context=auth.user_context(request,db)
            return page(request,'notifications.html',title='通知设置',pref=db.get(MailPreference,user.id),
                        policy=db.get(NotificationPolicy,user.id),
                        mail_ready=mail.load_config(db,cipher)['enabled'],**context)

    @router.post('/notifications/{action}')
    async def preferences_action(request:Request,action:str):
        values=await request.json()
        with session_scope(engine) as db:
            user,record,_=auth.user_context(request,db); auth.csrf(record,values.get('csrf_token',''))
            pref=db.get(MailPreference,user.id)
            if not pref: pref=MailPreference(user_id=user.id); db.add(pref); db.flush()
            try:
                if action=='save':
                    email=mail.email_address(values.get('email',''))
                    if email!=pref.email:
                        pref.email=email; pref.verified=False; pref.enabled=False; pref.code_hash=None
                    else: pref.enabled=values.get('enabled') is True and pref.verified
                    message='接收设置已保存。新邮箱须验证后才能接收通知。'
                elif action=='policy':
                    events=values.get('events',[])
                    if not isinstance(events,list) or any(e not in mail.EVENTS for e in events):raise ValueError('通知类型不正确')
                    start,end,interval=int(values.get('quiet_start',23)),int(values.get('quiet_end',8)),int(values.get('interval_minutes',60))
                    if not 0<=start<=23 or not 0<=end<=23 or interval not in (15,60,180):raise ValueError('免打扰时间或频率不正确')
                    if start==end and values.get('quiet_enabled'):raise ValueError('开始与结束时间不能相同')
                    db.merge(NotificationPolicy(user_id=user.id,events=','.join(events),quiet_start=start,quiet_end=end,quiet_enabled=values.get('quiet_enabled') is True,interval_minutes=interval))
                    message='通知偏好已保存，验证邮件不受免打扰限制。'
                elif action=='verify-send':
                    if not mail.load_config(db,cipher)['enabled']: raise ValueError('管理员尚未启用邮件服务')
                    from spark_console.mail_worker import read_health
                    if not read_health(request.app.state.settings.data_dir)['online']:
                        raise HTTPException(503,'邮件投递服务暂时不可用，请稍后重试')
                    if not pref.email: raise ValueError('请先保存接收邮箱')
                    now=datetime.now(timezone.utc)
                    recent=db.scalar(select(MailDelivery.id).where(MailDelivery.user_id==user.id,MailDelivery.event=='verify',MailDelivery.created_at>now-timedelta(minutes=1)))
                    if recent: raise ValueError('请等待 60 秒后再发送')
                    if pref.code_until and mail.aware(pref.code_until)>now+timedelta(minutes=9): raise ValueError('请等待 60 秒后再发送')
                    code=f'{secrets.randbelow(1000000):06d}'
                    pref.code_hash=cipher.digest('mail-verify',user.id+'\0'+code);pref.code_version=2; pref.code_until=now+timedelta(minutes=10); pref.attempts=0
                    db.add(seal_body(MailDelivery(event_key=uuid_string(),user_id=user.id,recipient=pref.email,event='verify',subject='火花守护 · 验证接收邮箱',body=f'你的验证码是 {code}，10 分钟内有效。如非本人操作，请忽略。'),cipher))
                    message='验证邮件已排队，请在 10 分钟内填写验证码。'
                elif action=='verify':
                    if not pref.code_hash or not pref.code_until or mail.aware(pref.code_until)<datetime.now(timezone.utc) or pref.attempts>=5: raise ValueError('验证码过期或尝试次数已用完，请重新获取')
                    pref.attempts+=1
                    code=str(values.get('code','')).strip()
                    supplied=cipher.digest('mail-verify',user.id+'\0'+code) if pref.code_version==2 else hashlib.sha256((user.id+code).encode()).hexdigest()
                    if not secrets.compare_digest(supplied,pref.code_hash): raise ValueError('验证码不正确')
                    pref.verified=True; pref.enabled=True; pref.code_hash=None
                    message='邮箱验证成功，已开启账号异常、恢复与到期提醒。'
                else: raise HTTPException(404)
            except ValueError as error: return JSONResponse({'message':str(error)},status_code=400)
        return JSONResponse({'message':message})
    return router
