"""User-scoped inbox derived from existing facts, without external delivery."""
import hashlib
from urllib.parse import quote
from datetime import datetime,timedelta,timezone
from sqlalchemy import select
from spark_console.models import AuditEvent,DouyinAccount,DouyinContactIdentity,DouyinStreakMilestone,SparkTask,TaskRun,UserSubscription

INBOX_ACCOUNT_LIMIT = 100
INBOX_READ_EVENT_LIMIT = 1000

def entries(db,user_id):
    since=datetime.now(timezone.utc)-timedelta(days=7)
    result=[]
    def add(key,title,detail,url,at,tone='warning'):
        result.append(dict(id=hashlib.sha256(key.encode()).hexdigest()[:36],title=title,detail=detail,url=url,at=at,tone=tone))
    accounts=db.scalars(select(DouyinAccount).where(
        DouyinAccount.owner_user_id==user_id,
        DouyinAccount.validation_state.in_(('temporary','verification','invalid')),
    ).order_by(DouyinAccount.updated_at.desc(), DouyinAccount.id)
        .limit(INBOX_ACCOUNT_LIMIT)).all()
    for a in accounts:
        if a.validation_state in ('temporary','verification','invalid'):
            add('account:'+a.id+':'+a.validation_state+':'+str(a.last_verified_at),a.display_name+' · '+{'temporary':'暂时无法确认','verification':'需要本人验证','invalid':'需要重新登录'}[a.validation_state],'相关任务等待账号恢复；不会自动尝试密码或验证码。','/accounts#account-'+a.id,a.updated_at)
    events=db.scalars(select(AuditEvent).where(AuditEvent.actor_user_id==user_id,AuditEvent.action.like('account.health.%'),AuditEvent.created_at>=since).order_by(AuditEvent.created_at.desc()).limit(500)).all()
    previous={}; recovered={}
    for event in reversed(events):
        state=event.action.rsplit('.',1)[-1]
        if state=='valid' and previous.get(event.resource_id) in ('temporary','verification','invalid'):recovered[event.resource_id]=event
        previous[event.resource_id]=state
    recovered_ids = list(recovered)[:INBOX_ACCOUNT_LIMIT]
    if recovered_ids:
        recovered_accounts = db.scalars(select(DouyinAccount).where(
            DouyinAccount.owner_user_id==user_id,
            DouyinAccount.validation_state=='valid',
            DouyinAccount.id.in_(recovered_ids),
        )).all()
        for a in recovered_accounts:
            e=recovered[a.id];add('recovered:'+str(e.id),a.display_name+' · 已恢复','账号已恢复正常，后续计划继续执行，不补发历史消息。','/accounts',e.created_at,'success')
    rows=db.execute(select(TaskRun,SparkTask).join(SparkTask).where(SparkTask.owner_user_id==user_id,TaskRun.scheduled_for>=since,TaskRun.status.in_(['failed','submitted_unconfirmed'])).order_by(TaskRun.scheduled_for.desc()).limit(500)).all()
    groups={}
    for run,task in rows:
        key=(task.id,run.status)
        if key not in groups:groups[key]=[run,task,0]
        groups[key][2]+=1
    for run,task,count in groups.values():
        pending=run.status=='submitted_unconfirmed'
        add('run:'+run.id,task.target_name+(' · 发送待确认' if pending else ' · 执行异常'),f'最近七天同类记录 {count} 条。'+('请先核对抖音聊天记录，不要直接重发。' if pending else '请查看执行详情与账号状态。'),'/runs?q='+quote(task.target_name),run.scheduled_for)
    milestone_rows=db.execute(select(DouyinStreakMilestone,DouyinAccount,DouyinContactIdentity).join(
        DouyinAccount,DouyinAccount.id==DouyinStreakMilestone.account_id
    ).outerjoin(DouyinContactIdentity,(DouyinContactIdentity.account_id==DouyinStreakMilestone.account_id)&
        (DouyinContactIdentity.sec_uid==DouyinStreakMilestone.sec_uid)
    ).where(DouyinAccount.owner_user_id==user_id,DouyinStreakMilestone.observed_at>=since
    ).order_by(DouyinStreakMilestone.observed_at.desc(),DouyinStreakMilestone.days.desc()).limit(200)).all()
    by_relationship={}
    for milestone,account,contact in milestone_rows:
        key=(account.id,milestone.sec_uid)
        by_relationship.setdefault(key,[]).append((milestone,account,contact))
    for values in by_relationship.values():
        crossed=[item for item in values if item[0].kind=='crossed']
        selected=crossed or [max(values,key=lambda item:item[0].days)]
        for milestone,account,contact in selected:
            name=(contact.remark_name or contact.nickname) if contact else None
            name=name or '好友'
            observed=milestone.kind=='first_observed'
            title=f'{name} · {milestone.days} 天火花里程碑'
            detail=(f'抖音平台首次读取到 {milestone.days} 天；实际达到日期未知。' if observed
                    else f'抖音平台观测到火花天数跨过 {milestone.days} 天。')
            url='/studio?'+ 'account='+quote(account.id,safe='')+'&spark='+quote(milestone.sec_uid,safe='')
            add(f'spark-milestone:{account.id}:{milestone.sec_uid}:{milestone.days}',title,detail,url,milestone.observed_at,'success')
    sub=db.get(UserSubscription,user_id)
    if sub and sub.valid_until and sub.valid_until.replace(tzinfo=timezone.utc)<datetime.now(timezone.utc)+timedelta(days=3):
        add('expiry:'+sub.valid_until.isoformat(),'会员到期提醒','请查看有效期与额度；到期后保留 1 个好友任务，其余任务暂不执行。','/subscription',sub.valid_until)
    notification_ids = [item['id'] for item in result]
    read = set()
    if notification_ids:
        read=set(db.scalars(select(AuditEvent.resource_id).where(
            AuditEvent.actor_user_id==user_id,
            AuditEvent.action=='inbox.read',
            AuditEvent.created_at>=since,
            AuditEvent.resource_id.in_(notification_ids),
        ).order_by(AuditEvent.created_at.desc()).limit(INBOX_READ_EVENT_LIMIT)).all())
    for item in result:item['read']=item['id'] in read
    return sorted(result,key=lambda e:(e['read'],-e['at'].replace(tzinfo=timezone.utc).timestamp()))
