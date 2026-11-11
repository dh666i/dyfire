"""Persist platform observations separately from task delivery evidence.

Absence is not a zero count. Unknown or older reads never overwrite a known
snapshot, and the calendar date of a milestone is never back-calculated.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import islice

from sqlalchemy import and_, case, delete, func, select, tuple_, union

from spark_console.models import (
    DouyinAccount, DouyinContactAvatar, DouyinContactIdentity,
    DouyinStreakMilestone, DouyinStreakObservation, DouyinStreakSnapshot,
    SparkTask, SparkTaskTargetIdentity, utc_now,
)
from spark_console.services import NotFound, ValidationError

STATES = frozenset({"unknown", "active", "broken", "recovering"})
SOURCES = frozenset({"douyin_dom", "douyin_im"})
MILESTONES = (100, 365, 520, 1000)
STALE_AFTER = timedelta(hours=24)
MAX_HISTORY_ROWS = 1000
OBSERVATION_FIELDS = ("days", "state", "recover_current", "recover_total")
FILTERS = frozenset({"all", "active", "has_streak", "needs_attention", "without_task"})


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _iso(value):
    return _aware(value).isoformat() if value is not None else None


def _integer(value, *, maximum=100000):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum else None


def unknown_snapshot():
    return {
        "days": None, "state": "unknown", "recover_current": None,
        "recover_total": None, "platform_text": "", "source": None,
        "observed_at": None, "updated_at": None, "stale": True,
        "expires_at": None, "milestones": [],
    }


def snapshot_view(row, *, now=None, milestones=()):
    if row is None:
        return unknown_snapshot()
    return {
        **{name: getattr(row, name) for name in OBSERVATION_FIELDS},
        "platform_text": row.platform_text, "source": row.source,
        "observed_at": _iso(row.observed_at), "updated_at": _iso(row.updated_at),
        "stale": _aware(row.observed_at) < _aware(now or utc_now()) - STALE_AFTER,
        # No platform expiry semantics have been verified. Never invent a timer.
        "expires_at": None,
        "milestones": [milestone_view(item) for item in milestones],
    }


def milestone_view(row):
    return {"days": row.days, "kind": row.kind, "observed_at": _iso(row.observed_at)}


def record_observations(db, account_id, observations, *, observed_at=None):
    """Apply at most 1,000 verified identity observations within caller's transaction.

    The caller must obtain the account browser-operation lease before collecting.
    This row lock also serializes writers on PostgreSQL. A missing/unknown
    observation cannot refresh an old known snapshot's freshness timestamp.
    """
    account = db.scalar(select(DouyinAccount).where(DouyinAccount.id == account_id).with_for_update())
    if account is None:
        raise NotFound("账号不存在")
    now = utc_now()
    fallback_time = _aware(observed_at or now)
    accepted = 0
    for item in islice(observations or (), 1000):
        if not isinstance(item, dict):
            continue
        uid = item.get("sec_uid")
        source = item.get("source")
        if not isinstance(uid, str) or not uid.strip() or len(uid) > 256 or not isinstance(source, str) or source not in SOURCES:
            continue
        uid = uid.strip()
        stamp = item.get("observed_at") or fallback_time
        if isinstance(stamp, str):
            try:
                stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue
        if not isinstance(stamp, datetime):
            continue
        stamp = _aware(stamp)
        if stamp > now + timedelta(minutes=5):
            continue
        state = item.get("state", "unknown")
        if not isinstance(state, str) or state not in STATES:
            continue
        days = _integer(item.get("days"))
        if item.get("days") is not None and days is None:
            continue
        if state == "unknown" and days is None:
            continue
        row = db.get(DouyinStreakSnapshot, (account_id, uid))
        if row is not None and stamp <= _aware(row.observed_at):
            continue
        if row is not None and row.state != "unknown" and state == "unknown":
            continue
        before = tuple(getattr(row, name) for name in OBSERVATION_FIELDS) if row else None
        old_days = row.days if row else None
        if row is None:
            row = DouyinStreakSnapshot(account_id=account_id, sec_uid=uid, state="unknown")
            db.add(row)
        if days is not None:
            row.days = days
        if state != "unknown":
            row.state = state
        current, total = _integer(item.get("recover_current")), _integer(item.get("recover_total"))
        if row.state == "recovering" and current is not None and total and current <= total:
            row.recover_current, row.recover_total = current, total
        elif state in {"active", "broken"}:
            row.recover_current = row.recover_total = None
        row.platform_text = str(item.get("platform_text") or "")[:512]
        row.source, row.observed_at, row.updated_at = source, stamp, now
        row.expires_at = None
        db.flush()
        after = tuple(getattr(row, name) for name in OBSERVATION_FIELDS)
        if before != after:
            db.add(DouyinStreakObservation(
                account_id=account_id, sec_uid=uid, kind="initial" if before is None else "change",
                **{name: getattr(row, name) for name in OBSERVATION_FIELDS},
                platform_text=row.platform_text, source=source, observed_at=stamp, recorded_at=now,
            ))
            db.flush()
            overflow = select(DouyinStreakObservation.id).where(
                DouyinStreakObservation.account_id == account_id,
                DouyinStreakObservation.sec_uid == uid,
            ).order_by(DouyinStreakObservation.observed_at.desc(), DouyinStreakObservation.id.desc()).offset(MAX_HISTORY_ROWS)
            db.execute(delete(DouyinStreakObservation).where(DouyinStreakObservation.id.in_(overflow)))
        # These timestamps mean "observed", never the historical attainment day.
        for threshold in MILESTONES:
            if days is None or days < threshold or (old_days is not None and old_days >= threshold):
                continue
            if db.get(DouyinStreakMilestone, (account_id, uid, threshold)) is None:
                db.add(DouyinStreakMilestone(
                    account_id=account_id, sec_uid=uid, days=threshold,
                    kind="first_observed" if old_days is None else "crossed", observed_at=stamp,
                ))
        accepted += 1
    db.flush()
    return accepted


def _snapshot_map(db, pairs, *, now=None):
    pairs = list(dict.fromkeys(pairs))
    result = {}
    # Bound each SQL IN expression even when an internal caller loads many tasks.
    for start in range(0, len(pairs), 200):
        batch = pairs[start:start + 200]
        milestones = defaultdict(list)
        for row in db.scalars(select(DouyinStreakMilestone).where(
            tuple_(DouyinStreakMilestone.account_id, DouyinStreakMilestone.sec_uid).in_(batch)
        ).order_by(DouyinStreakMilestone.days)):
            milestones[(row.account_id, row.sec_uid)].append(row)
        for row in db.scalars(select(DouyinStreakSnapshot).where(
            tuple_(DouyinStreakSnapshot.account_id, DouyinStreakSnapshot.sec_uid).in_(batch)
        )):
            key = row.account_id, row.sec_uid
            result[key] = snapshot_view(row, now=now, milestones=milestones[key])
    return result


def task_snapshot_map(db, tasks, identities=None):
    """Return task-id keyed views; legacy display-name matching is forbidden."""
    tasks = list(tasks)
    result = {task.id: unknown_snapshot() for task in tasks}
    ids = [task.id for task in tasks]
    if identities is None:
        identities = {}
        for start in range(0, len(ids), 400):
            identities.update({row.task_id: row.sec_uid for row in db.scalars(
                select(SparkTaskTargetIdentity).where(SparkTaskTargetIdentity.task_id.in_(ids[start:start + 400]))
            )})
    else:
        identities = {
            task_id: value.sec_uid if isinstance(value, SparkTaskTargetIdentity) else value
            for task_id, value in identities.items()
        }
    pairs = {(task.douyin_account_id, identities[task.id]) for task in tasks
             if task.douyin_account_id and task.id in identities}
    snapshots = _snapshot_map(db, pairs)
    for task in tasks:
        result[task.id] = snapshots.get((task.douyin_account_id, identities.get(task.id)), unknown_snapshot())
    return result


def contact_snapshot_map(db, account_id, sec_uids=None):
    """Account-local stable-identity map for existing bounded contact responses."""
    if sec_uids is None:
        sec_uids = db.scalars(select(DouyinStreakSnapshot.sec_uid).where(
            DouyinStreakSnapshot.account_id == account_id
        ).order_by(DouyinStreakSnapshot.sec_uid).limit(1000)).all()
    pairs = [(account_id, uid) for uid in list(sec_uids)[:1000] if uid]
    snapshots = _snapshot_map(db, pairs)
    return {uid: snapshots.get((account_id, uid), unknown_snapshot()) for _, uid in pairs}


def _owned_account(db, actor, account_id):
    account = db.get(DouyinAccount, account_id)
    if account is None or (actor.role != "admin" and account.owner_user_id != actor.id):
        raise NotFound("账号不存在")
    return account


def _pagination(offset, limit):
    if not isinstance(offset, int) or offset < 0 or offset > 100000 or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValidationError("分页范围不正确")


def list_streaks(db, actor, *, account_id=None, task_id=None, filter="all", order="days_desc", offset=0, limit=50):
    _pagination(offset, limit)
    if filter not in FILTERS or order not in {"days_desc", "name"}:
        raise ValidationError("筛选条件不正确")
    if account_id:
        _owned_account(db, actor, account_id)
    target_uid = None
    if task_id:
        task = db.get(SparkTask, task_id)
        if task is None or (actor.role != "admin" and task.owner_user_id != actor.id):
            raise NotFound("任务不存在")
        if account_id and account_id != task.douyin_account_id:
            raise NotFound("任务不存在")
        account_id = task.douyin_account_id
        identity = db.get(SparkTaskTargetIdentity, task.id)
        target_uid = identity.sec_uid if identity else ""
        if not account_id or not target_uid:
            return {"items": [], "total": 0, "offset": offset, "limit": limit,
                    "has_more": False, "next_offset": offset,
                    "metadata": {"scope": "synced_conversations", "may_have_more": True, "identity_unresolved": True}}
        _owned_account(db, actor, account_id)

    task_summary = select(
        SparkTask.douyin_account_id.label("account_id"), SparkTaskTargetIdentity.sec_uid.label("sec_uid"),
        func.count().label("task_count"), func.sum(case((SparkTask.enabled, 1), else_=0)).label("enabled_task_count"),
        func.min(SparkTask.target_name).label("target_name"),
    ).join(SparkTaskTargetIdentity, SparkTaskTargetIdentity.task_id == SparkTask.id).group_by(
        SparkTask.douyin_account_id, SparkTaskTargetIdentity.sec_uid,
    ).subquery()
    keys = union(
        select(DouyinContactIdentity.account_id, DouyinContactIdentity.sec_uid),
        select(DouyinStreakSnapshot.account_id, DouyinStreakSnapshot.sec_uid),
        select(task_summary.c.account_id, task_summary.c.sec_uid),
    ).subquery()
    contact = DouyinContactIdentity
    snap = DouyinStreakSnapshot
    name = func.coalesce(func.nullif(contact.remark_name, ""), func.nullif(contact.nickname, ""), task_summary.c.target_name, keys.c.sec_uid)
    query = select(keys.c.account_id, keys.c.sec_uid, name.label("name"),
                   DouyinContactAvatar.avatar_url, func.coalesce(task_summary.c.task_count, 0).label("task_count"),
                   func.coalesce(task_summary.c.enabled_task_count, 0).label("enabled_task_count")).select_from(keys).join(
        DouyinAccount, DouyinAccount.id == keys.c.account_id
    ).outerjoin(contact, and_(contact.account_id == keys.c.account_id, contact.sec_uid == keys.c.sec_uid)).outerjoin(
        snap, and_(snap.account_id == keys.c.account_id, snap.sec_uid == keys.c.sec_uid)
    ).outerjoin(DouyinContactAvatar, and_(DouyinContactAvatar.account_id == keys.c.account_id, DouyinContactAvatar.sec_uid == keys.c.sec_uid)).outerjoin(
        task_summary, and_(task_summary.c.account_id == keys.c.account_id, task_summary.c.sec_uid == keys.c.sec_uid)
    )
    if actor.role != "admin":
        query = query.where(DouyinAccount.owner_user_id == actor.id)
    if account_id:
        query = query.where(keys.c.account_id == account_id)
    if target_uid:
        query = query.where(keys.c.sec_uid == target_uid)
    if filter == "active":
        query = query.where(snap.state == "active")
    elif filter == "has_streak":
        query = query.where(snap.days > 0)
    elif filter == "needs_attention":
        query = query.where(snap.state.in_(["broken", "recovering"]))
    elif filter == "without_task":
        query = query.where(task_summary.c.task_count.is_(None))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    query = query.order_by(*( [func.coalesce(snap.days, -1).desc(), name] if order == "days_desc" else [name]), keys.c.account_id, keys.c.sec_uid)
    rows = db.execute(query.offset(offset).limit(limit)).all()
    pairs = [(row.account_id, row.sec_uid) for row in rows]
    snapshots = _snapshot_map(db, pairs)
    tasks = defaultdict(list)
    if pairs:
        # A per-target rank keeps pathological account data from making the JSON unbounded.
        ranked = select(SparkTask.id, SparkTask.douyin_account_id.label("account_id"), SparkTaskTargetIdentity.sec_uid,
                        func.row_number().over(partition_by=(SparkTask.douyin_account_id, SparkTaskTargetIdentity.sec_uid), order_by=SparkTask.id).label("rn")).join(
            SparkTaskTargetIdentity, SparkTaskTargetIdentity.task_id == SparkTask.id
        ).where(tuple_(SparkTask.douyin_account_id, SparkTaskTargetIdentity.sec_uid).in_(pairs)).subquery()
        for row in db.execute(select(ranked).where(ranked.c.rn <= 100)):
            tasks[(row.account_id, row.sec_uid)].append(row.id)
    items = [{"account_id": row.account_id, "sec_uid": row.sec_uid, "name": row.name,
              "avatar": row.avatar_url, "task_ids": tasks[(row.account_id, row.sec_uid)],
              "task_count": row.task_count, "enabled_task_count": row.enabled_task_count,
              **snapshots.get((row.account_id, row.sec_uid), unknown_snapshot())} for row in rows]
    # Directory sync is capped, so even has_more=false from that job is not
    # evidence that this contains every mobile-app friend.
    unresolved = select(SparkTask.id).join(DouyinAccount, DouyinAccount.id == SparkTask.douyin_account_id).outerjoin(
        SparkTaskTargetIdentity, SparkTaskTargetIdentity.task_id == SparkTask.id
    ).where(SparkTaskTargetIdentity.task_id.is_(None))
    if actor.role != "admin":
        unresolved = unresolved.where(DouyinAccount.owner_user_id == actor.id)
    if account_id:
        unresolved = unresolved.where(DouyinAccount.id == account_id)
    identity_unresolved = db.scalar(unresolved.limit(1)) is not None
    return {"items": items, "total": total, "offset": offset, "limit": limit,
            "has_more": offset + len(items) < total, "next_offset": offset + len(items),
            "metadata": {"scope": "synced_conversations", "may_have_more": True, "identity_unresolved": identity_unresolved}}


def streak_history(db, actor, *, account_id, sec_uid, offset=0, limit=30):
    _pagination(offset, limit)
    _owned_account(db, actor, account_id)
    if not sec_uid or len(sec_uid) > 256:
        raise ValidationError("好友身份不正确")
    current = contact_snapshot_map(db, account_id, [sec_uid]).get(sec_uid, unknown_snapshot())
    query = select(DouyinStreakObservation).where(DouyinStreakObservation.account_id == account_id, DouyinStreakObservation.sec_uid == sec_uid)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(DouyinStreakObservation.observed_at.desc(), DouyinStreakObservation.id.desc()).offset(offset).limit(limit))
    items = [{"id": row.id, "kind": row.kind, **{name: getattr(row, name) for name in OBSERVATION_FIELDS},
              "platform_text": row.platform_text, "source": row.source, "observed_at": _iso(row.observed_at),
              "recorded_at": _iso(row.recorded_at)} for row in rows]
    return {"current": current, "milestones": current["milestones"], "items": items,
            "total": total, "offset": offset, "limit": limit, "has_more": offset + len(items) < total,
            "next_offset": offset + len(items)}
