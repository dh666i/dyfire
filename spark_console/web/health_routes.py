from datetime import timedelta, timezone
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, text
from spark_console.db import session_scope
from spark_console.models import DouyinAccount, AccountHealth, AccountOperation, utc_now
from spark_console.services.account_health import views, record, snapshot, is_relogging
from spark_console.services.subscriptions import SubscriptionService
from spark_console.services.audits import AuditService


def build_health_router(engine, auth):
    router = APIRouter()

    @router.get('/accounts/health-state')
    def health_state(request: Request):
        with session_scope(engine) as db:
            user, _ = auth.current(request, db)
            accounts = db.scalars(select(DouyinAccount).where(DouyinAccount.owner_user_id == user.id)).all()
            return JSONResponse(jsonable_encoder(views(db, accounts)), headers={'Cache-Control':'no-store'})

    @router.post('/accounts/{account_id}/health-check')
    def request_check(request: Request, account_id: str, csrf_token: str = Form(default='')):
        with session_scope(engine) as db:
            if engine.dialect.name == 'sqlite':
                db.execute(text('BEGIN IMMEDIATE'))
            user, session = auth.current(request, db)
            auth.csrf(session, csrf_token)
            account = db.scalar(select(DouyinAccount).where(DouyinAccount.id == account_id, DouyinAccount.owner_user_id == user.id))
            if not account:
                raise HTTPException(404, '账号不存在')
            if user.role != 'admin' and not SubscriptionService(db, AuditService(db)).active(user.id):
                raise HTTPException(403, '请先开通有效会员')
            if account.validation_state == 'verification' or is_relogging(db, account_id):
                raise HTTPException(409, '请先完成当前登录或安全验证，系统不会自动尝试验证码或密码')
            if db.scalar(select(AccountOperation.account_id).where(AccountOperation.account_id == account_id, AccountOperation.expires_at > utc_now())):
                raise HTTPException(409, '该账号正在执行云端操作，请完成后再复核')
            health = db.get(AccountHealth, account_id)
            if health and health.checked_at and health.checked_at.replace(tzinfo=timezone.utc) > utc_now()-timedelta(minutes=5):
                raise HTTPException(429, '已在复核或刚完成检查，请等待五分钟后重试')
            if health:
                health.attempts = health.negative_checks = 0
            record(db, account_id, snapshot(account), 'temporary', '用户请求复核登录状态')
            db.flush()
            db.get(AccountHealth, account_id).next_check_at = utc_now()
            return JSONResponse({'message':'已安排登录复核，不会发送消息'}, status_code=202, headers={'Cache-Control':'no-store'})
    return router
