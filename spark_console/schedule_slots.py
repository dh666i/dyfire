"""Spread new tasks across quieter minutes instead of one shared send time."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from spark_console.models import SparkTask

SHANGHAI = ZoneInfo("Asia/Shanghai")
DAY_START = 8 * 60
DAY_END = 22 * 60
MINUTES_IN_DAY = 24 * 60


def stagger_gap_minutes() -> int:
    return max(1, int(os.environ.get("SPARK_TASK_STAGGER_MINUTES", "5")))


def to_minutes(hhmm: str) -> int:
    hour, minute = map(int, hhmm.split(":"))
    return hour * 60 + minute


def from_minutes(total: int) -> str:
    total %= MINUTES_IN_DAY
    return f"{total // 60:02d}:{total % 60:02d}"


def enabled_counts(db) -> dict[str, int]:
    rows = db.execute(
        select(SparkTask.send_time, func.count())
        .where(SparkTask.enabled.is_(True))
        .group_by(SparkTask.send_time)
    ).all()
    return {str(send_time): int(count) for send_time, count in rows}


def account_occupied(db, account_id: str | None) -> set[str]:
    if not account_id:
        return set()
    return {
        str(send_time)
        for send_time in db.scalars(
            select(SparkTask.send_time).where(
                SparkTask.douyin_account_id == account_id,
                SparkTask.enabled.is_(True),
            )
        )
    }


def stagger_from(
    start: str,
    count: int,
    occupied: set[str] | None = None,
    *,
    gap: int | None = None,
) -> list[str]:
    if count < 1:
        return []
    gap = stagger_gap_minutes() if gap is None else max(1, gap)
    busy = occupied or set()
    times: list[str] = []
    cursor = to_minutes(start)
    for _ in range(MINUTES_IN_DAY + count * gap):
        hhmm = from_minutes(cursor)
        if hhmm not in busy:
            times.append(hhmm)
            if len(times) == count:
                return times
        cursor += gap
    return times


def window_load(times: list[str], counts: dict[str, int]) -> int:
    return sum(counts.get(time, 0) for time in times)


def _daytime(times: list[str]) -> bool:
    return all(DAY_START <= to_minutes(time) <= DAY_END for time in times)


def suggest_windows(
    counts: dict[str, int],
    occupied: set[str],
    count: int,
    preferred: str | None = None,
    *,
    now: datetime | None = None,
    limit: int = 3,
) -> list[dict[str, object]]:
    gap = stagger_gap_minutes()
    local = now.astimezone(SHANGHAI) if now and now.tzinfo else (now or datetime.now(SHANGHAI))
    now_min = local.hour * 60 + local.minute
    preferred_min = to_minutes(preferred) if preferred else None
    ranked: list[tuple[float, int, str, list[str]]] = []
    for start in range(DAY_START, DAY_END + 1):
        hhmm = from_minutes(start)
        if hhmm in occupied:
            continue
        times = stagger_from(hhmm, count, occupied, gap=gap)
        if len(times) != count or times[0] != hhmm or not _daytime(times):
            continue
        load = window_load(times, counts)
        tomorrow = 1 if start <= now_min else 0
        distance = abs(start - preferred_min) if preferred_min is not None else 0
        score = load * 100 + tomorrow * 40 + min(distance, 240) / 10
        ranked.append((score, load, hhmm, times))
    ranked.sort()
    picked: list[dict[str, object]] = []
    seen: set[str] = set()
    for _score, load, hhmm, times in ranked:
        if hhmm in seen:
            continue
        seen.add(hhmm)
        picked.append({"start": hhmm, "times": times, "load": load})
        if len(picked) >= limit:
            break
    return picked


def plan(db, account_id: str | None, count: int, preferred: str) -> dict[str, object]:
    counts = enabled_counts(db)
    occupied = account_occupied(db, account_id)
    times = stagger_from(preferred, max(1, count), occupied)
    suggestions = [
        item
        for item in suggest_windows(counts, occupied, max(1, count), preferred)
        if item["start"] != times[0]
    ]
    return {
        "times": times,
        "suggested": suggestions[:3],
        "load": window_load(times, counts),
        "gap_minutes": stagger_gap_minutes(),
    }


def next_runs(times: list[str], now_utc: datetime | None = None) -> list[datetime]:
    from spark_console.scheduler import compute_next_run

    if not times:
        return []
    now_utc = now_utc or datetime.now(timezone.utc)
    first_local = compute_next_run(times[0], now_utc).astimezone(SHANGHAI)
    runs = []
    for hhmm in times:
        hour, minute = map(int, hhmm.split(":"))
        local = first_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if local < first_local:
            local += timedelta(days=1)
        runs.append(local.astimezone(timezone.utc))
    return runs
