from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, select
from sqlalchemy.engine import Engine
from core.avatars import safe_avatar_url

from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import create_engine_for, create_schema, session_scope
from spark_console.models import (
    DouyinAccount,
    DouyinContactIdentity,
    DouyinContactAvatar,
    DouyinConversation,
    SparkTask,
    SparkTaskTargetIdentity,
    TaskRun,
    User,
)
from spark_console.rate_limit import FailedAttemptLimiter
from spark_console.security import PasswordService, SessionService
from spark_console.services.accounts import AccountReloginActive, AccountService
from spark_console.services.audits import AuditService
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.tasks import TaskService
from spark_console.services.subscriptions import SubscriptionService
from spark_console.web.account_scan_routes import build_account_scan_router
from spark_console.web.directory_routes import build_directory_router
from spark_console.web.health_routes import build_health_router
from spark_console.services.account_health import views as health_views
from spark_console.services.directory import profile_map, task_avatar_map, job_view
from spark_console.models import ContactDirectoryJob
from spark_console.web.auth import WebAuth
from spark_console.web.admin_routes import build_admin_router, render_admin_codes
from spark_console.web.registration_routes import (
    build_registration_router,
    registration_client_key,
)
from spark_console.web.subscription_routes import build_subscription_router


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
templates = Jinja2Templates(directory=str(PACKAGE_ROOT / "templates"))

RUN_STATUS_LABELS = {
    "running": "执行中",
    "success": "成功",
    "submitted_unconfirmed": "已提交，待核实",
    "failed": "失败",
    "skipped": "已跳过",
}
RUN_STAGE_LABELS = {
    "starting": "准备执行",
    "authenticating": "验证登录",
    "selecting_target": "查找好友",
    "worker_error": "执行异常",
    "sending": "发送消息",
    "confirming": "确认送达",
    "submitted": "已提交发送",
    "claimed": "已领取",
    "complete": "已完成",
    "missed_startup": "执行器离线",
    "late": "超过发送窗口",
    "clock_check": "时间校验",
    "entitlement_check": "会员校验",
    "navigation": "打开聊天页",
    "target_search": "查找好友",
    "message_input": "填写消息",
    "delivery_confirmation": "确认送达",
}
UI_NOTICES = {
    "password-updated": ("success", "登录密码已更新。"),
    "task-created": ("success", "续火任务已创建并启用。"),
    "task-updated": ("success", "任务设置已保存。"),
    "task-enabled": ("success", "任务已启用，将按计划继续执行。"),
    "task-paused": ("info", "任务已暂停，不会自动发送。"),
    "task-deleted": ("success", "任务及其执行记录已删除。"),
    "account-relogin": ("info", "该抖音账号登录状态已失效，请先重新登录。"),
    "account-login-active": ("info", "该账号正在重新登录，请先完成或取消登录后再删除。"),
    "account-running": ("info", "该账号有任务正在执行，请完成后再删除。"),
    "task-running": ("info", "任务正在执行，请完成后再删除。"),
}


def shanghai_time(value) -> str:
    if value is None:
        return "—"
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


def run_view_status(run) -> dict[str, str]:
    status = str(getattr(run, "status", "") or "")
    error_code = str(getattr(run, "error_code", "") or "")
    if error_code.startswith("retry_scheduled_"):
        return {"label": "等待重试", "tone": "warn", "kind": "retry"}
    if status == "running":
        return {"label": "执行中", "tone": "progress", "kind": "running"}
    if status == "success":
        return {"label": "成功", "tone": "good", "kind": "success"}
    if status == "submitted_unconfirmed":
        return {
            "label": "已提交，待核实",
            "tone": "warn",
            "kind": "unconfirmed",
        }
    if status == "skipped":
        return {"label": "已跳过", "tone": "warn", "kind": "skipped"}
    return {"label": RUN_STATUS_LABELS.get(status, status or "未知"), "tone": "off", "kind": "failed"}


def run_guidance(run) -> dict[str, str] | None:
    code = str(getattr(run, "error_code", "") or "")
    if code in {'account_health_pending', 'account_health_blocked'}:
        return {'text':'账号暂缓发送，正在复核或等待处理；任务开关保持不变，恢复后继续后续计划，本次不会自动补发。', 'action':'account'}
    if code == 'account_verification_required':
        return {'text':'抖音要求安全验证，请前往账号页完成验证。系统不会自动尝试密码或验证码。', 'action':'account'}
    if code == 'account_busy':
        return {'text':'正在等待该账号的其他云端操作完成，无需重复操作。', 'action':'none'}
    if code.startswith("retry_scheduled_"):
        return {"text": "临时故障，系统已安排自动重试，无需重复创建任务。", "action": "none"}
    if code in {"cookie_invalid", "credential_invalid"}:
        return {"text": "请在账号页查看复核结果或重新登录；已启用任务将在恢复后继续后续计划。", "action": "account"}
    if code == "subscription_inactive":
        return {"text": "会员当前不可用，续费后可继续执行后续计划。", "action": "subscription"}
    if code in {"target_not_found", "conversation_not_opened"}:
        return {"text": "请检查目标好友，并从该账号的最近会话中重新选择。", "action": "task"}
    if code in {"account_unavailable", "task_inactive"}:
        return {"text": "发送账号或任务当前不可用，请检查任务设置。", "action": "task"}
    if code in {"worker_was_offline", "missed_window"}:
        return {"text": "本次已错过发送窗口且不会补发，后续每日计划不受影响。", "action": "none"}
    if code == "delivery_confirmation_unavailable":
        return {
            "text": "系统已经执行发送，但未能从抖音页面取得确认。为避免重复消息，本次不会自动重发，请先到抖音聊天记录核对。",
            "action": "none",
        }
    if code == "system_time_unhealthy":
        return {"text": "服务器时间异常，请联系管理员处理。", "action": "none"}
    if code in {"automation_failed", "unexpected_error"}:
        return {"text": "页面操作未完成；如连续出现，请联系管理员并提供本次执行时间。", "action": "none"}
    return None


templates.env.filters["shanghai_time"] = shanghai_time
templates.env.filters["run_status"] = lambda value: RUN_STATUS_LABELS.get(value, value)
templates.env.filters["run_stage"] = lambda value: RUN_STAGE_LABELS.get(value, value)
templates.env.filters["run_view_status"] = run_view_status
templates.env.filters["run_guidance"] = run_guidance


def create_app(settings: Settings, engine: Engine) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.engine = engine
    app.mount("/static", StaticFiles(directory=str(PACKAGE_ROOT / "static")), name="static")
    passwords = PasswordService()
    sessions = SessionService(settings.session_key_file.read_bytes())
    auth = WebAuth(sessions)
    from spark_console.web.update_routes import build_update_router
    app.include_router(build_update_router(engine, auth))
    @app.get('/api/version')
    def version_info():
        from spark_console.version import VERSION
        return {'version': VERSION}
    registration_limiter = FailedAttemptLimiter()
    login_limiter = FailedAttemptLimiter()
    cipher = CookieCipher(settings.cookie_key_file.read_bytes())
    from spark_console.web.studio_routes import build_studio_router
    app.include_router(build_studio_router(engine, auth, cipher))
    frontend_assets = PACKAGE_ROOT.parent / 'frontend' / 'dist' / 'assets'
    if frontend_assets.is_dir():
        app.mount('/assets', StaticFiles(directory=str(frontend_assets)), name='frontend-assets')

    def page(request: Request, name: str, status_code: int = 200, **context):
        notice = UI_NOTICES.get(request.query_params.get("notice", ""))
        if notice and "ui_notice" not in context:
            context["ui_notice"] = {"tone": notice[0], "message": notice[1]}
        return templates.TemplateResponse(
            request=request, name=name, context=context, status_code=status_code,
            headers={"Cache-Control": "no-store"},
        )

    def render_admin(request: Request, db, context: dict, **extra):
        return render_admin_codes(
            request,
            db,
            context,
            page_renderer=page,
            cipher=cipher,
            **extra,
        )

    app.include_router(
        build_registration_router(
            engine, passwords, registration_limiter, auth, page, cipher
        )
    )
    app.include_router(build_account_scan_router(engine, auth, cipher))
    app.include_router(build_directory_router(engine, auth, cipher))
    app.include_router(build_health_router(engine, auth))
    from spark_console.web.mail_routes import build_mail_router
    app.include_router(build_mail_router(engine, auth, cipher, page))
    from spark_console.web.reliability_routes import build_reliability_router
    app.include_router(build_reliability_router(engine, auth, cipher))
    from spark_console.web.product_routes import build_product_router
    app.include_router(build_product_router(engine, auth, page))
    from spark_console.web.password_recovery import build_password_recovery_router
    app.include_router(build_password_recovery_router(engine,auth,cipher,passwords,page))
    app.include_router(
        build_subscription_router(engine, auth, page, render_admin)
    )
    app.include_router(
        build_admin_router(engine, settings, auth, passwords, cipher, page)
    )

    @app.exception_handler(401)
    async def unauthorized(request: Request, _exc):
        if request.url.path.startswith('/api/'):
            return JSONResponse({'detail': '请登录后继续'}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(409)
    async def change_required(request: Request, exc):
        if request.url.path.startswith('/api/'):
            return JSONResponse({'detail': str(exc.detail)}, status_code=409)
        if exc.detail == "password-change-required":
            return RedirectResponse("/change-password", status_code=303)
        return page(request, "error.html", status_code=409, message="请求冲突")

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        return {"status": "ready"}

    @app.get("/")
    def root():
        return RedirectResponse('/dashboard', status_code=303)

    @app.get("/login")
    def login_page(request: Request):
        return page(
            request,
            "login.html",
            title="登录",
            nav=False,
            registered=request.query_params.get("registered") == "1",
        )

    @app.post("/login")
    def login(request: Request, username: str = Form(), password: str = Form()):
        client_key = registration_client_key(request)
        if not login_limiter.allow(client_key):
            return page(
                request,
                "login.html",
                status_code=429,
                title="登录",
                nav=False,
                error="登录尝试过多，请稍后再试",
                username_value=username.strip(),
            )
        with session_scope(engine) as db:
            user = db.scalar(select(User).where(User.username == username.strip().lower()))
            valid = user is not None and user.status == "active" and passwords.verify(user.password_hash, password)
            if not valid:
                login_limiter.record_failure(client_key)
                if user is not None:
                    user.failed_login_count += 1
                return page(
                    request,
                    "login.html",
                    status_code=400,
                    title="登录",
                    nav=False,
                    error="用户名或密码错误",
                    username_value=username.strip(),
                )
            user.failed_login_count = 0
            login_limiter.clear(client_key)
            raw, record = sessions.create_record(user.id)
            db.add(record)
            destination = "/change-password" if user.must_change_password else '/dashboard'
        response = RedirectResponse(destination, status_code=303)
        response.set_cookie(
            "spark_session",
            raw,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="strict",
            max_age=8 * 3600,
        )
        return response

    @app.post("/logout")
    def logout(request: Request, csrf_token: str = Form(default="")):
        with session_scope(engine) as db:
            user, record = auth.current(request, db, allow_change=True)
            auth.csrf(record, csrf_token)
            db.delete(record)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("spark_session")
        return response

    @app.get("/change-password")
    def change_password_page(request: Request):
        with session_scope(engine) as db:
            user, record = auth.current(request, db, allow_change=True)
            return page(
                request,
                "change_password.html",
                title="设置新密码" if user.must_change_password else "修改密码",
                nav=not user.must_change_password,
                user=user,
                csrf_token=record.csrf_token,
                is_admin=user.role == "admin",
            )

    @app.post("/change-password")
    def change_password(
        request: Request,
        csrf_token: str = Form(default=""),
        current_password: str = Form(),
        new_password: str = Form(),
        new_password_confirmation: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            user, record = auth.current(request, db, allow_change=True)
            auth.csrf(record, csrf_token)
            if not passwords.verify(user.password_hash, current_password):
                return page(
                    request,
                    "change_password.html",
                    400,
                    title="修改密码",
                    nav=not user.must_change_password,
                    user=user,
                    csrf_token=record.csrf_token,
                    is_admin=user.role == "admin",
                    error="当前密码错误",
                )
            if new_password != new_password_confirmation:
                return page(
                    request,
                    "change_password.html",
                    400,
                    title="修改密码",
                    nav=not user.must_change_password,
                    user=user,
                    csrf_token=record.csrf_token,
                    is_admin=user.role == "admin",
                    error="两次输入的新密码不一致",
                )
            try:
                user.password_hash = passwords.hash(new_password)
            except ValueError:
                return page(
                    request,
                    "change_password.html",
                    400,
                    title="修改密码",
                    nav=not user.must_change_password,
                    user=user,
                    csrf_token=record.csrf_token,
                    is_admin=user.role == "admin",
                    error="新密码不能为空",
                )
            user.must_change_password = False
        return RedirectResponse("/dashboard?notice=password-updated", status_code=303)

    @app.get("/dashboard")
    def dashboard(request: Request):
        with session_scope(engine) as db:
            user, _record, context = auth.user_context(request, db)
            tasks = db.scalars(select(SparkTask).where(SparkTask.owner_user_id == user.id)).all()
            accounts = db.scalars(select(DouyinAccount).where(DouyinAccount.owner_user_id == user.id)).all()
            runs = db.scalars(select(TaskRun).join(SparkTask).where(SparkTask.owner_user_id == user.id).order_by(TaskRun.scheduled_for.desc()).limit(5)).all()
            local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
            today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
            today_runs = db.scalars(
                select(TaskRun)
                .join(SparkTask)
                .where(
                    SparkTask.owner_user_id == user.id,
                    TaskRun.scheduled_for >= today_start,
                )
            ).all()
            valid_accounts = sum(item.validation_state == "valid" for item in accounts)
            accounts.sort(key=lambda item: (item.validation_state == 'valid', item.display_name.casefold()))
            account_attention = sum(item.validation_state in ('invalid','temporary','verification') for item in accounts)
            next_task = min(
                (task for task in tasks if task.enabled and task.next_run_at is not None),
                key=lambda task: task.next_run_at.replace(tzinfo=timezone.utc)
                if task.next_run_at.tzinfo is None
                else task.next_run_at.astimezone(timezone.utc),
                default=None,
            )
            onboarding = {
                "membership": bool(context.get("membership", {}).get("active")),
                "account": bool(accounts),
                "task": bool(tasks),
                "success": any(run.status == "success" for run in runs)
                or db.scalar(
                    select(TaskRun.id)
                    .join(SparkTask)
                    .where(
                        SparkTask.owner_user_id == user.id,
                        TaskRun.status == "success",
                    )
                    .limit(1)
                )
                is not None,
            }
            return page(
                request,
                "dashboard.html",
                title="今日续火",
                task_avatars=task_avatar_map(db, tasks),
                account_profiles=profile_map(db, [a.id for a in accounts]),
                account_health=health_views(db, accounts),
                tasks=tasks,
                accounts=accounts,
                runs=runs,
                onboarding=onboarding,
                valid_accounts=valid_accounts,
                account_attention=account_attention,
                today_success=sum(run.status == "success" for run in today_runs),
                today_attention=sum(
                    run.status in {"failed", "skipped", "submitted_unconfirmed"}
                    for run in today_runs
                ),
                today_unconfirmed=sum(
                    run.status == "submitted_unconfirmed" for run in today_runs
                ),
                next_task=next_task,
                **context,
            )

    @app.get("/accounts")
    def accounts_page(request: Request):
        with session_scope(engine) as db:
            user, _record, context = auth.user_context(request, db)
            accounts = db.scalars(
                select(DouyinAccount)
                .where(DouyinAccount.owner_user_id == user.id)
                .order_by(DouyinAccount.created_at)
            ).all()
            return page(request, "accounts.html", title="抖音账号", accounts=accounts, account_profiles=profile_map(db, [a.id for a in accounts]), account_health=health_views(db, accounts), **context)

    @app.post("/accounts/{account_id}/delete")
    def delete_account(request: Request, account_id: str, csrf_token: str = Form(default="")):
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            auth.csrf(record, csrf_token)
            try:
                AccountService(db, cipher, AuditService(db)).delete_owned(
                    user.id, account_id
                )
            except AccountReloginActive:
                return RedirectResponse(
                    "/accounts?notice=account-login-active", status_code=303
                )
            except Conflict:
                return RedirectResponse(
                    "/accounts?notice=account-running", status_code=303
                )
        return RedirectResponse("/accounts", status_code=303)

    @app.get("/accounts/{account_id}/conversations")
    def account_conversations(request: Request, account_id: str):
        with session_scope(engine) as db:
            user, _record = auth.current(request, db)
            service = AccountService(db, cipher, AuditService(db))
            try:
                service.get_owned(user.id, account_id)
            except NotFound as error:
                raise HTTPException(status_code=404, detail="账号不存在") from error
            conversation_names = db.scalars(
                select(DouyinConversation.display_name)
                .where(DouyinConversation.account_id == account_id)
                .order_by(DouyinConversation.display_name)
            ).all()
            contacts = db.scalars(
                select(DouyinContactIdentity)
                .where(DouyinContactIdentity.account_id == account_id)
            ).all()
            avatars = {
                item.sec_uid: safe_avatar_url(item.avatar_url)
                for item in db.scalars(select(DouyinContactAvatar).where(DouyinContactAvatar.account_id == account_id))
            }
            aliases = {
                value
                for contact in contacts
                for value in (
                    contact.remark_name,
                    contact.nickname,
                    contact.unique_id,
                    contact.short_id,
                )
                if value
            }
            items = [
                {
                    "name": contact.remark_name
                    or contact.nickname
                    or contact.unique_id
                    or contact.short_id,
                    "sec_uid": contact.sec_uid,
                    "nickname": contact.nickname,
                    "remark_name": contact.remark_name,
                    "unique_id": contact.unique_id,
                    "short_id": contact.short_id,
                    **({"avatar_url": avatars[contact.sec_uid]} if avatars.get(contact.sec_uid) else {}),
                }
                for contact in contacts
                if contact.remark_name
                or contact.nickname
                or contact.unique_id
                or contact.short_id
            ]
            items.extend(
                {"name": name, "sec_uid": None}
                for name in conversation_names
                if name not in aliases
            )
            latest = db.scalar(select(ContactDirectoryJob).where(ContactDirectoryJob.account_id == account_id,
                ContactDirectoryJob.query == "", ContactDirectoryJob.status == "succeeded").order_by(ContactDirectoryJob.finished_at.desc()).limit(1))
            active = db.scalar(select(ContactDirectoryJob).where(ContactDirectoryJob.account_id == account_id,
                ContactDirectoryJob.status.in_(['queued', 'running'])).order_by(ContactDirectoryJob.created_at.desc()).limit(1))
            return JSONResponse({"items": sorted(items, key=lambda item: item["name"]), "sync": job_view(latest) if latest else None,
                "active_sync": job_view(active) if active else None}, headers={"Cache-Control": "no-store"})

    @app.get("/tasks")
    def tasks_page(request: Request):
        with session_scope(engine) as db:
            user, _record, context = auth.user_context(request, db)
            task_service = TaskService(db, AccountService(db, cipher, AuditService(db)), AuditService(db))
            accounts = AccountService(db, cipher, AuditService(db)).list_owned(user.id)
            tasks = task_service.list_owned(user.id)
            latest_runs: dict[str, TaskRun] = {}
            if tasks:
                latest_schedule = (
                    select(
                        TaskRun.task_id,
                        func.max(TaskRun.scheduled_for).label("scheduled_for"),
                    )
                    .join(SparkTask, SparkTask.id == TaskRun.task_id)
                    .where(SparkTask.owner_user_id == user.id)
                    .group_by(TaskRun.task_id)
                    .subquery()
                )
                latest_runs = {
                    run.task_id: run
                    for run in db.scalars(
                        select(TaskRun).join(
                            latest_schedule,
                            and_(
                                TaskRun.task_id == latest_schedule.c.task_id,
                                TaskRun.scheduled_for
                                == latest_schedule.c.scheduled_for,
                            ),
                        )
                    ).all()
                }
            return page(
                request,
                "tasks.html",
                title="续火任务",
                task_avatars=task_avatar_map(db, tasks),
                tasks=tasks,
                account_health=health_views(db, db.scalars(select(DouyinAccount).where(DouyinAccount.owner_user_id == user.id)).all()),
                accounts=accounts,
                account_names={item["id"]: item["display_name"] for item in accounts},
                latest_runs=latest_runs,
                **context,
            )

    @app.post("/tasks")
    def add_task(
        request: Request,
        csrf_token: str = Form(default=""),
        account_id: str = Form(),
        target_name: list[str] = Form(),
        target_sec_uid: list[str] | None = Form(default=None),
        send_time: str = Form(),
        message_template: str = Form(default=""),
        message_kind: str = Form(default="text"),
    ):
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            auth.csrf(record, csrf_token)
            sec_uids = target_sec_uid or []
            if sec_uids and len(sec_uids) != len(target_name):
                raise HTTPException(400, "好友选择数据不完整，请重新选择")
            if not sec_uids:
                sec_uids = [""] * len(target_name)
            try:
                targets = TaskService.normalize_targets(
                    zip(target_name, sec_uids, strict=True)
                )
            except ValidationError as error:
                raise HTTPException(400, str(error)) from error
            if user.role != "admin":
                try:
                    SubscriptionService(
                        db, AuditService(db)
                    ).require_task_capacity(user.id, len(targets))
                except (ValidationError, Conflict):
                    if request.headers.get('accept')=='application/json':
                        raise HTTPException(403,'会员额度不足或已到期，请查看订阅后重试；填写内容仍保留')
                    return RedirectResponse(
                        "/subscription?required=1", status_code=303
                    )
            service = TaskService(db, AccountService(db, cipher, AuditService(db)), AuditService(db))
            try:
                created = service.create_many(
                    user.id,
                    account_id,
                    targets,
                    send_time,
                    message_template,
                    message_kind=message_kind,
                )
            except ValidationError as error:
                if "重新登录" in str(error):
                    if request.headers.get('accept')=='application/json':
                        raise HTTPException(409,str(error)) from error
                    return RedirectResponse(
                        "/accounts?notice=account-relogin", status_code=303
                    )
                raise HTTPException(400, str(error)) from error
            except Conflict as error:
                raise HTTPException(409, str(error)) from error
            created_id=created[0].id
        if request.headers.get('accept')=='application/json':
            return JSONResponse({'url':'/tasks?notice=task-created#task-'+created_id},status_code=201)
        return RedirectResponse("/tasks?notice=task-created", status_code=303)

    @app.get("/tasks/{task_id}/edit")
    def edit_task_page(request: Request, task_id: str):
        with session_scope(engine) as db:
            user, _record, context = auth.user_context(request, db)
            account_service = AccountService(db, cipher, AuditService(db))
            service = TaskService(db, account_service, AuditService(db))
            try:
                task = service.get_owned(user.id, task_id)
            except NotFound as error:
                raise HTTPException(404) from error
            binding = db.get(SparkTaskTargetIdentity, task.id)
            form = {
                "account_id": task.douyin_account_id or "",
                "target_name": task.target_name,
                "target_sec_uid": binding.sec_uid if binding else "",
                "send_time": task.send_time,
                "message_template": task.message_template,
                "message_kind": task.message_kind,
            }
            return page(
                request,
                "task_edit.html",
                title="编辑续火任务",
                task=task,
                accounts=account_service.list_owned(user.id),
                form=form,
                error=None,
                **context,
            )

    @app.post("/tasks/{task_id}/edit")
    def edit_task(
        request: Request,
        task_id: str,
        csrf_token: str = Form(default=""),
        account_id: str = Form(),
        target_name: str = Form(),
        target_sec_uid: str = Form(default=""),
        send_time: str = Form(),
        message_template: str = Form(default=""),
        message_kind: str = Form(default="text"),
    ):
        with session_scope(engine) as db:
            user, record, context = auth.user_context(request, db)
            auth.csrf(record, csrf_token)
            account_service = AccountService(db, cipher, AuditService(db))
            service = TaskService(db, account_service, AuditService(db))
            try:
                task = service.get_owned(user.id, task_id)
                account_id = task.douyin_account_id
                target_name = task.target_name
                binding = db.get(SparkTaskTargetIdentity, task.id)
                target_sec_uid = binding.sec_uid if binding else ""
                service.update_owned(
                    user.id,
                    task_id,
                    account_id,
                    target_name,
                    send_time,
                    message_template,
                    target_sec_uid=target_sec_uid,
                    message_kind=message_kind,
                )
            except NotFound as error:
                raise HTTPException(404) from error
            except (ValidationError, Conflict) as error:
                form = {
                    "account_id": account_id,
                    "target_name": target_name,
                    "target_sec_uid": target_sec_uid,
                    "send_time": send_time,
                    "message_template": message_template,
                    "message_kind": message_kind,
                }
                return page(
                    request,
                    "task_edit.html",
                    status_code=400,
                    title="编辑续火任务",
                    task=task,
                    accounts=account_service.list_owned(user.id),
                    form=form,
                    error=str(error),
                    **context,
                )
        return RedirectResponse("/tasks?notice=task-updated", status_code=303)

    @app.post("/tasks/{task_id}/toggle")
    def toggle_task(request: Request, task_id: str, csrf_token: str = Form(default="")):
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            auth.csrf(record, csrf_token)
            service = TaskService(db, AccountService(db, cipher, AuditService(db)), AuditService(db))
            task = service.get_owned(user.id, task_id)
            if not task.enabled and user.role != "admin":
                try:
                    SubscriptionService(db, AuditService(db)).require_active(
                        user.id
                    )
                except ValidationError:
                    return RedirectResponse(
                        "/subscription?required=1", status_code=303
                    )
            enabled = not task.enabled
            try:
                service.set_enabled_owned(user.id, task_id, enabled)
            except ValidationError as error:
                if "重新登录" in str(error):
                    return RedirectResponse(
                        "/accounts?notice=account-relogin", status_code=303
                    )
                raise
            except Conflict as error:
                raise HTTPException(409, str(error)) from error
        notice = "task-enabled" if enabled else "task-paused"
        return RedirectResponse(f"/tasks?notice={notice}", status_code=303)

    @app.post("/tasks/{task_id}/delete")
    def delete_task(request: Request, task_id: str, csrf_token: str = Form(default="")):
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            auth.csrf(record, csrf_token)
            try:
                TaskService(
                    db,
                    AccountService(db, cipher, AuditService(db)),
                    AuditService(db),
                ).delete_owned(user.id, task_id)
            except Conflict:
                return RedirectResponse(
                    "/tasks?notice=task-running", status_code=303
                )
        return RedirectResponse("/tasks?notice=task-deleted", status_code=303)

    @app.get("/runs")
    def runs_page(
        request: Request, q: str = "", status: str = "",
        page_number: int = Query(1, alias="page"), page_size: int = 12,
        account_id: str = '', start: str = '', end: str = '',
    ):
        with session_scope(engine) as db:
            user, _record, context = auth.user_context(request, db)
            q = q.strip()[:100]
            allowed_statuses = (*RUN_STATUS_LABELS, "retrying", "attention")
            status = status if status in allowed_statuses else ""
            page_size = page_size if page_size in (12, 24, 48) else 12
            criteria = [SparkTask.owner_user_id == user.id]
            if account_id: criteria.append(SparkTask.douyin_account_id==account_id)
            from datetime import timedelta
            try:
                if start: criteria.append(TaskRun.scheduled_for>=datetime.strptime(start,'%Y-%m-%d').replace(tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc))
                if end: criteria.append(TaskRun.scheduled_for<(datetime.strptime(end,'%Y-%m-%d').replace(tzinfo=ZoneInfo('Asia/Shanghai'))+timedelta(days=1)).astimezone(timezone.utc))
                if start and end and start>end: raise ValueError()
            except ValueError: raise HTTPException(400,'日期范围不正确，请选择起始日期和结束日期')
            if q:
                criteria.append(SparkTask.target_name.contains(q, autoescape=True))
            if status == "attention":
                criteria.append(TaskRun.status.in_(("failed", "submitted_unconfirmed", "retrying")))
            elif status:
                criteria.append(TaskRun.status == status)
            total = db.scalar(select(func.count()).select_from(TaskRun).join(SparkTask).where(*criteria)) or 0
            pages = max(1, (total + page_size - 1) // page_size)
            current = min(max(1, page_number), pages)
            runs = db.execute(
                select(TaskRun, SparkTask).join(SparkTask).where(*criteria)
                .order_by(TaskRun.scheduled_for.desc(), TaskRun.id.desc())
                .offset((current - 1) * page_size).limit(page_size)
            ).all()
            accounts = db.scalars(
                select(DouyinAccount).where(DouyinAccount.owner_user_id == user.id)
            ).all()
            return page(
                request,
                "runs.html",
                title="执行记录",
                runs=runs,
                q=q,
                status=status,
                account_id=account_id,start=start,end=end,accounts=accounts,
                pagination={"page": current, "pages": pages, "total": total, "page_size": page_size},
                account_names={item.id: item.display_name for item in accounts},
                **context,
            )

    return app


def main() -> None:
    settings = Settings.from_env(os.environ)
    engine = create_engine_for(settings)
    create_schema(engine)
    uvicorn.run(create_app(settings, engine), host=settings.web_bind, port=settings.web_port, proxy_headers=False)


if __name__ == "__main__":
    main()
