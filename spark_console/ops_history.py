from __future__ import annotations

import logging
import os
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, or_, select

from spark_console.config import Settings
from spark_console.db import create_engine_for, session_scope
from spark_console.models import DouyinLoginSession, TaskRun
from spark_console.ops_monitor import (
    ACTIVE_LOGIN_STATES, _aware, _disk_metrics, _load_metrics,
    _memory_metrics, _read_cpu_ticks, _round_percent,
)

SAMPLE_SECONDS = 5
RETENTION_SECONDS = 72 * 3600
HISTORY_FILE = "operations-history.sqlite3"
FIELDS = ("cpu", "memory", "disk", "load", "logins", "sends")


def history_path(settings: Settings) -> Path:
    return settings.data_dir / HISTORY_FILE


def write_sample(path: Path, sample: dict) -> None:
    with closing(sqlite3.connect(path, timeout=2)) as connection, connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS samples (at INTEGER PRIMARY KEY, "
            "cpu REAL, memory REAL, disk REAL, load REAL, logins INTEGER, sends INTEGER)"
        )
        connection.execute(
            "INSERT OR REPLACE INTO samples VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sample["at"], *(sample.get(field) for field in FIELDS)),
        )
        connection.execute("DELETE FROM samples WHERE at < ?", (sample["at"] - RETENTION_SECONDS,))


def read_history(path: Path, hours: int = 1, *, now: int | None = None) -> dict:
    hours = hours if hours in (1, 6, 24) else 1
    now = int(time.time()) if now is None else now
    bucket = {1: 5, 6: 30, 24: 120}[hours]
    start = now - hours * 3600
    result = {"hours": hours, "start": start, "end": now, "bucket_seconds": bucket,
              "sample_seconds": SAMPLE_SECONDS, "points": [], "latest_at": None,
              "state": "empty", "cpu_peak": None, "memory_peak": None}
    if not path.is_file():
        return result
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as connection:
            rows = connection.execute(
                "SELECT (at / ?) * ?, MAX(cpu), MAX(memory), MAX(disk), MAX(load), "
                "MAX(logins), MAX(sends), COUNT(*) FROM samples "
                "WHERE at >= ? AND at <= ? GROUP BY (at / ?) ORDER BY 1",
                (bucket, bucket, start, now, bucket),
            ).fetchall()
            last_row = connection.execute("SELECT at, cpu, memory FROM samples WHERE at <= ? ORDER BY at DESC LIMIT 1", (now,)).fetchone()
            latest = last_row[0] if last_row else None
        result["latest_at"] = latest
        result["state"] = "fresh" if latest is not None and now - latest <= 30 else "stale"
        if result["state"] == "fresh" and (last_row[1] is None or last_row[2] is None):
            result["state"] = "partial"
        result["points"] = [dict(zip(("at", *FIELDS, "samples"), row)) for row in rows]
        for field in ("cpu", "memory"):
            values = [row[field] for row in result["points"] if row[field] is not None]
            result[field + "_peak"] = max(values) if values else None
    except sqlite3.Error:
        result["state"] = "unavailable"
    return result


def activity_counts(db, now: datetime, since: datetime) -> tuple[int, int]:
    # Include sessions/runs that finished between samples, even if they were
    # shorter than one interval. Never read QR images, credentials or messages.
    logins = db.scalar(select(func.count()).select_from(DouyinLoginSession).where(
        DouyinLoginSession.created_at <= now,
        or_(
            DouyinLoginSession.finished_at >= since,
            (DouyinLoginSession.status.in_(ACTIVE_LOGIN_STATES)) & (DouyinLoginSession.expires_at > since),
        ),
    )) or 0
    sends = db.scalar(select(func.count()).select_from(TaskRun).where(
        TaskRun.started_at <= now,
        or_(TaskRun.finished_at >= since,
            (TaskRun.status == "running") & (TaskRun.lease_until > since)),
    )) or 0
    return int(logins), int(sends)


def recent_activity(db, hours: int, *, now: datetime | None = None) -> list[dict]:
    now = _aware(now or datetime.now(timezone.utc))
    since = now - timedelta(hours=hours if hours in (1, 6, 24) else 1)
    events = []
    queries = (
        ("login", select(DouyinLoginSession.id, DouyinLoginSession.created_at,
                         DouyinLoginSession.finished_at, DouyinLoginSession.status,
                         DouyinLoginSession.expires_at)
         .where(DouyinLoginSession.created_at >= since, DouyinLoginSession.created_at <= now)
         .order_by(DouyinLoginSession.created_at.desc()).limit(12)),
        ("send", select(TaskRun.id, TaskRun.started_at, TaskRun.finished_at,
                        TaskRun.status, TaskRun.lease_until)
         .where(TaskRun.started_at >= since, TaskRun.started_at <= now)
         .order_by(TaskRun.started_at.desc()).limit(12)),
    )
    for kind, query in queries:
        for identifier, started, finished, status, deadline in db.execute(query):
            started = _aware(started)
            ended = _aware(finished) if finished else None
            active_states = ACTIVE_LOGIN_STATES if kind == "login" else ("running",)
            active = status in active_states and deadline is not None and _aware(deadline) > now
            duration = max(0, int(((now if active else ended) - started).total_seconds())) if active or ended else None
            events.append({"kind": kind, "id": identifier, "start": int(started.timestamp()),
                           "end": int(ended.timestamp()) if ended else None,
                           "status": status, "active": active, "duration": duration})
    return sorted(events, key=lambda item: item["start"], reverse=True)[:12]


def collect_sample(db, root: Path, previous_ticks, *, now: datetime, since: datetime):
    ticks = _read_cpu_ticks(root)
    cpu = None
    if ticks and previous_ticks:
        total, idle = ticks[0] - previous_ticks[0], ticks[1] - previous_ticks[1]
        if total > 0 and 0 <= idle <= total:
            cpu = _round_percent(100 * (total - idle) / total)
    logins, sends = activity_counts(db, now, since)
    return {"at": int(now.timestamp()), "cpu": cpu,
            "memory": _memory_metrics(root, None)["percent"],
            "disk": _disk_metrics(root / "rootfs", include_reserved=False)["percent"],
            "load": _load_metrics(root)["one"], "logins": logins, "sends": sends}, ticks


def main() -> None:
    if "--health" in sys.argv:
        path = Path(os.environ.get("SPARK_DATA_DIR", "/data")) / HISTORY_FILE
        raise SystemExit(0 if read_history(path)["state"] == "fresh" else 1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env(os.environ, service='monitor')
    root = Path(os.environ["SPARK_HOST_METRICS_DIR"])
    engine = create_engine_for(settings)
    previous_ticks = None
    previous_time = datetime.now(timezone.utc)
    while True:
        started = time.monotonic()
        now = datetime.now(timezone.utc)
        try:
            with session_scope(engine) as db:
                sample, ticks = collect_sample(db, root, previous_ticks, now=now, since=previous_time)
            write_sample(history_path(settings), sample)
            previous_ticks, previous_time = ticks, now
        except Exception:
            # Keep business data and SQL parameters out of the monitor log.
            logging.error("Resource sample failed; retrying next interval")
            previous_ticks, previous_time = None, now
        time.sleep(max(0.1, SAMPLE_SECONDS - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
