import asyncio,json
from datetime import timedelta,timezone
from sqlalchemy import select,update,delete
from spark_console.models import (TaskProbe,SparkTask,SparkTaskTargetIdentity,TaskRun,DouyinAccount,DouyinAccountIdentity,User,AccountOperation,utc_now)
from spark_console.db import session_scope
from spark_console.services import NotFound,Conflict,ValidationError
from spark_console.services.audits import AuditService
from spark_console.services.accounts import AccountService
from spark_console.services.account_health import acquire,release,snapshot,is_relogging


def view(row):
    result=json.loads(row.result_json) if row.result_json and row.status not in ('queued','running') else None
    return {'id':row.id,'task_id':row.task_id,'kind':row.kind,'status':row.status,'result':result,
            'created_at':row.created_at.replace(tzinfo=timezone.utc).isoformat(),
            'status_url':'/api/task-probes/'+row.id}


def queue(db,user,task_id,*,run_id=None):
    task=db.get(SparkTask,task_id)
    if task is None or (user.role!='admin' and task.owner_user_id!=user.id):raise NotFound('任务不存在')
    recent=db.scalar(select(TaskProbe).where(TaskProbe.task_id==task.id,TaskProbe.created_at>utc_now()-timedelta(minutes=2)).order_by(TaskProbe.created_at.desc()).limit(1))
    if recent:return recent
    account=db.get(DouyinAccount,task.douyin_account_id) if task.douyin_account_id else None
    if account is None:raise ValidationError('请先绑定发送账号')
    if run_id:
        run=db.get(TaskRun,run_id)
        if run is None or run.task_id!=task.id or run.status!='submitted_unconfirmed':raise ValidationError('此记录无需结果核对')
    row=TaskProbe(task_id=task.id,owner_id=task.owner_user_id,actor_id=user.id,run_id=run_id,
        kind='reconcile' if run_id else 'selfcheck',result_json=json.dumps({'revision':task.updated_at.isoformat()}))
    db.add(row);db.flush();AuditService(db).write(user.id,'task.probe_requested','task_probe',row.id)
    return row


async def process(engine,cipher,reader=None):
    from spark_console.read_only_probe import inspect_task
    now=utc_now();raw=None;identifier=None;account_id=None
    with session_scope(engine) as db:
        db.execute(update(TaskProbe).where(TaskProbe.status=='queued',TaskProbe.created_at<now-timedelta(minutes=15)).values(status='failed',finished_at=now,result_json=json.dumps({'summary':'账号忙碌，自检等待已结束；可在任务空闲时重新发起，未发送消息'})))
        db.execute(update(TaskProbe).where(TaskProbe.status=='running',TaskProbe.started_at<now-timedelta(minutes=4)).values(status='failed',finished_at=now,result_json=json.dumps({'summary':'自检进程已中断，未发送消息'})))
        db.execute(delete(TaskProbe).where(TaskProbe.finished_at<now-timedelta(days=30)))
        row=db.scalar(select(TaskProbe).where(TaskProbe.status=='queued').order_by(TaskProbe.created_at,TaskProbe.id).limit(1))
        if row is None:return False
        task=db.get(SparkTask,row.task_id);account=db.get(DouyinAccount,task.douyin_account_id) if task and task.douyin_account_id else None
        user=db.get(User,row.owner_id)
        if not task or not account or not user or user.status!='active' or json.loads(row.result_json or '{}').get('revision')!=task.updated_at.isoformat():
            row.status='failed';row.finished_at=now;row.result_json=json.dumps({'summary':'任务或账号已变化，请重新发起自检'});return True
        imminent=db.scalar(select(SparkTask.id).where(SparkTask.douyin_account_id==account.id,SparkTask.enabled.is_(True),SparkTask.next_run_at<=now+timedelta(minutes=3)).limit(1))
        if imminent or is_relogging(db,account.id):return False
        if not acquire(db,account.id,row.id,'probe',now,180):return False
        changed=db.execute(update(TaskProbe).where(TaskProbe.id==row.id,TaskProbe.status=='queued').values(status='running',started_at=now)).rowcount
        if not changed:release(db,account.id,row.id);return False
        identifier,account_id=row.id,account.id;expected=snapshot(account)
        binding=db.get(SparkTaskTargetIdentity,task.id);uid=binding.sec_uid if binding else ''
        identity=db.get(DouyinAccountIdentity,account.id)
        run=db.get(TaskRun,row.run_id) if row.run_id else None
        receipt=json.loads(run.receipt_json) if run and run.receipt_json else {}
        msg_id=receipt.get('message_id') if receipt.get('run_id')==row.run_id and receipt.get('task_id')==task.id else None
        raw=AccountService(db,cipher,AuditService(db)).decrypt_for_worker(account.id)
        args=(account.cookie_version,task.target_name,uid,task.message_kind)
        expected_uid=identity.douyin_user_id if identity else None
    try:
        result=await asyncio.wait_for((reader or inspect_task)(raw,*args,message_id=msg_id,expected_user_id=expected_uid),timeout=150)
        with session_scope(engine) as db:
            row=db.get(TaskProbe,identifier);account=db.get(DouyinAccount,account_id)
            if row is None or row.status!='running':return True
            lease=db.get(AccountOperation,account_id)
            if not account or snapshot(account)!=expected or lease is None or lease.token!=identifier or lease.expires_at.replace(tzinfo=timezone.utc)<=utc_now():
                result={'error_code':'context_changed','summary':'账号或操作已变化，自检结果未应用'}
            if row.kind=='reconcile':
                run=db.get(TaskRun,row.run_id)
                if run and run.status=='submitted_unconfirmed' and msg_id and result.get('delivery')=='message_id_found' and result.get('checks',{}).get('stable_target'):
                    from spark_console.scheduler import finish_run
                    finish_run(run,'success','reconciled',utc_now());AuditService(db).write(row.actor_id,'run.reconciled','task_run',run.id)
                    result['summary']='已通过原目标会话中的平台消息编号核对，未重新发送消息'
                elif not result.get('error_code'):result['summary']='未取得足够的可关联证据，保留待核实；未重新发送消息'
            row.status='failed' if result.get('error_code') else 'succeeded';row.result_json=json.dumps(result,ensure_ascii=False);row.finished_at=utc_now()
    except asyncio.CancelledError:raise
    except Exception as error:
        with session_scope(engine) as db:
            row=db.get(TaskProbe,identifier)
            if row:row.status='failed';row.result_json=json.dumps({'error_code':type(error).__name__,'summary':'自检未完成，未发送消息'});row.finished_at=utc_now()
    finally:
        if raw is not None:raw[:]=b'\0'*len(raw);raw.clear()
        with session_scope(engine) as db:release(db,account_id,identifier)
    return True
