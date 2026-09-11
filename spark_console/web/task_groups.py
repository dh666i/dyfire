"""Account-level pagination; child tasks are loaded only when a group opens."""
from sqlalchemy import select, func, case, or_
from spark_console.models import SparkTask, User, DouyinAccount, DouyinAccountProfile
from spark_console.web.admin_queries import _pagination, AdminTaskRow, ADMIN_PAGE_SIZE


def filtered_tasks(q='', status=''):
    statement = (select(SparkTask, User, DouyinAccount).join(User, User.id == SparkTask.owner_user_id)
        .outerjoin(DouyinAccount, DouyinAccount.id == SparkTask.douyin_account_id)
        .outerjoin(DouyinAccountProfile, DouyinAccountProfile.account_id == DouyinAccount.id))
    if q.strip():
        pattern = '%' + q.strip() + '%'
        statement = statement.where(or_(*[field.ilike(pattern) for field in (
            User.username, SparkTask.target_name, SparkTask.message_template,
            DouyinAccount.display_name, DouyinAccountProfile.nickname)]))
    if status in ('enabled', 'disabled'):
        statement = statement.where(SparkTask.enabled.is_(status == 'enabled'))
    return statement


def list_task_groups(db, q='', status='', page=1):
    grouped = (filtered_tasks(q, status).with_only_columns(
        SparkTask.owner_user_id.label('owner_id'), SparkTask.douyin_account_id.label('account_id'),
        User.username, DouyinAccount.display_name.label('remark'), DouyinAccount.validation_state,
        DouyinAccountProfile.nickname, DouyinAccountProfile.avatar_url,
        func.count(SparkTask.id).label('total'),
        func.sum(case((SparkTask.enabled.is_(True), 1), else_=0)).label('enabled'))
        .group_by(SparkTask.owner_user_id, SparkTask.douyin_account_id, User.username,
                  DouyinAccount.display_name, DouyinAccount.validation_state, DouyinAccountProfile.nickname, DouyinAccountProfile.avatar_url))
    total = db.scalar(select(func.count()).select_from(grouped.subquery())) or 0
    pagination = _pagination(page, total)
    rows = db.execute(grouped.order_by(User.username, SparkTask.owner_user_id, SparkTask.douyin_account_id)
        .limit(ADMIN_PAGE_SIZE).offset((pagination['page']-1)*ADMIN_PAGE_SIZE)).mappings().all()
    return rows, pagination


def list_group_tasks(db, owner_id, account_id, q='', status='', page=1):
    statement = filtered_tasks(q, status).where(SparkTask.owner_user_id == owner_id,
        SparkTask.douyin_account_id == (None if account_id == 'unbound' else account_id))
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    pagination = _pagination(page, total)
    rows = db.execute(statement.order_by(SparkTask.send_time, SparkTask.id)
        .limit(ADMIN_PAGE_SIZE).offset((pagination['page']-1)*ADMIN_PAGE_SIZE)).all()
    return [AdminTaskRow(*row) for row in rows], pagination
