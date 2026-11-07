from datetime import timedelta
import pytest
from sqlalchemy import select,func
from spark_console.db import session_scope
from spark_console.models import AccountIncident,TaskRun,User,utc_now
from spark_console.services.account_health import record
from spark_console.services.incidents import views
from tests.console import test_account_health as health_tests


@pytest.fixture
def account_case():
    f=health_tests.AccountHealthTests();f.setUp()
    yield f
    f.doCleanups()


def test_repeated_health_signals_merge_until_recovery_and_count_distinct_tasks(account_case):
    f=account_case;now=f.now
    with session_scope(f.engine) as db:
        record(db,f.account,f.expected,'temporary','first',now=now)
        record(db,f.account,f.expected,'verification','second',now=now+timedelta(seconds=30))
        first=db.scalar(select(AccountIncident));identifier=first.id
        assert first.observations==2 and first.state=='verification'
        db.add_all([TaskRun(task_id=f.tasks[0],scheduled_for=now-timedelta(seconds=10),finished_at=now+timedelta(seconds=1),status='failed'),
                    TaskRun(task_id=f.tasks[0],scheduled_for=now+timedelta(seconds=20),finished_at=now+timedelta(seconds=22),status='skipped')])
    with session_scope(f.engine) as db:
        row=db.get(AccountIncident,identifier)
        assert views(db,[row],now=now+timedelta(minutes=2))[0]['affected_tasks']==1
        record(db,f.account,f.expected,'valid','recovered',now=now+timedelta(minutes=3))
        assert row.resolved_at is not None
        record(db,f.account,f.expected,'temporary','new episode',now=now+timedelta(minutes=4))
        assert db.scalar(select(func.count()).select_from(AccountIncident))==2
        assert db.scalar(select(func.count()).select_from(AccountIncident).where(AccountIncident.resolved_at.is_(None)))==1


def test_incident_history_obeys_account_ownership_and_bootstrap_shows_current_episode(account_case):
    f=account_case;f.anomaly('verification')
    response=f.client.get(f'/api/accounts/{f.account}/incidents')
    assert response.status_code==200 and len(response.json()['items'])==1
    data=f.client.get('/api/app/bootstrap').json()
    row=next(item for item in data['accounts'] if item['id']==f.account)
    assert row['incident']['open'] and row['incident']['state']=='verification'
    with session_scope(f.engine) as db:
        other=User(username='incident-other',password_hash='unused',role='user',must_change_password=False)
        db.add(other);db.flush()
        from spark_console.security import SessionService
        token,session=SessionService(b's'*32).create_record(other.id);db.add(session)
    f.client.cookies.clear();f.client.cookies.set('spark_session',token)
    assert f.client.get(f'/api/accounts/{f.account}/incidents').status_code==404


def test_stale_credentials_cannot_open_or_close_an_incident(account_case):
    f=account_case
    with session_scope(f.engine) as db:
        assert not record(db,f.account,(b'outdated',b'outdated',1),'verification','stale')
        assert db.scalar(select(AccountIncident)) is None


def test_retention_preserves_open_incident_and_removes_only_old_resolved_episode(account_case):
    f=account_case
    from spark_console.retention import retain,RetentionPolicy
    now=utc_now()
    with session_scope(f.engine) as db:
        db.add_all([AccountIncident(account_id=f.account,state='invalid',started_at=now-timedelta(days=500),observed_at=now),
                    AccountIncident(account_id=f.account,state='temporary',started_at=now-timedelta(days=500),observed_at=now-timedelta(days=499),resolved_at=now-timedelta(days=499))])
    with session_scope(f.engine) as db:
        plan=retain(db,RetentionPolicy(),now=now)
        assert plan.categories['account_incidents'].candidates==1 and plan.categories['account_incidents'].protected==1
        retain(db,RetentionPolicy(),now=now,dry_run=False)
        assert db.scalar(select(func.count()).select_from(AccountIncident))==1


def test_account_state_changes_do_not_repeat_alert_within_one_incident(account_case):
    f=account_case
    from spark_console.models import MailPreference,MailDelivery
    from spark_console.services import mail
    f.anomaly('verification')
    config={**mail.DEFAULTS,'enabled':True}
    with session_scope(f.engine) as db:
        db.add(MailPreference(user_id=f.owner,email='fixture@example.com',enabled=True,verified=True))
    with session_scope(f.engine) as db:
        mail.collect(db,config,f.now,f.cipher)
        record(db,f.account,f.expected,'invalid','second signal',now=f.now+timedelta(seconds=60))
        mail.collect(db,config,f.now+timedelta(seconds=61),f.cipher)
        assert db.scalar(select(func.count()).select_from(MailDelivery).where(MailDelivery.event=='account_alert'))==1
        episode=db.scalar(select(AccountIncident))
        assert episode.alert_delivery_id is not None
        view=views(db,[episode])[0]
        assert view['email_notice']=='queued' and view['started_at'].endswith('+00:00')
