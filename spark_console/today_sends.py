"""Today's send board, grouped by Douyin account."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from spark_console.models import DouyinAccount, SparkTask, TaskRun, User

SHANGHAI = ZoneInfo("Asia/Shanghai")
HEALTH_TONE = {
    "valid": "good",
    "temporary": "warn",
    "unknown": "off",
    "verification": "danger",
    "invalid": "danger",
    "relogin": "danger",
}
HEALTH_LABEL = {
    "valid": "正常",
    "temporary": "暂时异常",
    "unknown": "待验证",
    "verification": "需要验证",
    "invalid": "确认失效",
    "relogin": "正在重新登录",
}


def day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local = current.astimezone(SHANGHAI)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def account_health_view(state: str | None) -> dict[str, str]:
    resolved = str(state or "unknown")
    return {
        "state": resolved,
        "label": HEALTH_LABEL.get(resolved, "待验证"),
        "tone": HEALTH_TONE.get(resolved, "off"),
    }


def _empty_counts() -> dict[str, int]:
    return {
        "planned": 0,
        "success": 0,
        "missed": 0,
        "unconfirmed": 0,
        "login": 0,
        "running": 0,
        "other": 0,
    }


def board(db, now: datetime | None = None) -> dict[str, object]:
    from spark_console.web.app import run_guidance, run_view_status

    start, end = day_bounds(now)
    local_day = start.astimezone(SHANGHAI).strftime("%Y-%m-%d")
    planned_rows = db.execute(
        select(
            SparkTask.douyin_account_id,
            User.username,
            DouyinAccount.display_name,
            DouyinAccount.validation_state,
            func.count(),
        )
        .join(User, SparkTask.owner_user_id == User.id)
        .outerjoin(DouyinAccount, SparkTask.douyin_account_id == DouyinAccount.id)
        .where(SparkTask.enabled.is_(True))
        .group_by(
            SparkTask.douyin_account_id,
            User.username,
            DouyinAccount.display_name,
            DouyinAccount.validation_state,
        )
    ).all()
    groups: dict[str, dict[str, object]] = {}
    for account_id, username, display_name, health_state, planned in planned_rows:
        key = str(account_id or f"missing:{username}")
        groups[key] = {
            "key": key,
            "account_id": account_id,
            "account_name": display_name or "账号不可用",
            "username": username,
            "health": account_health_view(health_state),
            "counts": _empty_counts(),
            "runs": [],
        }
        groups[key]["counts"]["planned"] = int(planned)

    runs = db.execute(
        select(TaskRun, SparkTask, User, DouyinAccount)
        .join(SparkTask, TaskRun.task_id == SparkTask.id)
        .join(User, SparkTask.owner_user_id == User.id)
        .outerjoin(DouyinAccount, SparkTask.douyin_account_id == DouyinAccount.id)
        .where(TaskRun.scheduled_for >= start, TaskRun.scheduled_for < end)
        .order_by(TaskRun.scheduled_for, SparkTask.target_name)
    ).all()
    for run, task, user, account in runs:
        key = str(task.douyin_account_id or f"missing:{user.username}")
        if key not in groups:
            groups[key] = {
                "key": key,
                "account_id": task.douyin_account_id,
                "account_name": account.display_name if account else "账号不可用",
                "username": user.username,
                "health": account_health_view(account.validation_state if account else None),
                "counts": _empty_counts(),
                "runs": [],
            }
        view = run_view_status(run)
        guidance = run_guidance(run)
        kind = view["kind"]
        counts = groups[key]["counts"]
        if kind == "success":
            counts["success"] += 1
        elif kind in {"missed", "retry", "skipped"}:
            counts["missed"] += 1
        elif kind == "unconfirmed":
            counts["unconfirmed"] += 1
        elif kind == "login":
            counts["login"] += 1
        elif kind == "running":
            counts["running"] += 1
        else:
            counts["other"] += 1
        groups[key]["runs"].append(
            {
                "id": run.id,
                "target_name": task.target_name,
                "send_time": task.send_time,
                "scheduled_for": run.scheduled_for,
                "view": view,
                "can_retry": bool(guidance and guidance.get("action") == "retry"),
            }
        )

    ordered = sorted(
        groups.values(),
        key=lambda item: (
            -(item["counts"]["login"] + item["counts"]["missed"] + item["counts"]["unconfirmed"]),
            item["username"],
            item["account_name"],
        ),
    )
    totals = _empty_counts()
    for group in ordered:
        for key, value in group["counts"].items():
            totals[key] += value
    return {"day": local_day, "groups": ordered, "totals": totals}
