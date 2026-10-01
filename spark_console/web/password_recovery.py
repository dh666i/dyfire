import hashlib
import secrets
import threading
import time
from collections import OrderedDict
from datetime import datetime,timedelta,timezone
from fastapi import APIRouter,Request,Form
from fastapi.responses import RedirectResponse
from sqlalchemy import select,delete,update
from spark_console.models import User,MailPreference,PasswordReset,MailDelivery,WebSession,uuid_string
from spark_console.db import session_scope
from spark_console.services import ValidationError
from spark_console.services.mail import load_config,aware
from spark_console.services.audits import AuditService
from spark_console.services.users import validate_registration_password


def build_password_recovery_router(engine,auth,cipher,passwords,page,redis_backend=None):
    router=APIRouter();limiter=OrderedDict();lock=threading.Lock()
    distributed = bool(redis_backend is not None and redis_backend.enabled)
    def allowed(request):
        key=request.client.host if request.client else 'unknown';now=time.monotonic()
        if distributed:
            try:
                return bool(redis_backend.allow_rate_limit(
                    f"password-recovery:{key}", limit=10, window_seconds=600
                ))
            except Exception:
                # Explicitly enabled Redis must fail closed. A local fallback
                # would make a multi-worker deployment bypass the shared limit.
                return False
        with lock:
            values=[v for v in limiter.get(key,[]) if now-v<600]
            if len(values)>=10:return False
            values.append(now);limiter[key]=values;limiter.move_to_end(key)
            while len(limiter)>10000:limiter.popitem(last=False)
            return True
    @router.get('/forgot-password')
    def start(request:Request):
        with session_scope(engine) as db:
            return page(request,'forgot_password.html',title='找回密码',nav=False,ready=load_config(db,cipher)['enabled'])
    @router.post('/forgot-password')
    def send(request:Request,username:str=Form(''),email:str=Form('')):
        now=datetime.now(timezone.utc)
        with session_scope(engine) as db:
            config=load_config(db,cipher)
            if allowed(request) and config['enabled']:
                user=db.scalar(select(User).where(User.username==username.strip()[:32],User.status=='active'))
                pref=db.get(MailPreference,user.id) if user else None
                reset=db.get(PasswordReset,user.id) if user else None
                if pref and pref.verified and pref.email==email.strip() and (not reset or aware(reset.created_at)<now-timedelta(minutes=1)):
                    # A 12-character hexadecimal code gives 48 bits of
                    # entropy while remaining easy to copy from email.  The
                    # reset verifier intentionally accepts legacy 8-character
                    # rows already issued before this change until they expire.
                    code=secrets.token_hex(6).upper()
                    digest=hashlib.sha256((user.id+code).encode()).hexdigest()
                    db.merge(PasswordReset(user_id=user.id,digest=digest,created_at=now,expires_at=now+timedelta(minutes=10),attempts=0))
                    db.execute(update(MailDelivery).where(MailDelivery.user_id==user.id,MailDelivery.event=='reset',MailDelivery.status=='queued').values(status='cancelled'))
                    db.add(MailDelivery(event_key=uuid_string(),user_id=user.id,recipient=pref.email,event='reset',subject='火花守护 · 重置网站密码',body=f'重置代码：{code}\n十分钟内有效。仅用于重置火花守护网站密码。如非本人操作，请忽略。'))
            return page(request,'forgot_password.html',title='找回密码',nav=False,ready=config['enabled'],sent=True,username=username[:32])
    @router.post('/forgot-password/reset')
    def reset_password(request:Request,username:str=Form(''),code:str=Form(''),password:str=Form(''),confirmation:str=Form('')):
        success=False
        password_valid = bool(password and password == confirmation)
        if password_valid:
            try:
                validate_registration_password(password, username=username)
            except ValidationError:
                password_valid = False
        if allowed(request) and password_valid:
            with session_scope(engine) as db:
                user=db.scalar(select(User).where(User.username==username.strip()[:32],User.status=='active'))
                reset=db.get(PasswordReset,user.id) if user else None
                if reset and aware(reset.expires_at)>datetime.now(timezone.utc) and reset.attempts<5:
                    reset.attempts+=1
                    if secrets.compare_digest(hashlib.sha256((user.id+code.strip().upper()).encode()).hexdigest(),reset.digest):
                        changed=db.execute(delete(PasswordReset).where(PasswordReset.user_id==user.id,PasswordReset.digest==reset.digest)).rowcount
                        if changed:
                            user.password_hash=passwords.hash(password);user.must_change_password=False;user.failed_login_count=0
                            db.execute(delete(WebSession).where(WebSession.user_id==user.id));success=True
                            AuditService(db).write(user.id,'password.recovered','user',user.id)
        if success:return RedirectResponse('/login',status_code=303)
        return page(request,'forgot_password.html',title='找回密码',nav=False,ready=True,sent=True,username=username[:32],error='重置未完成：请检查账号、代码有效期以及两次密码是否一致。',status_code=400)
    return router
