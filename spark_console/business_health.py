from datetime import datetime, timedelta, timezone
from math import ceil
from sqlalchemy import select, func, or_
from spark_console.models import DouyinAccount, DouyinLoginSession, TaskRun, MailDelivery, SparkTask, AccountIncident, ContactDirectoryJob


def schedule_capacity(slots, p95, *, shards=1, gap_seconds=8):
    """Conservative workload estimate, never a promise of platform throughput."""
    buckets=[0]*288
    for send_time,count in slots:
        hour,minute=map(int,send_time.split(':'))
        buckets[(hour*60+minute)//5]+=count
    peaks=[]
    for window in (1,3):
        index=max(range(288),key=lambda i:sum(buckets[(i+j)%288] for j in range(window)))
        count=sum(buckets[(index+j)%288] for j in range(window))
        peaks.append({'window_minutes':window*5,'start':f'{index//12:02d}:{index%12*5:02d}',
                      'tasks':count,'estimated_drain_seconds':round(count*(p95+gap_seconds),1) if p95 is not None else None})
    return {'peaks':peaks,'duration_p95_seconds':p95,'worker_shards':max(1,shards),'estimate_only':True,
            'sample_required':p95 is None,'assumption':'single_slot_conservative'}


def business_health(db):
    now=datetime.now(timezone.utc);since=now-timedelta(hours=24)
    counts=dict(db.execute(select(TaskRun.status,func.count()).where(TaskRun.scheduled_for>=since,TaskRun.scheduled_for<=now).group_by(TaskRun.status)).all())
    terminal=sum(counts.get(k,0) for k in ('success','failed','skipped','submitted_unconfirmed'))
    oldest=db.scalar(select(func.min(DouyinLoginSession.created_at)).where(DouyinLoginSession.status=='queued',DouyinLoginSession.expires_at>now))
    if oldest and oldest.tzinfo is None: oldest=oldest.replace(tzinfo=timezone.utc)
    failures=db.execute(select(TaskRun.error_code,func.count()).where(TaskRun.scheduled_for>=since,TaskRun.status=='failed').group_by(TaskRun.error_code).order_by(func.count().desc()).limit(5)).all()
    samples=db.execute(select(TaskRun.started_at,TaskRun.finished_at).where(TaskRun.started_at>=since,TaskRun.finished_at.is_not(None)).order_by(TaskRun.started_at.desc()).limit(500)).all()
    durations=sorted(max(0,(finished-started).total_seconds()) for started,finished in samples)
    p95=round(durations[ceil(len(durations)*.95)-1],1) if durations else None
    slots=db.execute(select(SparkTask.send_time,func.count()).where(SparkTask.enabled.is_(True)).group_by(SparkTask.send_time)).all()
    latencies=sorted(max(0,(started-scheduled).total_seconds()) for scheduled,started in db.execute(select(TaskRun.scheduled_for,TaskRun.started_at).where(TaskRun.scheduled_for>=since,TaskRun.started_at.is_not(None)).limit(500)))
    peak=db.execute(select(SparkTask.send_time,func.count()).where(SparkTask.enabled.is_(True)).group_by(SparkTask.send_time).order_by(func.count().desc()).limit(1)).first()
    overdue=db.scalar(select(func.count()).select_from(SparkTask).where(SparkTask.enabled.is_(True),SparkTask.next_run_at<now-timedelta(minutes=5)))
    incidents=db.execute(select(AccountIncident.started_at,AccountIncident.resolved_at).where(or_(AccountIncident.resolved_at>=since,AccountIncident.resolved_at.is_(None))).order_by(AccountIncident.observed_at.desc()).limit(500)).all()
    def aware(value):return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    recovered=sorted(max(0,(aware(end)-aware(start)).total_seconds()) for start,end in incidents if end)
    return {
        'queued_logins':db.scalar(select(func.count()).select_from(DouyinLoginSession).where(DouyinLoginSession.status=='queued',DouyinLoginSession.expires_at>now)),
        'login_wait_seconds':int((now-oldest).total_seconds()) if oldest else 0,
        'attention_accounts':db.scalar(select(func.count()).select_from(DouyinAccount).where(DouyinAccount.validation_state.in_(['temporary','verification','invalid']))),
        'unconfirmed_percent':round(counts.get('submitted_unconfirmed',0)*100/terminal,1) if terminal else None,
        'mail_attention':db.scalar(select(func.count()).select_from(MailDelivery).where(MailDelivery.status.in_(['failed','uncertain']),MailDelivery.created_at>=since)),
        'failures':[list(row) for row in failures],
        'duration_p95':p95,'duration_samples':len(durations),'peak_time':peak[0] if peak else '—','peak_tasks':peak[1] if peak else 0,'overdue_tasks':overdue,
        'recorded_plan_success_percent':round(counts.get('success',0)*100/terminal,1) if terminal else None,
        'recorded_terminal_runs':terminal,'skipped_runs':counts.get('skipped',0),
        'queue_delay_p95_seconds':round(latencies[ceil(len(latencies)*.95)-1],1) if latencies else None,
        'active_incidents':db.scalar(select(func.count()).select_from(AccountIncident).where(AccountIncident.resolved_at.is_(None))) or 0,
        'blocked_account_seconds':round(sum(max(0,((aware(end) if end else now)-max(since,aware(start))).total_seconds()) for start,end in incidents),1),
        'incident_duration_sample_limit':500,
        'recovery_p95_seconds':round(recovered[ceil(len(recovered)*.95)-1],1) if recovered else None,
        'queued_directory_jobs':db.scalar(select(func.count()).select_from(ContactDirectoryJob).where(ContactDirectoryJob.status=='queued')) or 0,
        'capacity':schedule_capacity(slots,p95),
    }
