"""JSON bootstrap endpoints for the single React console.

The existing command and automation endpoints remain the source of truth.  These
small read models keep the browser shell independent from the legacy templates
without duplicating any task or account mutation logic.
"""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

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
from spark_console.services.subscriptions import SubscriptionService
from spark_console.web.membership import membership_summary
from spark_console.web.auth import WebAuth
from spark_console.web.admin_queries import admin_stats, list_audit_events, list_subscriptions, list_users
from spark_console.web.task_groups import list_task_groups, list_group_tasks
from spark_console.ops_monitor import operations_snapshot
from spark_console.web.app import run_view_status, run_guidance, shanghai_time, RUN_STAGE_LABELS


def _task_payload(db, tasks):
    identities = {t.id: db.get(SparkTaskTargetIdentity, t.id) for t in tasks}
    avatars = task_avatar_map(db, tasks)
    return [
        {
            "id": t.id,
            "name": t.target_name,
            "accountId": t.douyin_account_id,
            "sec_uid": identities[t.id].sec_uid if identities[t.id] else "",
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
            accounts = db.scalars(
                select(DouyinAccount).where(DouyinAccount.owner_user_id == user.id)
            ).all()
            tasks = db.scalars(
                select(SparkTask)
                .where(SparkTask.owner_user_id == user.id)
                .order_by(SparkTask.send_time, SparkTask.target_name)
            ).all()
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
            latest = {}
            for run in runs:
                latest.setdefault(run.task_id, run.status)
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
                    }
                    for r, task in recent_run_rows
                ],
                "membership": {k: membership.get(k) for k in ("active", "status_label", "days_remaining", "max_accounts", "max_tasks", "can_add_task")},
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
            groups, pagination = list_task_groups(db, q=q, status="", page=1)
            subscriptions, subscriptions_page = list_subscriptions(db, page=1)
            audit_rows, audit_page = list_audit_events(db, page=1)
            redemption_codes = db.scalars(
                select(RedemptionCode).order_by(RedemptionCode.created_at.desc()).limit(50)
            ).all()
            invite_codes = db.scalars(
                select(InviteCode).order_by(InviteCode.created_at.desc()).limit(50)
            ).all()
            mail = {}
            if cipher is not None:
                from spark_console.services import mail as mail_service
                config = mail_service.load_config(db, cipher)
                mail = {
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
            subscription_map = {item.id: item for item in subscriptions}
            return {
                "user": {"id": admin.id, "name": admin.username, "role": admin.role},
                "csrf": record.csrf_token,
                "stats": jsonable_encoder(stats),
                "counts": {"users": users_total, "accounts": account_total, "tasks": task_total, "runs": run_total},
                "users": [
                    {"id": row.id, "username": row.username, "role": row.role, "status": row.status, "created": shanghai_time(row.created_at),
                     "account_count": row.account_count, "task_count": row.task_count,
                     "membership_status": row.membership_status, "membership_label": row.membership_label}
                    for row in user_rows
                ],
                "groups": [
                    {
                        "owner_id": row.get("owner_id"),
                        "account_id": row.get("account_id"),
                        "username": row.get("username"),
                        "remark": row.get("remark") or row.get("nickname") or "抖音账号",
                        "nickname": row.get("nickname"),
                        "avatar_url": row.get("avatar_url"),
                        "validation_state": row.get("validation_state"),
                        "total": int(row.get("total") or 0),
                        "enabled": int(row.get("enabled") or 0),
                    }
                    for row in groups
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
                "subscriptions": [
                    {
                        "id": item.id,
                        "username": item.username,
                        "role": item.role,
                        "status": item.status,
                        "membership_status": item.membership_status,
                        "membership_label": item.membership_label,
                        "valid_until": _date_value(item.valid_until),
                        "days_remaining": item.days_remaining,
                        "max_accounts": item.max_accounts,
                        "max_tasks": item.max_tasks,
                        "account_count": item.account_count,
                        "task_count": item.task_count,
                    }
                    for item in subscriptions
                ],
                "redemptionCodes": [
                    {
                        "id": item.id,
                        "status": "已兑换" if item.redeemed_at else "已撤销" if item.revoked_at else "待兑换",
                        "duration_days": item.duration_days,
                        "max_accounts": item.max_accounts,
                        "max_tasks": item.max_tasks,
                        "created_at": _date_value(item.created_at),
                        "expires_at": _date_value(item.expires_at),
                    }
                    for item in redemption_codes
                ],
                "invites": [
                    {
                        "id": item.id,
                        "status": "已使用" if item.used_at else "已撤销" if item.revoked_at else "有效",
                        "created_at": _date_value(item.created_at),
                        "expires_at": _date_value(item.expires_at),
                    }
                    for item in invite_codes
                ],
                "audit": [
                    {
                        "id": item.id,
                        "event": item.action,
                        "subject_type": item.resource_type,
                        "subject_id": item.resource_id,
                        "detail": item.detail,
                        "created_at": _date_value(item.created_at),
                    }
                    for item in audit_rows
                ],
                "mail": mail,
                "page": page,
                "pageSize": user_pagination["page_size"],
                "total": user_pagination["total"],
                "usersPagination": user_pagination,
            }

    @router.get("/api/admin/subscriptions")
    def admin_subscriptions(request: Request, q: str = Query("", max_length=100), status: str = Query("", max_length=24), page: int = Query(1, ge=1)):
        with session_scope(engine) as db:
            auth.admin_context(request, db)
            rows, pagination = list_subscriptions(db, q=q, status=status, page=page)
            return {"items": [{"id": row.id, "username": row.username, "role": row.role, "status": row.status,
                "membership_status": row.membership_status, "membership_label": row.membership_label,
                "valid_until": _date_value(row.valid_until), "days_remaining": row.days_remaining,
                "max_accounts": row.max_accounts, "max_tasks": row.max_tasks,
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

    @router.get("/api/admin/audit")
    def admin_audit(request: Request, q: str = Query("", max_length=120), status: str = Query("", max_length=64), page: int = Query(1, ge=1)):
        with session_scope(engine) as db:
            auth.admin_context(request, db)
            rows, pagination = list_audit_events(db, q=q, status=status, page=page)
            return {"items": [{"id": row.id, "event": row.action, "subject_type": row.resource_type,
                "subject_id": row.resource_id, "actor": row.actor_username or "系统", "detail": row.detail,
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
            run_rows = db.execute(select(TaskRun.status, TaskRun.scheduled_for).where(TaskRun.scheduled_for >= start)
                .order_by(TaskRun.scheduled_for.desc()).limit(20000)).all()
            counts = Counter()
            totals = Counter()
            failures = Counter()
            for run_status, scheduled in run_rows:
                day = _aware(scheduled).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
                if _aware(scheduled) > now:
                    continue
                totals[day] += 1
                if run_status == "success": counts[day] += 1
                elif run_status in {"failed", "submitted_unconfirmed"}: failures[day] += 1
            trend = [{"date": (local_today - timedelta(days=offset)).isoformat(),
                "total": totals[(local_today - timedelta(days=offset)).isoformat()],
                "success": counts[(local_today - timedelta(days=offset)).isoformat()],
                "failed": failures[(local_today - timedelta(days=offset)).isoformat()]} for offset in range(days - 1, -1, -1)]
            accounts = db.scalars(select(DouyinAccount).where(DouyinAccount.validation_state.in_(["invalid", "verification", "temporary"])).order_by(DouyinAccount.updated_at.desc()).limit(50)).all()
            account_owners = {row.id: db.get(User, row.owner_user_id) for row in accounts}
            account_alerts = [{"id": account.id, "name": account.display_name, "owner_id": account.owner_user_id, "owner": account_owners[account.id].username if account_owners.get(account.id) else "—",
                "state": account.validation_state, "label": account_health_views(db, [account]).get(account.id, {}).get("label", "需要复核")} for account in accounts]
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
                "detail": event.detail, "created_at": _date_value(event.created_at)}

    @router.get("/api/admin/users/{user_id}/workspace")
    def admin_user_workspace(request: Request, user_id: str):
        """One customer workbench: identity, subscription, accounts, tasks and runs."""
        with session_scope(engine) as db:
            auth.admin_context(request, db)
            user = db.get(User, user_id)
            if user is None:
                return JSONResponse({"detail": "用户不存在"}, status_code=404)
            accounts = db.scalars(
                select(DouyinAccount).where(DouyinAccount.owner_user_id == user.id).order_by(DouyinAccount.created_at.desc())
            ).all()
            tasks = db.scalars(
                select(SparkTask).where(SparkTask.owner_user_id == user.id).order_by(SparkTask.send_time, SparkTask.target_name)
            ).all()
            identities = {t.id: db.get(SparkTaskTargetIdentity, t.id) for t in tasks}
            profiles = profile_map(db, [a.id for a in accounts])
            avatars = task_avatar_map(db, tasks)
            runs = db.execute(
                select(TaskRun, SparkTask).join(SparkTask, TaskRun.task_id == SparkTask.id)
                .where(SparkTask.owner_user_id == user.id)
                .order_by(TaskRun.scheduled_for.desc()).limit(30)
            ).all()
            membership = membership_summary(db, user)
            return {
                "user": {"id": user.id, "username": user.username, "role": user.role, "status": user.status, "created": shanghai_time(user.created_at)},
                "membership": {k: membership.get(k) for k in ("active", "status_label", "valid_until", "days_remaining", "max_accounts", "max_tasks", "account_count", "task_count", "is_admin")},
                "accounts": [{"id": a.id, "name": a.display_name, "validation_state": a.validation_state, "profile": {"nickname": getattr(profiles.get(a.id), "nickname", None), "avatar_url": getattr(profiles.get(a.id), "avatar_url", None)}, "health": account_health_views(db, [a]).get(a.id, {})} for a in accounts],
                "tasks": [{"id": t.id, "name": t.target_name, "accountId": t.douyin_account_id, "sec_uid": identities[t.id].sec_uid if identities[t.id] else "", "time": t.send_time, "kind": "续火花表情" if t.message_kind == "douyin_sticker" else "文字消息", "message": t.message_template, "enabled": t.enabled, "avatar": avatars.get(t.id)} for t in tasks],
                "runs": [{"id": r.id, "taskId": r.task_id, "name": t.target_name, "status": r.status, "label": run_view_status(r)["label"], "time": shanghai_time(r.scheduled_for), "error": r.error_summary} for r, t in runs],
            }

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
        from pathlib import Path
        import json
        import os
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

            backup = {"status": "unconfigured", "message": "尚未配置备份任务"}
            try:
                value = json.loads((Path(os.environ.get("DYFIRE_UPDATE_DIR", "/run/dyfire-updates")) / "backup-status.json").read_text())
                if isinstance(value, dict):
                    backup = value
            except (OSError, ValueError):
                pass
            return JSONResponse({"items": items, "backup": backup}, headers={"Cache-Control": "no-store"})

    @router.get("/api/admin/tasks/groups/{owner_id}/{account_id}")
    def admin_group_tasks(request: Request, owner_id: str, account_id: str, q: str = Query("", max_length=100), status: str = Query("", max_length=24), page: int = Query(1, ge=1)):
        with session_scope(engine) as db:
            auth.admin_context(request, db)
            rows, pagination = list_group_tasks(db, owner_id, account_id, q=q, status=status, page=page)
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
                    "target_sec_uid": (db.get(SparkTaskTargetIdentity, row.id).sec_uid if db.get(SparkTaskTargetIdentity, row.id) else ""),
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
