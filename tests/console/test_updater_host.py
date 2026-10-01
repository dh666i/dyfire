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
    monkeypatch.setattr(module,'latest',lambda force=False:target)
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
    with pytest.raises(TimeoutError):
        module._real_compose('image', 'pull', 'spark-web')
    assert processes[-1].killed

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
