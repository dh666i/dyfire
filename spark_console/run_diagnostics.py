"""A minimal support bundle: no cookies, names, chat content or page dumps."""
from spark_console.execution_diagnostics import decode
from sqlalchemy import select
from spark_console.models import TaskRunEvent
from spark_console.version import VERSION
from datetime import timezone


def support_id(run):
    return 'DF-'+run.id


def bundle(db, run):
    def stamp(value):return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat() if value else None
    def duration(start,end):return max(0,round((end-start).total_seconds(),3)) if start and end else None
    events=db.scalars(select(TaskRunEvent).where(TaskRunEvent.run_id==run.id).order_by(TaskRunEvent.sequence).limit(100)).all()
    return {'schema':1,'support_id':support_id(run),'version':run.execution_version,'report_version':VERSION,'run_id':run.id,
            'status':run.status,'stage':run.stage,'reason_code':run.error_code,
            'attempt':run.attempt,'submitted':run.submitted_at is not None,
            'scheduled_for':stamp(run.scheduled_for),'finished_at':stamp(run.finished_at),
            'timings_seconds':{'queue':duration(run.scheduled_for,run.started_at),
                'before_submission':duration(run.started_at,run.submitted_at),
                'confirmation':duration(run.submitted_at,run.finished_at),
                'total':duration(run.started_at,run.finished_at)},
            'structure': [decode(e.detail) for e in events if e.event=='diagnostic'],
            'events':[{'stage':e.stage,'event':e.event,'at':stamp(e.occurred_at)} for e in events]}
