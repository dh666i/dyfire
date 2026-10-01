from __future__ import annotations

import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from spark_console.config import Settings
from spark_console.models import (
    DouyinAccount,
    DouyinLoginSession,
    SparkTask,
    TaskRun,
    User,
    UserSubscription,
    WorkerLock,
)


PROC_ROOT = Path("/proc")
CGROUP_ROOT = Path("/sys/fs/cgroup")
AUTH_HEARTBEAT_MAX_AGE_SECONDS = 60
CPU_SAMPLE_SECONDS = 0.1
OPERATIONS_REFRESH_SECONDS = 30
ACTIVE_LOGIN_STATES = (
    "queued",
    "loading_qr",
    "awaiting_scan",
    "confirming",
)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _round_percent(value: float) -> float:
    return round(min(100.0, max(0.0, value)), 1)


def _read_cpu_ticks(proc_root: Path = PROC_ROOT) -> tuple[int, int] | None:
    """Return aggregate (total, idle) CPU ticks without process information."""
    try:
        first_line = (proc_root / "stat").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()[0]
        fields = first_line.split()
        if not fields or fields[0] != "cpu":
            return None
        ticks = [int(value) for value in fields[1:]]
        if len(ticks) < 4:
            return None
    except (OSError, ValueError, IndexError):
        return None
    # guest and guest_nice are already included in user and nice.
    total = sum(ticks[:8])
    idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
    return total, idle


def _cpuset_cpu_count(cgroup_root: Path = CGROUP_ROOT) -> int | None:
    try:
        raw = (cgroup_root / "cpuset.cpus.effective").read_text(
            encoding="utf-8", errors="replace"
        ).strip()
    except OSError:
        return None
    count = 0
    try:
        for segment in raw.split(","):
            start_raw, separator, end_raw = segment.strip().partition("-")
            start = int(start_raw)
            end = int(end_raw) if separator else start
            if end < start:
                return None
            count += end - start + 1
    except ValueError:
        return None
    return count or None


def _read_cgroup_cpu_sample(
    cgroup_root: Path = CGROUP_ROOT,
) -> tuple[int, float] | None:
    try:
        values = {}
        for line in (cgroup_root / "cpu.stat").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            key, _, value = line.partition(" ")
            values[key] = int(value)
        usage_usec = values["usage_usec"]
        quota_raw, period_raw = (cgroup_root / "cpu.max").read_text(
            encoding="utf-8", errors="replace"
        ).strip().split()[:2]
        if quota_raw != "max":
            capacity = int(quota_raw) / int(period_raw)
        else:
            capacity = float(_cpuset_cpu_count(cgroup_root) or os.cpu_count() or 1)
        if capacity <= 0:
            return None
        return usage_usec, capacity
    except (OSError, ValueError, ZeroDivisionError, KeyError, IndexError):
        return None


def _cpu_usage_metrics(
    proc_root: Path = PROC_ROOT,
    cgroup_root: Path | None = CGROUP_ROOT,
    sample_seconds: float | None = None,
) -> dict[str, Any]:
    delay = CPU_SAMPLE_SECONDS if sample_seconds is None else max(0.0, sample_seconds)
    cgroup_first = _read_cgroup_cpu_sample(cgroup_root) if cgroup_root is not None else None
    if cgroup_first is not None:
        started = time.monotonic()
        if delay:
            time.sleep(delay)
        cgroup_second = _read_cgroup_cpu_sample(cgroup_root)
        elapsed = time.monotonic() - started
        if cgroup_second is not None and elapsed > 0:
            usage_delta = cgroup_second[0] - cgroup_first[0]
            if usage_delta >= 0:
                capacity = cgroup_second[1]
                percent = usage_delta / (elapsed * 1_000_000 * capacity) * 100.0
                return {
                    "available": True,
                    "percent": _round_percent(percent),
                    "logical_cores": os.cpu_count(),
                    "capacity_cores": round(capacity, 2),
                    "source": "cgroup_v2",
                    "scope": "web_container",
                }

    first = _read_cpu_ticks(proc_root)
    if first is not None:
        if delay:
            time.sleep(delay)
        second = _read_cpu_ticks(proc_root)
        if second is not None:
            total_delta = second[0] - first[0]
            idle_delta = second[1] - first[1]
            if total_delta > 0:
                return {
                    "available": True,
                    "percent": _round_percent(
                        (total_delta - idle_delta) * 100.0 / total_delta
                    ),
                    "logical_cores": os.cpu_count(),
                    "capacity_cores": None,
                    "source": "proc_stat",
                    "scope": "runtime_visible",
                }
    return {
        "available": False,
        "percent": None,
        "logical_cores": os.cpu_count(),
        "capacity_cores": None,
        "source": "unavailable",
        "scope": "runtime_visible",
    }


def _cpu_usage_percent(
    proc_root: Path = PROC_ROOT,
    sample_seconds: float | None = None,
    cgroup_root: Path = CGROUP_ROOT,
) -> float | None:
    return _cpu_usage_metrics(proc_root, cgroup_root, sample_seconds)["percent"]


def _memory_metrics(
    proc_root: Path = PROC_ROOT, cgroup_root: Path | None = CGROUP_ROOT
) -> dict[str, Any]:
    values: dict[str, int] = {}
    try:
        for line in (proc_root / "meminfo").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            key, separator, remainder = line.partition(":")
            if not separator:
                continue
            raw_value = remainder.strip().split()[0]
            values[key] = int(raw_value) * 1024
    except (OSError, ValueError, IndexError):
        proc_memory = {
            "available": False,
            "used_bytes": None,
            "total_bytes": None,
            "percent": None,
            "source": "unavailable",
            "scope": "runtime_visible",
        }
    else:
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable")
        if available is None:
            available = sum(
                values.get(name, 0) for name in ("MemFree", "Buffers", "Cached")
            )
        used = max(0, total - min(total, available)) if total > 0 else 0
        proc_memory = {
            "available": total > 0,
            "used_bytes": used if total > 0 else None,
            "total_bytes": total if total > 0 else None,
            "percent": _round_percent(used * 100.0 / total) if total > 0 else None,
            "source": "proc_meminfo" if total > 0 else "unavailable",
            "scope": "runtime_visible",
        }

    if cgroup_root is None:
        return proc_memory
    try:
        cgroup_used = int(
            (cgroup_root / "memory.current").read_text(
                encoding="utf-8", errors="replace"
            ).strip()
        )
        limit_raw = (cgroup_root / "memory.max").read_text(
            encoding="utf-8", errors="replace"
        ).strip()
        cgroup_limit = int(limit_raw) if limit_raw != "max" else 0
    except (OSError, ValueError):
        return proc_memory
    host_total = int(proc_memory.get("total_bytes") or 0)
    if (
        cgroup_limit <= 0
        or cgroup_limit >= (1 << 60)
        or (host_total and cgroup_limit >= host_total)
    ):
        return proc_memory
    cgroup_used = min(max(0, cgroup_used), cgroup_limit)
    return {
        "available": True,
        "used_bytes": cgroup_used,
        "total_bytes": cgroup_limit,
        "percent": _round_percent(cgroup_used * 100.0 / cgroup_limit),
        "source": "cgroup_v2",
        "scope": "web_container",
    }


def _disk_metrics(data_dir: Path, *, include_reserved: bool = True) -> dict[str, Any]:
    try:
        statvfs = getattr(os, "statvfs", None)
        if statvfs is not None:
            disk = statvfs(data_dir)
            block_size = disk.f_frsize or disk.f_bsize
            total = int(disk.f_blocks * block_size)
            free = int((disk.f_bavail if include_reserved else disk.f_bfree) * block_size)
            source = "statvfs"
        else:
            usage = shutil.disk_usage(data_dir)
            total = int(usage.total)
            free = int(usage.free)
            source = "disk_usage"
    except OSError:
        return {
            "available": False,
            "used_bytes": None,
            "total_bytes": None,
            "percent": None,
            "source": "unavailable",
        }
    if total <= 0:
        return {
            "available": False,
            "used_bytes": None,
            "total_bytes": None,
            "percent": None,
            "source": "unavailable",
        }
    free = max(0, min(total, free))
    used = total - free
    return {
        "available": True,
        "used_bytes": used,
        "total_bytes": total,
        "percent": _round_percent(used * 100.0 / total),
        "source": source,
    }


def _load_metrics(proc_root: Path | None = None) -> dict[str, Any]:
    try:
        if proc_root is None:
            one, five, fifteen = os.getloadavg()
        else:
            one, five, fifteen = map(float, (proc_root / "loadavg").read_text().split()[:3])
    except (AttributeError, OSError, ValueError):
        return {
            "available": False,
            "one": None,
            "five": None,
            "fifteen": None,
            "source": "unavailable",
        }
    return {
        "available": True,
        "one": round(one, 2),
        "five": round(five, 2),
        "fifteen": round(fifteen, 2),
        "source": "proc_loadavg" if proc_root is not None else "loadavg",
    }


def _uptime_seconds(proc_root: Path = PROC_ROOT) -> int | None:
    try:
        raw = (proc_root / "uptime").read_text(
            encoding="utf-8", errors="replace"
        ).split()[0]
        return max(0, int(float(raw)))
    except (OSError, ValueError, IndexError):
        return None


def _database_metrics(settings: Settings) -> dict[str, Any]:
    try:
        url = make_url(settings.database_url)
        backend = url.get_backend_name()
    except Exception:
        return {
            "backend": "unknown",
            "size_available": False,
            "size_bytes": None,
        }
    size: int | None = None
    if backend == "sqlite" and url.database and url.database != ":memory:":
        database_path = Path(url.database)
        try:
            if database_path.is_file():
                size = sum(
                    path.stat().st_size
                    for path in (
                        database_path,
                        Path(f"{database_path}-wal"),
                        Path(f"{database_path}-shm"),
                    )
                    if path.is_file()
                )
        except OSError:
            size = None
    return {
        "backend": backend,
        "size_available": size is not None,
        "size_bytes": size,
    }


def _count(db: Session, model, *criteria) -> int:
    statement = select(func.count()).select_from(model)
    if criteria:
        statement = statement.where(*criteria)
    return int(db.scalar(statement) or 0)


def _scheduler_eligible_task_filters(now: datetime):
    return (
        User.status == "active",
        or_(
            User.role == "admin",
            and_(
                UserSubscription.valid_from <= now,
                UserSubscription.valid_until > now,
            ),
        ),
        SparkTask.enabled.is_(True),
        SparkTask.douyin_account_id.is_not(None),
        SparkTask.next_run_at.is_not(None),
    )


def _eligible_due_tasks(db: Session, now: datetime) -> int:
    return int(
        db.scalar(
            select(func.count(SparkTask.id))
            .select_from(SparkTask)
            .join(User, User.id == SparkTask.owner_user_id)
            .join(
                DouyinAccount,
                and_(
                    DouyinAccount.id == SparkTask.douyin_account_id,
                    DouyinAccount.owner_user_id == SparkTask.owner_user_id,
                ),
            )
            .outerjoin(
                UserSubscription,
                UserSubscription.user_id == SparkTask.owner_user_id,
            )
            .where(
                *_scheduler_eligible_task_filters(now),
                SparkTask.next_run_at <= now,
            )
        )
        or 0
    )


def _current_retries(db: Session, now: datetime) -> int:
    from spark_console.models import TaskHealthRetry
    from spark_console.health_retry import invalid_reason
    recovery = sum(1 for run, pending in db.execute(
        select(TaskRun, TaskHealthRetry).join(TaskHealthRetry, TaskHealthRetry.run_id == TaskRun.id)
        .where(TaskRun.status == "retrying")
    ) if invalid_reason(db, run, pending, now) is None)
    return recovery + int(
        db.scalar(
            select(func.count(func.distinct(TaskRun.task_id)))
            .select_from(TaskRun)
            .join(SparkTask, SparkTask.id == TaskRun.task_id)
            .join(User, User.id == SparkTask.owner_user_id)
            .join(
                DouyinAccount,
                and_(
                    DouyinAccount.id == SparkTask.douyin_account_id,
                    DouyinAccount.owner_user_id == SparkTask.owner_user_id,
                ),
            )
            .outerjoin(
                UserSubscription,
                UserSubscription.user_id == SparkTask.owner_user_id,
            )
            .where(
                *_scheduler_eligible_task_filters(now),
                TaskRun.status == "retrying",
                TaskRun.retry_after.is_not(None),
                TaskRun.retry_after == SparkTask.next_run_at,
                ~select(TaskHealthRetry.run_id).where(TaskHealthRetry.run_id == TaskRun.id).exists(),
            )
        )
        or 0
    )


def _queue_metrics(db: Session, now: datetime) -> dict[str, int]:
    recent = now - timedelta(hours=24)
    return {
        "enabled_tasks": _count(db, SparkTask, SparkTask.enabled.is_(True)),
        "total_accounts": _count(db, DouyinAccount),
        "due_tasks": _eligible_due_tasks(db, now),
        "running_runs": _count(
            db,
            TaskRun,
            TaskRun.status == "running",
            TaskRun.lease_until.is_not(None),
            TaskRun.lease_until > now,
        ),
        "retrying_runs": _current_retries(db, now),
        "active_logins": _count(
            db,
            DouyinLoginSession,
            DouyinLoginSession.status.in_(ACTIVE_LOGIN_STATES),
            DouyinLoginSession.expires_at > now,
        ),
        "attention_24h": _count(
            db,
            TaskRun,
            TaskRun.scheduled_for >= recent,
            TaskRun.status.in_(("failed", "submitted_unconfirmed")),
        ),
        "invalid_accounts": _count(
            db, DouyinAccount, DouyinAccount.validation_state == "invalid"
        ),
    }


def _auth_service(settings: Settings, now: datetime) -> dict[str, Any]:
    heartbeat = settings.data_dir / "auth-worker.heartbeat"
    try:
        modified_at = datetime.fromtimestamp(heartbeat.stat().st_mtime, timezone.utc)
    except OSError:
        return {
            "status": "offline",
            "label": "登录服务",
            "detail": "尚未收到登录进程心跳",
            "last_seen_at": None,
            "age_seconds": None,
        }
    age = (now - modified_at).total_seconds()
    healthy = -30 <= age <= AUTH_HEARTBEAT_MAX_AGE_SECONDS
    return {
        "status": "healthy" if healthy else "offline",
        "label": "登录服务",
        "detail": (
            "进程心跳正常，未执行登录功能探测"
            if healthy
            else "进程心跳已超过 60 秒"
        ),
        "last_seen_at": modified_at.isoformat(),
        "age_seconds": max(0, int(age)),
    }


def _worker_service(db: Session, now: datetime) -> dict[str, Any]:
    locks = db.scalars(select(WorkerLock).order_by(WorkerLock.id)).all()
    active = [
        lock for lock in locks
        if lock.worker_id and lock.lease_until and _aware(lock.lease_until) > now
    ]
    lease_until = max((_aware(lock.lease_until) for lock in active), default=None)
    healthy = bool(active)
    return {
        "status": "healthy" if healthy else "offline",
        "label": "续火执行器",
        "detail": (
            f"{len(active)} 个分片租约正常，未执行发送功能探测"
            if healthy
            else "当前没有有效执行租约"
        ),
        "lease_until": lease_until.isoformat() if lease_until else None,
        "lease_remaining_seconds": (
            max(0, int((lease_until - now).total_seconds())) if lease_until else None
        ),
        "active_shards": [lock.id - 1 for lock in active],
    }


def _host_system_metrics(root: Path, sample_seconds: float | None = None) -> dict[str, Any]:
    # Read only the explicitly mounted host counters, never container quotas.
    cpu = _cpu_usage_metrics(root, None, sample_seconds)
    try:
        cpu_lines = (root / "stat").read_text().splitlines()
        cores = sum(
            bool(line.split() and line.split()[0].startswith("cpu")
                 and line.split()[0][3:].isdigit())
            for line in cpu_lines
        ) or None
    except OSError:
        cores = None
    cpu.update(scope="host", logical_cores=cores, capacity_cores=cores)
    memory = _memory_metrics(root, None)
    memory["scope"] = "host"
    # A single file on the host root filesystem is enough for statvfs; no
    # host directory contents or Docker socket need to be exposed to Web.
    disk = _disk_metrics(root / "rootfs", include_reserved=False)
    disk["scope"] = "host_root"
    load = _load_metrics(root)
    uptime = _uptime_seconds(root)
    available = all(item["available"] for item in (cpu, memory, disk, load)) and uptime is not None
    return {
        "scope": "host",
        "scope_label": "本台服务器整体资源" if available else "本台服务器（部分指标不可读取）",
        "cpu": cpu,
        "memory": memory,
        "disk": disk,
        "load": load,
        "uptime_seconds": uptime,
        "uptime_available": uptime is not None,
    }


def operations_snapshot(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    proc_root: Path = PROC_ROOT,
    cgroup_root: Path = CGROUP_ROOT,
    cpu_sample_seconds: float | None = None,
    host_metrics_root: Path | None = None,
) -> dict[str, Any]:
    """Build a read-only, redacted operations snapshot for administrators."""
    generated_at = _aware(now or datetime.now(timezone.utc))
    host_path = host_metrics_root or os.environ.get("SPARK_HOST_METRICS_DIR")
    if host_path:
        system = _host_system_metrics(Path(host_path), cpu_sample_seconds)
    else:
        cpu = _cpu_usage_metrics(proc_root, cgroup_root, cpu_sample_seconds)
        memory = _memory_metrics(proc_root, cgroup_root)
        uptime = _uptime_seconds(proc_root)
        container_scoped = cpu.get("scope") == "web_container" or memory.get("scope") == "web_container"
        system = {
            "scope": "web_container" if container_scoped else "runtime_visible",
            "scope_label": "未配置服务器采集；当前为 Web 容器/运行环境数据",
            "cpu": cpu,
            "memory": memory,
            "disk": _disk_metrics(settings.data_dir),
            "load": _load_metrics(),
            "uptime_seconds": uptime,
            "uptime_available": uptime is not None,
        }
    return {
        "version": 1,
        "generated_at": generated_at.isoformat(),
        "refresh_interval_seconds": OPERATIONS_REFRESH_SECONDS,
        "units": {
            "storage": "bytes",
            "utilization": "percent",
            "uptime": "seconds",
            "load": "runnable_tasks",
        },
        "system": system,
        "services": {
            "web": {
                "status": "healthy",
                "label": "Web 控制台",
                "detail": "当前页面响应与数据库读取正常",
                "checked_at": generated_at.isoformat(),
            },
            "auth": _auth_service(settings, generated_at),
            "worker": _worker_service(db, generated_at),
        },
        "database": _database_metrics(settings),
        "queue": _queue_metrics(db, generated_at),
    }
