import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def host(tmp_path,monkeypatch):
    if sys.platform=='win32':
        monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace())
    spec=importlib.util.spec_from_file_location('dyfire_updater',Path(__file__).resolve().parents[2]/'deploy/updater.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.ROOT=tmp_path;module.QUEUE=tmp_path/'updates';module.QUEUE.mkdir();module.STATE={}
    (tmp_path/'data').mkdir()
    module.DATA_DIR=tmp_path/'data'
    module._resolve_data_dir = module.resolve_data_dir
    monkeypatch.setattr(module,'resolve_data_dir',lambda:module.DATA_DIR)
    with sqlite3.connect(tmp_path/'data/spark.db') as db:
        db.execute('CREATE TABLE task_runs (status TEXT)')
        db.execute('CREATE TABLE douyin_login_sessions (status TEXT)')
    previous={'version':'0.1.0','schema':1,'image':module.IMAGE+'@sha256:'+'a'*64}
    target={'version':'0.2.0','schema':1,'image':module.IMAGE+'@sha256:'+'b'*64}
    (tmp_path/'installed.json').write_text(json.dumps(previous))
    (tmp_path/'.env.console').write_text('SECRET=keep-this-value\nSPARK_IMAGE_NAME=old\n')
    monkeypatch.setattr(module,'latest',lambda force=False:target)
    module._real_release_for_target = module.release_for_target
    monkeypatch.setattr(module,'release_for_target',lambda value, operation_id='':target)
    monkeypatch.setattr(module.shutil,'disk_usage',lambda _:SimpleNamespace(free=10*1024**3))
    calls=[]
    module._real_compose = module.compose
    monkeypatch.setattr(module,'compose',lambda *args:calls.append(args))
    # Install tests stub the host-only Docker/API verification. Dedicated
    # runtime tests below exercise that read-only check with mocked commands.
    module._real_verify_runtime = module.verify_runtime
    monkeypatch.setattr(module, 'verify_runtime', lambda *args, **kwargs: {})
    return module,previous,target,calls


def test_rolls_back_image_but_does_not_rewind_database(host,monkeypatch):
    module,previous,target,calls=host
    def readiness(version):
        if version==target['version']:
            # A new process can write before the health check fails.
            with sqlite3.connect(module.ROOT/'data/spark.db') as db:
                db.execute("INSERT INTO task_runs VALUES ('submitted_unconfirmed')")
            raise RuntimeError('failed health')
    monkeypatch.setattr(module,'ready',readiness)
    with pytest.raises(RuntimeError):module.install('0.2.0')
    assert calls[-1][0]==previous['image']
    assert json.loads((module.ROOT/'installed.json').read_text())==previous
    with sqlite3.connect(module.ROOT/'data/spark.db') as db:
        assert db.execute('SELECT count(*) FROM task_runs').fetchone()[0]==1
    assert len(list((module.ROOT/'backups').glob('*/data/spark.db')))==1


def test_busy_accounts_block_update_before_stop(host):
    module,_,_,calls=host
    with sqlite3.connect(module.ROOT/'data/spark.db') as db:
        db.execute("INSERT INTO task_runs VALUES ('running')")
    with pytest.raises(ValueError,match='扫码或发送'):module.install('0.2.0')
    assert calls==[]


def test_active_readonly_probe_blocks_update_before_stopping_services(host):
    module,_,_,calls=host
    with sqlite3.connect(module.DATA_DIR/'spark.db') as db:
        db.execute('CREATE TABLE task_probes (status TEXT)')
        db.execute("INSERT INTO task_probes VALUES ('running')")
    with pytest.raises(ValueError,match='扫码或发送'):
        module.install('0.2.0')
    assert calls==[]


def test_active_probe_account_lease_blocks_update(host):
    module,_,_,calls=host
    with sqlite3.connect(module.DATA_DIR/'spark.db') as db:
        db.execute('CREATE TABLE account_operations (kind TEXT, expires_at DATETIME)')
        db.execute("INSERT INTO account_operations VALUES ('probe', datetime('now','+3 minutes'))")
    with pytest.raises(ValueError,match='扫码或发送'):
        module.install('0.2.0')
    assert calls==[]


def test_active_operation_guard_retries_transient_sqlite_database_errors(host, monkeypatch):
    module, _, _, _ = host
    real_connect = sqlite3.connect
    attempts = {'count': 0}

    def flaky_connect(*args, **kwargs):
        attempts['count'] += 1
        if attempts['count'] < 3:
            raise sqlite3.DatabaseError('database disk image is malformed')
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(module.sqlite3, 'connect', flaky_connect)
    module.assert_no_active_operations(module.DATA_DIR)
    assert attempts['count'] == 3


def test_success_records_actual_install(host,monkeypatch):
    module,_,target,calls=host
    monkeypatch.setattr(module,'ready',lambda _:None)
    module.install('0.2.0')
    assert json.loads((module.ROOT/'installed.json').read_text())==target
    assert module.STATE['phase']=='complete'


def test_arbitrary_image_rejected(host):
    module,previous,_,calls=host
    previous['image']='attacker/image:latest'
    (module.ROOT/'installed.json').write_text(json.dumps(previous))
    with pytest.raises(ValueError,match='固定镜像'):module.install('0.2.0')
    assert calls==[]


def test_web_install_keeps_other_services_running(host,monkeypatch):
    module,previous,target,calls=host
    monkeypatch.setattr(module,'ready',lambda _:None)
    module.install_web('0.2.0')
    assert calls == [(target['image'], 'pull', 'spark-web'),
                     (target['image'], 'up', '-d', '--no-build', 'spark-web')]
    assert json.loads((module.ROOT/'installed.json').read_text()) == previous
    assert json.loads((module.ROOT/'web-installed.json').read_text()) == target
    assert module.STATE['phase'] == 'web_complete'


def test_rollback_selects_the_newest_allowed_release(host, monkeypatch):
    module, previous, _target, calls = host
    releases = [
        {'version': '0.1.9', 'image': module.IMAGE + '@sha256:' + 'c' * 64},
        {'version': '0.1.8', 'image': module.IMAGE + '@sha256:' + 'd' * 64},
    ]
    monkeypatch.setattr(module, 'rollback_versions', lambda _current: releases)
    monkeypatch.setattr(module, 'ready', lambda _version: None)
    module.rollback()
    assert calls == [
        (releases[0]['image'], 'pull', *module.SERVICES),
        (previous['image'], 'stop', '-t', '180', *module.SERVICES),
        (releases[0]['image'], 'up', '-d', '--no-build', *module.SERVICES),
    ]
    assert json.loads((module.ROOT / 'installed.json').read_text())['version'] == '0.1.9'
    assert module.STATE['phase'] == 'rollback_complete'


def test_rollback_refuses_active_send_or_login_before_stopping(host, monkeypatch):
    module, _previous, _target, calls = host
    releases = [
        {'version': '0.1.9', 'image': module.IMAGE + '@sha256:' + 'c' * 64},
    ]
    monkeypatch.setattr(module, 'rollback_versions', lambda _current: releases)
    with sqlite3.connect(module.ROOT / 'data/spark.db') as db:
        db.execute("INSERT INTO task_runs VALUES ('running')")
        db.execute("INSERT INTO douyin_login_sessions VALUES ('queued')")
    with pytest.raises(ValueError, match='扫码或发送'):
        module.rollback()
    # A rejected rollback must not pull or stop any service.
    assert calls == []


def test_rollback_rechecks_after_pull_without_recovery_switch(host, monkeypatch):
    module, _previous, _target, calls = host
    releases = [
        {'version': '0.1.9', 'image': module.IMAGE + '@sha256:' + 'c' * 64},
    ]
    monkeypatch.setattr(module, 'rollback_versions', lambda _current: releases)

    def pull_then_become_busy(*args):
        calls.append(args)
        if args[1] == 'pull':
            with sqlite3.connect(module.ROOT / 'data/spark.db') as db:
                db.execute("INSERT INTO task_runs VALUES ('running')")

    monkeypatch.setattr(module, 'compose', pull_then_become_busy)
    with pytest.raises(ValueError, match='扫码或发送'):
        module.rollback()
    # The late guard must reject before stop/up and therefore must not invoke
    # the recovery branch, which could otherwise restart services needlessly.
    assert calls == [(releases[0]['image'], 'pull', *module.SERVICES)]


def test_release_manifest_must_match_tag_and_digest(host):
    module, *_ = host
    manifest = json.dumps({
        'version': '0.2.0',
        'schema': 1,
        'image': module.IMAGE + '@sha256:' + 'e' * 64,
    })
    release = {'tag_name': 'v0.2.0', 'body': '<!-- dyfire-manifest ' + manifest + ' -->'}
    assert module._release_from_payload(release, resolve_registry=False)['image'].endswith('e' * 64)
    mismatched = {'tag_name': 'v0.2.1', 'body': '<!-- dyfire-manifest ' + manifest + ' -->'}
    assert module._release_from_payload(mismatched, resolve_registry=False) == {}


def test_compose_uses_longer_timeout_only_for_image_pulls(host, monkeypatch):
    module, *_ = host
    from deploy import image_pull
    module.IMAGE_PULL_TIMEOUT_SECONDS = 7
    module.COMPOSE_OPERATION_TIMEOUT_SECONDS = 1
    class Process:
        def __init__(self): self.killed = False
        def poll(self): return None
        def kill(self): self.killed = True
        def wait(self): return None
    processes = []
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: processes.append(Process()) or processes[-1])
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    monkeypatch.setattr(module, 'write_state', lambda: None)
    ticks = iter([0, 8])
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(ticks))
    arguments=[]
    def pull(image,layers,publish,*,timeout):arguments.append((image,timeout))
    monkeypatch.setattr(image_pull,'pull',pull)
    monkeypatch.setattr(module,'image_layers',lambda image:{})
    module._real_compose('image', 'pull', 'spark-web')
    assert arguments==[('image',7)] and processes==[]

    ticks = iter([0, 2])
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(TimeoutError):
        module._real_compose('image', 'stop', '-t', '180', 'spark-web')
    assert processes[-1].killed


def test_updater_failure_state_keeps_a_truncated_safe_detail(host, monkeypatch):
    module, *_ = host
    captured = {}
    monkeypatch.setattr(module, "write_state", lambda **values: captured.update(values))
    error = ValueError("路径校验失败\n" + "x" * 400)
    detail = str(error).strip().replace("\n", " ")[:240]
    captured.update(
        phase="failed",
        message="操作未完成，请检查更新服务日志和发行配置",
        error_code=type(error).__name__.lower(),
        error_detail=detail or "未提供详细错误",
    )
    assert captured["error_code"] == "valueerror"
    assert captured["error_detail"].startswith("路径校验失败")
    assert len(captured["error_detail"]) <= 240


def test_resolves_live_compose_data_bind_mount(host,monkeypatch):
    module, *_ = host
    (module.ROOT/'data'/'spark.db').touch()
    responses=iter([
        SimpleNamespace(stdout='container-id\n'),
        SimpleNamespace(stdout=json.dumps([{'Mounts':[{
            'Destination':'/data','Type':'bind','Source':str(module.ROOT/'data')
        }]}])),
    ])
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        return next(responses)
    monkeypatch.setattr(module.subprocess,'run',run)
    assert module._resolve_data_dir()==module.ROOT/'data'
    assert calls[0][-3:]==['ps','-q','spark-web']


def test_rejects_data_mount_outside_canonical_root(host,monkeypatch,tmp_path):
    module, *_ = host
    external=module.ROOT.parent/(module.ROOT.name+'-external');external.mkdir();(external/'spark.db').touch()
    responses=iter([
        SimpleNamespace(stdout='container-id\n'),
        SimpleNamespace(stdout=json.dumps([{'Mounts':[{
            'Destination':'/data','Type':'bind','Source':str(external)
        }]}])),
    ])
    monkeypatch.setattr(module.subprocess,'run',lambda *args,**kwargs:next(responses))
    with pytest.raises(ValueError,match='标准部署目录'):
        module._resolve_data_dir()


def test_runtime_report_detects_recorded_image_drift(host, monkeypatch):
    module, previous, _target, _calls = host
    class Response:
        def __enter__(self): return self
        def __exit__(self, *exc): return None
        def read(self): return b'{"version":"0.1.0"}'

    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *args, **kwargs: Response())
    monkeypatch.setattr(module, '_container_for_service', lambda service: service + '-id')
    monkeypatch.setattr(module, '_inspect_container', lambda container_id: {
        'container_id': container_id[:12], 'running': True,
        'image': module.IMAGE + '@sha256:' + ('b' * 64), 'repo_digests': [],
    })
    report = module.runtime_report(previous['version'], previous['image'], ['spark-web'])
    assert report['api_version'] == previous['version']
    assert report['drift'] is True
    assert report['services']['spark-web']['running'] is True


def test_runtime_report_marks_missing_or_stopped_service(host, monkeypatch):
    module, previous, _target, _calls = host

    class Response:
        def __enter__(self): return self
        def __exit__(self, *exc): return None

    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *args, **kwargs: Response())
    monkeypatch.setattr(module.json, 'load', lambda _response: {'version': previous['version']})
    monkeypatch.setattr(module, '_container_for_service', lambda _service: 'service-id')
    monkeypatch.setattr(module, '_inspect_container', lambda _container: {
        'container_id': 'service-id', 'running': False, 'image': previous['image'],
        'repo_digests': [],
    })
    report = module.runtime_report(previous['version'], previous['image'], ['spark-web'])
    assert report['drift'] is True


def test_verify_runtime_raises_before_install_record_on_drift(host, monkeypatch):
    module, _previous, _target, _calls = host
    monkeypatch.setattr(module, 'runtime_report', lambda *args, **kwargs: {'drift': True})
    with pytest.raises(RuntimeError, match='运行容器版本或镜像'):
        module._real_verify_runtime('0.2.0', module.IMAGE + '@sha256:' + 'b' * 64, ['spark-web'])


def test_install_runtime_drift_restores_old_record_and_image(host, monkeypatch):
    module, previous, target, calls = host

    def verify(expected_version, *_args, **_kwargs):
        if expected_version == target['version']:
            raise RuntimeError('运行容器版本或镜像与部署记录不一致')

    monkeypatch.setattr(module, 'ready', lambda _version: None)
    monkeypatch.setattr(module, 'verify_runtime', verify)
    with pytest.raises(RuntimeError, match='运行容器版本或镜像'):
        module.install(target['version'])
    assert calls[-2:] == [
        (target['image'], 'up', '-d', '--no-build', *module.SERVICES),
        (previous['image'], 'up', '-d', '--no-build', *module.SERVICES),
    ]
    assert json.loads((module.ROOT / 'installed.json').read_text()) == previous


def test_download_plan_reuses_shared_layers_and_counts_new_compressed_bytes(host,monkeypatch):
    module,previous,target,_calls=host
    shared='sha256:'+'c'*64;new='sha256:'+'d'*64
    def registry(path,accept=None):
        if path.endswith(target['image'].split('@')[1]):return {'layers':[{'digest':shared,'size':300000000},{'digest':new,'size':2000000}]},{}
        if path.endswith(previous['image'].split('@')[1]):return {'layers':[{'digest':shared,'size':300000000}]},{}
        raise AssertionError('Unexpected registry path')
    monkeypatch.setattr(module,'registry',registry)
    plan=module.download_plan(target['image'],previous['image'])
    assert plan['known'] and plan['compressed_image_bytes']==302000000
    assert plan['download_upper_bound_bytes']==2000000 and plan['reused_layers']==1
    assert plan['shared_by_services']==4
    assert module.download_plan(target['image'],target['image'])['download_upper_bound_bytes']==0


def test_download_estimate_failure_does_not_block_installation(host,monkeypatch):
    module,_previous,target,_calls=host
    monkeypatch.setattr(module,'registry',lambda *a,**k:(_ for _ in ()).throw(TimeoutError()))
    assert module.download_plan(target['image'],_previous['image'])=={'known':False}


def test_multiplatform_estimate_selects_server_architecture_and_caches_layers(host,monkeypatch):
    import platform
    module,_previous,target,_calls=host
    monkeypatch.setattr(platform,'machine',lambda:'x86_64')
    calls=[];manifest='sha256:'+'e'*64
    def registry(path,accept=None):
        calls.append(path)
        if path.endswith(manifest):return {'layers':[{'digest':'sha256:'+'f'*64,'size':10}]},{}
        return {'manifests':[{'digest':manifest,'platform':{'os':'linux','architecture':'amd64'}}, {'digest':'sha256:'+'0'*64,'platform':{'os':'unknown','architecture':'unknown'}}]},{}
    monkeypatch.setattr(module,'registry',registry)
    assert module.image_layers(target['image'])=={'sha256:'+'f'*64:10}
    module.image_layers(target['image']);assert len(calls)==2


def accept_operation(module, action='install-web', target='0.2.0'):
    import time
    request = {'operation_id': '1' * 32, 'action': action, 'version': target, 'requested_at': time.time()}
    journal = module.Journal(module.ROOT)
    journal.accept(request)
    return journal, request


def test_web_health_failure_restores_only_web_and_keeps_new_database_records(host, monkeypatch):
    module, previous, target, calls = host
    journal, _ = accept_operation(module)
    journal.update(release=target)
    def ready(value):
        if value == target['version']:
            with sqlite3.connect(module.DATA_DIR / 'spark.db') as db:
                db.execute("INSERT INTO task_runs VALUES ('succeeded')")
            raise RuntimeError('unhealthy Web')
    monkeypatch.setattr(module, 'ready', ready)
    with pytest.raises(RuntimeError, match='unhealthy Web'):
        module.install_web(target['version'], '1' * 32)
    assert journal.read()['stage'] == 'rolled_back'
    assert calls[-1][0] == previous['image']
    assert all(call[-1] == 'spark-web' for call in calls)
    assert json.loads((module.ROOT / 'web-installed.json').read_text()) == previous
    with sqlite3.connect(module.DATA_DIR / 'spark.db') as db:
        assert db.execute('SELECT count(*) FROM task_runs').fetchone()[0] == 1


def test_incompatible_browser_prevents_web_only_before_pull(host):
    module, _, target, calls = host
    target['compatibility'] = {'api': 1, 'browser': 2, 'database': 1}
    with pytest.raises(ValueError, match='完整更新'):
        module.install_web(target['version'])
    assert calls == []


def test_web_success_keeps_full_update_available_for_older_browser(host, monkeypatch):
    module, previous, target, _ = host
    monkeypatch.setattr(module, 'ready', lambda _: None)
    module.install_web(target['version'])
    assert module.STATE['component_versions'] == {'web': target['version'], 'browser': previous['version']}
    assert module.STATE['component_update_available'] is True
    assert module.STATE['web_update_available'] is False
    module.install(target['version'])
    assert module.STATE['component_update_available'] is False


def test_full_update_cannot_downgrade_a_newer_web_install(host, monkeypatch):
    module, previous, target, calls = host
    (module.ROOT / 'web-installed.json').write_text(json.dumps({**previous, 'version': '0.3.0'}))
    with pytest.raises(ValueError, match='版本'):
        module.install(target['version'])
    assert calls == []


def test_release_v2_requires_component_schema_and_normalizes_database_version(host):
    module, _, target, _ = host
    browser = module.IMAGE + '@sha256:' + 'c' * 64
    manifest = {**target, 'schema': 2, 'manifest_version': 2,
                'compatibility': {'api': 1, 'browser': 1, 'database': 1},
                'components': {'web': {'image': target['image'], 'index_image': target['image']},
                               'browser': {'image': browser, 'index_image': browser}}}
    def payload():
        return {'tag_name': 'v0.2.0', 'body': '<!-- dyfire-manifest ' + json.dumps(manifest) + ' -->'}
    release = module._release_from_payload(payload(), resolve_registry=False)
    assert release['schema'] == 1 and release['manifest_schema'] == 2
    assert module.service_images(release)['spark-worker'] == browser
    assert module.service_images(release)['spark-monitor'] == target['image']
    manifest['schema'] = 1
    assert module._release_from_payload(payload(), resolve_registry=False) == {}
    manifest['schema'] = 2
    manifest['components']['browser']['image'] = 'attacker/image:latest'
    assert module._release_from_payload(payload(), resolve_registry=False) == {}
    manifest['manifest_version'] = 3
    assert module._release_from_payload(payload(), resolve_registry=False) == {}


def test_target_release_is_exact_and_accepted_digest_survives_release_changes(host, monkeypatch):
    module, _, target, _ = host
    journal, _ = accept_operation(module)
    queries = []
    def fetch(path, **kwargs):
        queries.append(path)
        return {'tag_name': 'v0.2.0', 'body': '<!-- dyfire-manifest ' + json.dumps(target) + ' -->'}
    monkeypatch.setattr(module, '_cached_github', fetch)
    first = module._real_release_for_target('0.2.0', '1' * 32)
    assert queries == ['/releases/tags/v0.2.0']
    monkeypatch.setattr(module, '_cached_github', lambda *a, **k: pytest.fail('must reuse accepted digest'))
    assert module._real_release_for_target('0.2.0', '1' * 32) == first
    with pytest.raises(ValueError, match='指定正式发行'):
        module._real_release_for_target('0.3.0', '1' * 32)


def test_completed_request_is_not_deployed_twice(host, monkeypatch):
    module, _, _, _ = host
    journal, request = accept_operation(module)
    calls = []
    monkeypatch.setattr(module, 'install_web', lambda *a: calls.append(a))
    module.process_request(request)
    module.process_request(request)
    assert len(calls) == 1
    assert journal.read()['stage'] == 'complete'


def test_completed_handoff_crash_commits_verified_image_without_recreating(host, monkeypatch):
    module, previous, target, calls = host
    journal, _ = accept_operation(module)
    journal.update(previous=previous, previous_web=previous, release=target, stage='verifying')
    (module.QUEUE / 'web-handoff.json').write_text(json.dumps({'phase': 'complete', 'operation_id': '1' * 32}))
    monkeypatch.setattr(module, 'ready', lambda _: None)
    module.recover_transaction(journal)
    assert calls == []
    assert journal.read()['stage'] == 'complete'
    assert json.loads((module.ROOT / 'web-installed.json').read_text()) == target
    assert json.loads((module.ROOT / 'installed.json').read_text()) == previous


def test_interrupted_full_update_restores_exact_mixed_service_versions(host, monkeypatch):
    module, previous, target, calls = host
    old_web = {**previous, 'version': '0.1.5', 'image': module.IMAGE + '@sha256:' + 'c' * 64}
    journal, _ = accept_operation(module, 'install')
    journal.update(previous=previous, previous_web=old_web, release=target, stage='switching')
    def verify(expected, *args, **kwargs):
        if expected == target['version']:
            raise RuntimeError('incomplete switch')
    monkeypatch.setattr(module, 'verify_runtime', verify)
    monkeypatch.setattr(module, 'ready', lambda _: None)
    module.recover_transaction(journal)
    assert calls == [(module.installed_images(previous, old_web), 'up', '-d', '--no-build', *module.SERVICES)]
    assert journal.read()['stage'] == 'rolled_back'
    assert json.loads((module.ROOT / 'web-installed.json').read_text()) == old_web


def test_recovery_already_in_rollback_never_commits_the_rejected_release(host, monkeypatch):
    module, previous, target, calls = host
    journal, _ = accept_operation(module)
    journal.update(previous=previous, previous_web=previous, release=target, stage='recovering')
    monkeypatch.setattr(module, 'ready', lambda _: None)
    module.recover_transaction(journal)
    assert journal.read()['stage'] == 'rolled_back'
    assert calls[-1][0] == previous['image']


def test_rollback_download_resume_uses_accepted_release(host, monkeypatch):
    module, previous, target, calls = host
    journal, _ = accept_operation(module, 'rollback', '')
    journal.update(previous=previous, previous_web=previous, release=target, stage='pulling')
    monkeypatch.setattr(module, 'rollback_versions', lambda _: pytest.fail('must not change accepted rollback release'))
    monkeypatch.setattr(module, 'ready', lambda _: None)
    module.rollback(operation_id='1' * 32)
    assert calls[0][0] == target['image']


def test_combined_plan_deduplicates_shared_layers_between_two_images(host, monkeypatch):
    module, previous, target, _ = host
    browser = module.IMAGE + '@sha256:' + 'c' * 64
    layers = {previous['image']: {'base': 100}, target['image']: {'base': 100, 'app': 10},
              browser: {'base': 100, 'browser': 200, 'app': 10}}
    monkeypatch.setattr(module, 'image_layers', lambda image: layers[image])
    plan = module.combined_download_plan({'web': target['image'], 'auth': browser, 'worker': browser}, {'web': previous['image']})
    assert plan['compressed_image_bytes'] == 310
    assert plan['download_upper_bound_bytes'] == 210


def test_journal_accepted_download_can_resume_long_after_request_expires(tmp_path):
    from deploy.update_transaction import Journal
    journal = Journal(tmp_path)
    request = {'operation_id': 'a' * 32, 'action': 'install', 'version': '0.2.0', 'requested_at': 1000}
    accepted = journal.accept(request, now=1001)
    assert journal.accept(request, now=100000)['accepted_at'] == accepted['accepted_at']
    with pytest.raises(ValueError, match='内容'):
        journal.accept({**request, 'version': '0.3.0'}, now=1001)
    with pytest.raises(ValueError, match='尚未恢复'):
        journal.accept({**request, 'operation_id': 'b' * 32}, now=1001)
    journal.update(stage='complete')
    with pytest.raises(ValueError, match='过期'):
        journal.accept({**request, 'operation_id': 'b' * 32}, now=100000)


def test_persisted_compose_images_preserve_other_configuration_and_mixed_versions(host):
    module, previous, target, _ = host
    path = module.ROOT / '.env.console'
    path.write_text('# custom deployment\nSECRET=literal$keep\nSPARK_IMAGE_NAME=old\nexport SPARK_WEB_IMAGE=old\n')
    module.persist_images(module.installed_images(previous, target))
    value = path.read_text()
    assert value.startswith('# custom deployment\nSECRET=literal$keep\n')
    assert value.count('SPARK_WEB_IMAGE=') == 1
    assert 'SPARK_WEB_IMAGE=' + target['image'] in value
    assert 'SPARK_BROWSER_IMAGE=' + previous['image'] in value
    assert 'SPARK_MONITOR_IMAGE=' + previous['image'] in value


@pytest.mark.parametrize('payload', [{}, [], ['invalid'], {'action': 'check'}])
def test_invalid_queue_payload_does_not_crash_or_block_the_next_request(host, monkeypatch, payload):
    module, _, _, _ = host
    folder = module.QUEUE / 'requests'
    folder.mkdir()
    request = folder / 'request.json'
    request.write_text(json.dumps(payload))
    monkeypatch.setattr(module, 'fcntl', None)
    def end_iteration(_):
        raise StopIteration()
    monkeypatch.setattr(module.time, 'sleep', end_iteration)
    with pytest.raises(StopIteration):
        module.main()
    assert not request.exists()
    assert module.STATE['phase'] == 'failed'


def test_all_component_health_is_verified_after_starting(host, monkeypatch):
    module, _, target, _ = host
    results = iter([{'drift': True, 'services': {'spark-auth': {'health': 'starting'}}},
                    {'drift': False, 'services': {'spark-auth': {'health': 'healthy'}}}])
    monkeypatch.setattr(module, 'runtime_report', lambda *a: next(results))
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    assert module._real_verify_runtime(target['version'], target['image'])['drift'] is False
    monkeypatch.setattr(module, 'runtime_report', lambda *a: {'drift': True, 'services': {'spark-auth': {'health': 'unhealthy'}}})
    with pytest.raises(RuntimeError, match='健康检查'):
        module._real_verify_runtime(target['version'], target['image'])
