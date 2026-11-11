"""Health-checked, recoverable Web handoff without restarting browser workers.

The temporary candidate bridges replacement of the canonical Compose Web
container. The final service name and port remain unchanged. Only the two
Dyfire origin vhosts use the managed upstream; installation is explicit.
"""
from __future__ import annotations

import copy
import hashlib
import http.client
import json
from pathlib import Path
import re
import socket
import subprocess
import time
from urllib.parse import quote
import urllib.request


CANONICAL_PORT = 8899
CANDIDATE_PORT = 18999
LABEL = 'cn.dyfire.web-candidate'
ENTRYPOINT = '''import os
from sqlalchemy import text
from spark_console.config import Settings
from spark_console.db import create_engine_for, SQLITE_SCHEMA_VERSION
from spark_console.web.app import create_app
import uvicorn
settings=Settings.from_env(os.environ)
engine=create_engine_for(settings)
if engine.dialect.name == 'sqlite':
 with engine.connect() as conn:
  actual=conn.execute(text('SELECT MAX(version) FROM schema_migrations')).scalar()
  if actual != SQLITE_SCHEMA_VERSION:
   raise RuntimeError('Candidate requires the already-installed database schema')
# A candidate must never run create_schema or a sender/collector loop.
uvicorn.run(create_app(settings,engine),host=settings.web_bind,port=settings.web_port,proxy_headers=False)
'''


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        stream.write(data)
        stream.flush()
        import os
        os.fsync(stream.fileno())
    temporary.chmod(0o644)
    temporary.replace(path)


def upstream(port):
    if port not in (CANONICAL_PORT, CANDIDATE_PORT):
        raise ValueError('Unsupported Web port')
    return 'upstream dyfire_web_active {\n    server 127.0.0.1:' + str(port) + ';\n    keepalive 16;\n}\n'


def enabled(root):
    root = Path(root)
    marker = root / 'deploy/web-switch.json'
    if not marker.is_file():
        return False
    config = json.loads(marker.read_text())
    if config != {'version': 1, 'canonical_port': CANONICAL_PORT, 'candidate_port': CANDIDATE_PORT}:
        raise ValueError('Invalid Web handoff configuration')
    if not (root / 'deploy/web-upstream.conf').is_file():
        raise ValueError('Web handoff upstream is missing')
    return True


class DockerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/run/docker.sock')


def _docker(method, path, body=None, allowed=(200, 201, 204)):
    client = DockerConnection('docker', timeout=40)
    try:
        payload = None if body is None else json.dumps(body).encode()
        client.request(method, path, body=payload, headers={'Content-Type': 'application/json'})
        response = client.getresponse()
        raw = response.read(4 * 1024 * 1024)
        if response.status not in allowed:
            # Docker responses can contain environment/mount details. Do not
            # expose its response text through administrator progress messages.
            raise RuntimeError('Web container operation failed (' + str(response.status) + ')')
        return json.loads(raw) if raw and response.status != 404 else None
    finally:
        client.close()


def _candidate_name(project, operation_id):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', project):
        raise ValueError('Invalid Compose project')
    if not operation_id or len(operation_id) > 200:
        raise ValueError('A bounded update operation ID is required')
    return project + '-web-candidate-' + hashlib.sha256(operation_id.encode()).hexdigest()[:12]


def candidate_config(source, image, operation_id):
    """Clone only Web settings; never copy its service DNS alias or fixed IP."""
    if not re.fullmatch(r'[a-zA-Z0-9._:/-]+@sha256:[a-f0-9]{64}', image):
        raise ValueError('Candidate image must be pinned by digest')
    config = source.get('Config') or {}
    labels = config.get('Labels') or {}
    if labels.get('com.docker.compose.service') != 'spark-web':
        raise ValueError('Candidate source is not the canonical Web service')
    if config.get('Cmd') not in (None, ['python', '-m', 'spark_console.web.app']):
        raise ValueError('Canonical Web command is not supported for handoff')
    allowed = ('User', 'Env', 'WorkingDir', 'Healthcheck', 'StopSignal')
    result = {key: copy.deepcopy(config[key]) for key in allowed if key in config}
    result.update(Image=image, Entrypoint=[], Cmd=['python', '-c', ENTRYPOINT],
                  ExposedPorts={'8899/tcp': {}},
                  Labels={LABEL: operation_id, 'cn.dyfire.web-source': source['Id']})
    original = source.get('HostConfig') or {}
    allowed_host = (
        'Binds', 'Mounts', 'LogConfig', 'ReadonlyRootfs', 'SecurityOpt', 'CapAdd',
        'CapDrop', 'Memory', 'NanoCpus', 'CpuShares', 'CpuPeriod', 'CpuQuota',
        'MemorySwap', 'MemoryReservation', 'PidsLimit', 'Ulimits', 'Init',
        'Tmpfs', 'ShmSize', 'Dns', 'DnsOptions', 'DnsSearch', 'ExtraHosts',
    )
    host = {key: copy.deepcopy(original[key]) for key in allowed_host if key in original}
    host.update(PortBindings={'8899/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(CANDIDATE_PORT)}]},
                RestartPolicy={'Name': 'unless-stopped'}, AutoRemove=False,
                Privileged=False, PublishAllPorts=False)
    networks = (source.get('NetworkSettings') or {}).get('Networks') or {}
    if len(networks) != 1:
        raise ValueError('Canonical Web must have one dedicated application network')
    network = next(iter(networks))
    host['NetworkMode'] = network
    result['HostConfig'] = host
    result['NetworkingConfig'] = {'EndpointsConfig': {network: {}}}
    return result


class Host:
    def __init__(self, root, project, publish):
        self.root = Path(root)
        self.project = project
        self.publish = publish
        self.draining = set()

    def workers(self):
        result = subprocess.run(['ps', '-eo', 'pid=,args='], capture_output=True, text=True, check=True, timeout=10)
        values = {}
        for line in result.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[1].startswith('nginx: worker process'):
                values[int(parts[0])] = parts[1]
        return values

    def source(self):
        command = ['docker', 'compose', '--project-name', self.project, '--env-file', str(self.root / '.env.console'),
                   '--file', str(self.root / 'compose.console.yml'), 'ps', '-q', 'spark-web']
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
        ids = result.stdout.split()
        if len(ids) != 1:
            raise RuntimeError('Cannot identify the canonical Web container')
        return _docker('GET', '/containers/' + quote(ids[0], safe='') + '/json')

    def create(self, name, image, operation_id):
        existing = _docker('GET', '/containers/' + quote(name, safe='') + '/json', allowed=(200, 404))
        if existing:
            if (existing.get('Config', {}).get('Labels', {}).get(LABEL) != operation_id
                    or existing.get('Config', {}).get('Image') != image):
                raise RuntimeError('Candidate container does not belong to this update')
        else:
            _docker('POST', '/containers/create?name=' + quote(name, safe=''), candidate_config(self.source(), image, operation_id))
        _docker('POST', '/containers/' + quote(name, safe='') + '/start', allowed=(204, 304))

    def remove(self, name, operation_id):
        existing = _docker('GET', '/containers/' + quote(name, safe='') + '/json', allowed=(200, 404))
        if existing is None:
            return
        if existing.get('Config', {}).get('Labels', {}).get(LABEL) != operation_id:
            raise RuntimeError('Refusing to remove an unrelated container')
        _docker('POST', '/containers/' + quote(name, safe='') + '/stop?t=30', allowed=(204, 304))
        _docker('DELETE', '/containers/' + quote(name, safe=''), allowed=(204,))

    def ready(self, port, version, seconds=90):
        deadline = time.monotonic() + seconds
        while True:
            try:
                values = []
                for path in ('health/ready', 'api/version'):
                    with urllib.request.urlopen('http://127.0.0.1:' + str(port) + '/' + path, timeout=3) as response:
                        values.append(json.load(response))
                if values[0].get('status') == 'ready' and values[1].get('version') == version:
                    return
            except (OSError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('Web candidate did not pass readiness and version checks')
            self.publish()
            time.sleep(1)

    def route(self, port):
        path = self.root / 'deploy/web-upstream.conf'
        before = path.read_text()
        target = upstream(port)
        workers_before = set(self.workers())
        if not workers_before:
            raise RuntimeError('Nginx has no active workers')
        # The updater's systemd sandbox can only write /opt/dyfire. A fixed
        # oneshot unit validates then reloads nginx outside that sandbox.
        _write(path, target)
        try:
            subprocess.run(['systemctl', 'start', 'dyfire-web-route.service'], check=True, timeout=60,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # systemctl reload returns after signaling nginx. Wait until the
            # new generation exists before draining or replacing its backend.
            deadline = time.monotonic() + 15
            while not set(self.workers()) - workers_before:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Nginx did not activate the new Web upstream')
                self.publish()
                time.sleep(0.2)
            self.draining.update(workers_before)
        except Exception:
            _write(path, before)
            subprocess.run(['systemctl', 'start', 'dyfire-web-route.service'], check=True, timeout=60,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            raise

    def drain(self, seconds=120):
        deadline = time.monotonic() + seconds
        while True:
            workers = self.workers()
            pending = self.draining.intersection(workers)
            pending.update(pid for pid, command in workers.items() if 'shutting down' in command)
            if not pending:
                self.draining.clear()
                return
            if time.monotonic() >= deadline:
                raise RuntimeError('Existing Web requests are still draining; active service retained')
            self.publish()
            time.sleep(1)


def _journal(root, data):
    _write(Path(root) / 'updates/web-handoff.json', json.dumps(data, ensure_ascii=False))


def _rollback(root, data, host, recreate, ready, publish):
    data['phase'] = 'restoring'
    _journal(root, data)
    publish(phase='web_restoring', message='正在恢复原 Web 版本')
    try:
        host.ready(CANONICAL_PORT, data['previous_version'], seconds=1)
    except Exception:
        # While the canonical container is restored, the candidate remains
        # available if nginx already routed to it.
        recreate(data['previous_image'])
        ready(data['previous_version'])
    host.route(CANONICAL_PORT)
    host.ready(CANONICAL_PORT, data['previous_version'])
    data['phase'] = 'restored'
    _journal(root, data)
    try:
        host.drain()
        host.remove(data['candidate'], data['operation_id'])
        data['cleanup_complete'] = True
        data.pop('cleanup_pending', None)
    except Exception:
        # Old nginx workers may still have in-flight requests. Preserve their
        # candidate rather than turning graceful rollback into dropped requests.
        data['cleanup_pending'] = True
    _journal(root, data)


def maintenance(root, *, project='douyin-spark-console', host=None):
    """Reap a terminal bridge only when all old nginx workers have exited.

    This is an idle-loop check, not recovery: it never changes the upstream,
    recreates Web, or waits for an in-flight connection. A terminal journal
    without either cleanup flag can result from a crash immediately after
    committing the switch, so it also needs this check.

    Return True after confirmed cleanup, False when a later check is needed,
    or None when there is no terminal handoff to maintain. Call serially with
    update operations under the updater's process lock.
    """
    path = Path(root) / 'updates/web-handoff.json'
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    if data.get('phase') not in ('complete', 'restored'):
        return None
    if data.get('cleanup_complete'):
        return True
    if (Path(root) / 'deploy/web-upstream.conf').read_text() != upstream(CANONICAL_PORT):
        # A manual or incomplete route change must not lose its active backend.
        return False
    host = host or Host(root, project, lambda **_: None)
    try:
        host.drain(seconds=0)
        host.remove(data['candidate'], data['operation_id'])
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False
    data['cleanup_complete'] = True
    data.pop('cleanup_pending', None)
    _journal(root, data)
    return True


def recover(root, recreate, ready, publish=lambda **_: None, *, project='douyin-spark-console', host=None):
    """Restore an interrupted handoff before an updater retries its operation."""
    path = Path(root) / 'updates/web-handoff.json'
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    host = host or Host(root, project, publish)
    if data['phase'] in ('complete', 'restored'):
        if not data.get('cleanup_complete'):
            host.drain()
            host.remove(data['candidate'], data['operation_id'])
            data.pop('cleanup_pending', None)
            data['cleanup_complete'] = True
            _journal(root, data)
        return data
    _rollback(root, data, host, recreate, ready, publish)
    return data


def switch(root, image, version, operation_id, recreate, ready, publish=lambda **_: None,
           *, previous_image, previous_version, project='douyin-spark-console', host=None):
    """Recreate only canonical Web via callbacks, with a serving bridge.

    Call only after release/component/schema validation and image pull. The
    ``recreate(image)`` callback must use Compose --no-deps for spark-web only;
    ``ready(version)`` verifies canonical health, version and pinned image.
    Installation records are committed by the caller only after return.
    """
    if not enabled(root):
        raise RuntimeError('Web handoff has not been installed on this host')
    host = host or Host(root, project, publish)
    recover(root, recreate, ready, publish, project=project, host=host)
    name = _candidate_name(project, operation_id)
    data = {'phase': 'preparing', 'operation_id': operation_id, 'candidate': name,
            'image': image, 'version': version, 'previous_image': previous_image,
            'previous_version': previous_version}
    _journal(root, data)
    try:
        publish(phase='web_candidate', message='正在检查新 Web 版本，原页面保持可用')
        host.create(name, image, operation_id)
        host.ready(CANDIDATE_PORT, version)
        data['phase'] = 'candidate_ready'
        _journal(root, data)
        host.route(CANDIDATE_PORT)
        data['phase'] = 'candidate_routed'
        _journal(root, data)
        host.drain()
        data['phase'] = 'canonical_replacing'
        _journal(root, data)
        publish(phase='web_installing', message='正在切换 Web 服务，发送服务保持运行')
        recreate(image)
        ready(version)
        data['phase'] = 'canonical_ready'
        _journal(root, data)
        host.route(CANONICAL_PORT)
        host.ready(CANONICAL_PORT, version)
        data['phase'] = 'complete'
        _journal(root, data)
        try:
            host.drain()
            host.remove(name, operation_id)
            data['cleanup_complete'] = True
            data.pop('cleanup_pending', None)
            _journal(root, data)
        except Exception:
            data['cleanup_pending'] = True
            _journal(root, data)
        return data
    except Exception:
        _rollback(root, data, host, recreate, ready, publish)
        raise
