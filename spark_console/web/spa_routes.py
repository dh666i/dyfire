"""JSON bootstrap endpoints for the single React console.

The existing command and automation endpoints remain the source of truth.  These
small read models keep the browser shell independent from the legacy templates
without duplicating any task or account mutation logic.
"""
import math
from datetime import datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, or_, select

from spark_console.db import session_scope
from spark_console.models import (
    AuditEvent,
    DouyinAccount,
    InviteCode,
    MailDelivery,
    MailPreference,
    NotificationPolicy,
    RedemptionCode,
    SparkTask,
    SparkTaskTargetIdentity,
    TaskRun,
    User,
    UserSubscription,
)
from spark_console.services.account_health import views as account_health_views
from spark_console.services.directory import profile_map, task_avatar_map
from spark_console.services.accounts import AccountService
from spark_console.services.tasks import TaskService
from spark_console.services.audits import AuditService
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.users import UserService
from spark_console.security import PasswordService
from spark_console.services.subscriptions import (
    NEVER_EXPIRES_AT,
    SubscriptionService,
    pricing_plans,
    task_limit_label,
)
from spark_console.web.membership import membership_summary
from spark_console.web.auth import WebAuth
from spark_console.web.admin_queries import admin_stats, list_audit_events, list_runs, list_subscriptions, list_users
from spark_console.web.task_groups import list_task_groups, list_group_tasks
from spark_console.ops_monitor import operations_snapshot
from spark_console.today_sends import board as today_send_board
from spark_console.run_attempts import for_runs as attempts_for_runs
from spark_console.run_events import for_runs as events_for_runs
from spark_console.web.app import run_view_status, run_guidance, shanghai_time, RUN_STAGE_LABELS


# The shell bootstrap is fetched on every route transition. Keep it bounded so
# one customer with a large "unlimited" plan cannot make every page load
# proportional to their complete task history. The dedicated tasks endpoint
# below loads additional rows only when the task workspace asks for them.
APP_BOOTSTRAP_PAGE_SIZE = 100
WORKSPACE_LIST_PAGE_SIZE = 100


def _task_payload(db, tasks):
    task_ids = [task.id for task in tasks]
    identities = {
        identity.task_id: identity
        for identity in db.scalars(
            select(SparkTaskTargetIdentity).where(SparkTaskTargetIdentity.task_id.in_(task_ids))
        ).all()
    } if task_ids else {}
    avatars = task_avatar_map(db, tasks)
    return [
        {
            "id": t.id,
            "name": t.target_name,
            "accountId": t.douyin_account_id,
            "sec_uid": identities[t.id].sec_uid if identities.get(t.id) else "",
            "time": t.send_time,
            "kind": "续火花表情" if t.message_kind == "douyin_sticker" else "文字消息",
            "message": t.message_template,
            "enabled": t.enabled,
            "avatar": avatars.get(t.id),
        }
        for t in tasks
    ]


def _date_value(value):
    return value.isoformat() if value is not None else None


def _aware(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def build_spa_router(engine, auth: WebAuth, settings=None, cipher=None):
    router = APIRouter()

    @router.get("/api/app/bootstrap")
    def bootstrap(request: Request):
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            account_total = db.scalar(
                select(func.count(DouyinAccount.id)).where(
                    DouyinAccount.owner_user_id == user.id
                )
            ) or 0
            task_total = db.scalar(
                select(func.count(SparkTask.id)).where(
                    SparkTask.owner_user_id == user.id
                )
            ) or 0
            accounts = db.scalars(
                select(DouyinAccount)
                .where(DouyinAccount.owner_user_id == user.id)
                .order_by(DouyinAccount.created_at.desc(), DouyinAccount.id)
                .limit(APP_BOOTSTRAP_PAGE_SIZE)
            ).all()
            tasks = db.scalars(
                select(SparkTask)
                .where(SparkTask.owner_user_id == user.id)
                .order_by(SparkTask.send_time, SparkTask.target_name, SparkTask.id)
                .limit(APP_BOOTSTRAP_PAGE_SIZE)
            ).all()
            task_counts = dict(
                db.execute(
                    select(SparkTask.douyin_account_id, func.count(SparkTask.id))
                    .where(SparkTask.owner_user_id == user.id)
                    .group_by(SparkTask.douyin_account_id)
                ).all()
            )
            enabled_task_count = int(
                db.scalar(
                    select(func.count(SparkTask.id)).where(
                        SparkTask.owner_user_id == user.id,
                        SparkTask.enabled.is_(True),
                    )
                )
                or 0
            )
            health = account_health_views(db, accounts)
            profiles = profile_map(db, [a.id for a in accounts])
            now = datetime.now(timezone.utc)
            shanghai = ZoneInfo("Asia/Shanghai")
            local_now = now.astimezone(shanghai)
            today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
            runs = db.scalars(
                select(TaskRun)
                .join(SparkTask)
                .where(
                    SparkTask.owner_user_id == user.id,
                    TaskRun.scheduled_for >= today_start,
                )
                .order_by(TaskRun.scheduled_for.desc())
                .limit(80)
            ).all()
            recent_run_rows = db.execute(
                select(TaskRun, SparkTask)
                .join(SparkTask)
                .where(
                    SparkTask.owner_user_id == user.id,
                    TaskRun.scheduled_for >= now - timedelta(days=7),
                )
                .order_by(TaskRun.scheduled_for.desc())
                .limit(30)
            ).all()
            bootstrap_event_ids = [r.id for r in runs] + [r.id for r, _task in recent_run_rows]
            bootstrap_events = events_for_runs(db, bootstrap_event_ids)

            def timeline_for(run):
                return [{
                    "sequence": event.sequence,
                    "event": event.event,
                    "stage": RUN_STAGE_LABELS.get(event.stage, event.stage),
                    "occurred_at": shanghai_time(event.occurred_at),
                    "detail": event.detail,
                } for event in bootstrap_events.get(run.id, [])]
            latest = {}
            for run in runs:
                latest.setdefault(run.task_id, run.status)
            # Group the 365 displayed Shanghai calendar days in the database.
            # SQLite stores UTC datetimes without their timezone suffix, so
            # adding eight hours before extracting the date gives the same
            # local-day boundary as shanghai_time() without loading every run.
            first_activity_day = local_now.date() - timedelta(days=364)
            activity_start = datetime.combine(first_activity_day, time.min, tzinfo=shanghai).astimezone(timezone.utc)
            activity_end = datetime.combine(local_now.date() + timedelta(days=1), time.min, tzinfo=shanghai).astimezone(timezone.utc)
            if db.bind.dialect.name == "sqlite":
                activity_date = func.date(TaskRun.scheduled_for, "+8 hours")
            else:
                activity_date = func.date(func.timezone("Asia/Shanghai", TaskRun.scheduled_for))
            activity_rows = db.execute(
                select(
                    activity_date.label("activity_day"),
                    func.coalesce(
                        func.sum(case((TaskRun.status == "success", 1), else_=0)),
                        0,
                    ).label("success_count"),
                )
                .join(SparkTask, SparkTask.id == TaskRun.task_id)
                .where(
                    SparkTask.owner_user_id == user.id,
                    TaskRun.scheduled_for >= activity_start,
                    TaskRun.scheduled_for < activity_end,
                )
                .group_by(activity_date)
            ).all()
            activity_counts = {str(day): count for day, count in activity_rows}
            activity_days = [
                {"date": (local_now.date() - timedelta(days=offset)).isoformat(), "count": activity_counts.get((local_now.date() - timedelta(days=offset)).isoformat(), 0)}
                for offset in range(364, -1, -1)
            ]
            membership = membership_summary(db, user)
            account_rows = []
            for account in accounts:
                h = health.get(account.id, {})
                profile_obj = profiles.get(account.id)
                profile = {
                    "nickname": getattr(profile_obj, "nickname", None),
                    "avatar_url": getattr(profile_obj, "avatar_url", None),
                    "unique_id": getattr(profile_obj, "unique_id", None),
                }
                account_rows.append(
                    {
                        "id": account.id,
                        "name": account.display_name,
                        "health": h,
                        "profile": profile,
                        "validation_state": account.validation_state,
                    }
                )
            task_rows = _task_payload(db, tasks)
            for row in task_rows:
                row["status"] = latest.get(row["id"], "scheduled")
                row["confirmed"] = row["status"] == "success"
            return {
                "user": {"id": user.id, "name": user.username, "role": user.role},
                "csrf": record.csrf_token,
                "accounts": account_rows,
                "tasks": task_rows,
                "resource_limits": {
                    "page_size": APP_BOOTSTRAP_PAGE_SIZE,
                    "accounts_total": int(account_total),
                    "tasks_total": int(task_total),
                    "tasks_enabled_total": enabled_task_count,
                    "tasks_paused_total": max(0, int(task_total) - enabled_task_count),
                    "accounts_truncated": account_total > len(accounts),
                    "tasks_truncated": task_total > len(tasks),
                    "task_counts_by_account": {
                        str(account_id): int(count)
                        for account_id, count in task_counts.items()
                        if account_id is not None
                    },
                },
                "tasks_pagination": {
                    "page": 1,
                    "page_size": APP_BOOTSTRAP_PAGE_SIZE,
                    "total": int(task_total),
                    "pages": max(1, math.ceil(task_total / APP_BOOTSTRAP_PAGE_SIZE)),
                    "has_previous": False,
                    "has_prev": False,
                    "has_next": task_total > len(tasks),
                },
                "runs": [
                    {
                        "id": r.id,
                        "taskId": r.task_id,
                        "status": r.status,
                        "label": run_view_status(r)["label"],
                        "stage": RUN_STAGE_LABELS.get(r.stage, r.stage),
                        "time": shanghai_time(r.scheduled_for),
                        "error": r.error_summary,
                        "guidance": run_guidance(r),
                        "timeline": timeline_for(r),
                    }
                    for r in runs
                ],
                "recentRuns": [
                    {
                        "id": r.id,
                        "name": task.target_name,
                        "status": r.status,
                        "label": run_view_status(r)["label"],
                        "stage": RUN_STAGE_LABELS.get(r.stage, r.stage),
                        "time": shanghai_time(r.scheduled_for),
                        "error": r.error_summary,
                        "guidance": run_guidance(r),
                        "timeline": timeline_for(r),
                    }
                    for r, task in recent_run_rows
                ],
                "activity": {"period_days": 365, "total": sum(item["count"] for item in activity_days), "days": activity_days},
                "membership": {k: membership.get(k) for k in ("active", "status_label", "days_remaining", "max_accounts", "max_tasks", "task_limit_label", "can_add_task")},
                "plans": pricing_plans(),
            }

    @router.get("/api/app/tasks")
    def app_tasks(
        request: Request,
        page: int = Query(1, ge=1),
        account_id: str = Query("", max_length=64),
        q: str = Query("", max_length=100),
    ):
        """Return bounded task pages for the task workspace.

        The global bootstrap intentionally contains only the first page. This
        endpoint keeps the same ownership boundary while allowing an
        "unlimited" customer to inspect the rest without making every route
        load the full task table.
        """
        with session_scope(engine) as db:
            user, _record = auth.current(request, db)
            criteria = [SparkTask.owner_user_id == user.id]
            if account_id:
                criteria.append(SparkTask.douyin_account_id == account_id)
            query = q.strip()
            if query:
                criteria.append(SparkTask.target_name.ilike(f"%{query}%"))
            total = int(
                db.scalar(select(func.count(SparkTask.id)).where(*criteria)) or 0
            )
            pages = max(1, math.ceil(total / APP_BOOTSTRAP_PAGE_SIZE))
            current_page = min(page, pages)
            tasks = db.scalars(
                select(SparkTask)
                .where(*criteria)
                .order_by(SparkTask.send_time, SparkTask.target_name, SparkTask.id)
                .offset((current_page - 1) * APP_BOOTSTRAP_PAGE_SIZE)
                .limit(APP_BOOTSTRAP_PAGE_SIZE)
            ).all()
            rows = _task_payload(db, tasks)
            latest = {}
            if tasks:
                latest_rank = (
                    select(
                        TaskRun.task_id,
                        TaskRun.status,
                        func.row_number()
                        .over(
                            partition_by=TaskRun.task_id,
                            order_by=(TaskRun.scheduled_for.desc(), TaskRun.id.desc()),
                        )
                        .label("row_number"),
                    )
                    .where(TaskRun.task_id.in_([task.id for task in tasks]))
                    .subquery()
                )
                for task_id, status in db.execute(
                    select(latest_rank.c.task_id, latest_rank.c.status).where(
                        latest_rank.c.row_number == 1
                    )
                ).all():
                    latest.setdefault(task_id, status)
            for row in rows:
                row["status"] = latest.get(row["id"], "scheduled")
                row["confirmed"] = row["status"] == "success"
            return {
                "items": rows,
                "pagination": {
                    "page": current_page,
                    "page_size": APP_BOOTSTRAP_PAGE_SIZE,
                    "total": total,
                    "pages": pages,
                    "has_previous": current_page > 1,
                    "has_prev": current_page > 1,
                    "has_next": current_page < pages,
                },
            }

    @router.get("/api/admin/bootstrap")
    def admin_bootstrap(
        request: Request,
        q: str = Query("", max_length=100),
        page: int = Query(1, ge=1),
        status: str = Query("", max_length=24),
    ):
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            stats = admin_stats(db)
            users_total = db.scalar(select(func.count(User.id))) or 0
            account_total = db.scalar(select(func.count(DouyinAccount.id))) or 0
            task_total = db.scalar(select(func.count(SparkTask.id))) or 0
            run_total = db.scalar(select(func.count(TaskRun.id))) or 0
            user_rows, user_pagination = list_users(db, q=q, status=status, page=page)
            recent_runs = db.execute(
                select(TaskRun, SparkTask, User)
                .join(SparkTask, SparkTask.id == TaskRun.task_id)
                .join(User, User.id == SparkTask.owner_user_id)
                .order_by(TaskRun.scheduled_for.desc())
                .limit(30)
            ).all()
            return {
                "user": {"id": admin.id, "name": admin.username, "role": admin.role},
                "csrf": record.csrf_token,
                "stats": jsonable_encoder(stats),
                "counts": {"users": users_total, "accounts": account_total, "tasks": task_total, "runs": run_total},
                "users": [
                    {"id": row.id, "username": row.username, "role": row.role, "status": row.status, "created": shanghai_time(row.created_at), "admin_note": row.admin_note,
                     "account_count": row.account_count, "task_count": row.task_count,
                     "membership_status": row.membership_status,
                     "membership_label": row.membership_label,
                     "account_health_status": row.account_health_status, "account_health_label": row.account_health_label,
                     "today_success": row.today_success_count, "today_failed": row.today_failed_count}
                    for row in user_rows
                ],
                "recentRuns": [
                    {
                        "id": run.id,
                        "owner": user.username,
                        "target": task.target_name,
                        "status": run_view_status(run)["label"],
                        "tone": run_view_status(run)["tone"],
                        "time": shanghai_time(run.scheduled_for),
                    }
                    for run, task, user in recent_runs
                ],
                "operations": jsonable_encoder(operations_snapshot(db, settings)) if settings else {},
                # These collections are loaded by their tab-specific endpoints.
                # Keep empty compatibility keys so older clients do not fail while
                # avoiding unrelated joins and large reads on every admin page.
                "groups": [],
                "subscriptions": [],
                # Redemption codes are loaded by the dedicated codes screen.
                # Keeping plaintext codes out of the global bootstrap prevents
                # every admin page load from exposing all active credentials.
                "redemptionCodes": [],
                "plans": pricing_plans(),
                "invites": [],
                "audit": [],
                "mail": {},
                "page": page,
                "pageSize": user_pagination["page_size"],
                "total": user_pagination["total"],
                "usersPagination": user_pagination,
            }

    @router.get("/api/admin/mail")
    def admin_mail(request: Request):
        """Load mail settings only for the mail settings screen."""
        with session_scope(engine) as db:
            auth.admin_context(request, db)
            if cipher is None:
                return {"enabled": False, "deliveries": []}
            from spark_console.services import mail as mail_service

            config = mail_service.load_config(db, cipher)
            return {
                "enabled": bool(config.get("enabled")),
                "host": config.get("host", ""),
                "port": config.get("port"),
                "mode": config.get("mode", "ssl"),
                "username": config.get("username", ""),
                "sender": config.get("sender", ""),
                "sender_name": config.get("sender_name", ""),
                "events": list(config.get("events", [])),
                "password_configured": bool(config.get("password")),
                "templates": config.get("templates", {}),
                "deliveries": [
                    {
                        "id": item.id,
                        "event": item.event,
                        "recipient": item.recipient,
                        "status": item.status,
                        "error": item.error,
                        "created_at": _date_value(item.created_at),
                    }
                    for item in db.scalars(
                        select(MailDelivery)
                        .order_by(MailDelivery.created_at.desc())
                        .limit(30)
                    ).all()
                ],
            }

    @router.get("/api/admin/redemption-codes")
    def admin_redemption_codes(
        request: Request,
        q: str = Query("", max_length=100),
        status: str = Query("", max_length=24),
        page: int = Query(1, ge=1),
    ):
        """Load redemption codes only when an administrator opens that screen."""
        with session_scope(engine) as db:
            auth.admin_context(request, db)
            now = datetime.now(timezone.utc)
            criteria = []
            query = q.strip()
            if query:
                pattern = f"%{query}%"
                criteria.append(
                    or_(
                        RedemptionCode.id.ilike(pattern),
                        RedemptionCode.code.ilike(pattern),
                        User.username.ilike(pattern),
                    )
                )
            if status in {"active", "valid", "available"}:
                criteria.extend(
                    [RedemptionCode.redeemed_at.is_(None), RedemptionCode.revoked_at.is_(None)]
                )
            elif status in {"used", "redeemed"}:
                criteria.append(RedemptionCode.redeemed_at.is_not(None))
            elif status == "revoked":
                criteria.append(RedemptionCode.revoked_at.is_not(None))
            elif status == "expired":
                criteria.extend(
                    [
                        RedemptionCode.redeemed_at.is_not(None),
                        RedemptionCode.revoked_at.is_(None),
                        RedemptionCode.expires_at < NEVER_EXPIRES_AT,
                        RedemptionCode.expires_at <= now,
                    ]
                )
            base = (
                select(RedemptionCode, User.username)
                .outerjoin(User, RedemptionCode.redeemed_by_user_id == User.id)
                .where(*criteria)
            )
            total = db.scalar(
                select(func.count()).select_from(base.order_by(None).subquery())
            ) or 0
            page_size = 20
            pages = max(1, math.ceil(total / page_size))
            current_page = min(page, pages)
            rows = db.execute(
                base.order_by(RedemptionCode.created_at.desc(), RedemptionCode.id.desc())
                .offset((current_page - 1) * page_size)
                .limit(page_size)
            ).all()

            def status_label(code):
                if code.redeemed_at is not None:
                    return "已兑换"
                if code.revoked_at is not None:
                    return "已撤销"
                expires_at = _aware(code.expires_at)
                if expires_at is not None and expires_at < NEVER_EXPIRES_AT and expires_at <= now:
                    return "已过期"
                return "待兑换"

            return {
                "items": [
                    {
                        "id": item.id,
                        "code": item.code,
                        "status": status_label(item),
                        "username": username,
                        "duration_days": item.duration_days,
                        "max_accounts": item.max_accounts,
                        "max_tasks": item.max_tasks,
                        "task_limit_label": task_limit_label(item.max_tasks),
                        "created_at": _date_value(item.created_at),
                        "redeemed_at": _date_value(item.redeemed_at),
                        "redeemed_by_user_id": item.redeemed_by_user_id,
                        "expires_at": _date_value(item.expires_at) if item.redeemed_at else None,
                    }
                    for item, username in rows
                ],
                "pagination": {
                    "page": current_page,
                    "page_size": page_size,
                    "total": total,
                    "pages": pages,
                    "has_previous": current_page > 1,
                    "has_prev": current_page > 1,
                    "has_next": current_page < pages,
                },
            }

    @router.get("/api/admin/subscriptions")
    def admin_subscriptions(request: Request, q: str = Query("", max_length=100), status: str = Query("", max_length=24), page: int = Query(1, ge=1)):
        with session_scope(engine) as db:
            auth.admin_context(request, db)
            rows, pagination = list_subscriptions(db, q=q, status=status, page=page)
            return {"items": [{"id": row.id, "username": row.username, "role": row.role, "status": row.status, "admin_note": row.admin_note,
                "membership_status": row.membership_status, "membership_label": row.membership_label,
                "valid_until": _date_value(row.valid_until), "days_remaining": row.days_remaining,
                "max_accounts": row.max_accounts, "max_tasks": row.max_tasks,
                "task_limit_label": row.task_limit_label,
                "account_count": row.account_count, "task_count": row.task_count} for row in rows], "pagination": pagination}

    @router.get("/api/admin/task-groups")
    def admin_task_groups(request: Request, q: str = Query("", max_length=100), status: str = Query("", max_length=24), page: int = Query(1, ge=1)):
        with session_scope(engine) as db:
            auth.admin_context(request, db)
            groups, pagination = list_task_groups(db, q=q, status=status, page=page)
            return {"items": [{"owner_id": row.get("owner_id"), "account_id": row.get("account_id"),
                "username": row.get("username"), "remark": row.get("remark") or row.get("nickname") or "抖音账号",
                "nickname": row.get("nickname"), "avatar_url": row.get("avatar_url"),
                "validation_state": row.get("validation_state"), "total": int(row.get("total") or 0),
                "enabled": int(row.get("enabled") or 0)} for row in groups], "pagination": pagination}

    @router.get("/api/admin/runs")
    def admin_runs(request: Request, q: str = Query("", max_length=120), status: str = Query("", max_length=40),
                   start: str = Query("", max_length=10), end: str = Query("", max_length=10), day: str = Query("", max_length=16),
                   page: int = Query(1, ge=1)):
        with session_scope(engine) as db:
            auth.admin_context(request, db)
            if day == "today":
                local_day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
                start = end = local_day
                board_data = today_send_board(db)
            else:
                board_data = None
            try:
                rows, pagination = list_runs(db, q=q, status=status, page=page, start=start, end=end)
            except ValueError:
                return JSONResponse({"detail": "日期范围不正确"}, status_code=400)
            attempts = attempts_for_runs(db, [row.id for row in rows])
            events = events_for_runs(db, [row.id for row in rows])
            account_ids = {row.task.douyin_account_id for row in rows if row.task.douyin_account_id}
            accounts = {
                account.id: account for account in db.scalars(
                    select(DouyinAccount).where(DouyinAccount.id.in_(account_ids))
                ).all()
            } if account_ids else {}
            items = []
            for row in rows:
                run = row.run
                view = run_view_status(run)
                guidance = run_guidance(run)
                account = accounts.get(row.task.douyin_account_id)
                items.append({"id": run.id, "task_id": row.task.id, "owner_id": row.user.id, "target": row.target_name,
                    "owner": row.username, "account": account.display_name if account else "账号不可用",
                    "scheduled_for": shanghai_time(run.scheduled_for), "status": run.status,
                    "label": view["label"], "tone": view["tone"], "stage": RUN_STAGE_LABELS.get(run.stage, run.stage),
                    "stage_code": run.stage, "error_code": run.error_code, "error": run.error_summary,
                    "guidance": guidance, "can_retry": bool(guidance and guidance.get("action") == "retry"),
                    "attempt": run.attempt, "submitted_at": shanghai_time(run.submitted_at) if run.submitted_at else None,
                     "attempts": [{"number": attempt.number, "status": attempt.status,
                        "stage": RUN_STAGE_LABELS.get(attempt.stage, attempt.stage), "reason": attempt.reason,
                        "started_at": shanghai_time(attempt.started_at),
                        "finished_at": shanghai_time(attempt.finished_at) if attempt.finished_at else None,
                        "submitted_at": shanghai_time(attempt.submitted_at) if attempt.submitted_at else None}
                         for attempt in attempts.get(run.id, [])],
                     "timeline": [{"sequence": event.sequence, "event": event.event,
                         "stage": RUN_STAGE_LABELS.get(event.stage, event.stage),
                         "occurred_at": shanghai_time(event.occurred_at),
                         "detail": event.detail} for event in events.get(run.id, [])]})
            return {"items": items, "pagination": pagination,
                "today_board": jsonable_encoder(board_data) if board_data else None}

    @router.get("/api/admin/audit")
    def admin_audit(request: Request, q: str = Query("", max_length=120), status: str = Query("", max_length=64), page: int = Query(1, ge=1)):
        with session_scope(engine) as db:
            auth.admin_context(request, db)
            rows, pagination = list_audit_events(db, q=q, status=status, page=page)
            return {"items": [{"id": row.id, "event": row.action, "subject_type": row.resource_type,
                "subject_id": row.resource_id, "actor": row.actor_username or "系统", "detail": row.detail,
                "request_id": row.event.request_id, "client_ip": row.event.client_ip,
                "outcome": row.event.outcome, "error_code": row.event.error_code,
                "created_at": _date_value(row.created_at)} for row in rows], "pagination": pagination}

    @router.get("/api/admin/insights")
    def admin_insights(request: Request, days: int = Query(7, ge=7, le=30)):
        from collections import Counter
        from spark_console.web.admin_queries import overview_expiring_users
        with session_scope(engine) as db:
            auth.admin_context(request, db)
            now = datetime.now(timezone.utc)
            local_today = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
            start = datetime.combine(local_today - timedelta(days=days - 1), datetime.min.time(), ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)
            counts = Counter()
            totals = Counter()
            failures = Counter()
            # Aggregate in SQL instead of materialising up to 20,000 runs in
            # the web process.  The result is bounded by days * statuses and
            # remains small even when the run table grows into the millions.
            if db.bind.dialect.name == "sqlite":
                run_day = func.date(TaskRun.scheduled_for, "+8 hours")
            else:
                run_day = func.date(func.timezone("Asia/Shanghai", TaskRun.scheduled_for))
            run_rows = db.execute(
                select(run_day.label("run_day"), TaskRun.status, func.count(TaskRun.id))
                .where(
                    TaskRun.scheduled_for >= start,
                    TaskRun.scheduled_for <= now,
                )
                .group_by(run_day, TaskRun.status)
            ).all()
            for day_value, run_status, total in run_rows:
                day = str(day_value)
                totals[day] += int(total or 0)
                if run_status == "success":
                    counts[day] += int(total or 0)
                elif run_status in {"failed", "submitted_unconfirmed"}:
                    failures[day] += int(total or 0)
            trend = [{"date": (local_today - timedelta(days=offset)).isoformat(),
                "total": totals[(local_today - timedelta(days=offset)).isoformat()],
                "success": counts[(local_today - timedelta(days=offset)).isoformat()],
                "failed": failures[(local_today - timedelta(days=offset)).isoformat()]} for offset in range(days - 1, -1, -1)]
            account_rows = db.execute(
                select(DouyinAccount, User.username)
                .join(User, User.id == DouyinAccount.owner_user_id)
                .where(DouyinAccount.validation_state.in_(["invalid", "verification", "temporary"]))
                .order_by(DouyinAccount.updated_at.desc())
                .limit(50)
            ).all()
            alert_health = account_health_views(db, [account for account, _ in account_rows])
            account_alerts = [{"id": account.id, "name": account.display_name, "owner_id": account.owner_user_id, "owner": owner_username or "—",
                "state": account.validation_state, "label": alert_health.get(account.id, {}).get("label", "需要复核")} for account, owner_username in account_rows]
            failures = db.execute(select(TaskRun, SparkTask, User).join(SparkTask, SparkTask.id == TaskRun.task_id)
                .join(User, User.id == SparkTask.owner_user_id).where(TaskRun.status.in_(["failed", "submitted_unconfirmed"]), TaskRun.scheduled_for >= now - timedelta(days=7))
                .order_by(TaskRun.scheduled_for.desc()).limit(20)).all()
            expiring = overview_expiring_users(db, limit=20)
            return {"days": days, "trend": trend, "alerts": {"accounts": account_alerts,
                "expiring": [{"id": row.id, "username": row.username, "valid_until": _date_value(row.valid_until), "days_remaining": row.days_remaining} for row in expiring],
                "failed_runs": [{"id": run.id, "task_id": task.id, "name": task.target_name, "username": user.username,
                    "status": run.status, "error": run.error_summary, "time": shanghai_time(run.scheduled_for)} for run, task, user in failures]}}

    @router.post("/api/admin/users/bulk")
    async def admin_bulk_users(request: Request):
        values = await request.json()
        selected = list(dict.fromkeys(str(item) for item in values.get("user_ids", []) if item))
        action = str(values.get("action", ""))
        if action not in {"enable", "disable"} or not selected:
            return JSONResponse({"detail": "请选择用户和有效操作"}, status_code=400)
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            auth.csrf(record, str(values.get("csrf_token", "")))
            users = db.scalars(select(User).where(User.id.in_(selected))).all()
            if len(users) != len(selected):
                return JSONResponse({"detail": "部分用户不存在"}, status_code=404)
            if any(user.role == "admin" or user.id == admin.id for user in users):
                return JSONResponse({"detail": "批量操作仅支持普通用户"}, status_code=400)
            service = UserService(db, PasswordService(), AuditService(db))
            for user in users:
                service.set_disabled(admin.id, user.id, action == "disable")
            return {"ok": True, "updated": len(users)}

    @router.post("/api/admin/tasks/bulk")
    async def admin_bulk_tasks(request: Request):
        values = await request.json()
        selected = list(dict.fromkeys(str(item) for item in values.get("task_ids", []) if item))
        action = str(values.get("action", ""))
        if action not in {"pause", "resume"} or not selected:
            return JSONResponse({"detail": "请选择任务和有效操作"}, status_code=400)
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            auth.csrf(record, str(values.get("csrf_token", "")))
            tasks = db.scalars(select(SparkTask).where(SparkTask.id.in_(selected))).all()
            if len(tasks) != len(selected):
                return JSONResponse({"detail": "部分任务不存在"}, status_code=404)
            service = TaskService(db, AccountService(db, cipher, AuditService(db)), AuditService(db))
            try:
                for task in tasks:
                    service.set_enabled_owned(task.owner_user_id, task.id, action == "resume")
            except (NotFound, ValidationError, Conflict) as error:
                db.rollback()
                return JSONResponse({"detail": str(error)}, status_code=409)
            AuditService(db).write(admin.id, "admin.tasks.bulk." + action, "spark_task", None, f"count={len(tasks)}")
            return {"ok": True, "updated": len(tasks)}

    @router.post("/api/admin/subscriptions/{user_id}/limits")
    async def admin_update_subscription_limits(request: Request, user_id: str):
        values = await request.json()
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            auth.csrf(record, str(values.get("csrf_token", "")))
            try:
                item = SubscriptionService(db, AuditService(db)).set_limits(admin.id, user_id,
                    max_accounts=int(values.get("max_accounts", 1)), max_tasks=int(values.get("max_tasks", 10)))
            except NotFound as error:
                return JSONResponse({"detail": str(error)}, status_code=404)
            except (ValidationError, ValueError) as error:
                return JSONResponse({"detail": str(error)}, status_code=400)
            return {"ok": True, "max_accounts": item.max_accounts, "max_tasks": item.max_tasks}

    @router.post("/api/admin/subscriptions/{user_id}/revoke")
    async def admin_revoke_subscription(request: Request, user_id: str):
        values = await request.json()
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            auth.csrf(record, str(values.get("csrf_token", "")))
            try:
                SubscriptionService(db, AuditService(db)).revoke_subscription(admin.id, user_id)
            except NotFound as error:
                return JSONResponse({"detail": str(error)}, status_code=404)
            except (Conflict, ValidationError) as error:
                return JSONResponse({"detail": str(error)}, status_code=409)
            return {"ok": True}

    @router.post("/api/admin/subscriptions/bulk")
    async def admin_bulk_subscriptions(request: Request):
        values = await request.json()
        selected = list(dict.fromkeys(str(item) for item in values.get("user_ids", []) if item))
        action = str(values.get("action", ""))
        if action not in {"extend", "revoke"} or not selected:
            return JSONResponse({"detail": "请选择订阅用户和有效操作"}, status_code=400)
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            auth.csrf(record, str(values.get("csrf_token", "")))
            targets = db.scalars(select(User).where(User.id.in_(selected), User.role != "admin")).all()
            if len(targets) != len(selected):
                return JSONResponse({"detail": "部分普通用户不存在或不能管理"}, status_code=404)
            service = SubscriptionService(db, AuditService(db))
            try:
                for user in targets:
                    if action == "extend":
                        service.extend(admin.id, user.id, duration_days=int(values.get("duration_days", 30)),
                            max_accounts=int(values.get("max_accounts", 1)), max_tasks=int(values.get("max_tasks", 10)))
                    else:
                        service.revoke_subscription(admin.id, user.id)
            except (NotFound, Conflict, ValidationError, ValueError) as error:
                db.rollback()
                return JSONResponse({"detail": str(error)}, status_code=409)
            AuditService(db).write(admin.id, "admin.subscriptions.bulk." + action, "user_subscription", None, f"count={len(targets)}")
            return {"ok": True, "updated": len(targets)}

    @router.get("/api/admin/audit/{event_id}")
    def admin_audit_detail(request: Request, event_id: str):
        with session_scope(engine) as db:
            auth.admin_context(request, db)
            event = db.get(AuditEvent, event_id)
            if event is None:
                return JSONResponse({"detail": "审计记录不存在"}, status_code=404)
            actor = db.get(User, event.actor_user_id) if event.actor_user_id else None
            return {"id": event.id, "event": event.action, "subject_type": event.resource_type,
                "subject_id": event.resource_id, "actor": actor.username if actor else "系统",
                "detail": event.detail, "request_id": event.request_id,
                "client_ip": event.client_ip, "outcome": event.outcome,
                "error_code": event.error_code, "created_at": _date_value(event.created_at)}

    @router.get("/api/admin/users/{user_id}/workspace")
    def admin_user_workspace(request: Request, user_id: str):
        """One customer workbench: identity, subscription, accounts, tasks and runs."""
        with session_scope(engine) as db:
            auth.admin_context(request, db)
            user = db.get(User, user_id)
            if user is None:
                return JSONResponse({"detail": "用户不存在"}, status_code=404)
            accounts = db.scalars(
                select(DouyinAccount)
                .where(DouyinAccount.owner_user_id == user.id)
                .order_by(DouyinAccount.created_at.desc(), DouyinAccount.id)
                .limit(WORKSPACE_LIST_PAGE_SIZE)
            ).all()
            tasks = db.scalars(
                select(SparkTask)
                .where(SparkTask.owner_user_id == user.id)
                .order_by(SparkTask.send_time, SparkTask.target_name, SparkTask.id)
                .limit(WORKSPACE_LIST_PAGE_SIZE)
            ).all()
            account_total = int(
                db.scalar(
                    select(func.count(DouyinAccount.id)).where(
                        DouyinAccount.owner_user_id == user.id
                    )
                )
                or 0
            )
            task_total = int(
                db.scalar(
                    select(func.count(SparkTask.id)).where(
                        SparkTask.owner_user_id == user.id
                    )
                )
                or 0
            )
            task_ids = [task.id for task in tasks]
            identities = {
                identity.task_id: identity
                for identity in db.scalars(
                    select(SparkTaskTargetIdentity).where(
                        SparkTaskTargetIdentity.task_id.in_(task_ids)
                    )
                ).all()
            } if task_ids else {}
            profiles = profile_map(db, [a.id for a in accounts])
            account_health = account_health_views(db, accounts)
            avatars = task_avatar_map(db, tasks)
            runs = db.execute(
                select(TaskRun, SparkTask).join(SparkTask, TaskRun.task_id == SparkTask.id)
                .where(SparkTask.owner_user_id == user.id)
                .order_by(TaskRun.scheduled_for.desc()).limit(30)
            ).all()
            membership = membership_summary(db, user)
            return {
                "user": {"id": user.id, "username": user.username, "role": user.role, "status": user.status, "created": shanghai_time(user.created_at), "admin_note": user.admin_note or ""},
                "membership": {k: membership.get(k) for k in ("active", "status_label", "valid_until", "days_remaining", "max_accounts", "max_tasks", "task_limit_label", "account_count", "task_count", "is_admin")},
                "resource_limits": {
                    "page_size": WORKSPACE_LIST_PAGE_SIZE,
                    "accounts_total": account_total,
                    "tasks_total": task_total,
                    "accounts_truncated": account_total > len(accounts),
                    "tasks_truncated": task_total > len(tasks),
                },
                "accounts": [{"id": a.id, "name": a.display_name, "validation_state": a.validation_state, "profile": {"nickname": getattr(profiles.get(a.id), "nickname", None), "avatar_url": getattr(profiles.get(a.id), "avatar_url", None)}, "health": account_health.get(a.id, {})} for a in accounts],
                "tasks": [{"id": t.id, "name": t.target_name, "accountId": t.douyin_account_id, "sec_uid": (identity.sec_uid if (identity := identities.get(t.id)) else ""), "time": t.send_time, "kind": "续火花表情" if t.message_kind == "douyin_sticker" else "文字消息", "message": t.message_template, "enabled": t.enabled, "avatar": avatars.get(t.id)} for t in tasks],
                "runs": [{"id": r.id, "taskId": r.task_id, "name": t.target_name, "status": r.status, "label": run_view_status(r)["label"], "time": shanghai_time(r.scheduled_for), "error": r.error_summary} for r, t in runs],
            }

    @router.post("/api/admin/users/{user_id}/note")
    async def admin_update_user_note(request: Request, user_id: str):
        values = await request.json()
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            auth.csrf(record, str(values.get("csrf_token", "")))
            user = db.get(User, user_id)
            if user is None:
                return JSONResponse({"detail": "用户不存在"}, status_code=404)
            raw_note = values.get("admin_note", values.get("note", ""))
            if raw_note is None:
                raw_note = ""
            if not isinstance(raw_note, str):
                return JSONResponse({"detail": "备注格式不正确"}, status_code=400)
            note = raw_note.strip()
            if len(note) > 500:
                return JSONResponse({"detail": "备注最多 500 个字符"}, status_code=400)
            user.admin_note = note or None
            AuditService(db).write(admin.id, "user.admin_note_updated", "user", user.id)
            return {"ok": True, "admin_note": note}

    @router.post("/api/admin/users/{user_id}/role")
    async def admin_update_user_role(request: Request, user_id: str):
        values = await request.json()
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            auth.csrf(record, str(values.get("csrf_token", "")))
            role = str(values.get("role", "")).strip().lower()
            try:
                updated = UserService(db, PasswordService(), AuditService(db)).set_role(admin.id, user_id, role)
            except NotFound as error:
                return JSONResponse({"detail": str(error)}, status_code=404)
            except (ValidationError, Conflict) as error:
                return JSONResponse({"detail": str(error)}, status_code=400)
            return {"ok": True, "role": updated.role}

    @router.post("/api/admin/redemption-codes")
    async def admin_create_redemption_codes(request: Request):
        values = await request.json()
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            auth.csrf(record, str(values.get("csrf_token", "")))
            try:
                quantity = max(1, min(20, int(values.get("quantity", 1))))
                service = SubscriptionService(db, AuditService(db))
                codes = [service.create_redemption_code(
                    admin.id,
                    duration_days=int(values.get("duration_days", 30)),
                    max_accounts=int(values.get("max_accounts", 1)),
                    max_tasks=int(values.get("max_tasks", 10)),
                    plan_id=str(values.get("plan_id") or "") or None,
                )[1] for _ in range(quantity)]
            except (ValidationError, ValueError) as error:
                return JSONResponse({"detail": str(error)}, status_code=400)
            return {"ok": True, "codes": codes}

    @router.post("/api/admin/runs/{run_id}/retry")
    async def admin_retry_run(request: Request, run_id: str):
        values = await request.json()
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            auth.csrf(record, str(values.get("csrf_token", "")))
            run = db.get(TaskRun, run_id)
            if run is None:
                return JSONResponse({"detail": "执行记录不存在"}, status_code=404)
            task = db.get(SparkTask, run.task_id)
            if task is None:
                return JSONResponse({"detail": "任务不存在"}, status_code=404)
            try:
                TaskService(db, AccountService(db, cipher, AuditService(db)), AuditService(db)).request_unsubmitted_retry(
                    task.owner_user_id, run_id, datetime.now(timezone.utc))
            except (NotFound, ValidationError, Conflict) as error:
                return JSONResponse({"detail": str(error)}, status_code=409)
            AuditService(db).write(admin.id, "admin.run.retry_requested", "task_run", run.id)
            return {"ok": True}

    @router.post("/api/admin/users/{user_id}/toggle")
    async def admin_toggle_user(request: Request, user_id: str):
        values = await request.json()
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            auth.csrf(record, str(values.get("csrf_token", "")))
            user = db.get(User, user_id)
            if user is None:
                return JSONResponse({"detail": "用户不存在"}, status_code=404)
            if user.id == admin.id:
                return JSONResponse({"detail": "不能停用当前管理员账号"}, status_code=400)
            try:
                updated = UserService(db, PasswordService(), AuditService(db)).set_disabled(admin.id, user.id, user.status == "active")
            except NotFound as error:
                return JSONResponse({"detail": str(error)}, status_code=404)
            return {"ok": True, "status": updated.status}

    @router.post("/api/admin/users/{user_id}/reset-password")
    async def admin_reset_user_password(request: Request, user_id: str):
        values = await request.json()
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            auth.csrf(record, str(values.get("csrf_token", "")))
            try:
                temporary = UserService(db, PasswordService(), AuditService(db)).reset_password(admin.id, user_id)
            except NotFound as error:
                return JSONResponse({"detail": str(error)}, status_code=404)
            return {"ok": True, "temporary_password": temporary}

    @router.patch("/api/admin/tasks/{task_id}")
    async def admin_update_task(request: Request, task_id: str):
        values = await request.json()
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            auth.csrf(record, str(values.get("csrf_token", "")))
            task = db.get(SparkTask, task_id)
            if task is None:
                return JSONResponse({"detail": "任务不存在"}, status_code=404)
            old_owner = task.owner_user_id
            account_id = str(values.get("account_id") or task.douyin_account_id or "")
            target_name = str(values.get("target_name") or task.target_name)
            binding = db.get(SparkTaskTargetIdentity, task.id)
            target_uid = str(values.get("target_sec_uid") or (binding.sec_uid if binding else ""))
            try:
                updated = TaskService(db, AccountService(db, cipher, AuditService(db)), AuditService(db)).update_owned(
                    old_owner, task.id, account_id, target_name, str(values.get("send_time") or task.send_time),
                    str(values.get("message_template") or ""), target_sec_uid=target_uid,
                    message_kind=str(values.get("message_kind") or task.message_kind or "text"),
                )
            except (NotFound, ValidationError, Conflict) as error:
                return JSONResponse({"detail": str(error)}, status_code=409)
            AuditService(db).write(admin.id, "admin.task.updated", "spark_task", updated.id)
            return {"ok": True, "task": {"id": updated.id, "time": updated.send_time, "enabled": updated.enabled}}

    @router.post("/api/admin/tasks/{task_id}/enabled")
    async def admin_toggle_task(request: Request, task_id: str):
        values = await request.json()
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            auth.csrf(record, str(values.get("csrf_token", "")))
            task = db.get(SparkTask, task_id)
            if task is None:
                return JSONResponse({"detail": "任务不存在"}, status_code=404)
            try:
                updated = TaskService(db, AccountService(db, cipher, AuditService(db)), AuditService(db)).set_enabled_owned(task.owner_user_id, task.id, bool(values.get("enabled")))
            except (NotFound, ValidationError, Conflict) as error:
                return JSONResponse({"detail": str(error)}, status_code=409)
            AuditService(db).write(admin.id, "admin.task.enabled" if updated.enabled else "admin.task.disabled", "spark_task", updated.id)
            return {"ok": True, "enabled": updated.enabled}

    @router.delete("/api/admin/tasks/{task_id}")
    async def admin_delete_task(request: Request, task_id: str):
        values = await request.json()
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            auth.csrf(record, str(values.get("csrf_token", "")))
            task = db.get(SparkTask, task_id)
            if task is None:
                return JSONResponse({"detail": "任务不存在"}, status_code=404)
            owner_id = task.owner_user_id
            try:
                TaskService(db, AccountService(db, cipher, AuditService(db)), AuditService(db)).delete_owned(owner_id, task.id)
            except (NotFound, ValidationError, Conflict) as error:
                return JSONResponse({"detail": str(error)}, status_code=409)
            AuditService(db).write(admin.id, "admin.task.deleted", "spark_task", task_id)
            return {"ok": True}

    @router.get("/api/admin/diagnostics")
    def admin_diagnostics(request: Request):
        """Read-only diagnostics model for the canonical React operations page."""
        from spark_console.models import TaskRun, SparkTask

        with session_scope(engine) as db:
            auth.admin_context(request, db)
            rows = db.execute(
                select(TaskRun, SparkTask)
                .join(SparkTask, TaskRun.task_id == SparkTask.id)
                .order_by(TaskRun.scheduled_for.desc())
                .limit(30)
            ).all()

            def seconds(start, end):
                if not start or not end:
                    return None
                return round(max(0, (end - start).total_seconds()), 1)

            items = [{
                "id": run.id,
                "name": task.target_name,
                "status": run.status,
                "stage": run.stage,
                "attempt": run.attempt,
                "error_code": run.error_code,
                "error_summary": run.error_summary,
                "queue": seconds(run.scheduled_for, run.started_at),
                "before_submit": seconds(run.started_at, run.submitted_at),
                "confirmation": seconds(run.submitted_at, run.finished_at),
                "total": seconds(run.started_at, run.finished_at),
            } for run, task in rows]

            return JSONResponse({"items": items}, headers={"Cache-Control": "no-store"})

    @router.get("/api/admin/tasks/groups/{owner_id}/{account_id}")
    def admin_group_tasks(request: Request, owner_id: str, account_id: str, q: str = Query("", max_length=100), status: str = Query("", max_length=24), page: int = Query(1, ge=1)):
        with session_scope(engine) as db:
            auth.admin_context(request, db)
            rows, pagination = list_group_tasks(db, owner_id, account_id, q=q, status=status, page=page)
            task_ids = [row.id for row in rows]
            identities = {
                identity.task_id: identity.sec_uid
                for identity in db.scalars(
                    select(SparkTaskTargetIdentity).where(
                        SparkTaskTargetIdentity.task_id.in_(task_ids)
                    )
                ).all()
            } if task_ids else {}
            return {
                "items": [{
                    "id": row.id,
                    "username": row.username,
                    "name": row.target_name,
                    "time": row.send_time,
                    "enabled": row.enabled,
                    "account": row.account_name,
                    "account_id": row.account.id if row.account else None,
                    "owner_id": row.user.id,
                    "message_kind": row.task.message_kind,
                    "message": row.task.message_template,
                    "target_sec_uid": identities.get(row.id, ""),
                } for row in rows],
                "pagination": pagination,
            }

    @router.get("/api/notifications")
    def notification_bootstrap(request: Request):
        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            pref = db.get(MailPreference, user.id)
            policy = db.get(NotificationPolicy, user.id)
            return {
                "csrf": record.csrf_token,
                "mail_ready": bool(cipher and __import__("spark_console.services.mail", fromlist=["load_config"]).load_config(db, cipher).get("enabled")),
                "preference": {
                    "email": pref.email if pref else "",
                    "enabled": bool(pref and pref.enabled),
                    "verified": bool(pref and pref.verified),
                },
                "policy": {
                    "events": policy.events.split(",") if policy and policy.events else [],
                    "quiet_start": policy.quiet_start if policy else 23,
                    "quiet_end": policy.quiet_end if policy else 8,
                    "quiet_enabled": bool(policy and policy.quiet_enabled),
                    "interval_minutes": policy.interval_minutes if policy else 60,
                },
            }

    return router
