from sqlalchemy import select
from sqlalchemy.orm import object_session
from spark_console.models import TaskRunAttempt


def save(run, status, stage, now, reason=None):
    db = object_session(run)
    if db is None or not run.id or not run.started_at or run.attempt < 1:
        return
    row = db.get(TaskRunAttempt, (run.id, run.attempt))
    if row is not None:
        return  # A policy cancellation must not overwrite the actual attempt.
    db.add(TaskRunAttempt(run_id=run.id, number=run.attempt,
        started_at=run.started_at, finished_at=now, submitted_at=run.submitted_at,
        status=status, stage=stage, reason=(reason or "")[:240]))
    db.flush()


def for_runs(db, ids):
    result = {}
    if ids:
        for row in db.scalars(select(TaskRunAttempt).where(
            TaskRunAttempt.run_id.in_(ids)).order_by(TaskRunAttempt.number)):
            result.setdefault(row.run_id, []).append(row)
    return result
