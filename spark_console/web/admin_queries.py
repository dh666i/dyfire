from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from spark_console.models import (
    AuditEvent,
    DouyinAccount,
    InviteCode,
    RedemptionCode,
    SparkTask,
    TaskRun,
    User,
    UserSubscription,
)
from spark_console.services.subscriptions import (
    FREE_MAX_ACCOUNTS,
    FREE_MAX_TASKS,
    is_unlimited_tasks,
    reconcile_subscription,
    task_limit_label,
)


ADMIN_PAGE_SIZE = 20


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _pagination(page: int, total: int, page_size: int = ADMIN_PAGE_SIZE) -> dict[str, int | bool]:
    pages = max(1, math.ceil(total / page_size))
    current = min(max(1, page), pages)
    has_previous = current > 1
    has_next = current < pages
    return {
        "page": current,
        "page_size": page_size,
        "total": total,
        "pages": pages,
        "has_previous": has_previous,
        "has_next": has_next,
        # Short aliases keep the context convenient for existing pagination macros.
        "has_prev": has_previous,
        "has_next": has_next,
    }


@dataclass(frozen=True)
class AdminUserRow:
    user: User
    subscription: UserSubscription | None
    account_count: int
    task_count: int
    now: datetime
    enabled_task_count: int = 0
    invalid_account_count: int = 0
    today_success_count: int = 0
    today_failed_count: int = 0

    @property
    def id(self) -> str:
        return self.user.id

    @property
    def username(self) -> str:
        return self.user.username

    @property
    def role(self) -> str:
        return self.user.role

    @property
    def status(self) -> str:
        return self.user.status

    @property
    def admin_note(self) -> str:
        return self.user.admin_note or ""

    @property
    def created_at(self) -> datetime:
        return self.user.created_at

    @property
    def valid_until(self) -> datetime | None:
        return self.subscription.valid_until if self.subscription else None

    @property
    def max_accounts(self) -> int | None:
        if self.user.role == "admin":
            return None
        if self.subscription is None:
            return FREE_MAX_ACCOUNTS
        if _aware(self.subscription.valid_from) > self.now or _aware(self.subscription.valid_until) <= self.now:
            return FREE_MAX_ACCOUNTS
        return self.subscription.max_accounts

    @property
    def max_tasks(self) -> int | None:
        if self.user.role == "admin":
            return None
        if self.subscription is None:
            return FREE_MAX_TASKS
        if _aware(self.subscription.valid_from) > self.now or _aware(self.subscription.valid_until) <= self.now:
            return FREE_MAX_TASKS
        return self.subscription.max_tasks

    @property
    def task_limit_label(self) -> str:
        return task_limit_label(self.max_tasks)

    @property
    def pending_subscription(self) -> dict[str, object] | None:
        subscription = self.subscription
        if subscription is None or subscription.pending_valid_from is None:
            return None
        pending_tasks = subscription.pending_max_tasks
        pending_accounts = subscription.pending_max_accounts
        if pending_tasks is None and pending_accounts is None:
            return None
        pending_from = _aware(subscription.pending_valid_from)
        return {
            "valid_from": pending_from,
            "valid_until": None,
            "max_accounts": pending_accounts or self.max_accounts or 1,
            "max_tasks": pending_tasks or self.max_tasks or 10,
            "task_limit_label": task_limit_label(
                pending_tasks if pending_tasks is not None else self.max_tasks
            ),
            "status_label": "下期生效",
        }

    @property
    def task_overage(self) -> int:
        if self.user.role == "admin" or self.max_tasks is None:
            return 0
        if is_unlimited_tasks(self.max_tasks):
            return 0
        return max(0, self.enabled_task_count - int(self.max_tasks or 0))

    @property
    def membership_status(self) -> str:
        if self.user.status != "active":
            return "disabled"
        if self.user.role == "admin":
            return "admin"
        if self.subscription is None:
            return "inactive"
        if _aware(self.subscription.valid_from) > self.now:
            return "inactive"
        valid_until = _aware(self.subscription.valid_until)
        if valid_until <= self.now:
            return "expired"
        if valid_until <= self.now + timedelta(days=7):
            return "expiring"
        return "active"

    @property
    def membership_label(self) -> str:
        return {
            "admin": "管理员",
            "disabled": "账号已停用",
            "inactive": "未开通",
            "expired": "已过期",
            "expiring": "即将到期",
            "active": "使用中",
        }[self.membership_status]

    @property
    def days_remaining(self) -> int | None:
        if self.user.role == "admin":
            return None
        if self.subscription is None or self.membership_status not in {"active", "expiring"}:
            return 0
        return math.ceil(
            max(0.0, (_aware(self.subscription.valid_until) - self.now).total_seconds())
            / 86400
        )

    @property
    def membership(self) -> dict[str, object]:
        return {
            "subscription": self.subscription,
            "active": self.membership_status in {"admin", "active", "expiring"},
            "status": self.membership_status,
            "status_label": self.membership_label,
            "valid_until": self.valid_until,
            "days_remaining": self.days_remaining,
            "max_accounts": self.max_accounts,
            "max_tasks": self.max_tasks,
            "task_limit_label": self.task_limit_label,
            "account_count": self.account_count,
            "task_count": self.task_count,
            "enabled_task_count": self.enabled_task_count,
            "task_overage": self.task_overage,
            "over_limit_task_count": self.task_overage,
            "over_limit_enabled_tasks": self.task_overage,
            "task_capacity_status": "over_limit" if self.task_overage else "available",
            "pending_subscription": self.pending_subscription,
            "pending_plan": self.pending_subscription,
            "is_admin": self.user.role == "admin",
        }

    @property
    def account_health_status(self) -> str:
        if self.invalid_account_count:
            return "需处理"
        if self.account_count:
            return "正常"
        return "未绑定"

    @property
    def account_health_label(self) -> str:
        if self.invalid_account_count:
            return f"{self.invalid_account_count} 个账号需复核"
        if self.account_count:
            return "账号正常"
        return "暂无抖音账号"


@dataclass(frozen=True)
class AdminTaskRow:
    task: SparkTask
    user: User
    account: DouyinAccount | None

    @property
    def id(self) -> str:
        return self.task.id

    @property
    def username(self) -> str:
        return self.user.username

    @property
    def target_name(self) -> str:
        return self.task.target_name

    @property
    def send_time(self) -> str:
        return self.task.send_time

    @property
    def enabled(self) -> bool:
        return self.task.enabled

    @property
    def next_run_at(self) -> datetime | None:
        return self.task.next_run_at

    @property
    def account_name(self) -> str | None:
        return self.account.display_name if self.account else None


@dataclass(frozen=True)
class AdminRunRow:
    run: TaskRun
    task: SparkTask
    user: User

    @property
    def id(self) -> str:
        return self.run.id

    @property
    def username(self) -> str:
        return self.user.username

    @property
    def target_name(self) -> str:
        return self.task.target_name

    @property
    def send_time(self) -> str:
        return self.task.send_time

    @property
    def status(self) -> str:
        return self.run.status

    @property
    def stage(self) -> str:
        return self.run.stage

    @property
    def scheduled_for(self) -> datetime:
        return self.run.scheduled_for

    @property
    def error_summary(self) -> str | None:
        return self.run.error_summary


@dataclass(frozen=True)
class AdminAuditRow:
    event: AuditEvent
    actor_username: str | None

    @property
    def id(self) -> str:
        return self.event.id

    @property
    def action(self) -> str:
        return self.event.action

    @property
    def resource_type(self) -> str:
        return self.event.resource_type

    @property
    def resource_id(self) -> str | None:
        return self.event.resource_id

    @property
    def detail(self) -> str | None:
        return self.event.detail

    @property
    def created_at(self) -> datetime:
        return self.event.created_at


def _user_aggregate_statement(now: datetime):
    account_counts = (
        select(
            DouyinAccount.owner_user_id.label("owner_id"),
            func.count(DouyinAccount.id).label("account_count"),
        )
        .group_by(DouyinAccount.owner_user_id)
        .subquery()
    )
    task_counts = (
        select(
            SparkTask.owner_user_id.label("owner_id"),
            func.count(SparkTask.id).label("task_count"),
        )
        .group_by(SparkTask.owner_user_id)
        .subquery()
    )
    enabled_task_counts = (
        select(
            SparkTask.owner_user_id.label("owner_id"),
            func.count(SparkTask.id).label("enabled_task_count"),
        )
        .where(SparkTask.enabled.is_(True))
        .group_by(SparkTask.owner_user_id)
        .subquery()
    )
    return (
        select(
            User,
            UserSubscription,
            func.coalesce(account_counts.c.account_count, 0),
            func.coalesce(task_counts.c.task_count, 0),
            func.coalesce(enabled_task_counts.c.enabled_task_count, 0),
        )
        .outerjoin(UserSubscription, UserSubscription.user_id == User.id)
        .outerjoin(account_counts, account_counts.c.owner_id == User.id)
        .outerjoin(task_counts, task_counts.c.owner_id == User.id)
        .outerjoin(enabled_task_counts, enabled_task_counts.c.owner_id == User.id)
    )


def _to_user_rows(
    rows,
    now: datetime,
    activity: dict[str, dict[str, int]] | None = None,
    db: Session | None = None,
) -> list[AdminUserRow]:
    activity = activity or {}
    if db is not None:
        for _user, subscription, *_counts in rows:
            if subscription is not None:
                reconcile_subscription(db, subscription, now)
    return [
        AdminUserRow(
            user=user,
            subscription=subscription,
            account_count=int(account_count or 0),
            task_count=int(task_count or 0),
            enabled_task_count=int(enabled_task_count or 0),
            now=now,
            invalid_account_count=activity.get(user.id, {}).get("invalid_accounts", 0),
            today_success_count=activity.get(user.id, {}).get("today_success", 0),
            today_failed_count=activity.get(user.id, {}).get("today_failed", 0),
        )
        for user, subscription, account_count, task_count, enabled_task_count in rows
    ]


def _user_activity(db: Session, user_ids: list[str], now: datetime) -> dict[str, dict[str, int]]:
    if not user_ids:
        return {}
    today = now.astimezone(ZoneInfo("Asia/Shanghai")).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)
    result = {user_id: {"invalid_accounts": 0, "today_success": 0, "today_failed": 0} for user_id in user_ids}
    for owner_id, count in db.execute(
        select(DouyinAccount.owner_user_id, func.count(DouyinAccount.id))
        .where(
            DouyinAccount.owner_user_id.in_(user_ids),
            DouyinAccount.validation_state.in_(("invalid", "verification", "temporary")),
        )
        .group_by(DouyinAccount.owner_user_id)
    ):
        result[owner_id]["invalid_accounts"] = int(count or 0)
    for owner_id, status, count in db.execute(
        select(SparkTask.owner_user_id, TaskRun.status, func.count(TaskRun.id))
        .join(SparkTask, SparkTask.id == TaskRun.task_id)
        .where(SparkTask.owner_user_id.in_(user_ids), TaskRun.scheduled_for >= today)
        .group_by(SparkTask.owner_user_id, TaskRun.status)
    ):
        if status == "success":
            result[owner_id]["today_success"] = int(count or 0)
        elif status in ("failed", "submitted_unconfirmed"):
            result[owner_id]["today_failed"] += int(count or 0)
    return result


def list_users(
    db: Session, q: str = "", status: str = "", page: int = 1
) -> tuple[list[AdminUserRow], dict[str, int | bool]]:
    now = datetime.now(timezone.utc)
    filters = []
    query = q.strip()
    if query:
        filters.append(User.username.ilike(f"%{query}%"))
    if status in {"active", "disabled"}:
        filters.append(User.status == status)
    total = int(db.scalar(select(func.count(User.id)).where(*filters)) or 0)
    pagination = _pagination(page, total)
    rows = db.execute(
        _user_aggregate_statement(now)
        .where(*filters)
        .order_by(User.created_at.desc(), User.id)
        .limit(ADMIN_PAGE_SIZE)
        .offset((int(pagination["page"]) - 1) * ADMIN_PAGE_SIZE)
    ).all()
    user_rows = _to_user_rows(rows, now, db=db)
    activity = _user_activity(db, [row.id for row in user_rows], now)
    return _to_user_rows(rows, now, activity, db), pagination


def list_subscriptions(
    db: Session, q: str = "", status: str = "", page: int = 1
) -> tuple[list[AdminUserRow], dict[str, int | bool]]:
    now = datetime.now(timezone.utc)
    soon = now + timedelta(days=7)
    filters = [User.role != "admin"]
    query = q.strip()
    if query:
        filters.append(User.username.ilike(f"%{query}%"))
    if status == "active":
        filters.extend(
            [
                User.status == "active",
                UserSubscription.user_id.is_not(None),
                UserSubscription.valid_from <= now,
                UserSubscription.valid_until > soon,
            ]
        )
    elif status == "expiring":
        filters.extend(
            [
                User.status == "active",
                UserSubscription.user_id.is_not(None),
                UserSubscription.valid_from <= now,
                UserSubscription.valid_until > now,
                UserSubscription.valid_until <= soon,
            ]
        )
    elif status == "expired":
        filters.extend(
            [
                UserSubscription.user_id.is_not(None),
                UserSubscription.valid_until <= now,
            ]
        )
    elif status == "inactive":
        filters.append(
            or_(
                UserSubscription.user_id.is_(None),
                UserSubscription.valid_from > now,
            )
        )

    count_statement = (
        select(func.count(User.id))
        .select_from(User)
        .outerjoin(UserSubscription, UserSubscription.user_id == User.id)
        .where(*filters)
    )
    total = int(db.scalar(count_statement) or 0)
    pagination = _pagination(page, total)
    rows = db.execute(
        _user_aggregate_statement(now)
        .where(*filters)
        .order_by(UserSubscription.valid_until.asc(), User.created_at.desc())
        .limit(ADMIN_PAGE_SIZE)
        .offset((int(pagination["page"]) - 1) * ADMIN_PAGE_SIZE)
    ).all()
    return _to_user_rows(rows, now, db=db), pagination


def list_tasks(
    db: Session, q: str = "", status: str = "", page: int = 1
) -> tuple[list[AdminTaskRow], dict[str, int | bool]]:
    filters = []
    query = q.strip()
    if query:
        pattern = f"%{query}%"
        filters.append(
            or_(
                User.username.ilike(pattern),
                SparkTask.target_name.ilike(pattern),
                SparkTask.message_template.ilike(pattern),
                DouyinAccount.display_name.ilike(pattern),
            )
        )
    if status == "enabled":
        filters.append(SparkTask.enabled.is_(True))
    elif status == "disabled":
        filters.append(SparkTask.enabled.is_(False))
    joined = (
        select(SparkTask, User, DouyinAccount)
        .join(User, SparkTask.owner_user_id == User.id)
        .outerjoin(DouyinAccount, SparkTask.douyin_account_id == DouyinAccount.id)
    )
    total = int(
        db.scalar(
            select(func.count(SparkTask.id))
            .select_from(SparkTask)
            .join(User, SparkTask.owner_user_id == User.id)
            .outerjoin(DouyinAccount, SparkTask.douyin_account_id == DouyinAccount.id)
            .where(*filters)
        )
        or 0
    )
    pagination = _pagination(page, total)
    rows = db.execute(
        joined.where(*filters)
        .order_by(SparkTask.send_time, SparkTask.created_at.desc())
        .limit(ADMIN_PAGE_SIZE)
        .offset((int(pagination["page"]) - 1) * ADMIN_PAGE_SIZE)
    ).all()
    return [AdminTaskRow(*row) for row in rows], pagination


def list_runs(
    db: Session, q: str = "", status: str = "", page: int = 1,
    start: str = "", end: str = "",
) -> tuple[list[AdminRunRow], dict[str, int | bool]]:
    filters = []
    if start:
        filters.append(TaskRun.scheduled_for >= datetime.strptime(start, '%Y-%m-%d').replace(tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc))
    if end:
        filters.append(TaskRun.scheduled_for < (datetime.strptime(end, '%Y-%m-%d').replace(tzinfo=ZoneInfo('Asia/Shanghai')) + timedelta(days=1)).astimezone(timezone.utc))
    if start and end and start > end:
        raise ValueError('Invalid date range')
    query = q.strip()
    if query:
        pattern = f"%{query}%"
        filters.append(
            or_(
                User.username.ilike(pattern),
                SparkTask.target_name.ilike(pattern),
                TaskRun.stage.ilike(pattern),
                TaskRun.error_code.ilike(pattern),
                TaskRun.error_summary.ilike(pattern),
            )
        )
    if status:
        filters.append(TaskRun.status == status)
    joined = (
        select(TaskRun, SparkTask, User)
        .join(SparkTask, TaskRun.task_id == SparkTask.id)
        .join(User, SparkTask.owner_user_id == User.id)
    )
    count_statement = (
        select(func.count(TaskRun.id))
        .select_from(TaskRun)
        .join(SparkTask, TaskRun.task_id == SparkTask.id)
        .join(User, SparkTask.owner_user_id == User.id)
        .where(*filters)
    )
    total = int(db.scalar(count_statement) or 0)
    pagination = _pagination(page, total)
    rows = db.execute(
        joined.where(*filters)
        .order_by(TaskRun.scheduled_for.desc(), TaskRun.id)
        .limit(ADMIN_PAGE_SIZE)
        .offset((int(pagination["page"]) - 1) * ADMIN_PAGE_SIZE)
    ).all()
    return [AdminRunRow(*row) for row in rows], pagination


def list_audit_events(
    db: Session, q: str = "", status: str = "", page: int = 1
) -> tuple[list[AdminAuditRow], dict[str, int | bool]]:
    filters = []
    query = q.strip()
    if query:
        pattern = f"%{query}%"
        filters.append(
            or_(
                AuditEvent.action.ilike(pattern),
                AuditEvent.resource_type.ilike(pattern),
                AuditEvent.resource_id.ilike(pattern),
                AuditEvent.detail.ilike(pattern),
                AuditEvent.request_id.ilike(pattern),
                AuditEvent.client_ip.ilike(pattern),
                AuditEvent.error_code.ilike(pattern),
                User.username.ilike(pattern),
            )
        )
    if status:
        filters.append(
            or_(
                AuditEvent.resource_type == status,
                AuditEvent.action.ilike(f"{status}.%"),
            )
        )
    joined = select(AuditEvent, User.username).outerjoin(
        User, AuditEvent.actor_user_id == User.id
    )
    count_statement = (
        select(func.count(AuditEvent.id))
        .select_from(AuditEvent)
        .outerjoin(User, AuditEvent.actor_user_id == User.id)
        .where(*filters)
    )
    total = int(db.scalar(count_statement) or 0)
    pagination = _pagination(page, total)
    rows = db.execute(
        joined.where(*filters)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id)
        .limit(ADMIN_PAGE_SIZE)
        .offset((int(pagination["page"]) - 1) * ADMIN_PAGE_SIZE)
    ).all()
    return [AdminAuditRow(event, username) for event, username in rows], pagination


def overview_recent_runs(db: Session, limit: int = 8) -> list[AdminRunRow]:
    rows = db.execute(
        select(TaskRun, SparkTask, User)
        .join(SparkTask, TaskRun.task_id == SparkTask.id)
        .join(User, SparkTask.owner_user_id == User.id)
        .order_by(TaskRun.scheduled_for.desc(), TaskRun.id)
        .limit(limit)
    ).all()
    return [AdminRunRow(*row) for row in rows]


def overview_expiring_users(db: Session, limit: int = 8) -> list[AdminUserRow]:
    now = datetime.now(timezone.utc)
    rows = db.execute(
        _user_aggregate_statement(now)
        .where(
            User.role != "admin",
            User.status == "active",
            UserSubscription.valid_from <= now,
            UserSubscription.valid_until > now,
            UserSubscription.valid_until <= now + timedelta(days=7),
        )
        .order_by(UserSubscription.valid_until.asc())
        .limit(limit)
    ).all()
    return _to_user_rows(rows, now, db=db)


def admin_stats(db: Session) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    shanghai_now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    day_start = shanghai_now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)
    total_users = int(db.scalar(select(func.count(User.id))) or 0)
    active_users = int(
        db.scalar(select(func.count(User.id)).where(User.status == "active")) or 0
    )
    active_subscriptions = int(
        db.scalar(
            select(func.count(UserSubscription.user_id)).where(
                UserSubscription.user_id == User.id,
                User.status == "active",
                User.role != "admin",
                UserSubscription.valid_from <= now,
                UserSubscription.valid_until > now,
            )
        )
        or 0
    )
    total_tasks = int(db.scalar(select(func.count(SparkTask.id))) or 0)
    enabled_tasks = int(
        db.scalar(select(func.count(SparkTask.id)).where(SparkTask.enabled.is_(True)))
        or 0
    )
    total_runs = int(db.scalar(select(func.count(TaskRun.id))) or 0)
    runs_today = int(
        db.scalar(
            select(func.count(TaskRun.id)).where(TaskRun.scheduled_for >= day_start)
        )
        or 0
    )
    failed_runs = int(
        db.scalar(
            select(func.count(TaskRun.id)).where(
                TaskRun.status == "failed", TaskRun.scheduled_for >= day_start
            )
        )
        or 0
    )
    unconfirmed_runs = int(
        db.scalar(
            select(func.count(TaskRun.id)).where(
                TaskRun.status == "submitted_unconfirmed",
                TaskRun.scheduled_for >= day_start,
            )
        )
        or 0
    )
    total_invites = int(db.scalar(select(func.count(InviteCode.id))) or 0)
    total_redemption_codes = int(
        db.scalar(select(func.count(RedemptionCode.id))) or 0
    )
    return {
        "total_users": total_users,
        "active_users": active_users,
        "active_subscriptions": active_subscriptions,
        "total_tasks": total_tasks,
        "enabled_tasks": enabled_tasks,
        "total_runs": total_runs,
        "runs_today": runs_today,
        "failed_runs": failed_runs,
        "failed_runs_today": failed_runs,
        "unconfirmed_runs_today": unconfirmed_runs,
        "attention_runs_today": failed_runs + unconfirmed_runs,
        "total_invites": total_invites,
        "total_redemption_codes": total_redemption_codes,
        # Compatibility aliases make small template iterations deployment-neutral.
        "users": total_users,
        "subscriptions": active_subscriptions,
        "tasks": total_tasks,
        "runs": total_runs,
    }
