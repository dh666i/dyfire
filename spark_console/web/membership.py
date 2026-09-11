from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from spark_console.models import DouyinAccount, SparkTask, User, UserSubscription, utc_now


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def membership_summary(
    session: Session, user: User, now: datetime | None = None
) -> dict[str, object]:
    current = _aware(now or utc_now())
    account_count = int(
        session.scalar(
            select(func.count())
            .select_from(DouyinAccount)
            .where(DouyinAccount.owner_user_id == user.id)
        )
        or 0
    )
    task_count = int(
        session.scalar(
            select(func.count())
            .select_from(SparkTask)
            .where(SparkTask.owner_user_id == user.id)
        )
        or 0
    )
    subscription = session.get(UserSubscription, user.id)

    if user.status != "active":
        status = "disabled"
        status_label = "账号已停用"
        active = False
        valid_until = subscription.valid_until if subscription else None
        days_remaining = 0
        max_accounts = subscription.max_accounts if subscription else 0
        max_tasks = subscription.max_tasks if subscription else 0
        is_admin = user.role == "admin"
    elif user.role == "admin":
        status = "admin"
        status_label = "管理员"
        active = True
        valid_until = None
        days_remaining = None
        max_accounts = None
        max_tasks = None
        is_admin = True
    elif subscription is None:
        status = "inactive"
        status_label = "未开通"
        active = False
        valid_until = None
        days_remaining = 0
        max_accounts = 0
        max_tasks = 0
        is_admin = False
    else:
        valid_until = _aware(subscription.valid_until)
        active = _aware(subscription.valid_from) <= current < valid_until
        remaining_seconds = max(0, (valid_until - current).total_seconds())
        days_remaining = math.ceil(remaining_seconds / 86400)
        if not active:
            status = "expired"
            status_label = "已过期"
        elif days_remaining <= 7:
            status = "expiring"
            status_label = "即将到期"
        else:
            status = "active"
            status_label = "使用中"
        max_accounts = subscription.max_accounts
        max_tasks = subscription.max_tasks
        is_admin = False

    return {
        "subscription": subscription,
        "active": active,
        "status": status,
        "status_label": status_label,
        "valid_until": valid_until,
        "days_remaining": days_remaining,
        "max_accounts": max_accounts,
        "max_tasks": max_tasks,
        "account_count": account_count,
        "task_count": task_count,
        "can_add_account": active
        and (is_admin or account_count < int(max_accounts or 0)),
        "can_add_task": active and (is_admin or task_count < int(max_tasks or 0)),
        "is_admin": is_admin,
    }
