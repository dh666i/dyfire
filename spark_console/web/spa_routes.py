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
from spark_console.web.membership import membership_summary
from spark_console.web.auth import WebAuth
from spark_console.web.admin_queries import admin_stats, list_audit_events, list_subscriptions
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
    ):
        with session_scope(engine) as db:
            admin, record, _ = auth.admin_context(request, db)
            stats = admin_stats(db)
            users_total = db.scalar(select(func.count(User.id))) or 0
            account_total = db.scalar(select(func.count(DouyinAccount.id))) or 0
            task_total = db.scalar(select(func.count(SparkTask.id))) or 0
            run_total = db.scalar(select(func.count(TaskRun.id))) or 0
            users = db.scalars(
                select(User)
                .where(User.username.ilike(f"%{q.strip()}%") if q.strip() else True)
                .order_by(User.created_at.desc())
                .limit(50)
                .offset((page - 1) * 50)
            ).all()
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
            return {
                "user": {"id": admin.id, "name": admin.username, "role": admin.role},
                "csrf": record.csrf_token,
                "stats": jsonable_encoder(stats),
                "counts": {"users": users_total, "accounts": account_total, "tasks": task_total, "runs": run_total},
                "users": [
                    {"id": u.id, "username": u.username, "role": u.role, "status": u.status, "created": shanghai_time(u.created_at)}
                    for u in users
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
                "pageSize": 50,
                "total": users_total,
            }

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
    def admin_group_tasks(request: Request, owner_id: str, account_id: str, q: str = "", status: str = "", page: int = 1):
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
