"""Read-only subscription quotes share the actual downgrade decision."""
from datetime import timedelta
from sqlalchemy import select
from spark_console.models import UserSubscription,SubscriptionGrant,RedemptionCode,utc_now
from spark_console.services import ValidationError


def is_downgrade(active,old_accounts,old_tasks,new_accounts,new_tasks):
    return active and (new_accounts<old_accounts or new_tasks<old_tasks)


def preview(db,user_id,plaintext,now=None):
    from spark_console.services.subscriptions import _digest,_aware,NEVER_EXPIRES_AT
    now=now or utc_now()
    code=db.scalar(select(RedemptionCode).where(RedemptionCode.code_hash==_digest(plaintext.strip())))
    if code is None or code.redeemed_at or code.revoked_at or (code.expires_at and _aware(code.expires_at)<NEVER_EXPIRES_AT and _aware(code.expires_at)<=now):
        raise ValidationError('兑换码不可用，请检查输入或联系管理员')
    sub=db.get(UserSubscription,user_id)
    active=bool(sub and _aware(sub.valid_from)<=now<_aware(sub.valid_until))
    old_accounts,old_tasks=(sub.max_accounts,sub.max_tasks) if active else (1,1)
    if active and sub.pending_valid_from and _aware(sub.pending_valid_from)<=now:
        grant=db.scalar(select(SubscriptionGrant).where(SubscriptionGrant.user_id==user_id,
            SubscriptionGrant.valid_from>=_aware(sub.pending_valid_from),SubscriptionGrant.valid_from<=now,
            SubscriptionGrant.valid_until>now,SubscriptionGrant.superseded_at.is_(None))
            .order_by(SubscriptionGrant.valid_from.desc(),SubscriptionGrant.created_at.desc()).limit(1))
        if grant:old_accounts,old_tasks=grant.max_accounts,grant.max_tasks
    start=max(now,_aware(sub.valid_until)) if sub else now
    later=is_downgrade(active,old_accounts,old_tasks,code.max_accounts,code.max_tasks)
    try:until=start+timedelta(days=code.duration_days)
    except OverflowError:raise ValidationError('有效期超出可处理范围') from None
    return {'duration_days':code.duration_days,'max_accounts':code.max_accounts,'max_tasks':code.max_tasks,
        'previous_max_accounts':old_accounts,'previous_max_tasks':old_tasks,'valid_until':until.isoformat(),
        'effective_from':(start if later else now).isoformat(),'quota_effect':'after_current_term' if later else 'immediate',
        'quota_additive':False,'code_consumed':False}
