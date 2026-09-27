"""Small persistence helper for the user-facing execution timeline."""

from sqlalchemy import func, select
from sqlalchemy.orm import object_session

from spark_console.models import TaskRunEvent, utc_now


def record(run, event: str, stage: str | None = None, detail: str | None = None, now=None):
    db = object_session(run)
    if db is None or not run.id:
        return None
    next_sequence = (db.scalar(
        select(func.coalesce(func.max(TaskRunEvent.sequence), 0) + 1).where(
            TaskRunEvent.run_id == run.id
        )
    ) or 1)
    item = TaskRunEvent(
        run_id=run.id,
        sequence=int(next_sequence),
        occurred_at=now or utc_now(),
        event=str(event)[:32],
        stage=str(stage or getattr(run, "stage", "queued"))[:24],
        detail=(str(detail)[:240] if detail else None),
    )
    db.add(item)
    db.flush()
    return item


def for_runs(db, ids):
    result = {}
    if ids:
        for row in db.scalars(
            select(TaskRunEvent)
            .where(TaskRunEvent.run_id.in_(ids))
            .order_by(TaskRunEvent.run_id, TaskRunEvent.sequence)
        ):
            result.setdefault(row.run_id, []).append(row)
    return result
