from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from spark_console.models import (
    DouyinAccount,
    SparkTask,
    SubscriptionGrant,
    User,
    UserSubscription,
    utc_now,
)
from spark_console.services.subscriptions import (
    FREE_MAX_ACCOUNTS,
    FREE_MAX_TASKS,
    is_unlimited_tasks,
    reconcile_subscription,
    task_limit_label,
)


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
    enabled_task_count = int(
        session.scalar(
            select(func.count())
            .select_from(SparkTask)
            .where(
                SparkTask.owner_user_id == user.id,
                SparkTask.enabled.is_(True),
            )
        )
        or 0
    )
    subscription = session.get(UserSubscription, user.id)
    if subscription is not None:
        # Apply a queued lower plan at its paid period boundary. Reads and
        # write guards then use the same effective entitlement.
        reconcile_subscription(session, subscription, current)

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
        status_label = "免费版"
        active = False
        valid_until = None
        days_remaining = 0
        # New users get a small free tier: one bound account and one enabled
        # friend task. ``active`` remains false because it denotes a paid
        # subscription; the explicit flag below tells callers why these
        # otherwise non-zero limits are available.
        max_accounts = FREE_MAX_ACCOUNTS
        max_tasks = FREE_MAX_TASKS
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

    pending_subscription = None
    if subscription is not None and subscription.pending_valid_from is not None:
        pending_from = _aware(subscription.pending_valid_from)
        pending_grant = session.scalar(
            select(SubscriptionGrant)
            .where(
                SubscriptionGrant.user_id == user.id,
                SubscriptionGrant.valid_from == pending_from,
                SubscriptionGrant.valid_until > pending_from,
                SubscriptionGrant.superseded_at.is_(None),
            )
            .order_by(SubscriptionGrant.created_at.desc())
        )
        pending_max_tasks = subscription.pending_max_tasks
        pending_max_accounts = subscription.pending_max_accounts
        pending_subscription = {
            "valid_from": pending_from,
            "valid_until": pending_grant.valid_until if pending_grant else None,
            "max_accounts": pending_max_accounts or max_accounts or 1,
            "max_tasks": pending_max_tasks or max_tasks or 10,
            "task_limit_label": task_limit_label(
                pending_max_tasks if pending_max_tasks is not None else max_tasks
            ),
            "status_label": "下期生效",
        } if pending_max_tasks is not None or pending_max_accounts is not None else None
    free_tier = (
        user.status == "active"
        and user.role != "admin"
        and subscription is None
    )
    if max_tasks is None or is_admin or is_unlimited_tasks(max_tasks):
        task_overage = 0
        task_capacity_status = "unlimited" if active or is_admin else "inactive"
    else:
        task_overage = max(0, enabled_task_count - int(max_tasks or 0))
        task_capacity_status = "over_limit" if task_overage else ("available" if active or free_tier else "inactive")

    return {
        "subscription": subscription,
        "active": active,
        "status": status,
        "status_label": status_label,
        "valid_until": valid_until,
        "days_remaining": days_remaining,
        "max_accounts": max_accounts,
        "max_tasks": max_tasks,
        "task_limit_label": task_limit_label(max_tasks),
        "account_count": account_count,
        "task_count": task_count,
        "can_add_account": (active or free_tier or is_admin)
        and (is_admin or account_count < int(max_accounts or 0)),
        # Paused tasks are retained history/configuration and do not consume
        # the live plan quota. Existing enabled tasks are deliberately left
        # running when a later plan is lower; only new/resumed tasks are
        # blocked once the active count reaches the effective limit.
        "enabled_task_count": enabled_task_count,
        "paused_task_count": max(0, task_count - enabled_task_count),
        "pending_max_accounts": subscription.pending_max_accounts if subscription else None,
        "pending_max_tasks": subscription.pending_max_tasks if subscription else None,
        "pending_valid_from": subscription.pending_valid_from if subscription else None,
        "task_overage": task_overage,
        "over_limit_task_count": task_overage,
        "over_limit_enabled_tasks": task_overage,
        "task_capacity_status": task_capacity_status,
        "task_capacity_label": f"超出当前额度 {task_overage} 个" if task_overage else ("不限任务" if task_capacity_status == "unlimited" else "额度可用"),
        "task_capacity_message": f"已有 {enabled_task_count} 个启用任务，当前上限为 {task_limit_label(max_tasks)}。" if task_overage else ("免费用户可使用 1 个好友任务；开通订阅可解锁更多。" if free_tier else "暂停任务不会占用额度；恢复和新增任务会按当前套餐检查。"),
        "pending_subscription": pending_subscription,
        "pending_plan": pending_subscription,
        "can_add_task": (active or free_tier or is_admin) and (
            is_admin
            or is_unlimited_tasks(max_tasks)
            or enabled_task_count < int(max_tasks or 0)
        ),
        "is_admin": is_admin,
        "free_tier": free_tier,
        "free_tier_label": "免费体验",
    }
