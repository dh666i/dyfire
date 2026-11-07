"""Merge repeated account health observations without storing page content."""
from sqlalchemy import select, func, or_
from spark_console.models import AccountIncident, SparkTask, TaskRun, DouyinLoginSession, MailDelivery, utc_now
from spark_console.mail_outbox import public_status

BLOCKED={'temporary','verification','invalid'}


def observe(db, account_id, state, *, now=None):
    now=now or utc_now()
    current=db.scalar(select(AccountIncident).where(AccountIncident.account_id==account_id,AccountIncident.resolved_at.is_(None)).with_for_update())
    if state in BLOCKED:
        if current is None:
            current=AccountIncident(account_id=account_id,state=state,started_at=now,observed_at=now,observations=1)
            db.add(current)
        else:
            current.state=state;current.observed_at=now;current.observations+=1
    elif state=='valid' and current is not None:
        current.resolved_at=now;current.observed_at=now
    return current


def views(db, incidents, *, now=None):
    rows=list(incidents)
    if not rows:return []
    ids=[row.id for row in rows]
    # The first failing run can have been scheduled before the first signal.
    # Include its finish time, while grouping by distinct task, not retries.
    impact=dict(db.execute(select(AccountIncident.id,func.count(func.distinct(TaskRun.task_id)))
        .join(SparkTask,SparkTask.douyin_account_id==AccountIncident.account_id)
        .join(TaskRun,TaskRun.task_id==SparkTask.id)
        .where(AccountIncident.id.in_(ids),TaskRun.status.in_(('failed','skipped','retrying','submitted_unconfirmed')),
               or_(TaskRun.finished_at>=AccountIncident.started_at,TaskRun.scheduled_for>=AccountIncident.started_at),
               or_(AccountIncident.resolved_at.is_(None),TaskRun.scheduled_for<=AccountIncident.resolved_at))
        .group_by(AccountIncident.id)).all())
    actions=dict(db.execute(select(AccountIncident.id,func.count(DouyinLoginSession.id))
        .join(DouyinLoginSession,DouyinLoginSession.account_id==AccountIncident.account_id)
        .where(AccountIncident.id.in_(ids),DouyinLoginSession.created_at>=AccountIncident.started_at,
               or_(AccountIncident.resolved_at.is_(None),DouyinLoginSession.created_at<=AccountIncident.resolved_at))
        .group_by(AccountIncident.id)).all())
    def utc(value):return value.replace(tzinfo=utc_now().tzinfo) if value.tzinfo is None else value
    def stamp(value):return utc(value).isoformat() if value else None
    now=now or utc_now()
    notice_ids=[row.alert_delivery_id for row in rows if row.alert_delivery_id]
    notices=dict(db.execute(select(MailDelivery.id,MailDelivery.status).where(MailDelivery.id.in_(notice_ids))).all()) if notice_ids else {}
    return [{'id':row.id,'account_id':row.account_id,'state':row.state,'open':row.resolved_at is None,
             'started_at':stamp(row.started_at),'last_seen_at':stamp(row.observed_at),'resolved_at':stamp(row.resolved_at),
             'duration_seconds':max(0,int((utc(row.resolved_at or now)-utc(row.started_at)).total_seconds())),
             'observations':row.observations,'affected_tasks':impact.get(row.id,0),'recovery_actions':actions.get(row.id,0),
             'email_notice':public_status(notices.get(row.alert_delivery_id)) if row.alert_delivery_id in notices else 'expired_record' if row.alert_queued_at else 'not_queued'} for row in rows]


def current_map(db, account_ids):
    rows=db.scalars(select(AccountIncident).where(AccountIncident.account_id.in_(list(account_ids)),AccountIncident.resolved_at.is_(None))).all()
    return {row['account_id']:row for row in views(db,rows)}
