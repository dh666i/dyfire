"""Announcements, recorded receipts and operator-entered cost inputs."""
from datetime import datetime,timezone,timedelta
from decimal import Decimal,InvalidOperation
import json,math,uuid
from sqlalchemy import select,func,or_
from spark_console.models import (Announcement,AnnouncementRead,PaymentRecord,OperationSettings,
    User,SubscriptionGrant,TaskRun,SparkTask,DouyinAccount,UserSubscription,utc_now)
from spark_console.services import ValidationError,NotFound,Conflict
from spark_console.services.audits import AuditService


def text(value,limit,*,required=False):
    if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 and c not in '\n\t' for c in value):raise ValidationError('内容格式或长度不正确')
    if required and not value.strip():raise ValidationError('请填写完整内容')
    return value.strip()


def money(value):
    try:
        number=Decimal(str(value))
        if not number.is_finite() or number<0 or number>10000000 or number.as_tuple().exponent<-2:raise ValueError()
        return int(number*100)
    except (ValueError,InvalidOperation,TypeError):raise ValidationError('金额须为非负数，最多两位小数') from None


def publish(db,actor,values):
    title=text(values.get('title',''),160,required=True);body=text(values.get('body',''),5000,required=True)
    target=values.get('user_id') or None
    if target:
        target_user=db.get(User,target)
        if target_user is None or target_user.role!='user':raise NotFound('用户不存在')
    try:days=int(values.get('days',7))
    except (ValueError,TypeError):raise ValidationError('有效期不正确') from None
    if not 1<=days<=365:raise ValidationError('有效期须为 1–365 天')
    row=Announcement(title=title,body=body,user_id=target,created_by=actor.id,expires_at=utc_now()+timedelta(days=days))
    db.add(row);db.flush();AuditService(db).write(actor.id,'announcement.published','announcement',row.id)
    return row


def announcements(db,user_id,*,admin=False):
    now=utc_now();query=select(Announcement)
    if not admin:query=query.where(Announcement.withdrawn_at.is_(None),Announcement.expires_at>now,or_(Announcement.user_id.is_(None),Announcement.user_id==user_id))
    rows=list(db.scalars(query.order_by(Announcement.created_at.desc(),Announcement.id).limit(100)))
    read=set(db.scalars(select(AnnouncementRead.announcement_id).where(AnnouncementRead.user_id==user_id,AnnouncementRead.announcement_id.in_([r.id for r in rows]))))
    def stamp(v):return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat() if v else None
    return [{'id':r.id,'title':r.title,'body':r.body,'user_id':r.user_id,'created_at':stamp(r.created_at),'expires_at':stamp(r.expires_at),'withdrawn':r.withdrawn_at is not None,'read':r.id in read} for r in rows]


def record_payment(db,actor,values):
    try:request=str(uuid.UUID(str(values.get('request_id'))))
    except (ValueError,TypeError):raise ValidationError('请求编号不正确，请重新打开表单') from None
    target=db.get(User,str(values.get('user_id','')))
    if target is None:raise NotFound('用户不存在')
    if target.role=='admin' and actor.role!='admin':raise NotFound('用户不存在')
    source=values.get('source','paid')
    if source not in ('paid','gift','admin_adjustment'):raise ValidationError('开通来源不正确')
    amount=money(values.get('amount') or '0')
    if source=='paid' and amount<=0:raise ValidationError('实收金额须大于 0')
    if source!='paid' and amount:raise ValidationError('非收款记录金额应为 0')
    grant_id=values.get('grant_id') or None
    if grant_id:
        grant=db.get(SubscriptionGrant,grant_id)
        if grant is None or grant.user_id!=target.id:raise ValidationError('开通记录不属于该用户')
    reference=text(values.get('reference',''),160);note=text(values.get('note',''),500)
    payload=json.dumps({'user_id':target.id,'source':source,'amount_cents':amount,'reference':reference,'note':note,'grant_id':grant_id},sort_keys=True,separators=(',',':'))
    digest=__import__('hashlib').sha256(payload.encode()).hexdigest()
    old=db.scalar(select(PaymentRecord).where(PaymentRecord.request_id==request))
    if old:
        if old.request_digest!=digest:raise Conflict('request_id_reused_with_different_payment')
        return old
    from sqlalchemy.exc import IntegrityError
    row=PaymentRecord(request_id=request,request_digest=digest,user_id=target.id,actor_id=actor.id,source=source,amount_cents=amount,
        reference=reference,note=note,grant_id=grant_id)
    try:
        with db.begin_nested():db.add(row);db.flush()
    except IntegrityError:
        old=db.scalar(select(PaymentRecord).where(PaymentRecord.request_id==request))
        if old and old.request_digest==digest:return old
        raise Conflict('request_id_reused_with_different_payment') from None
    AuditService(db).write(actor.id,'payment.recorded','payment_record',row.id,f'source={source};cents={amount}')
    return row


def config(db):
    row=db.get(OperationSettings,1)
    return json.loads(row.values_json) if row else {}


def save_config(db,actor,values):
    allowed={'monthly_server_yuan','monthly_other_yuan','support_hourly_yuan','support_minutes_month'}
    result=config(db)
    for key in allowed & values.keys():
        raw=values[key]
        if raw in ('',None):result.pop(key,None);continue
        try:parsed=float(raw)
        except (TypeError,ValueError):raise ValidationError('配置须为数值') from None
        if not math.isfinite(parsed) or not 0<=parsed<=10000000:raise ValidationError('配置超出范围')
        result[key]=parsed
    db.merge(OperationSettings(id=1,values_json=json.dumps(result),updated_at=utc_now()))
    AuditService(db).write(actor.id,'operations.settings_updated','operation_settings','1')
    return result


def business_report(db):
    from zoneinfo import ZoneInfo
    now=utc_now();start=now.astimezone(ZoneInfo('Asia/Shanghai')).replace(day=1,hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc)
    totals=dict(db.execute(select(PaymentRecord.source,func.sum(PaymentRecord.amount_cents)).where(PaymentRecord.created_at>=start).group_by(PaymentRecord.source)).all())
    active=db.scalar(select(func.count()).select_from(DouyinAccount).join(UserSubscription,UserSubscription.user_id==DouyinAccount.owner_user_id).where(UserSubscription.valid_from<=now,UserSubscription.valid_until>now)) or 0
    inputs=config(db);missing=[key for key in ('monthly_server_yuan','monthly_other_yuan','support_hourly_yuan','support_minutes_month') if key not in inputs]
    known=not missing
    cost=inputs.get('monthly_server_yuan',0)+inputs.get('monthly_other_yuan',0)+inputs.get('support_hourly_yuan',0)*inputs.get('support_minutes_month',0)/60 if known else None
    elapsed=(func.julianday(TaskRun.finished_at)-func.julianday(TaskRun.started_at))*86400 if db.get_bind().dialect.name=='sqlite' else func.extract('epoch',TaskRun.finished_at-TaskRun.started_at)
    rows=db.execute(select(SparkTask.douyin_account_id,func.sum(elapsed)).join(TaskRun,TaskRun.task_id==SparkTask.id)
        .where(TaskRun.started_at>=start,TaskRun.finished_at.is_not(None),elapsed>=0)
        .group_by(SparkTask.douyin_account_id).order_by(func.sum(elapsed).desc()).limit(100)).all()
    seconds={key:value for key,value in rows if key is not None}
    return {'period_start':start.isoformat(),'recorded_income_yuan':round(totals.get('paid',0)/100,2),'paid_active_accounts':active,
            'cost_inputs_complete':known,'missing_cost_inputs':missing,'estimated_monthly_cost_yuan':round(cost,2) if known else None,
            'estimated_cost_per_paid_account_yuan':round(cost/active,2) if known and active else None,
            'account_execution_seconds':[{ 'account_id':key,'seconds':round(value,1)} for key,value in sorted(seconds.items(),key=lambda item:-item[1])[:100]],
            'cost_inputs':inputs,'income_from_recorded_payments_only':True,'duration_means_wall_time':True}
