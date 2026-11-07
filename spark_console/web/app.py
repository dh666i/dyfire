from __future__ import annotations

import os
from urllib.parse import urlencode
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, case, delete, func, select, text
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
    WebSession,
    utc_now,
)
from spark_console.rate_limit import build_rate_limiter
from spark_console.redis_backend import RedisBackend
from spark_console.security import PasswordService, SessionService
from spark_console.services.accounts import AccountReloginActive, AccountService
from spark_console.services.audits import AuditService
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.tasks import TaskService
from spark_console.services.subscriptions import SubscriptionService
from spark_console.services.users import (
    PASSWORD_POLICY_HINT,
    UserService,
    validate_registration_password,
)
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
from spark_console.web.request_limits import RequestBodyLimitMiddleware
from spark_console.web.proxy_headers import TrustedProxyMiddleware
from spark_console.web.request_context import RequestCorrelationMiddleware
from spark_console.web.subscription_routes import build_subscription_router


PACKAGE_ROOT = Path(__file__).resolve().parents[1]

from spark_console.run_policy import RUN_STATUS_LABELS, RUN_STAGE_LABELS, run_view_status, run_guidance

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


# Legacy server-rendered views are still reachable from old bookmarks and
# automation links. Keep their initial payload bounded even though the main
# product now uses the React bootstrap APIs.
LEGACY_WORKSPACE_PAGE_SIZE = 100
ACCOUNT_CONVERSATION_PAGE_SIZE = 100




def create_app(settings: Settings, engine: Engine) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    # Forwarded scheme and client address are trusted only when the immediate
    # peer belongs to an explicitly configured proxy network.  An empty list
    # is the secure default for direct/local deployments.
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(RequestCorrelationMiddleware)
    app.state.settings = settings
    app.state.engine = engine
    # Redis is lazy: constructing this adapter validates configuration but
    # does not open a socket.  All limiters below fall back to the existing
    # process-local implementations unless Redis was explicitly enabled.
    redis_backend = RedisBackend.from_settings(settings)
    app.state.redis_backend = redis_backend

    app.mount("/static", StaticFiles(directory=str(PACKAGE_ROOT / "static")), name="static")
    passwords = PasswordService()
    sessions = SessionService(settings.session_key_file.read_bytes())
    auth = WebAuth(sessions)
    # Administrative writes use the authenticated admin session and CSRF.
    # Administrator actions use the same normal session, CSRF and role checks.
    from spark_console.web.update_routes import build_update_router
    app.include_router(build_update_router(engine, auth))
    from spark_console.web.operations_routes import build_operations_router
    app.include_router(build_operations_router(engine,auth))
    from spark_console.web.probe_routes import build_probe_router
    app.include_router(build_probe_router(engine,auth))
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
                'is_admin': user.role == 'admin',
            }
    registration_limiter = build_rate_limiter(
        redis_backend, namespace="registration"
    )
    login_limiter = build_rate_limiter(redis_backend, namespace="login")
    cipher = CookieCipher(settings.cookie_key_file.read_bytes())
    from spark_console.web.account_lifecycle_routes import build_account_lifecycle_router
    app.include_router(build_account_lifecycle_router(engine,auth,cipher))
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
    # Public authentication and signed-in routes share the React entry.
    # Form actions keep their established redirects and session handling.
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
        # Existing form actions remain compatible with the React client, but
        # no request can fall back to a second server-rendered interface.
        if request.method == 'GET' and status_code < 400:
            if spa_index.is_file():
                return FileResponse(spa_index, headers={'Cache-Control': 'no-store'})
            raise HTTPException(503, '页面资源尚未构建，请完成前端构建后启动服务')
        payload = {'ok': status_code < 400}
        if status_code >= 400:
            payload['detail'] = context.get('error') or context.get('message') or '请求未完成'
        for key in ('one_time_codes', 'one_time_password'):
            if context.get(key) is not None:
                payload[key] = context[key]
        return JSONResponse(payload, status_code=status_code, headers={'Cache-Control': 'no-store'})

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
    app.include_router(
        build_account_scan_router(
            engine, auth, cipher, redis_backend=redis_backend
        )
    )
    app.include_router(build_directory_router(engine, auth, cipher))
    app.include_router(build_health_router(engine, auth))
    from spark_console.web.mail_routes import build_mail_router
    app.include_router(build_mail_router(engine, auth, cipher, page))
    from spark_console.web.reliability_routes import build_reliability_router
    app.include_router(build_reliability_router(engine, auth, cipher))
    from spark_console.web.product_routes import build_product_router
    app.include_router(build_product_router(engine, auth, page))
    from spark_console.web.password_recovery import build_password_recovery_router
    app.include_router(
        build_password_recovery_router(
            engine, auth, cipher, passwords, page, redis_backend=redis_backend
        )
    )
    app.include_router(
        build_subscription_router(engine, auth, page, render_admin, cipher=cipher)
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
            # Structured schedule conflicts carry the refreshed plan so the
            # client can show exact per-friend times without creating a
            # partial batch. Preserve strings for the legacy API responses.
            detail = exc.detail if isinstance(exc.detail, (dict, list)) else str(exc.detail)
            return JSONResponse({'detail': detail}, status_code=409)
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
        # Redis is optional, but once explicitly enabled it is a required
        # dependency for the shared authentication and scan limiters. Report
        # the instance as unready instead of serving requests that will all
        # fail closed when Redis is unavailable.
        if redis_backend.enabled:
            try:
                redis_backend.ping()
            except Exception as error:
                raise HTTPException(503, "Redis 依赖暂时不可用") from error
        return {"status": "ready"}

    @app.get('/health/execution')
    def execution_health():
        """Public boolean for an external monitor; no customer or host details."""
        from spark_console.models import WorkerLock
        from spark_console.runtime_health import _fresh
        from spark_console.ops_history import read_history, history_path
        now=datetime.now(timezone.utc)
        with session_scope(engine) as db:
            leases={row.id:row for row in db.scalars(select(WorkerLock))}
            worker=all(
                (row:=leases.get(index+1)) is not None and row.worker_id and row.lease_until
                and (row.lease_until if row.lease_until.tzinfo else row.lease_until.replace(tzinfo=timezone.utc))>now
                for index in range(settings.worker_shards)
            )
        auth_fresh=_fresh(settings.data_dir/'auth-worker.heartbeat',60)
        monitor_fresh=read_history(history_path(settings)).get('state')=='fresh'
        ok=bool(worker and auth_fresh and monitor_fresh)
        return JSONResponse({'status':'ready' if ok else 'degraded'},status_code=200 if ok else 503,headers={'Cache-Control':'no-store'})

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
            user = UserService(db, passwords, AuditService(db)).authenticate(username, password)
            if user is None:
                login_limiter.record_failure(client_key)
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
            user.locked_until = None
            login_limiter.clear(client_key)
            # Expired session rows are safe to remove when issuing a new
            # session; this keeps the table from accumulating indefinitely.
            db.execute(delete(WebSession).where(WebSession.expires_at <= utc_now()))
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
                validate_registration_password(new_password, username=user.username)
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
                    error=PASSWORD_POLICY_HINT,
                )
            user.password_hash = passwords.hash(new_password)
            user.must_change_password = False
            # Revoke every other session and rotate this one in place. Keeping
            # the current browser signed in avoids stranding forced-change users.
            db.execute(
                delete(WebSession).where(
                    WebSession.user_id == user.id,
                    WebSession.id != record.id,
                )
            )
            raw, rotated = sessions.create_record(user.id)
            record.token_hash = rotated.token_hash
            record.csrf_token = rotated.csrf_token
            record.expires_at = rotated.expires_at
        response = RedirectResponse("/dashboard?notice=password-updated", status_code=303)
        response.set_cookie(
            "spark_session",
            raw,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="strict",
            max_age=8 * 3600,
        )
        return response

    @app.get("/dashboard")
    def dashboard(request: Request):
        with session_scope(engine) as db:
            user, _record, context = auth.user_context(request, db)
            tasks = db.scalars(
                select(SparkTask)
                .where(SparkTask.owner_user_id == user.id)
                .order_by(SparkTask.send_time, SparkTask.target_name, SparkTask.id)
                .limit(LEGACY_WORKSPACE_PAGE_SIZE)
            ).all()
            accounts = db.scalars(
                select(DouyinAccount)
                .where(DouyinAccount.owner_user_id == user.id)
                .order_by(DouyinAccount.created_at, DouyinAccount.id)
                .limit(LEGACY_WORKSPACE_PAGE_SIZE)
            ).all()
            task_total = int(
                db.scalar(
                    select(func.count(SparkTask.id)).where(
                        SparkTask.owner_user_id == user.id
                    )
                )
                or 0
            )
            account_total = int(
                db.scalar(
                    select(func.count(DouyinAccount.id)).where(
                        DouyinAccount.owner_user_id == user.id
                    )
                )
                or 0
            )
            runs = db.scalars(select(TaskRun).join(SparkTask).where(SparkTask.owner_user_id == user.id).order_by(TaskRun.scheduled_for.desc()).limit(5)).all()
            local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
            today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
            today_counts = db.execute(
                select(
                    func.count(TaskRun.id),
                    func.sum(case((TaskRun.status == "success", 1), else_=0)),
                    func.sum(case((TaskRun.status.in_(("failed", "skipped", "submitted_unconfirmed")), 1), else_=0)),
                    func.sum(case((TaskRun.status == "submitted_unconfirmed", 1), else_=0)),
                    func.sum(case((TaskRun.status == "failed", 1), else_=0)),
                )
                .join(SparkTask)
                .where(
                    SparkTask.owner_user_id == user.id,
                    TaskRun.scheduled_for >= today_start,
                )
            ).one()
            today_success, today_attention, today_unconfirmed, today_failed = (
                int(today_counts[index] or 0) for index in range(1, 5)
            )
            valid_accounts = int(
                db.scalar(
                    select(func.count(DouyinAccount.id)).where(
                        DouyinAccount.owner_user_id == user.id,
                        DouyinAccount.validation_state == "valid",
                    )
                )
                or 0
            )
            accounts.sort(key=lambda item: (item.validation_state == 'valid', item.display_name.casefold()))
            account_attention = int(
                db.scalar(
                    select(func.count(DouyinAccount.id)).where(
                        DouyinAccount.owner_user_id == user.id,
                        DouyinAccount.validation_state.in_(
                            ('invalid', 'temporary', 'verification')
                        ),
                    )
                )
                or 0
            )
            schedule_counts = db.execute(
                select(
                    func.sum(
                        case(
                            (
                                and_(
                                    SparkTask.enabled.is_(True),
                                    SparkTask.send_time < "12:00",
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    func.sum(
                        case(
                            (
                                and_(
                                    SparkTask.enabled.is_(True),
                                    SparkTask.send_time >= "12:00",
                                    SparkTask.send_time < "18:00",
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    func.sum(
                        case(
                            (
                                and_(
                                    SparkTask.enabled.is_(True),
                                    SparkTask.send_time >= "18:00",
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                ).where(SparkTask.owner_user_id == user.id)
            ).one()
            # Do not derive the next run from the bounded preview. A user with
            # more than 100 tasks must still see the actual next task.
            next_task = db.scalar(
                select(SparkTask)
                .where(
                    SparkTask.owner_user_id == user.id,
                    SparkTask.enabled.is_(True),
                    SparkTask.next_run_at.is_not(None),
                )
                .order_by(SparkTask.next_run_at, SparkTask.id)
                .limit(1)
            )
            onboarding = {
                "membership": bool(context.get("membership", {}).get("active")),
                "account": account_total > 0,
                "task": task_total > 0,
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
                today_success=today_success,
                today_attention=today_attention,
                today_unconfirmed=today_unconfirmed,
                today_failed=today_failed,
                next_task=next_task,
                workspace_limits={
                    "page_size": LEGACY_WORKSPACE_PAGE_SIZE,
                    "accounts_total": account_total,
                    "tasks_total": task_total,
                    "accounts_truncated": account_total > len(accounts),
                    "tasks_truncated": task_total > len(tasks),
                },
                schedule_periods={
                    "morning": int(schedule_counts[0] or 0),
                    "afternoon": int(schedule_counts[1] or 0),
                    "evening": int(schedule_counts[2] or 0),
                },
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
                .limit(LEGACY_WORKSPACE_PAGE_SIZE)
            ).all()
            account_total = int(
                db.scalar(
                    select(func.count(DouyinAccount.id)).where(
                        DouyinAccount.owner_user_id == user.id
                    )
                )
                or 0
            )
            return page(request, "accounts.html", title="抖音账号", accounts=accounts,
                        account_profiles=profile_map(db, [a.id for a in accounts]),
                        account_health=health_views(db, accounts),
                        workspace_limits={
                            "page_size": LEGACY_WORKSPACE_PAGE_SIZE,
                            "accounts_total": account_total,
                            "accounts_truncated": account_total > len(accounts),
                        }, **context)

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
    def account_conversations(
        request: Request,
        account_id: str,
        offset: int = Query(default=0, ge=0, le=1_000_000),
        limit: int = Query(default=ACCOUNT_CONVERSATION_PAGE_SIZE, ge=1, le=ACCOUNT_CONVERSATION_PAGE_SIZE),
    ):
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
                .order_by(DouyinContactIdentity.discovered_at.desc(), DouyinContactIdentity.sec_uid)
            ).all()
            contact_ids = [contact.sec_uid for contact in contacts]
            avatars = {
                item.sec_uid: safe_avatar_url(item.avatar_url)
                for item in db.scalars(
                    select(DouyinContactAvatar).where(
                        DouyinContactAvatar.account_id == account_id,
                        DouyinContactAvatar.sec_uid.in_(contact_ids),
                    )
                )
            } if contact_ids else {}
            conversation_count = int(
                db.scalar(
                    select(func.count(DouyinConversation.display_name)).where(
                        DouyinConversation.account_id == account_id
                    )
                )
                or 0
            )
            contact_count = int(
                db.scalar(
                    select(func.count(DouyinContactIdentity.sec_uid)).where(
                        DouyinContactIdentity.account_id == account_id
                    )
                )
                or 0
            )
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
            sorted_items = sorted(items, key=lambda item: item["name"])
            page = sorted_items[offset:offset + limit]
            next_offset = offset + len(page)
            sync_view = job_view(latest) if latest else None
            active_view = job_view(active) if active else None
            # Successful jobs expire after seven days, but their cached
            # contacts remain. No retained result therefore means unknown,
            # rather than a directory that has been exhausted.
            sync_has_more = (sync_view or {}).get("has_more")
            sync_limit = int((sync_view or {}).get("limit") or 0)
            sync_running = (active_view or {}).get("status") in ("queued", "running")
            can_sync_more = not sync_running and (
                sync_has_more is None or (sync_has_more is True and sync_limit < 100)
            )
            sync_next_limit = None
            if can_sync_more:
                known_limit = sync_limit if sync_has_more is True else max(sync_limit, len(sorted_items))
                # Cached aliases need not total a multiple of 20. Only send
                # a limit accepted by the directory-job endpoint, including
                # one final 100-contact probe when completeness is unknown.
                sync_next_limit = min(100, (max(known_limit, 0) // 20 + 1) * 20)
            return JSONResponse({"items": page,
                "offset": offset,
                "limit": limit,
                "next_offset": next_offset,
                "total": len(sorted_items),
                "has_more": next_offset < len(sorted_items),
                "sync_has_more": sync_has_more,
                "sync_limit": sync_limit,
                "can_sync_more": can_sync_more,
                "sync_next_limit": sync_next_limit,
                "sync": sync_view,
                "active_sync": active_view}, headers={"Cache-Control": "no-store"})

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
            if engine.dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
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
            if engine.dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            user, record = auth.current(request, db)
            auth.csrf(record, csrf_token)
            service = TaskService(db, AccountService(db, cipher, AuditService(db)), AuditService(db))
            task = service.get_owned(user.id, task_id)
            enabled = not task.enabled
            try:
                service.set_enabled_owned(
                    user.id,
                    task_id,
                    enabled,
                    enforce_capacity=user.role != "admin",
                )
            except ValidationError as error:
                if "重新登录" in str(error):
                    return RedirectResponse(
                        "/accounts?notice=account-relogin", status_code=303
                    )
                if "会员未开通或已过期" in str(error):
                    return RedirectResponse(
                        "/subscription?required=1", status_code=303
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
                select(DouyinAccount)
                .where(DouyinAccount.owner_user_id == user.id)
                .order_by(DouyinAccount.created_at, DouyinAccount.id)
                .limit(LEGACY_WORKSPACE_PAGE_SIZE)
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

    from spark_console.web.security_headers import SecurityHeadersMiddleware
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    # Resolve the real scheme/client before the SPA can return early.
    app.add_middleware(TrustedProxyMiddleware, trusted_proxy_cidrs=settings.trusted_proxy_cidrs)
    return app


def main() -> None:
    settings = Settings.from_env(os.environ)
    engine = create_engine_for(settings)
    create_schema(engine)
    uvicorn.run(create_app(settings, engine), host=settings.web_bind, port=settings.web_port, proxy_headers=False)


if __name__ == "__main__":
    main()
