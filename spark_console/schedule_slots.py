"""Choose daily send times from bounded, aggregated task load.

Plans never move existing tasks.  Account spacing is checked on a circular
24-hour clock because these are recurring tasks, including across midnight.
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import datetime, timedelta, timezone
import os
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from spark_console.models import SparkTask

SHANGHAI = ZoneInfo("Asia/Shanghai")
DAY_START = 8 * 60
DAY_END = 22 * 60
MINUTES_IN_DAY = 24 * 60
MINIMUM_LEAD_MINUTES = 5
ADAPTIVE_LOOKAHEAD_MINUTES = 30


def stagger_gap_minutes() -> int:
    # Never compress account sends below the existing safe five-minute gap.
    return max(5, min(MINUTES_IN_DAY, int(os.environ.get("SPARK_TASK_STAGGER_MINUTES", "5"))))


def to_minutes(hhmm: str) -> int:
    hour, minute = map(int, hhmm.split(":"))
    return hour * 60 + minute


def from_minutes(total: int) -> str:
    total %= MINUTES_IN_DAY
    return f"{total // 60:02d}:{total % 60:02d}"


def _local_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(SHANGHAI)
    return now.astimezone(SHANGHAI) if now.tzinfo else now.replace(tzinfo=SHANGHAI)


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
    # DISTINCT bounds this read to the 1,440 possible daily minutes even if
    # an old account has many tasks at the same minute.
    return set(db.scalars(
        select(SparkTask.send_time).distinct().where(
            SparkTask.douyin_account_id == account_id,
            SparkTask.enabled.is_(True),
        )
    ))


def _safe_minutes(occupied: set[str], gap: int) -> list[bool]:
    safe = [True] * MINUTES_IN_DAY
    for hhmm in occupied:
        minute = to_minutes(hhmm)
        for offset in range(1 - gap, gap):
            safe[(minute + offset) % MINUTES_IN_DAY] = False
    return safe


def _nearby_loads(counts: dict[str, int]) -> list[int]:
    histogram = [0] * MINUTES_IN_DAY
    for hhmm, count in counts.items():
        histogram[to_minutes(hhmm)] += count
    # Include nearby minutes rather than treating 09:30 and 09:31 as two
    # independent quiet windows.  The five-minute account gap remains separate.
    return [
        sum(histogram[(minute + offset) % MINUTES_IN_DAY] for offset in range(-4, 5))
        for minute in range(MINUTES_IN_DAY)
    ]


def _allocate(
    start: int,
    count: int,
    safe: list[bool],
    nearby: list[int],
    gap: int,
    *,
    daytime: bool,
) -> list[str]:
    """Greedy bounded allocation, reserving enough space for later friends."""
    end = start + MINUTES_IN_DAY - gap
    available = [
        minute for minute in range(start, end + 1)
        if safe[minute % MINUTES_IN_DAY]
        and (not daytime or DAY_START <= minute % MINUTES_IN_DAY <= DAY_END)
    ]
    if not available or available[0] != start:
        return []
    # Suffix capacity prevents choosing a quiet but late minute that leaves
    # too little room for the rest of a batch.  No minute can be reused tomorrow.
    capacity = [0] * (len(available) + 1)
    for index in range(len(available) - 1, -1, -1):
        following = bisect_left(available, available[index] + gap, lo=index + 1)
        capacity[index] = 1 + capacity[following]
    if capacity[0] < count:
        return []
    picked = [start]
    cursor = start
    for remaining in range(count - 1, 0, -1):
        first = bisect_left(available, cursor + gap)
        last = bisect_left(available, cursor + gap + ADAPTIVE_LOOKAHEAD_MINUTES + 1)
        candidates = [index for index in range(first, last) if capacity[index] >= remaining]
        if not candidates:
            # An occupied account window may be longer than the lookahead.
            if first >= len(available) or capacity[first] < remaining:
                return []
            candidates = [first]
        index = min(candidates, key=lambda value: (nearby[available[value] % MINUTES_IN_DAY], available[value]))
        cursor = available[index]
        picked.append(cursor)
    return [from_minutes(minute) for minute in picked]


def stagger_from(
    start: str,
    count: int,
    occupied: set[str] | None = None,
    *,
    gap: int | None = None,
) -> list[str]:
    if count < 1:
        return []
    gap = stagger_gap_minutes() if gap is None else max(5, gap)
    safe = _safe_minutes(occupied or set(), gap)
    anchor = to_minutes(start)
    for minute in range(anchor, anchor + MINUTES_IN_DAY):
        if safe[minute % MINUTES_IN_DAY]:
            # No global load means the earliest safe slots are chosen.
            return _allocate(minute, count, safe, [0] * MINUTES_IN_DAY, gap, daytime=False)
    return []


def window_load(times: list[str], counts: dict[str, int]) -> int:
    return sum(counts.get(time, 0) for time in times)


def _auto_candidates(safe: list[bool], nearby: list[int], now: datetime) -> list[int]:
    # Round upward so seconds in the current minute do not shorten the lead.
    future = now + timedelta(minutes=MINIMUM_LEAD_MINUTES)
    threshold = future.hour * 60 + future.minute + int(bool(future.second or future.microsecond))
    threshold += (future.date() - now.date()).days * MINUTES_IN_DAY
    candidates = []
    for minute in range(DAY_START, DAY_END + 1):
        if not safe[minute]:
            continue
        occurrence = minute if minute >= threshold else minute + MINUTES_IN_DAY
        candidates.append(occurrence)
    return sorted(candidates, key=lambda minute: (nearby[minute % MINUTES_IN_DAY], minute))


def suggest_windows(
    counts: dict[str, int],
    occupied: set[str],
    count: int,
    preferred: str | None = None,
    *,
    now: datetime | None = None,
    limit: int = 3,
) -> list[dict[str, object]]:
    if count < 1 or count > 100 or limit < 1:
        return []
    gap = stagger_gap_minutes()
    safe = _safe_minutes(occupied, gap)
    nearby = _nearby_loads(counts)
    picked = []
    for start in _auto_candidates(safe, nearby, _local_now(now)):
        times = _allocate(start, count, safe, nearby, gap, daytime=True)
        if len(times) != count:
            continue
        picked.append({
            "start": times[0], "times": times,
            "load": window_load(times, counts),
            "nearby_load": sum(nearby[to_minutes(time)] for time in times),
        })
        if len(picked) >= limit:
            break
    return picked


def plan(db, account_id: str | None, count: int, preferred: str = "09:30", *, mode: str = "manual", now: datetime | None = None) -> dict[str, object]:
    if not 1 <= count <= 100:
        raise ValueError("好友数量须为 1–100")
    if mode not in ("auto", "manual"):
        raise ValueError("发送时间模式无效")
    local = _local_now(now)
    counts = enabled_counts(db)
    occupied = account_occupied(db, account_id)
    gap = stagger_gap_minutes()
    nearby = _nearby_loads(counts)
    suggestions = suggest_windows(counts, occupied, count, preferred, now=local)
    if mode == "auto":
        times = list(suggestions[0]["times"]) if suggestions else []
    else:
        safe = _safe_minutes(occupied, gap)
        anchor = to_minutes(preferred)
        first = next((minute for minute in range(anchor, anchor + MINUTES_IN_DAY) if safe[minute % MINUTES_IN_DAY]), None)
        times = _allocate(first, count, safe, nearby, gap, daytime=False) if first is not None else []
    ready = len(times) == count
    runs = next_runs(times, local.astimezone(timezone.utc)) if ready else []
    entries = [
        {
            "index": index, "time": hhmm,
            "next_run_at": run.isoformat(),
            "next_run_local": run.astimezone(SHANGHAI).isoformat(),
            "day_offset": (run.astimezone(SHANGHAI).date() - local.date()).days,
        }
        for index, (hhmm, run) in enumerate(zip(times, runs, strict=True))
    ]
    if not ready:
        reason = "当前账号没有足够的安全发送时段，请减少好友数量或调整已有任务时间。"
    elif mode == "auto":
        reason = f"根据全站启用任务的时段负载选择较空闲时间，同一抖音账号至少间隔 {gap} 分钟。"
    elif times[0] != preferred:
        reason = f"所选时间与此账号已有任务过近，已顺延至安全时段；后续好友自适应错峰，至少间隔 {gap} 分钟。"
    else:
        reason = f"保留第一位好友的所选时间，后续好友根据全站负载自适应错峰，至少间隔 {gap} 分钟。"
    return {
        "mode": mode, "status": "ready" if ready else "unavailable",
        "reason": reason, "times": times, "entries": entries,
        "suggested": [item for item in suggestions if not times or item["start"] != times[0]][:3],
        "load": window_load(times, counts),
        "nearby_load": sum(nearby[to_minutes(time)] for time in times),
        "gap_minutes": gap, "minimum_lead_minutes": MINIMUM_LEAD_MINUTES,
        "timezone": "Asia/Shanghai",
    }


def next_runs(times: list[str], now_utc: datetime | None = None) -> list[datetime]:
    from spark_console.scheduler import compute_next_run

    if not times:
        return []
    now_utc = now_utc or datetime.now(timezone.utc)
    first_local = compute_next_run(times[0], now_utc).astimezone(SHANGHAI)
    runs = []
    local = first_local
    for hhmm in times:
        hour, minute = map(int, hhmm.split(":"))
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate < local:
            candidate += timedelta(days=1)
        runs.append(candidate.astimezone(timezone.utc))
        local = candidate
    return runs
