from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Callable

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import session_scope
from spark_console.run_attempts import for_runs as attempts_for_runs
from spark_console.run_events import for_runs as events_for_runs
from spark_console.models import (
    DouyinAccount,
    InviteCode,
    RedemptionCode,
    SparkTask,
    SparkTaskTargetIdentity,
    TaskRun,
    User,
    WorkerLock,
)
from spark_console.ops_monitor import operations_snapshot
from spark_console.ops_history import history_path, read_history, recent_activity
from spark_console.security import PasswordService
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.audits import AuditService
from spark_console.services.invites import InviteService
from spark_console.services.subscriptions import SubscriptionService
from spark_console.services.users import UserService
from spark_console.task_targets import targets_conflict, task_target_key
from spark_console.web.admin_queries import (
    ADMIN_PAGE_SIZE,
    admin_stats,
    list_audit_events,
    list_runs,
    list_subscriptions,
    list_tasks,
    list_users,
    overview_expiring_users,
    overview_recent_runs,
)
from spark_console.web.auth import WebAuth
from spark_console.today_sends import board as today_send_board
from spark_console.web.task_groups import list_task_groups, list_group_tasks
from spark_console.web.registration_routes import AdminInvite
from spark_console.web.subscription_routes import AdminRedemptionCode


PageRenderer = Callable[..., object]
OPERATIONS_NO_STORE = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _pagination(page: int, total: int) -> dict[str, int | bool]:
    pages = max(1, math.ceil(total / ADMIN_PAGE_SIZE))
    current = min(max(1, page), pages)
    return {
        "page": current,
        "page_size": ADMIN_PAGE_SIZE,
        "total": total,
        "pages": pages,
        "has_previous": current > 1,
        "has_prev": current > 1,
        "has_next": current < pages,
    }


def _page_offset(pagination: dict[str, int | bool]) -> int:
    return (int(pagination["page"]) - 1) * ADMIN_PAGE_SIZE


def _invite_status(invite: InviteCode, now: datetime) -> str:
    if invite.used_at is not None:
        return "已使用"
    if invite.revoked_at is not None:
        return "已撤销"
    if _aware(invite.expires_at) <= now:
        return "已过期"
    return "有效"


def _redemption_status(code: RedemptionCode, now: datetime) -> str:
    if code.redeemed_at is not None:
        return "已兑换"
    if code.revoked_at is not None:
        return "已撤销"
    if _aware(code.expires_at) <= now:
        return "已过期"
    return "待兑换"


def _invite_filters(q: str, status: str, now: datetime):
    filters = []
    query = q.strip()
    if query:
        pattern = f"%{query}%"
        filters.append(
            or_(InviteCode.id.ilike(pattern), User.username.ilike(pattern))
        )
    if status in {"active", "valid", "available"}:
        filters.extend(
            [
                InviteCode.used_at.is_(None),
                InviteCode.revoked_at.is_(None),
                InviteCode.expires_at > now,
            ]
        )
    elif status in {"used", "redeemed"}:
        filters.append(InviteCode.used_at.is_not(None))
    elif status == "revoked":
        filters.append(InviteCode.revoked_at.is_not(None))
    elif status == "expired":
        filters.extend(
            [
                InviteCode.used_at.is_(None),
                InviteCode.revoked_at.is_(None),
                InviteCode.expires_at <= now,
            ]
        )
    return filters


def _redemption_filters(q: str, status: str, now: datetime):
    filters = []
    query = q.strip()
    if query:
        pattern = f"%{query}%"
        filters.append(
            or_(RedemptionCode.id.ilike(pattern), User.username.ilike(pattern))
        )
    if status in {"active", "valid", "available"}:
        filters.extend(
            [
                RedemptionCode.redeemed_at.is_(None),
                RedemptionCode.revoked_at.is_(None),
                RedemptionCode.expires_at > now,
            ]
        )
    elif status in {"used", "redeemed"}:
        filters.append(RedemptionCode.redeemed_at.is_not(None))
    elif status == "revoked":
        filters.append(RedemptionCode.revoked_at.is_not(None))
    elif status == "expired":
        filters.extend(
            [
                RedemptionCode.redeemed_at.is_(None),
                RedemptionCode.revoked_at.is_(None),
                RedemptionCode.expires_at <= now,
            ]
        )
    return filters


def _list_invites(
    db: Session,
    cipher: CookieCipher,
    *,
    q: str,
    status: str,
    page: int,
) -> tuple[list[AdminInvite], dict[str, int | bool]]:
    now = datetime.now(timezone.utc)
    filters = _invite_filters(q, status, now)
    total = int(
        db.scalar(
            select(func.count(InviteCode.id))
            .select_from(InviteCode)
            .outerjoin(User, InviteCode.used_by_user_id == User.id)
            .where(*filters)
        )
        or 0
    )
    pagination = _pagination(page, total)
    rows = db.execute(
        select(InviteCode, User.username)
        .outerjoin(User, InviteCode.used_by_user_id == User.id)
        .where(*filters)
        .order_by(InviteCode.created_at.desc(), InviteCode.id)
        .limit(ADMIN_PAGE_SIZE)
        .offset(_page_offset(pagination))
    ).all()
    service = InviteService(db, AuditService(db), cipher)
    items = []
    for invite, username in rows:
        item_status = _invite_status(invite, now)
        items.append(
            AdminInvite(
                invite=invite,
                status=item_status,
                used_by_username=username,
                can_revoke=item_status == "有效",
                code=service.reveal(invite.id),
            )
        )
    return items, pagination


def _list_redemption_codes(
    db: Session,
    *,
    q: str,
    status: str,
    page: int,
) -> tuple[list[AdminRedemptionCode], dict[str, int | bool]]:
    now = datetime.now(timezone.utc)
    filters = _redemption_filters(q, status, now)
    total = int(
        db.scalar(
            select(func.count(RedemptionCode.id))
            .select_from(RedemptionCode)
            .outerjoin(User, RedemptionCode.redeemed_by_user_id == User.id)
            .where(*filters)
        )
        or 0
    )
    pagination = _pagination(page, total)
    rows = db.execute(
        select(RedemptionCode, User.username)
        .outerjoin(User, RedemptionCode.redeemed_by_user_id == User.id)
        .where(*filters)
        .order_by(RedemptionCode.created_at.desc(), RedemptionCode.id)
        .limit(ADMIN_PAGE_SIZE)
        .offset(_page_offset(pagination))
    ).all()
    items = []
    for code, username in rows:
        item_status = _redemption_status(code, now)
        items.append(
            AdminRedemptionCode(
                code=code,
                status=item_status,
                used_by_username=username,
                can_revoke=item_status == "待兑换",
            )
        )
    return items, pagination


def _worker_summary(db: Session) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    lock = db.get(WorkerLock, 1)
    online = bool(
        lock is not None
        and lock.worker_id
        and lock.lease_until is not None
        and _aware(lock.lease_until) > now
    )
    return {
        "online": online,
        "status": "online" if online else "offline",
        "worker_id": lock.worker_id if lock else None,
        "lease_until": lock.lease_until if lock else None,
    }


def _settings_summary(settings: Settings) -> dict[str, object]:
    try:
        database_backend = make_url(settings.database_url).get_backend_name()
    except Exception:
        database_backend = "unknown"
    return {
        "timezone": settings.timezone,
        "web_bind": settings.web_bind,
        "web_port": settings.web_port,
        "secure_cookies": settings.secure_cookies,
        "worker_poll_seconds": settings.worker_poll_seconds,
        "clock_offset_limit_seconds": settings.clock_offset_limit_seconds,
        "database_backend": database_backend,
        "data_dir": str(settings.data_dir),
    }


def _validate_tasks_can_enable(db: Session, tasks: list[SparkTask]) -> None:
    candidates = [task for task in tasks if not task.enabled]
    if not candidates:
        return
    owner_ids = {task.owner_user_id for task in candidates}
    owners = {
        owner.id: owner
        for owner in db.scalars(select(User).where(User.id.in_(owner_ids))).all()
    }
    subscriptions = SubscriptionService(db, AuditService(db))
    checked_owners: set[str] = set()
    bindings = {
        task_id: sec_uid
        for task_id, sec_uid in db.execute(
            select(
                SparkTaskTargetIdentity.task_id,
                SparkTaskTargetIdentity.sec_uid,
            ).where(
                SparkTaskTargetIdentity.task_id.in_([task.id for task in candidates])
            )
        ).all()
    }
    active_targets: dict[str, list[tuple[str, str, str | None]]] = {}
    account_ids = {
        task.douyin_account_id
        for task in candidates
        if task.douyin_account_id is not None
    }
    if account_ids:
        for account_id, send_time, target_name, sec_uid in db.execute(
            select(
                SparkTask.douyin_account_id,
                SparkTask.send_time,
                SparkTask.target_name,
                SparkTaskTargetIdentity.sec_uid,
            )
            .outerjoin(
                SparkTaskTargetIdentity,
                SparkTaskTargetIdentity.task_id == SparkTask.id,
            )
            .where(
                SparkTask.douyin_account_id.in_(account_ids),
                SparkTask.enabled.is_(True),
            )
        ).all():
            active_targets.setdefault(str(account_id), []).append(
                (send_time, target_name, sec_uid)
            )
    selected_targets: list[tuple[str, str, str, str | None]] = []
    target_key_updates: list[tuple[SparkTask, str]] = []
    for task in candidates:
        if task.douyin_account_id is None:
            raise HTTPException(400, "账号已删除，无法启用任务")
        account = db.get(DouyinAccount, task.douyin_account_id)
        if account is None or account.validation_state == "invalid":
            raise HTTPException(400, "抖音账号登录状态已失效，请先重新登录")
        owner = owners.get(task.owner_user_id)
        if owner is None:
            raise HTTPException(404, "任务归属用户不存在")
        if owner.role != "admin" and owner.id not in checked_owners:
            try:
                subscriptions.require_active(owner.id)
            except (ValidationError, Conflict) as error:
                raise HTTPException(400, str(error)) from error
            checked_owners.add(owner.id)
        stable_target = bindings.get(task.id)
        if any(
            selected_account_id == task.douyin_account_id
            and selected_send_time == task.send_time
            and targets_conflict(
                selected_name,
                selected_sec_uid,
                task.target_name,
                stable_target,
            )
            for (
                selected_account_id,
                selected_send_time,
                selected_name,
                selected_sec_uid,
            ) in selected_targets
        ):
            raise HTTPException(400, "相同账号、好友和时间的启用任务已存在")
        if any(
            active_send_time == task.send_time
            and targets_conflict(
                active_name,
                active_sec_uid,
                task.target_name,
                stable_target,
            )
            for active_send_time, active_name, active_sec_uid in active_targets.get(
                task.douyin_account_id, []
            )
        ):
            raise HTTPException(400, "相同账号、好友和时间的启用任务已存在")
        selected_targets.append(
            (
                task.douyin_account_id,
                task.send_time,
                task.target_name,
                stable_target,
            )
        )
        target_key_updates.append(
            (task, task_target_key(task.target_name, stable_target))
        )
    for task, target_key in target_key_updates:
        task.target_key = target_key


def render_admin_codes(
    request: Request,
    db: Session,
    context: dict,
    *,
    page_renderer: PageRenderer,
    cipher: CookieCipher,
    q: str = "",
    status: str = "",
    page: int = 1,
    invite_page: int | None = None,
    redemption_page: int | None = None,
    **extra,
):
    invites, invite_pagination = _list_invites(
        db,
        cipher,
        q=q,
        status=status,
        page=invite_page or page,
    )
    redemption_codes, redemption_pagination = _list_redemption_codes(
        db,
        q=q,
        status=status,
        page=redemption_page or page,
    )
    return page_renderer(
        request,
        "admin_codes.html",
        title="兑换与邀请",
        admin_section="codes",
        q=q,
        status=status,
        stats=admin_stats(db),
        invites=invites,
        redemption_codes=redemption_codes,
        invite_pagination=invite_pagination,
        redemption_pagination=redemption_pagination,
        pagination=redemption_pagination,
        **extra,
        **context,
    )


def build_admin_router(
    engine: Engine,
    settings: Settings,
    auth: WebAuth,
    passwords: PasswordService,
    cipher: CookieCipher,
    page_renderer: PageRenderer,
) -> APIRouter:
    router = APIRouter()

    def operations_admin_context(request: Request, db: Session) -> dict:
        admin, record = auth.current(request, db)
        if admin.role != "admin":
            raise HTTPException(404)
        return {
            "user": admin,
            "csrf_token": record.csrf_token,
            "is_admin": True,
        }

    @router.get("/admin")
    def admin_root(request: Request):
        with session_scope(engine) as db:
            auth.admin_context(request, db)
        return RedirectResponse("/admin/overview", status_code=303)

    @router.get("/admin/overview")
    def overview(request: Request):
        with session_scope(engine) as db:
            _admin, _record, context = auth.admin_context(request, db)
            return page_renderer(
                request,
                "admin_overview.html",
                title="业务概览",
                admin_section="overview",
                stats=admin_stats(db),
                today=today_send_board(db),
                recent_runs=overview_recent_runs(db),
                expiring_users=overview_expiring_users(db),
                worker=_worker_summary(db),
                **context,
            )

    @router.get("/admin/today")
    def today_page(request: Request):
        with session_scope(engine) as db:
            _admin, _record, context = auth.admin_context(request, db)
            return page_renderer(
                request,
                "admin_today.html",
                title="今日发送",
                admin_section="today",
                today=today_send_board(db),
                **context,
            )

    @router.post("/admin/runs/{run_id}/retry")
    def admin_retry_run(request: Request, run_id: str, csrf_token: str = Form(default="")):
        with session_scope(engine) as db:
            _admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            run = db.get(TaskRun, run_id)
            if run is None:
                raise HTTPException(404, "执行记录不存在")
            task = db.get(SparkTask, run.task_id)
            if task is None:
                raise HTTPException(404, "任务不存在")
            from spark_console.services.tasks import TaskService
            from spark_console.services.accounts import AccountService
            try:
                TaskService(db, AccountService(db, cipher, AuditService(db)), AuditService(db)).request_unsubmitted_retry(
                    task.owner_user_id, run_id, datetime.now(timezone.utc)
                )
            except NotFound:
                raise HTTPException(404, "执行记录不存在")
            except ValidationError:
                return RedirectResponse("/admin/today?notice=retry-rejected", status_code=303)
            except Conflict:
                return RedirectResponse("/admin/today?notice=retry-busy", status_code=303)
        return RedirectResponse("/admin/today?notice=retry-queued", status_code=303)

    @router.get("/admin/users")
    def users_page(
        request: Request, q: str = "", status: str = "", page: int = 1
    ):
        with session_scope(engine) as db:
            _admin, _record, context = auth.admin_context(request, db)
            users, pagination = list_users(db, q=q, status=status, page=page)
            return page_renderer(
                request,
                "admin_users.html",
                title="用户管理",
                admin_section="users",
                users=users,
                pagination=pagination,
                q=q,
                status=status,
                stats=admin_stats(db),
                **context,
            )

    @router.get("/admin/subscriptions")
    def subscriptions_page(
        request: Request, q: str = "", status: str = "", page: int = 1
    ):
        with session_scope(engine) as db:
            _admin, _record, context = auth.admin_context(request, db)
            subscriptions, pagination = list_subscriptions(
                db, q=q, status=status, page=page
            )
            return page_renderer(
                request,
                "admin_subscriptions.html",
                title="订阅管理",
                admin_section="subscriptions",
                subscriptions=subscriptions,
                users=subscriptions,
                pagination=pagination,
                q=q,
                status=status,
                stats=admin_stats(db),
                **context,
            )

    @router.get("/admin/codes")
    def codes_page(
        request: Request,
        q: str = "",
        status: str = "",
        page: int = 1,
        invite_page: int | None = None,
        redemption_page: int | None = None,
    ):
        with session_scope(engine) as db:
            _admin, _record, context = auth.admin_context(request, db)
            return render_admin_codes(
                request,
                db,
                context,
                page_renderer=page_renderer,
                cipher=cipher,
                q=q,
                status=status,
                page=page,
                invite_page=invite_page,
                redemption_page=redemption_page,
            )

    @router.get("/admin/tasks")
    def tasks_page(
        request: Request, q: str = "", status: str = "", page: int = 1
    ):
        with session_scope(engine) as db:
            _admin, _record, context = auth.admin_context(request, db)
            groups, pagination = list_task_groups(db, q=q, status=status, page=page)
            return page_renderer(
                request,
                "admin_tasks.html",
                title="任务管理",
                admin_section="tasks",
                groups=groups,
                pagination=pagination,
                q=q,
                status=status,
                stats=admin_stats(db),
                **context,
            )

    @router.get("/admin/tasks/groups/{owner_id}/{account_id}")
    def task_group_page(request: Request, owner_id: str, account_id: str, q: str = '', status: str = '', page: int = 1):
        with session_scope(engine) as db:
            _admin, _record, context = auth.admin_context(request, db)
            tasks, pagination = list_group_tasks(db, owner_id, account_id, q, status, page)
            response = page_renderer(request, 'admin_task_group.html', tasks=tasks, pagination=pagination,
                group_url=f'/admin/tasks/groups/{owner_id}/{account_id}', q=q, status=status, **context)
            response.headers['Cache-Control'] = 'no-store'
            return response

    @router.get("/admin/runs")
    def runs_page(
        request: Request, q: str = "", status: str = "", page: int = 1,
        start: str = "", end: str = "",
    ):
        with session_scope(engine) as db:
            _admin, _record, context = auth.admin_context(request, db)
            try:
                runs, pagination = list_runs(db, q=q, status=status, page=page, start=start, end=end)
            except ValueError:
                raise HTTPException(400, '日期范围不正确，请选择起始日期和结束日期')
            return page_renderer(
                request,
                "admin_runs.html",
                title="执行记录",
                admin_section="runs",
                runs=runs,
                attempt_history=attempts_for_runs(db, [r.id for r in runs]),
                event_history=events_for_runs(db, [r.id for r in runs]),
                start=start,
                end=end,
                pagination=pagination,
                q=q,
                status=status,
                stats=admin_stats(db),
                **context,
            )

    @router.get("/admin/audit")
    def audit_page(
        request: Request, q: str = "", status: str = "", page: int = 1
    ):
        with session_scope(engine) as db:
            _admin, _record, context = auth.admin_context(request, db)
            events, pagination = list_audit_events(
                db, q=q, status=status, page=page
            )
            return page_renderer(
                request,
                "admin_audit.html",
                title="审计日志",
                admin_section="audit",
                audit_events=events,
                events=events,
                pagination=pagination,
                q=q,
                status=status,
                stats=admin_stats(db),
                **context,
            )

    @router.get("/admin/operations")
    def operations_page(request: Request):
        with session_scope(engine) as db:
            context = operations_admin_context(request, db)
            snapshot = operations_snapshot(db, settings)
            from spark_console.business_health import business_health
            business = business_health(db)
            response = page_renderer(
                request,
                "admin_operations.html",
                title="运维监控",
                admin_section="operations",
                operations=snapshot,
                business=business,
                **context,
            )
            response.headers.update(OPERATIONS_NO_STORE)
            return response

    @router.get("/admin/operations/snapshot")
    def operations_snapshot_api(request: Request):
        with session_scope(engine) as db:
            operations_admin_context(request, db)
            snapshot = operations_snapshot(db, settings)
            from spark_console.business_health import business_health
            snapshot['business']=business_health(db)
        return JSONResponse(snapshot, headers=OPERATIONS_NO_STORE)

    @router.get("/admin/operations/history")
    def operations_history_api(request: Request, hours: int = 1):
        hours = hours if hours in (1, 6, 24) else 1
        with session_scope(engine) as db:
            operations_admin_context(request, db)
            history = read_history(history_path(settings), hours)
            history["events"] = recent_activity(db, hours)
        return JSONResponse(history, headers=OPERATIONS_NO_STORE)

    @router.get("/admin/settings")
    def settings_page(request: Request):
        with session_scope(engine) as db:
            _admin, _record, context = auth.admin_context(request, db)
            return page_renderer(
                request,
                "admin_settings.html",
                title="系统设置",
                admin_section="settings",
                system_settings=_settings_summary(settings),
                worker=_worker_summary(db),
                stats=admin_stats(db),
                **context,
            )

    @router.post("/admin/users")
    def create_user(
        request: Request,
        csrf_token: str = Form(default=""),
        username: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            _admin, record, context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                _user, temporary = UserService(
                    db, passwords, AuditService(db)
                ).create(username)
            except (Conflict, ValidationError, ValueError) as error:
                users, pagination = list_users(db)
                return page_renderer(
                    request,
                    "admin_users.html",
                    status_code=400,
                    title="用户管理",
                    admin_section="users",
                    users=users,
                    pagination=pagination,
                    q="",
                    status="",
                    stats=admin_stats(db),
                    error=str(error),
                    **context,
                )
            users, pagination = list_users(db)
            return page_renderer(
                request,
                "admin_users.html",
                title="用户管理",
                admin_section="users",
                users=users,
                pagination=pagination,
                q="",
                status="",
                stats=admin_stats(db),
                one_time_password=temporary,
                **context,
            )

    @router.post("/admin/users/{user_id}/reset-password")
    def reset_user_password(
        request: Request,
        user_id: str,
        csrf_token: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            admin, record, context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                temporary = UserService(
                    db, passwords, AuditService(db)
                ).reset_password(admin.id, user_id)
            except NotFound as error:
                raise HTTPException(404, str(error)) from error
            users, pagination = list_users(db)
            return page_renderer(
                request,
                "admin_users.html",
                title="用户管理",
                admin_section="users",
                users=users,
                pagination=pagination,
                q="",
                status="",
                stats=admin_stats(db),
                one_time_password=temporary,
                **context,
            )

    @router.post("/admin/users/bulk")
    def bulk_update_users(
        request: Request,
        csrf_token: str = Form(default=""),
        action: str = Form(default=""),
        user_ids: list[str] | None = Form(default=None),
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            normalized_action = action.strip().lower()
            if normalized_action not in {"enable", "disable"}:
                raise HTTPException(400, "不支持的批量操作")
            selected_ids = list(dict.fromkeys(user_ids or []))
            if not selected_ids:
                raise HTTPException(400, "请至少选择一个用户")
            users = db.scalars(
                select(User).where(User.id.in_(selected_ids))
            ).all()
            if len(users) != len(selected_ids):
                raise HTTPException(404, "部分用户不存在")
            service = UserService(db, passwords, AuditService(db))
            for user in users:
                if user.role == "admin":
                    continue
                service.set_disabled(
                    admin.id, user.id, normalized_action == "disable"
                )
        return RedirectResponse("/admin/users", status_code=303)

    @router.post("/admin/users/{user_id}/toggle")
    def toggle_user(
        request: Request,
        user_id: str,
        csrf_token: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            user = db.get(User, user_id)
            if user is None:
                raise HTTPException(404)
            if user.id == admin.id:
                raise HTTPException(400, "不能停用当前管理员账号")
            UserService(db, passwords, AuditService(db)).set_disabled(
                admin.id, user.id, user.status == "active"
            )
        return RedirectResponse("/admin/users", status_code=303)

    @router.post("/admin/users/{user_id}/delete")
    def delete_user(
        request: Request,
        user_id: str,
        csrf_token: str = Form(default=""),
        confirmation: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                UserService(db, passwords, AuditService(db)).delete(
                    admin.id, user_id, confirmation
                )
            except NotFound as error:
                raise HTTPException(404, str(error)) from error
            except (ValidationError, Conflict) as error:
                raise HTTPException(400, str(error)) from error
        return RedirectResponse("/admin/users", status_code=303)

    @router.post("/admin/subscriptions/{user_id}/extend")
    def extend_subscription(
        request: Request,
        user_id: str,
        csrf_token: str = Form(default=""),
        duration_days: int = Form(default=30),
        max_accounts: int = Form(default=1),
        max_tasks: int = Form(default=10),
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                SubscriptionService(db, AuditService(db)).extend(
                    admin.id,
                    user_id,
                    duration_days=duration_days,
                    max_accounts=max_accounts,
                    max_tasks=max_tasks,
                )
            except NotFound as error:
                raise HTTPException(404, str(error)) from error
            except ValidationError as error:
                raise HTTPException(400, str(error)) from error
        return RedirectResponse("/admin/subscriptions", status_code=303)

    @router.post("/admin/subscriptions/{user_id}/limits")
    def set_subscription_limits(
        request: Request,
        user_id: str,
        csrf_token: str = Form(default=""),
        max_accounts: int = Form(default=1),
        max_tasks: int = Form(default=10),
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                SubscriptionService(db, AuditService(db)).set_limits(
                    admin.id,
                    user_id,
                    max_accounts=max_accounts,
                    max_tasks=max_tasks,
                )
            except NotFound as error:
                raise HTTPException(404, str(error)) from error
            except ValidationError as error:
                raise HTTPException(400, str(error)) from error
        return RedirectResponse("/admin/subscriptions", status_code=303)

    @router.post("/admin/subscriptions/{user_id}/revoke")
    def revoke_subscription(
        request: Request,
        user_id: str,
        csrf_token: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                SubscriptionService(db, AuditService(db)).revoke(
                    admin.id, user_id
                )
            except NotFound as error:
                raise HTTPException(404, str(error)) from error
            except (Conflict, ValidationError) as error:
                raise HTTPException(400, str(error)) from error
        return RedirectResponse("/admin/subscriptions", status_code=303)

    @router.post("/admin/codes/invites")
    def create_invite(
        request: Request, csrf_token: str = Form(default="")
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            InviteService(db, AuditService(db), cipher).create(admin.id)
        return RedirectResponse("/admin/codes", status_code=303)

    @router.post("/admin/codes/invites/{invite_id}/revoke")
    def revoke_invite(
        request: Request,
        invite_id: str,
        csrf_token: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                InviteService(db, AuditService(db), cipher).revoke(
                    admin.id, invite_id
                )
            except ValidationError as error:
                raise HTTPException(400, str(error)) from error
        return RedirectResponse("/admin/codes", status_code=303)

    @router.post("/admin/codes/invites/{invite_id}/delete")
    def delete_invite(
        request: Request,
        invite_id: str,
        csrf_token: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                InviteService(db, AuditService(db), cipher).delete(
                    admin.id, invite_id
                )
            except ValidationError as error:
                raise HTTPException(404, "邀请码不存在") from error
        return RedirectResponse("/admin/codes", status_code=303)

    @router.post("/admin/codes/redemptions")
    def create_redemption_codes(
        request: Request,
        csrf_token: str = Form(default=""),
        duration_days: int = Form(default=30),
        max_accounts: int = Form(default=1),
        max_tasks: int = Form(default=10),
        quantity: int = Form(default=1),
    ):
        with session_scope(engine) as db:
            admin, record, context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            if not 1 <= quantity <= 20:
                raise HTTPException(400, "每次最多生成 20 个兑换码")
            service = SubscriptionService(db, AuditService(db))
            try:
                plaintext_codes = [
                    service.create_redemption_code(
                        admin.id,
                        duration_days=duration_days,
                        max_accounts=max_accounts,
                        max_tasks=max_tasks,
                    )[1]
                    for _ in range(quantity)
                ]
            except ValidationError as error:
                raise HTTPException(400, str(error)) from error
            return render_admin_codes(
                request,
                db,
                context,
                page_renderer=page_renderer,
                cipher=cipher,
                one_time_codes=plaintext_codes,
            )

    @router.post("/admin/codes/redemptions/{code_id}/revoke")
    def revoke_redemption_code(
        request: Request,
        code_id: str,
        csrf_token: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                SubscriptionService(
                    db, AuditService(db)
                ).revoke_redemption_code(admin.id, code_id)
            except NotFound as error:
                raise HTTPException(404, str(error)) from error
            except Conflict as error:
                raise HTTPException(409, str(error)) from error
        return RedirectResponse("/admin/codes", status_code=303)

    @router.post("/admin/tasks/{task_id}/toggle")
    def toggle_task(
        request: Request,
        task_id: str,
        csrf_token: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            task = db.get(SparkTask, task_id)
            if task is None:
                raise HTTPException(404)
            _validate_tasks_can_enable(db, [task])
            task.enabled = not task.enabled
            AuditService(db).write(
                admin.id,
                "task.enabled" if task.enabled else "task.disabled",
                "spark_task",
                task.id,
            )
        return RedirectResponse("/admin/tasks", status_code=303)

    @router.post("/admin/tasks/bulk")
    def bulk_update_tasks(
        request: Request,
        csrf_token: str = Form(default=""),
        action: str = Form(default=""),
        task_ids: list[str] | None = Form(default=None),
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            normalized_action = action.strip().lower()
            if normalized_action in {"pause", "disable"}:
                enable = False
            elif normalized_action in {"resume", "enable"}:
                enable = True
            else:
                raise HTTPException(400, "不支持的批量操作")
            selected_ids = list(dict.fromkeys(task_ids or []))
            if not selected_ids:
                raise HTTPException(400, "请至少选择一个任务")
            tasks = db.scalars(
                select(SparkTask).where(SparkTask.id.in_(selected_ids))
            ).all()
            if len(tasks) != len(selected_ids):
                raise HTTPException(404, "部分任务不存在")
            if enable:
                _validate_tasks_can_enable(db, tasks)
            audit = AuditService(db)
            for task in tasks:
                if task.enabled == enable:
                    continue
                task.enabled = enable
                audit.write(
                    admin.id,
                    "task.enabled" if enable else "task.disabled",
                    "spark_task",
                    task.id,
                )
        return RedirectResponse("/admin/tasks", status_code=303)

    @router.post("/admin/tasks/{task_id}/delete")
    def delete_task(
        request: Request,
        task_id: str,
        csrf_token: str = Form(default=""),
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            task = db.get(SparkTask, task_id)
            if task is None:
                raise HTTPException(404)
            running = db.scalar(
                select(TaskRun.id)
                .where(TaskRun.task_id == task.id, TaskRun.status == "running")
                .limit(1)
            )
            if running is not None:
                raise HTTPException(409, "任务正在执行，请完成后再删除")
            db.delete(task)
            AuditService(db).write(
                admin.id, "task.deleted", "spark_task", task_id
            )
        return RedirectResponse("/admin/tasks", status_code=303)

    return router
