import json
from pathlib import Path

import pytest

from deploy import web_switch as switcher
from deploy.install_web_switch import migrated_vhost


OLD = 'ghcr.io/dh666i/dyfire@sha256:' + 'a' * 64
NEW = 'ghcr.io/dh666i/dyfire-web@sha256:' + 'b' * 64


@pytest.fixture
def deployment(tmp_path):
    (tmp_path / 'deploy').mkdir()
    (tmp_path / 'deploy/web-switch.json').write_text(json.dumps({'version': 1, 'canonical_port': 8899, 'candidate_port': 18999}))
    (tmp_path / 'deploy/web-upstream.conf').write_text(switcher.upstream(8899))
    return tmp_path


class FakeHost:
    def __init__(self):
        self.events = []
        self.canonical = '0.4.5'
        self.candidate = None
        self.route_port = 8899
        self.fail_candidate = False
        self.fail_new_canonical = False
        self.fail_route = None
        self.fail_drain = False

    def create(self, name, image, operation_id):
        self.events.append(('create', image))
        self.candidate = name

    def ready(self, port, version, seconds=90):
        self.events.append(('ready', port, version))
        if port == 18999 and self.fail_candidate:
            raise RuntimeError('candidate unhealthy')
        if port == 8899 and (version != self.canonical or (version == '0.4.6' and self.fail_new_canonical)):
            raise RuntimeError('canonical unhealthy')

    def route(self, port):
        self.events.append(('route', port))
        if self.fail_route == port:
            self.fail_route = None
            raise RuntimeError('reload failed')
        self.route_port = port

    def drain(self, seconds=120):
        self.events.append(('drain',))
        if self.fail_drain:
            raise RuntimeError('long request')

    def remove(self, name, operation_id):
        self.events.append(('remove',))
        self.candidate = None

    def recreate(self, image):
        self.events.append(('recreate', image))
        self.canonical = '0.4.6' if image == NEW else '0.4.5'


def run_switch(deployment, host):
    return switcher.switch(deployment, NEW, '0.4.6', 'operation-one', host.recreate,
                           lambda version: host.ready(8899, version), previous_image=OLD,
                           previous_version='0.4.5', host=host)


def test_candidate_serves_until_canonical_is_healthy(deployment):
    host = FakeHost()
    result = run_switch(deployment, host)
    assert result['phase'] == 'complete'
    assert host.route_port == 8899 and host.canonical == '0.4.6' and host.candidate is None
    assert host.events.index(('ready', 18999, '0.4.6')) < host.events.index(('route', 18999))
    assert host.events.index(('route', 18999)) < host.events.index(('recreate', NEW))
    assert host.events.index(('ready', 8899, '0.4.6')) < host.events.index(('route', 8899))
    assert host.events.index(('drain',)) < host.events.index(('recreate', NEW))


def test_unhealthy_candidate_never_replaces_canonical(deployment):
    host = FakeHost()
    host.fail_candidate = True
    with pytest.raises(RuntimeError, match='candidate unhealthy'):
        run_switch(deployment, host)
    assert not any(event[0] == 'recreate' for event in host.events)
    assert host.route_port == 8899 and host.canonical == '0.4.5'
    assert json.loads((deployment / 'updates/web-handoff.json').read_text())['phase'] == 'restored'


def test_new_canonical_failure_restores_old_while_candidate_available(deployment):
    host = FakeHost()
    host.fail_new_canonical = True
    with pytest.raises(RuntimeError, match='canonical unhealthy'):
        run_switch(deployment, host)
    assert [event for event in host.events if event[0] == 'recreate'] == [('recreate', NEW), ('recreate', OLD)]
    assert host.events.index(('recreate', OLD)) < host.events.index(('route', 8899))
    assert host.route_port == 8899 and host.canonical == '0.4.5' and host.candidate is None


def test_route_reload_failure_restores_old_version(deployment):
    host = FakeHost()
    host.fail_route = 8899
    with pytest.raises(RuntimeError, match='reload failed'):
        run_switch(deployment, host)
    assert host.canonical == '0.4.5' and host.route_port == 8899


def test_long_request_prevents_canonical_replacement_and_retains_bridge(deployment):
    host = FakeHost()
    host.fail_drain = True
    with pytest.raises(RuntimeError, match='long request'):
        run_switch(deployment, host)
    assert not any(event[0] == 'recreate' for event in host.events)
    assert host.route_port == 8899 and host.candidate is not None
    record = json.loads((deployment / 'updates/web-handoff.json').read_text())
    assert record['phase'] == 'restored' and record['cleanup_pending']
    host.fail_drain = False
    switcher.recover(deployment, host.recreate, lambda version: host.ready(8899, version), host=host)
    assert host.candidate is None


@pytest.mark.parametrize('phase', ['preparing', 'candidate_ready', 'candidate_routed', 'canonical_replacing', 'canonical_ready', 'restoring'])
def test_restart_restores_interrupted_operation(deployment, phase):
    host = FakeHost()
    host.canonical = '0.4.6' if phase in ('canonical_ready', 'restoring') else '0.4.5'
    host.route_port = 18999
    host.candidate = 'candidate-one'
    switcher._journal(deployment, {'phase': phase, 'candidate': host.candidate, 'operation_id': 'one',
                                  'image': NEW, 'version': '0.4.6', 'previous_image': OLD, 'previous_version': '0.4.5'})
    result = switcher.recover(deployment, host.recreate, lambda version: host.ready(8899, version), host=host)
    assert result['phase'] == 'restored'
    assert host.route_port == 8899 and host.canonical == '0.4.5' and host.candidate is None


def test_candidate_retains_resources_but_not_original_service_identity():
    source = {'Id': 'old-container', 'Config': {'User': 'spark', 'Env': ['SPARK_WEB_PORT=8899'],
               'Cmd': ['python', '-m', 'spark_console.web.app'],
               'Labels': {'com.docker.compose.service': 'spark-web', 'com.docker.compose.project': 'dyfire'}},
              'HostConfig': {'Binds': ['/opt/dyfire/data:/data:rw'], 'ReadonlyRootfs': True,
                             'Memory': 256 * 1024 ** 2, 'NanoCpus': 500000000, 'Init': True,
                             'Privileged': False, 'PortBindings': {'8899/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '8899'}]}},
              'NetworkSettings': {'Networks': {'dyfire_spark-private': {'Aliases': ['spark-web'], 'IPAddress': '172.18.0.2'}}}}
    result = switcher.candidate_config(source, NEW, 'one')
    assert result['HostConfig']['Memory'] == 256 * 1024 ** 2
    assert result['HostConfig']['PortBindings'] == {'8899/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18999'}]}
    assert result['NetworkingConfig'] == {'EndpointsConfig': {'dyfire_spark-private': {}}}
    assert result['Labels'] == {switcher.LABEL: 'one', 'cn.dyfire.web-source': 'old-container'}
    assert result['HostConfig']['ReadonlyRootfs'] and result['HostConfig']['Init']
    assert result['Cmd'] == ['python', '-c', switcher.ENTRYPOINT]
    assert 'create_schema(' not in switcher.ENTRYPOINT
    assert result['Env'] == source['Config']['Env']


@pytest.mark.parametrize('service', ['spark-auth', 'spark-worker', 'spark-monitor'])
def test_never_clones_browser_or_monitor_service(service):
    with pytest.raises(ValueError, match='canonical Web'):
        switcher.candidate_config({'Config': {'Labels': {'com.docker.compose.service': service}}}, NEW, 'one')


def test_migration_only_changes_dyfire_proxy_destination():
    source = 'server {\n server_name dyfire.cn dyfire-origin.internal;\n allow 189.24.79.185;\n deny all;\n location / { proxy_pass http://127.0.0.1:8899; proxy_set_header Host dyfire.cn; }\n}'
    migrated = migrated_vhost(source)
    assert migrated == source.replace('http://127.0.0.1:8899', 'http://dyfire_web_active')
    assert migrated_vhost(migrated) == migrated
    with pytest.raises(ValueError, match='dedicated'):
        migrated_vhost(source.replace('dyfire.cn dyfire-origin.internal', 'www.chichiapi.cn'))
    with pytest.raises(ValueError, match='dedicated'):
        migrated_vhost(source.replace('dyfire.cn dyfire-origin.internal', 'dyfire.cn www.chichiapi.cn'))


def test_uninstalled_handoff_does_not_touch_host(tmp_path):
    host = FakeHost()
    with pytest.raises(RuntimeError, match='not been installed'):
        run_switch(tmp_path, host)
    assert host.events == []


def test_route_waits_for_new_workers_and_drain_waits_for_old_workers(deployment, monkeypatch):
    host = switcher.Host(deployment, 'dyfire', lambda **_: None)
    snapshots = iter([{10: 'nginx: worker process'}, {10: 'nginx: worker process'},
                      {10: 'nginx: worker process is shutting down', 20: 'nginx: worker process'},
                      {10: 'nginx: worker process is shutting down', 20: 'nginx: worker process'},
                      {20: 'nginx: worker process'}])
    monkeypatch.setattr(host, 'workers', lambda: next(snapshots))
    commands = []
    monkeypatch.setattr(switcher.subprocess, 'run', lambda command, **kwargs: commands.append(command))
    pauses = []
    monkeypatch.setattr(switcher.time, 'sleep', pauses.append)
    host.route(18999)
    assert host.draining == {10}
    host.drain()
    assert not host.draining
    assert pauses == [0.2, 1]
    assert commands == [['systemctl', 'start', 'dyfire-web-route.service']]


def test_invalid_nginx_reload_restores_the_previous_upstream(deployment, monkeypatch):
    host = switcher.Host(deployment, 'dyfire', lambda **_: None)
    monkeypatch.setattr(host, 'workers', lambda: {10: 'nginx: worker process'})
    calls = []
    def reload(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise RuntimeError('nginx configuration rejected')
    monkeypatch.setattr(switcher.subprocess, 'run', reload)
    with pytest.raises(RuntimeError, match='configuration rejected'):
        host.route(18999)
    assert (deployment / 'deploy/web-upstream.conf').read_text() == switcher.upstream(8899)
    assert len(calls) == 2


def test_candidate_cleanup_rejects_an_unrelated_container(deployment, monkeypatch):
    host = switcher.Host(deployment, 'dyfire', lambda **_: None)
    calls = []
    def engine(method, path, **kwargs):
        calls.append((method, path))
        return {'Config': {'Labels': {switcher.LABEL: 'different-operation'}}}
    monkeypatch.setattr(switcher, '_docker', engine)
    with pytest.raises(RuntimeError, match='unrelated'):
        host.remove('candidate', 'our-operation')
    assert len(calls) == 1 and calls[0][0] == 'GET'


def test_source_configuration_is_not_mutated():
    source = {'Id': 'old', 'Config': {'Cmd': ['python', '-m', 'spark_console.web.app'],
               'Labels': {'com.docker.compose.service': 'spark-web'}, 'Env': ['SECRET=not-printed']},
              'HostConfig': {'Binds': ['/data:/data']},
              'NetworkSettings': {'Networks': {'private': {'Aliases': ['spark-web']}}}}
    before = json.dumps(source, sort_keys=True)
    switcher.candidate_config(source, NEW, 'one')
    assert json.dumps(source, sort_keys=True) == before


@pytest.mark.parametrize('phase', ['complete', 'restored'])
def test_idle_maintenance_cleans_terminal_crash_without_pending_flag(deployment, phase):
    host = FakeHost()
    host.candidate = 'candidate-one'
    switcher._journal(deployment, {'phase': phase, 'candidate': host.candidate, 'operation_id': 'one'})
    assert switcher.maintenance(deployment, host=host) is True
    assert host.events == [('drain',), ('remove',)]
    record = json.loads((deployment / 'updates/web-handoff.json').read_text())
    assert record['cleanup_complete'] and 'cleanup_pending' not in record
    # Repeated idle checks do not call Docker or nginx again.
    assert switcher.maintenance(deployment, host=host) is True
    assert host.events == [('drain',), ('remove',)]


def test_idle_maintenance_never_waits_for_long_connections(deployment, monkeypatch):
    host = switcher.Host(deployment, 'dyfire', lambda **_: None)
    monkeypatch.setattr(host, 'workers', lambda: {42: 'nginx: worker process is shutting down'})
    monkeypatch.setattr(switcher.time, 'sleep', lambda _seconds: pytest.fail('maintenance must not wait'))
    monkeypatch.setattr(host, 'remove', lambda *_args: pytest.fail('active bridge must remain'))
    record = {'phase': 'complete', 'candidate': 'candidate-one', 'operation_id': 'one', 'cleanup_pending': True}
    switcher._journal(deployment, record)
    assert switcher.maintenance(deployment, host=host) is False
    assert json.loads((deployment / 'updates/web-handoff.json').read_text()) == record


@pytest.mark.parametrize('phase', ['preparing', 'candidate_routed', 'canonical_replacing', 'restoring'])
def test_idle_maintenance_does_not_interfere_with_unfinished_update(deployment, phase):
    host = FakeHost()
    switcher._journal(deployment, {'phase': phase, 'candidate': 'candidate-one', 'operation_id': 'one'})
    assert switcher.maintenance(deployment, host=host) is None
    assert host.events == []


def test_idle_maintenance_keeps_candidate_if_upstream_still_uses_it(deployment):
    host = FakeHost()
    (deployment / 'deploy/web-upstream.conf').write_text(switcher.upstream(18999))
    switcher._journal(deployment, {'phase': 'complete', 'candidate': 'candidate-one', 'operation_id': 'one'})
    assert switcher.maintenance(deployment, host=host) is False
    assert host.events == []


def test_idle_maintenance_preserves_retry_when_cleanup_fails(deployment):
    host = FakeHost()
    def failed_remove(*_args):
        raise RuntimeError('docker unavailable')
    host.remove = failed_remove
    switcher._journal(deployment, {'phase': 'restored', 'candidate': 'candidate-one', 'operation_id': 'one', 'cleanup_pending': True})
    assert switcher.maintenance(deployment, host=host) is False
    record = json.loads((deployment / 'updates/web-handoff.json').read_text())
    assert record['cleanup_pending'] and not record.get('cleanup_complete')
