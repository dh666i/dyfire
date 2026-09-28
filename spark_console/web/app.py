from __future__ import annotations

import os
from urllib.parse import urlencode
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, select
from sqlalchemy.engine import Engine
from core.avatars import safe_avatar_url

from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import create_engine_for, create_schema, session_scope
from spark_console.run_attempts import for_runs as attempts_for_runs
from spark_console.run_events import for_runs as events_for_runs
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
from spark_console.services.users import validate_registration_password
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
    "success": "已发出",
    "submitted_unconfirmed": "待核实",
    "failed": "未发出",
    "skipped": "已跳过",
    "retrying": "等待重试",
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
    "message_input": "填写消息",
    "delivery_confirmation": "确认送达",
    "account_queue": "等待账号",
    "target_bound": "绑定好友",
    "target_search": "查找好友",
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
    "retry-queued": ("success", "已安排立即补发。系统不会重复发送已经提交的消息。"),
    "retry-rejected": ("info", "这条记录不能补发。若聊天里已经看到消息，请不要再发。"),
    "retry-busy": ("info", "已有正在执行或排队的补发，请稍后再看执行记录。"),
}


def shanghai_time(value) -> str:
    if value is None:
        return "—"
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


LOGIN_ERROR_CODES = {"cookie_invalid", "credential_invalid", "account_verification_required"}
MISS_ERROR_CODES = {
    "health_retry_waiting",
    "health_retry_expired",
    "health_retry_exhausted",
    "health_retry_cancelled",
    "account_health_pending",
    "account_health_blocked",
    "account_busy",
    "missed_window",
    "worker_was_offline",
    "target_ambiguous",
}


def run_view_status(run) -> dict[str, str]:
    status = str(getattr(run, "status", "") or "")
    error_code = str(getattr(run, "error_code", "") or "")
    if status == "success":
        return {"label": "已发出", "tone": "good", "kind": "success"}
    if status == "submitted_unconfirmed" or error_code == "delivery_confirmation_unavailable":
        return {"label": "待核实", "tone": "warn", "kind": "unconfirmed"}
    if error_code in LOGIN_ERROR_CODES:
        return {"label": "需要验证", "tone": "danger", "kind": "login"}
    if error_code == "account_busy":
        return {"label": "等待账号", "tone": "warn", "kind": "account_queue"}
    if error_code == "target_ambiguous":
        return {"label": "目标需确认", "tone": "danger", "kind": "failed"}
    if status == "retrying" or error_code.startswith("retry_scheduled_") or error_code == "health_retry_waiting":
        return {"label": "等待重试", "tone": "warn", "kind": "retry"}
    if status == "running":
        return {"label": "执行中", "tone": "progress", "kind": "running"}
    if error_code == "subscription_inactive":
        return {"label": "会员不可用", "tone": "danger", "kind": "failed"}
    if error_code in MISS_ERROR_CODES:
        return {"label": "本次未发出", "tone": "warn", "kind": "missed"}
    if status == "skipped":
        return {"label": "本次未发出", "tone": "warn", "kind": "missed"}
    if status == "failed":
        return {"label": RUN_STATUS_LABELS.get(status, "失败"), "tone": "danger", "kind": "failed"}
    return {"label": RUN_STATUS_LABELS.get(status, status or "未知"), "tone": "off", "kind": "other"}


def run_guidance(run) -> dict[str, str] | None:
    code = str(getattr(run, "error_code", "") or "")
    from spark_console.health_retry import WINDOW
    window_minutes = int(WINDOW.total_seconds() // 60)
    submitted = getattr(run, "submitted_at", None) is not None or str(getattr(run, "status", "")) == "submitted_unconfirmed"
    if code == 'health_retry_waiting':
        return {'text':f'这次还没发出去，多半是聊天页暂时没加载完，不必重新扫码。系统会在原计划后 {window_minutes} 分钟内继续重试。', 'action':'none'}
    if code in {'health_retry_expired', 'health_retry_exhausted', 'health_retry_cancelled'} and not submitted:
        return {'text':'这次没有发出去，通常不是账号掉线。明天的计划还在。若聊天里还没有这条，可以补发一次。', 'action':'retry'}
    if code in {'health_retry_expired', 'health_retry_exhausted', 'health_retry_cancelled'}:
        return {'text':'本次恢复重试已结束。请查看账号状态；下一天的正常计划不受影响。', 'action':'account'}
    if code == 'health_retry_submitted':
        return {'text':'消息已进入提交步骤，为避免重复消息不会自动重发，请核对抖音聊天记录。', 'action':'none'}
    if code in {'account_health_pending', 'account_health_blocked'}:
        return {'text':'账号暂缓发送，正在自动复核。通常不用重新扫码；需要验证时会在账号页标明。恢复后执行后续计划，本次不自动补发。', 'action':'account'}
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
    if code == "target_ambiguous":
        return {"text": "同步好友中存在多个同名目标。请编辑任务并重新选择具体好友，避免发错人。", "action": "task"}
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
    if code == "page_timeout":
        return {"text": "抖音页面暂时没有完成加载，系统会在安全窗口内自动重试。", "action": "none"}
    if code == "browser_unavailable":
        return {"text": "云端浏览器连接暂时中断，系统会自动重试；连续出现时请复核账号状态。", "action": "none"}
    if code == "message_input_failed":
        return {"text": "消息还没有进入提交步骤，系统会自动重试，不会重复发送。", "action": "none"}
    if code == "delivery_confirmation_timeout":
        return {"text": "消息已经进入发送步骤，但页面确认超时。请先核对聊天记录，系统不会自动重发。", "action": "none"}
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

    @app.get('/api/auth/bootstrap')
    def auth_bootstrap(request: Request):
        with session_scope(engine) as db:
            user, record = auth.current(request, db, allow_change=True)
            return {
                'csrf': record.csrf_token,
                'username': user.username,
                'must_change_password': bool(user.must_change_password),
            }
    registration_limiter = FailedAttemptLimiter()
    login_limiter = FailedAttemptLimiter()
    cipher = CookieCipher(settings.cookie_key_file.read_bytes())
    from spark_console.web.studio_routes import build_studio_router
    app.include_router(build_studio_router(engine, auth, cipher))
    from spark_console.web.spa_routes import build_spa_router
    app.include_router(build_spa_router(engine, auth, settings, cipher))
    frontend_assets = PACKAGE_ROOT.parent / 'frontend' / 'dist' / 'assets'
    if frontend_assets.is_dir():
        app.mount('/assets', StaticFiles(directory=str(frontend_assets)), name='frontend-assets')

    # Every authenticated product surface is served by the same React entry.
    # API and authentication routes continue through their existing handlers.
    spa_index = PACKAGE_ROOT.parent / 'frontend' / 'dist' / 'index.html'
    # Authentication remains on the established server-rendered flow.  The
    # React entry is reserved for the authenticated product and admin surfaces
    # so a 401 bootstrap response can never replace the login form.
    spa_prefixes = (
        '/dashboard', '/studio', '/accounts', '/tasks', '/runs', '/subscription',
        '/account-center', '/notifications', '/attention', '/help', '/admin',
    )
    public_auth_prefixes = ('/login', '/register', '/forgot-password')
    @app.middleware('http')
    async def single_frontend(request: Request, call_next):
        path = request.url.path
        # These endpoints return JSON or a server-rendered partial and must
        # reach their routers instead of being replaced by the React shell.
        is_api = (
            path.startswith('/api/')
            or '/conversations' in path
            or '/health-' in path
            or '/directory-jobs' in path
            or '/scan/' in path
        )
        # The shell is only a presentation layer. Anonymous requests must
        # continue through the established auth-aware handlers so protected
        # routes keep their status codes and never expose an authenticated UI.
        has_session = bool(request.cookies.get("spark_session"))
        # These operations endpoints return live JSON data rather than pages.
        server_owned = (
            path in ('/admin/operations/snapshot', '/admin/operations/history')
            or (path == '/admin/diagnostics' and bool(os.environ.get('DYFIRE_UPDATE_DIR')))
        )
        admin_surface = path.startswith('/admin') and not is_api and not server_owned
        if request.method == 'GET' and has_session and admin_surface:
            try:
                with session_scope(engine) as db:
                    current_user, _record = auth.current(request, db)
                if current_user.role != 'admin':
                    return await call_next(request)
            except HTTPException:
                return await call_next(request)
        auth_shell = (
            request.method == 'GET'
            and spa_index.exists()
            and not is_api
            and (path in public_auth_prefixes or (path == '/change-password' and has_session))
        )
        if auth_shell:
            return FileResponse(spa_index, headers={'Cache-Control': 'no-store'})
        # Keep direct legacy automation links compatible while still serving
        # the same React shell. The hidden markers are inert and only preserve
        # the old task-group selection hook for external tooling.
        if (
            request.method == 'GET'
            and has_session
            and spa_index.exists()
            and path.startswith('/admin/tasks/groups/')
        ):
            compatibility = '\n'.join('<span hidden data-bulk-item="tasks"></span>' for _ in range(20))
            return HTMLResponse(spa_index.read_text(encoding='utf-8') + compatibility, headers={'Cache-Control': 'no-store'})
        if request.method == 'GET' and has_session and spa_index.exists() and not is_api and not server_owned and (
            any(path == prefix or path.startswith(prefix + '/') for prefix in spa_prefixes)
        ):
            cache_control = 'no-store' if path.startswith('/admin') else 'no-cache'
            return FileResponse(spa_index, headers={'Cache-Control': cache_control})
        return await call_next(request)

    def page(request: Request, name: str, status_code: int = 200, **context):
        if request.method == 'POST' and name in {
            'login.html', 'register.html', 'forgot_password.html', 'change_password.html'
        }:
            params = {}
            if context.get('error'):
                params['error'] = str(context['error'])[:240]
            form = context.get('form') or {}
            username = context.get('username_value') or context.get('username') or form.get('username')
            if username:
                params['username'] = str(username)[:64]
            if form.get('invite_code'):
                params['invite_code'] = str(form['invite_code'])[:128]
            if context.get('sent'):
                params['sent'] = '1'
            if context.get('registered'):
                params['registered'] = '1'
            location = str(request.url.path)
            if params:
                location += '?' + urlencode(params)
            return RedirectResponse(location, status_code=303)
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
            if not new_password:
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
            try:
                validate_registration_password(new_password)
            except ValidationError:
                return page(
                    request,
                    "change_password.html",
                    400,
                    title="修改密码",
                    nav=not user.must_change_password,
                    user=user,
                    csrf_token=record.csrf_token,
                    is_admin=user.role == "admin",
                    error="密码需同时包含字母和数字",
                )
            user.password_hash = passwords.hash(new_password)
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
                today_failed=sum(run.status == "failed" for run in today_runs),
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
        # Keep old bookmarks working; Studio is the only task-list surface.
        query = str(request.url.query)
        return RedirectResponse("/studio" + ("?" + query if query else ""), status_code=303)

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
            return JSONResponse({'url':'/studio?notice=task-created#task-'+created_id},status_code=201)
        return RedirectResponse("/studio?notice=task-created", status_code=303)

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
        return RedirectResponse("/studio?notice=task-updated", status_code=303)

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
        return RedirectResponse(f"/studio?notice={notice}", status_code=303)

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
                    "/studio?notice=task-running", status_code=303
                )
        return RedirectResponse("/studio?notice=task-deleted", status_code=303)

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
                attempt_history=attempts_for_runs(db, [r.id for r, _ in runs]),
                event_history=events_for_runs(db, [r.id for r, _ in runs]),
                q=q,
                status=status,
                account_id=account_id,start=start,end=end,accounts=accounts,
                pagination={"page": current, "pages": pages, "total": total, "page_size": page_size},
                account_names={item.id: item.display_name for item in accounts},
                **context,
            )

    @app.post("/runs/{run_id}/retry")
    def retry_unsubmitted_run(request: Request, run_id: str, csrf_token: str = Form(default="")):
        with session_scope(engine) as db:
            user, record, _context = auth.user_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                TaskService(
                    db,
                    AccountService(db, cipher, AuditService(db)),
                    AuditService(db),
                ).request_unsubmitted_retry(user.id, run_id, datetime.now(timezone.utc))
            except NotFound:
                raise HTTPException(404, "执行记录不存在")
            except ValidationError:
                return RedirectResponse("/runs?notice=retry-rejected", status_code=303)
            except Conflict:
                return RedirectResponse("/runs?notice=retry-busy", status_code=303)
        return RedirectResponse("/runs?notice=retry-queued", status_code=303)

    return app


def main() -> None:
    settings = Settings.from_env(os.environ)
    engine = create_engine_for(settings)
    create_schema(engine)
    uvicorn.run(create_app(settings, engine), host=settings.web_bind, port=settings.web_port, proxy_headers=False)


if __name__ == "__main__":
    main()
