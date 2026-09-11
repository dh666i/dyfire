from datetime import timedelta
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, func, text, update
from spark_console.db import session_scope
from spark_console.models import ContactDirectoryJob, utc_now
from spark_console.services import NotFound
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.services.directory import job_view
from spark_console.services.subscriptions import SubscriptionService


def build_directory_router(engine, auth, cipher):
    router = APIRouter()
    headers = {"Cache-Control": "no-store"}

    @router.post('/accounts/{account_id}/directory-jobs')
    def create_job(request: Request, account_id: str, csrf_token: str = Form(), query: str = Form(default=""), limit: int = Form(default=20)):
        with session_scope(engine) as db:
            if engine.dialect.name == 'sqlite':
                db.execute(text('BEGIN IMMEDIATE'))
            user, record = auth.current(request, db)
            auth.csrf(record, csrf_token)
            try:
                AccountService(db, cipher, AuditService(db)).get_owned(user.id, account_id)
            except NotFound:
                raise HTTPException(404, "账号不存在")
            # Search is intentionally local-only. The directory job only
            # refreshes recent conversations; the UI filters that snapshot.
            # Keeping query out of the worker avoids implying full Douyin
            # contact search, which the Web client does not expose reliably.
            if query.strip():
                raise HTTPException(422, "好友搜索仅支持已同步的最近会话，请先同步好友列表")
            if limit not in (20,40,60,80,100):
                raise HTTPException(422, "每次增加20条，最多100条")
            if user.role != 'admin' and not SubscriptionService(db, AuditService(db)).active(user.id):
                raise HTTPException(403, "请先开通有效会员")
            db.execute(update(ContactDirectoryJob).where(ContactDirectoryJob.status.in_(['queued', 'running']),
                ContactDirectoryJob.created_at < utc_now()-timedelta(minutes=5)).values(status='failed', error='同步超时，请重试', finished_at=utc_now()))
            active = db.scalar(select(ContactDirectoryJob).where(ContactDirectoryJob.owner_user_id == user.id,
                ContactDirectoryJob.status.in_(['queued','running']), ContactDirectoryJob.created_at > utc_now()-timedelta(minutes=5)).limit(1))
            if active:
                if active.account_id == account_id and active.query == query.strip() and active.limit == limit:
                    return JSONResponse(job_view(active), status_code=202, headers=headers)
                raise HTTPException(409, "已有同步或搜索进行中，请等待完成")
            recent = db.scalar(select(func.count()).select_from(ContactDirectoryJob).where(ContactDirectoryJob.owner_user_id == user.id,
                ContactDirectoryJob.created_at > utc_now()-timedelta(minutes=1)))
            queued = db.scalar(select(func.count()).select_from(ContactDirectoryJob).where(ContactDirectoryJob.status.in_(['queued','running']),
                ContactDirectoryJob.created_at > utc_now()-timedelta(minutes=5)))
            if recent >= 4 or queued >= 20:
                raise HTTPException(429, "同步请求较多，请稍后重试")
            job = ContactDirectoryJob(account_id=account_id, owner_user_id=user.id, query=query.strip(), limit=limit)
            db.add(job)
            db.flush()
            return JSONResponse(job_view(job), status_code=202, headers=headers)

    @router.get('/accounts/{account_id}/directory-jobs/{job_id}')
    def read_job(request: Request, account_id: str, job_id: str):
        with session_scope(engine) as db:
            user, _ = auth.current(request, db)
            job = db.get(ContactDirectoryJob, job_id)
            if not job or job.owner_user_id != user.id or job.account_id != account_id:
                raise HTTPException(404, "同步记录不存在")
            return JSONResponse(job_view(job), headers=headers)
    return router
