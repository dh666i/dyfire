import importlib.util
import json
from pathlib import Path
from datetime import datetime, timezone

from spark_console.business_health import schedule_capacity
from spark_console.db import session_scope
from spark_console.models import DouyinAccount, SparkTask, TaskRun, TaskRunEvent, User
from spark_console.db import create_schema
from tests.console import test_sensitive_lifecycle as lifecycle

configured=lifecycle.configured


def test_forecast_handles_midnight_peaks_and_insufficient_samples():
    forecast=schedule_capacity([('23:59',3),('00:01',4),('09:30',1)],20)
    peak=forecast['peaks'][1]
    assert peak['tasks']==7 and peak['estimated_drain_seconds']==196
    assert schedule_capacity([('09:30',1)],None)['sample_required']


def test_new_database_omits_retired_auth_tables_and_existing_tables_are_preserved(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    engine=create_engine(f'sqlite:///{tmp_path / "schema.db"}')
    create_schema(engine)
    tables=set(inspect(engine).get_table_names())
    assert 'admin_totp_credentials' not in tables
    assert 'admin_totp_recovery_codes' not in tables
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE admin_totp_credentials (user_id TEXT PRIMARY KEY, ciphertext BLOB)'))
        connection.execute(text("INSERT INTO admin_totp_credentials VALUES ('legacy-user', X'0102')"))
    create_schema(engine)
    with engine.connect() as connection:
        assert connection.execute(text('SELECT user_id, ciphertext FROM admin_totp_credentials')).one()==('legacy-user',b'\x01\x02')
    engine.dispose()


def test_diagnostics_excludes_message_and_event_details_and_protects_owner(configured):
    f=configured
    with session_scope(f.fixture.engine) as db:
        other=User(username='bundle-other',password_hash='unused',must_change_password=False)
        db.add(other);db.flush()
        account=DouyinAccount(owner_user_id=other.id,display_name='private-account',encrypted_cookies=b'private-cookie',cookie_nonce=b'x')
        db.add(account);db.flush()
        task=SparkTask(owner_user_id=other.id,douyin_account_id=account.id,target_name='private-friend',message_template='private-message',send_time='09:30',message_kind='text')
        db.add(task);db.flush()
        run=TaskRun(task_id=task.id,scheduled_for=datetime.now(timezone.utc),status='failed',stage='selecting_target',error_code='target_not_found')
        db.add(run);db.flush();identifier=run.id
        db.add(TaskRunEvent(run_id=identifier,sequence=1,event='error',stage='selecting_target',detail='private-event-content'))
    response=f.client.get(f'/api/runs/DF-{identifier}/diagnostics')
    assert response.status_code==200
    assert response.json()['reason_code']=='target_not_found'
    assert not any(secret in response.text for secret in ('private-cookie','private-friend','private-message','private-event-content'))
    assert f.client.get('/api/runs/nonexistent/diagnostics').status_code==404
    with session_scope(f.fixture.engine) as db:db.get(User,other.id).role='admin'
    assert f.client.get(f'/api/runs/{identifier}/diagnostics').status_code==200
    runs=f.client.get('/api/admin/runs',params={'q':f'DF-{identifier}'})
    assert runs.status_code==200 and runs.json()['items'][0]['support_id']==f'DF-{identifier}'
    with session_scope(f.fixture.engine) as db:db.get(User,f.uid).role='user'
    assert f.client.get(f'/api/runs/{identifier}/diagnostics').status_code==404


def test_snapshot_budget_never_selects_recent_recovery_points_or_unknown_dirs(tmp_path):
    spec=importlib.util.spec_from_file_location('snapshot_inventory',Path(__file__).resolve().parents[2]/'deploy/snapshot_inventory.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    for day in range(1,9):
        folder=tmp_path/'backups'/f'202601{day:02d}-010000';(folder/'data').mkdir(parents=True)
        (folder/'installed.json').write_text(json.dumps({'version':f'0.1.{day}','image':'ghcr.io/dh666i/dyfire@sha256:'+'a'*64}))
        (folder/'data/spark.db').write_bytes(b'fixture')
        import os
        os.utime(folder,(0,0))
    unknown=tmp_path/'backups/unrelated';unknown.mkdir();(unknown/'do-not-touch').write_text('preserve')
    result=module.inventory(tmp_path,keep=5,days=14,budget_bytes=1,now=2000000000)
    assert result['candidates']==['20260101-010000','20260102-010000','20260103-010000']
    assert result['over_budget']  # Minimum recovery points take precedence.
    assert len(list((tmp_path/'backups').iterdir()))==9  # Planning never deletes.
