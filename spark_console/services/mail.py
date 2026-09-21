from __future__ import annotations

import hashlib
import html
import json
import re
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr

from sqlalchemy import select, update
from spark_console.db import session_scope
from spark_console.models import (MailSettings, MailPreference, MailDelivery, MailAccountState,
                                  DouyinAccount, User, UserSubscription, SparkTask, TaskRun,
                                  uuid_string, NotificationPolicy, PasswordReset)

EVENTS = {'account_alert':'账号需要验证', 'account_recovered':'账号已恢复', 'task_failed':'任务执行异常', 'expiry':'会员即将到期'}
DEFAULTS = {
    'enabled':False, 'host':'', 'port':465, 'mode':'ssl', 'username':'', 'password':'',
    'sender':'', 'sender_name':'火花守护', 'events':list(EVENTS),
    'templates':{key:{'subject':f'火花守护 · {name}',
                      'body':'你好，{username}：\n\n{summary}\n\n请打开火花守护网站查看详情。\n你可以在通知设置中关闭此类邮件。'} for key,name in EVENTS.items()},
}


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def load_config(db, cipher):
    row = db.get(MailSettings, 1)
    if not row:
        return json.loads(json.dumps(DEFAULTS))
    saved = json.loads(cipher.decrypt(row.ciphertext, row.nonce))
    defaults = json.loads(json.dumps(DEFAULTS))
    if not isinstance(saved, dict):
        return defaults
    merged = {**defaults, **saved}
    merged['events'] = [event for event in saved.get('events', defaults['events']) if event in EVENTS]
    merged['templates'] = {**defaults['templates'], **(saved.get('templates') or {})}
    return merged


def email_address(value):
    value = str(value).strip()
    if len(value)>254 or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', value):
        raise ValueError('请输入有效的邮箱地址')
    return value


def validate(config):
    if config['mode'] not in ('ssl','starttls'):
        raise ValueError('请选择 SSL/TLS 或 STARTTLS')
    try: config['port'] = int(config['port'])
    except (TypeError, ValueError): raise ValueError('端口必须为整数')
    if not 1 <= config['port'] <= 65535: raise ValueError('端口范围为 1–65535')
    if not re.fullmatch(r'[A-Za-z0-9.-]{1,253}', config['host']): raise ValueError('SMTP 主机只填写域名，不包含协议或路径')
    config['sender'] = email_address(config['sender'])
    for key in ('username','sender_name'):
        if len(config[key])>254 or '\n' in config[key] or '\r' in config[key]: raise ValueError('发件信息格式不正确')
    if not config['username'] or not config['password']: raise ValueError('请填写 SMTP 用户名和授权码')
    return config


def save_config(db, cipher, values, *, persist=True):
    config = load_config(db,cipher)
    # Saved credentials must not be silently sent to a different host/account.
    changed = any(values.get(k,config[k]).strip()!=config[k] for k in ('host','username'))
    if changed and config['password'] and not values.get('password'):
        raise ValueError('更换 SMTP 主机或用户名后，请重新输入授权码')
    for key in ('host','username','sender','sender_name','mode'):
        if key in values: config[key] = str(values[key]).strip()
    config['port'] = values.get('port',config['port'])
    if values.get('password'): config['password'] = values['password']
    config['enabled'] = values.get('enabled') is True
    config['events'] = [key for key in values.get('events',config['events']) if key in EVENTS]
    if config['host'] or config['enabled']: validate(config)
    if not persist: return config
    payload = cipher.encrypt(json.dumps(config,ensure_ascii=False).encode())
    db.merge(MailSettings(id=1,ciphertext=payload.ciphertext,nonce=payload.nonce))
    return config


def store_template(db,cipher,event,subject,body):
    if event not in EVENTS: raise ValueError('未知通知类型')
    if not subject.strip() or len(subject)>180 or '\n' in subject or '\r' in subject: raise ValueError('主题须为单行，长度 1–180')
    if not body.strip() or len(body)>10000: raise ValueError('正文长度须为 1–10000')
    for text in (subject,body):
        if any(key not in ('username','summary') for key in re.findall(r'\{([^{}]+)\}',text)):
            raise ValueError('仅支持 {username} 和 {summary} 变量')
    config=load_config(db,cipher)
    config['templates'][event]={'subject':subject,'body':body}
    payload=cipher.encrypt(json.dumps(config,ensure_ascii=False).encode())
    db.merge(MailSettings(id=1,ciphertext=payload.ciphertext,nonce=payload.nonce))


def render(text,username,summary):
    return re.sub(r'\{(username|summary)\}',lambda m: username if m[1]=='username' else summary,text)


def preview(body):
    return '<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'"><body style="font:15px/1.8 system-ui;color:#27282b;padding:24px;background:#fafafa"><div style="white-space:pre-wrap">'+html.escape(render(body,'示例用户','你的抖音账号需要重新验证，请在账号页面完成验证。'))+'</div>'


def connect(config):
    validate(config)
    context=ssl.create_default_context()
    client=None
    try:
        if config['mode']=='ssl': client=smtplib.SMTP_SSL(config['host'],config['port'],timeout=12,context=context)
        else:
            client=smtplib.SMTP(config['host'],config['port'],timeout=12)
            client.ehlo(); client.starttls(context=context); client.ehlo()
        client.login(config['username'],config['password'])
        return client
    except Exception:
        if client: client.close()
        raise


def enqueue(db,config,event,key,user,recipient,summary):
    if db.scalar(select(MailDelivery.id).where(MailDelivery.event_key==key)): return
    template=config['templates'][event]
    db.add(MailDelivery(event_key=key,user_id=user.id,recipient=recipient,event=event,
                       subject=render(template['subject'],user.username,summary)[:200],
                       body=render(template['body'],user.username,summary)))


def collect(db,config,now):
    if not config['enabled']: return
    for user,pref in db.execute(select(User,MailPreference).join(MailPreference).where(User.status=='active',MailPreference.enabled.is_(True),MailPreference.verified.is_(True))):
        changes={key:[] for key in ('account_alert','account_recovered')}
        keys={key:[] for key in changes}
        for account in db.scalars(select(DouyinAccount).where(DouyinAccount.owner_user_id==user.id)):
            state=account.validation_state
            if state not in ('valid','invalid','verification'): continue
            previous=db.get(MailAccountState,account.id)
            if previous and previous.state==state: continue
            revision=previous.revision+1 if previous else 1
            event='account_recovered' if state=='valid' else 'account_alert'
            if state!='valid' or previous:
                changes[event].append(account.display_name)
                keys[event].append(f'{account.id}:{revision}')
            db.merge(MailAccountState(account_id=account.id,state=state,revision=revision))
        for event,names in changes.items():
            if names and event in config['events']:
                digest=hashlib.sha256('|'.join(sorted(keys[event])).encode()).hexdigest()
                summary=('以下账号已恢复，已启用的任务将执行后续计划；错过的消息不补发：' if event=='account_recovered' else '以下账号需要你完成安全验证，相关任务暂缓发送：')+'、'.join(names)
                enqueue(db,config,event,f'{event}:{user.id}:{digest}',user,pref.email,summary)
        if 'task_failed' in config['events']:
            failure_since = now - timedelta(hours=24)
            failed = db.execute(
                select(TaskRun, SparkTask)
                .join(SparkTask, TaskRun.task_id == SparkTask.id)
                .where(
                    SparkTask.owner_user_id == user.id,
                    TaskRun.scheduled_for >= failure_since,
                    TaskRun.status.in_(('failed', 'submitted_unconfirmed')),
                )
                .order_by(TaskRun.scheduled_for.desc(), TaskRun.id.desc())
                .limit(20)
            ).all()
            if failed:
                bucket = now.astimezone(timezone.utc).strftime('%Y%m%d%H')
                digest = hashlib.sha256('|'.join(sorted(run.id for run, _task in failed)).encode()).hexdigest()[:16]
                names = '、'.join(dict.fromkeys(task.target_name for _run, task in failed))
                summary = f'近 24 小时有 {len(failed)} 条任务需要关注：{names[:180]}。请打开执行记录查看诊断信息。'
                enqueue(db, config, 'task_failed', f'task_failed:{user.id}:{bucket}:{digest}', user, pref.email, summary)
        subscription=db.get(UserSubscription,user.id)
        if subscription and 'expiry' in config['events']:
            days=(aware(subscription.valid_until)-now).total_seconds()/86400
            threshold=1 if 0<days<=1 else 3 if 1<days<=3 else 7 if 3<days<=7 else None
            if threshold:
                enqueue(db,config,'expiry',f'expiry:{user.id}:{subscription.valid_until.isoformat()}:{threshold}',user,pref.email,f'会员将在 {threshold} 天内到期，请前往会员页面查看有效期。续费后仅执行后续计划。')


def deliver_one(engine,cipher):
    now=datetime.now(timezone.utc)
    with session_scope(engine) as db:
        config=load_config(db,cipher)
        if not config['enabled']: return
        # Interrupted or uncertain DATA is never automatically resent.
        db.execute(update(MailDelivery).where(MailDelivery.status=='sending',MailDelivery.updated_at<now-timedelta(minutes=10)).values(status='uncertain',error='发送进程中断，投递结果待核对'))
        # Stream the queue so quiet hours cannot starve later users or reset codes.
        candidates=db.scalars(select(MailDelivery).where(MailDelivery.status=='queued').order_by(MailDelivery.created_at,MailDelivery.id).execution_options(yield_per=100))
        row=None
        from zoneinfo import ZoneInfo
        hour=now.astimezone(ZoneInfo('Asia/Shanghai')).hour
        for candidate in candidates:
            policy=db.get(NotificationPolicy,candidate.user_id) if candidate.user_id else None
            if candidate.event in EVENTS and policy:
                if candidate.event not in policy.events.split(','):
                    candidate.status='cancelled';continue
                if policy.quiet_enabled:
                    quiet=policy.quiet_start<=hour<policy.quiet_end if policy.quiet_start<policy.quiet_end else hour>=policy.quiet_start or hour<policy.quiet_end
                    if quiet:continue
                recent=db.scalar(select(MailDelivery.id).where(MailDelivery.user_id==candidate.user_id,MailDelivery.event.in_(list(EVENTS)),MailDelivery.status=='accepted',MailDelivery.updated_at>now-timedelta(minutes=policy.interval_minutes)).limit(1))
                if recent:continue
            row=candidate;break
        if not row: return
        pref=db.get(MailPreference,row.user_id) if row.user_id else None
        user=db.get(User,row.user_id) if row.user_id else None
        if user and user.status!='active': row.status='cancelled';return
        if row.event=='reset':
            reset=db.get(PasswordReset,row.user_id)
            if not reset or mail_expired(reset.expires_at,now) or not pref or pref.email!=row.recipient or not pref.verified:
                row.status='cancelled';return
        if row.event=='verify' and (not pref or pref.email!=row.recipient or pref.verified or (now-aware(row.created_at)).total_seconds()>600):
            row.status='cancelled'; return
        if row.event in EVENTS and (not pref or not pref.enabled or not pref.verified or pref.email!=row.recipient or row.event not in config['events']):
            row.status='cancelled'; return
        if (now-aware(row.created_at)).total_seconds()>86400:
            row.status='cancelled'; row.error='通知已超过 24 小时，不再发送'; return
        claimed=db.execute(update(MailDelivery).where(MailDelivery.id==row.id,MailDelivery.status=='queued').values(status='sending',updated_at=now)).rowcount
        if not claimed: return
        identifier,recipient,subject,body=row.id,row.recipient,row.subject,row.body
    client=None; submitted=False
    try:
        client=connect(config)
        message=EmailMessage()
        message['From']=formataddr((config['sender_name'],config['sender']))
        message['To']=recipient; message['Subject']=subject
        message['Message-ID']=f'<{identifier}@{config["sender"].split("@")[1]}>'
        message.set_content(body)
        submitted=True
        client.send_message(message)
        status,error='accepted',None
    except (smtplib.SMTPAuthenticationError,smtplib.SMTPRecipientsRefused,smtplib.SMTPSenderRefused,smtplib.SMTPDataError):
        status,error='failed','邮件服务器拒绝认证、收件地址或内容，请核对 SMTP 配置'
    except Exception:
        status,error=('uncertain','投递结果不确定，请先核对收件箱，系统不会自动重发') if submitted else ('failed','连接或加密认证失败，请检查主机、端口和授权码')
    finally:
        if client: client.close()
    with session_scope(engine) as db:
        row=db.get(MailDelivery,identifier); row.status=status; row.error=error


def run_once(engine,cipher):
    with session_scope(engine) as db:
        collect(db,load_config(db,cipher),datetime.now(timezone.utc))
    for _ in range(5): deliver_one(engine,cipher)


def mail_expired(value,now):
    return aware(value)<=now
