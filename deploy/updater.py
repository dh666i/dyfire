"""Run on the host under systemd; no Docker socket is exposed to the web service.

Only immutable images from this repository can be installed. Releases with a
different data schema require a separately reviewed migration, not auto-update.
"""
import base64
from contextlib import closing
try:
    import fcntl
except ImportError:  # pragma: no cover - host service runs on Linux
    fcntl = None
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.request

ROOT = Path(os.environ.get('DYFIRE_DEPLOY_DIR', '/opt/dyfire')).resolve()
DATA_DIR = Path(os.environ.get('DYFIRE_DATA_DIR', ROOT / 'data')).resolve()
QUEUE = ROOT / 'updates'
STATE = {}
REPOSITORY = 'dh666i/dyfire'
IMAGE = 'ghcr.io/dh666i/dyfire'
GITHUB_API = 'https://api.github.com/repos/' + REPOSITORY
SERVICES = ['spark-web', 'spark-auth', 'spark-worker', 'spark-monitor']
WEB_SERVICES = ['spark-web']
COMPOSE_PROJECT = os.environ.get('DYFIRE_COMPOSE_PROJECT', 'dyfire')
RELEASE_CACHE_SECONDS = 20 * 60
COMPOSE_OPERATION_TIMEOUT_SECONDS = 10 * 60
IMAGE_PULL_TIMEOUT_SECONDS = 60 * 60
_RELEASE_CACHE = {}


def _compose_base():
    """Return the read-only Compose command for the canonical project."""
    return [
        'docker', 'compose', '--project-name', COMPOSE_PROJECT,
        '--env-file', str(ROOT / '.env.console'), '--file',
        str(ROOT / 'compose.console.yml'),
    ]


def _container_for_service(service):
    """Resolve exactly one running container for a Compose service.

    This deliberately uses ``docker compose ps`` instead of a broad Docker
    label query. An old Compose project must not be mistaken for the service
    managed by this updater.
    """
    result = subprocess.run(
        [*_compose_base(), 'ps', '-q', service], cwd=ROOT,
        capture_output=True, text=True, timeout=30, check=True,
    )
    ids = [value.strip() for value in result.stdout.splitlines() if value.strip()]
    if len(ids) != 1:
        raise ValueError(f'无法唯一定位 {service} 运行容器')
    return ids[0]


def _inspect_container(container_id):
    result = subprocess.run(
        ['docker', 'inspect', container_id], capture_output=True,
        text=True, timeout=30, check=True,
    )
    values = json.loads(result.stdout)
    if len(values) != 1 or not isinstance(values[0], dict):
        raise ValueError('容器检查结果无效')
    item = values[0]
    state = item.get('State') or {}
    config = item.get('Config') or {}
    image = str(config.get('Image') or '')
    repo_digests = [str(value) for value in (item.get('RepoDigests') or []) if value]
    return {
        'container_id': container_id[:12],
        'running': bool(state.get('Running')),
        'image': image,
        'repo_digests': repo_digests,
    }


def _image_matches(actual, expected):
    """Match a pinned image against Docker's Config.Image or RepoDigests."""
    if not re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', expected or ''):
        return False
    values = [actual.get('image', ''), *(actual.get('repo_digests') or [])]
    return expected in values


def runtime_report(expected_version=None, expected_image=None, services=None):
    """Read the canonical Compose runtime without changing host state.

    The report is safe to persist in the admin-only update status. ``drift``
    means the recorded version/image is not what the running containers and
    local API report; it also covers a missing or stopped container.
    """
    services = list(services or SERVICES)
    report = {
        'expected_version': expected_version,
        'expected_image': expected_image,
        'api_version': None,
        'services': {},
        'drift': False,
    }
    try:
        with urllib.request.urlopen('http://127.0.0.1:8899/api/version', timeout=3) as response:
            payload = json.load(response)
        report['api_version'] = payload.get('version') if isinstance(payload, dict) else None
    except (OSError, ValueError, KeyError):
        report['api_error'] = '无法读取本机 API 版本'

    for service in services:
        try:
            item = _inspect_container(_container_for_service(service))
        except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            item = {'running': False, 'error': type(error).__name__.lower()}
        report['services'][service] = item

    api_expected_version = (
        expected_version.get('spark-web')
        if isinstance(expected_version, dict)
        else expected_version
    )
    if api_expected_version is not None and report['api_version'] != api_expected_version:
        report['drift'] = True
    for service, item in report['services'].items():
        expected = (
            expected_image.get(service)
            if isinstance(expected_image, dict)
            else expected_image
        )
        if not item.get('running') or (expected and not _image_matches(item, expected)):
            report['drift'] = True
    return report


def verify_runtime(expected_version, expected_image, services=None):
    """Strict post-switch verification; raises before install records are written."""
    report = runtime_report(expected_version, expected_image, services)
    if report['drift']:
        raise RuntimeError('运行容器版本或镜像与部署记录不一致')
    return report


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('发行版本格式无效')
    return tuple(map(int, value.split('.')))


def _manifest(body):
    """Read the release manifest embedded by the build pipeline."""
    match = re.search(r'<!--\s*dyfire-manifest\s+(\{.*?\})\s*-->', body or '', re.S)
    if not match:
        return {}
    try:
        value = json.loads(match.group(1))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _release_from_payload(release, resolve_registry=True):
    """Normalize GitHub's release response into the updater's stable contract."""
    tag = str(release.get('tag_name') or '').removeprefix('v')
    if release.get('draft') or release.get('prerelease') or not re.fullmatch(r'\d+\.\d+\.\d+', tag):
        return {}
    body = release.get('body') or ''
    manifest = _manifest(body)
    manifest_version = str(manifest.get('version') or '').removeprefix('v')
    if manifest_version and manifest_version != tag:
        return {}
    image = manifest.get('image') or ''
    digest = image.removeprefix(IMAGE + '@') if image.startswith(IMAGE + '@') else ''
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest) and resolve_registry:
        try:
            _, headers = registry('/v2/' + REPOSITORY + '/manifests/' + tag)
            digest = headers.get('Docker-Content-Digest', '')
        except Exception:
            digest = ''
    normalized_image = IMAGE + '@' + digest if re.fullmatch(r'sha256:[a-f0-9]{64}', digest) else ''
    schema = manifest.get('schema', 1)
    if not isinstance(schema, int) or schema < 1:
        schema = 1
    return {
        'version': tag,
        'schema': schema,
        'image': normalized_image,
        'release_version': tag,
        'release_name': release.get('name') or ('Dyfire ' + tag),
        'release_body': body[:12000],
        'release_url': release.get('html_url') or '',
        'release_published_at': release.get('published_at') or '',
        'release_image': normalized_image,
    }


def write_state(**values):
    STATE.update(values, heartbeat=time.time())
    temp = QUEUE / 'status.tmp'
    temp.write_text(json.dumps(STATE, ensure_ascii=False), encoding='utf-8')
    os.chmod(temp, 0o644)
    temp.replace(QUEUE / 'status.json')


def registry_token():
    token = Path(os.environ['DYFIRE_GITHUB_TOKEN_FILE']).read_text().strip()
    if not token:
        raise ValueError('镜像读取凭据为空')
    credential = base64.b64encode(('dh666i:' + token).encode()).decode()
    req = urllib.request.Request(
        'https://ghcr.io/token?service=ghcr.io&scope=repository:' + REPOSITORY + ':pull',
        headers={'Authorization': 'Basic ' + credential, 'User-Agent': 'dyfire-updater'})
    with urllib.request.urlopen(req, timeout=20) as response:
        value = json.load(response).get('token')
    if not isinstance(value, str) or not value:
        raise ValueError('无法获取镜像读取令牌')
    return value


def registry(path, accept='application/vnd.oci.image.index.v1+json'):
    req = urllib.request.Request('https://ghcr.io' + path, headers={
        'Authorization': 'Bearer ' + registry_token(), 'Accept': accept,
        'User-Agent': 'dyfire-updater'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response), response.headers


def github_token():
    token_file = os.environ.get('DYFIRE_GITHUB_TOKEN_FILE')
    if not token_file:
        return ''
    try:
        return Path(token_file).read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def github_api(path):
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'dyfire-updater'}
    token = github_token()
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(GITHUB_API + path, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _cached_github(path, ttl=RELEASE_CACHE_SECONDS, force=False):
    """Cache read-only release metadata so a UI refresh cannot rate-limit GitHub."""
    now = time.time()
    cached = _RELEASE_CACHE.get(path)
    if not force and cached and now - cached[0] < ttl:
        return cached[1]
    value = github_api(path)
    _RELEASE_CACHE[path] = (now, value)
    return value


def release_metadata(version_value=None):
    endpoint = '/releases/latest' if not version_value else '/releases/tags/v' + version_value
    try:
        release = _cached_github(endpoint)
    except Exception:
        return {}
    return _release_from_payload(release)


def rollback_versions(current_version, limit=3):
    try:
        releases = _cached_github('/releases?per_page=15')
    except Exception:
        return []
    result = []
    for release in releases if isinstance(releases, list) else []:
        if release.get('draft') or release.get('prerelease'):
            continue
        # A release may predate the manifest annotation; resolve its immutable
        # registry digest before offering it as a rollback candidate.
        item = _release_from_payload(release, resolve_registry=True)
        candidate = item.get('release_version')
        if not candidate or version(candidate) >= version(current_version):
            continue
        image = item.get('release_image')
        if not re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', image or ''):
            continue
        result.append({'version': candidate, 'schema': item.get('schema', 1),
                       'url': item.get('release_url', ''),
                       'published_at': item.get('release_published_at', ''), 'image': image})
        if len(result) >= limit:
            break
    return sorted(result, key=lambda item: version(item['version']), reverse=True)


def latest(force=False):
    result = _release_from_payload(_cached_github('/releases/latest', force=force))
    if not result or not result.get('image'):
        raise ValueError('正式 Release 没有可验证的镜像摘要')
    return result


def compose(image, *args):
    env = dict(os.environ, SPARK_IMAGE_NAME=image, COMPOSE_PROJECT_NAME=COMPOSE_PROJECT)
    process = subprocess.Popen(['docker', 'compose', '--project-name', COMPOSE_PROJECT, '--env-file', str(ROOT / '.env.console'),
                    '--file', str(ROOT / 'compose.console.yml'), *args], cwd=ROOT, env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Registry pulls can be much slower than a local stop/start on a small
    # server. Keep the normal container operation bound tight, but allow a
    # fixed digest pull enough time to finish without interrupting it halfway.
    timeout = IMAGE_PULL_TIMEOUT_SECONDS if args and args[0] == 'pull' else COMPOSE_OPERATION_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        write_state()
        if time.monotonic() > deadline:
            process.kill();process.wait()
            raise TimeoutError('容器操作超时')
        time.sleep(2)
    if process.returncode:
        raise RuntimeError('容器操作失败')


def resolve_data_dir():
    """Resolve the live bind mount for the application's SQLite data.

    The environment value is only a fallback for test/initial setup. On a live
    deployment, inspect the Compose-managed web container so an obsolete
    named-volume path can never make an update snapshot the wrong directory.
    """
    result = subprocess.run(
        ['docker', 'compose', '--project-name', COMPOSE_PROJECT,
         '--env-file', str(ROOT / '.env.console'), '--file',
         str(ROOT / 'compose.console.yml'), 'ps', '-q', 'spark-web'],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=True,
    )
    container_id = result.stdout.strip().splitlines()
    if len(container_id) != 1 or not container_id[0]:
        raise ValueError('无法唯一定位运行中的 Web 容器')
    inspected = subprocess.run(
        ['docker', 'inspect', container_id[0]],
        capture_output=True, text=True, timeout=30, check=True,
    )
    containers = json.loads(inspected.stdout)
    if len(containers) != 1:
        raise ValueError('Web 容器检查结果无效')
    mounts = [mount for mount in containers[0].get('Mounts', [])
              if mount.get('Destination') == '/data']
    if len(mounts) != 1 or mounts[0].get('Type') != 'bind':
        raise ValueError('Web 容器未使用标准 /data 主机绑定目录')
    raw_source = Path(mounts[0].get('Source', ''))
    if raw_source.is_symlink():
        raise ValueError('Web 数据目录不能是符号链接')
    source = raw_source.resolve()
    expected = ROOT.resolve()
    if source != expected and expected not in source.parents:
        raise ValueError('Web 数据目录不在标准部署目录内')
    if not (source / 'spark.db').is_file():
        raise ValueError('标准 Web 数据目录或 spark.db 不存在')
    return source


def assert_no_active_operations(data_dir):
    """Refuse an image switch while sending or login recovery is in flight.

    Both install and rollback stop the worker and auth services.  Keeping this
    guard in one function prevents rollback from accidentally bypassing the
    same protection as a normal update.  The database is opened read-only so
    the updater cannot mutate production task, account, or login state while
    it is deciding whether a switch is safe.
    """
    database = Path(data_dir) / 'spark.db'
    last_error = None
    # A live SQLite WAL can briefly be busy while the worker commits a
    # heartbeat or run transition. Retry the read-only guard so a transient
    # checkpoint race does not abort an otherwise safe image update. A real
    # malformed database still fails after the bounded retry window.
    # A busy worker may hold a WAL checkpoint across several seconds. Keep
    # the guard conservative, but allow the read-only check enough time to
    # observe a stable snapshot before refusing an otherwise safe update.
    for attempt in range(20):
        try:
            with closing(sqlite3.connect(
                f'{database.as_uri()}?mode=ro', uri=True, timeout=5
            )) as db:
                db.execute('PRAGMA busy_timeout=5000')
                active = db.execute(
                    "SELECT count(*) FROM task_runs WHERE status='running'"
                ).fetchone()[0]
                active += db.execute(
                    "SELECT count(*) FROM douyin_login_sessions "
                    "WHERE status NOT IN ('succeeded','failed','expired','cancelled')"
                ).fetchone()[0]
            break
        except sqlite3.DatabaseError as error:
            last_error = error
            if attempt == 19:
                raise
            time.sleep(1)
    else:  # pragma: no cover - the loop either breaks or raises
        raise last_error
    if active:
        raise ValueError('当前仍有扫码或发送操作，请完成后再更新')


def ready(expected):
    for _ in range(45):
        write_state()
        try:
            with urllib.request.urlopen('http://127.0.0.1:8899/health/ready', timeout=3) as response:
                health = json.load(response)
            with urllib.request.urlopen('http://127.0.0.1:8899/api/version', timeout=3) as response:
                actual = json.load(response)
            if actual['version'] == expected:
                return
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError('新版本健康检查未通过')


def install(target, operation_id=''):
    release = latest(force=True)  # Re-fetch from trusted origin; never trust status.json.
    current_file = ROOT / 'installed.json'
    current = json.loads(current_file.read_text())
    if release['version'] != target or version(target) <= version(current['version']):
        raise ValueError('版本已变化或不高于当前版本，请重新检查更新')
    if current.get('schema') != release['schema']:
        raise ValueError('数据结构不兼容')
    if not re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', current['image']):
        raise ValueError('当前安装记录不是本仓库的固定镜像')
    data_dir = resolve_data_dir()
    required = sum(p.stat().st_size for p in data_dir.rglob('*') if p.is_file() and not any(part in ('backups', 'snapshots') for part in p.relative_to(data_dir).parts))
    if shutil.disk_usage(ROOT).free < required + 2 * 1024**3:
        raise ValueError('备份空间不足，请清理磁盘后重试')
    assert_no_active_operations(data_dir)
    write_state(phase='pulling', message='正在下载版本', operation_id=operation_id)
    compose(release['image'], 'pull', *SERVICES)
    backup = ROOT / 'backups' / time.strftime('%Y%m%d-%H%M%S')
    backup.mkdir(parents=True, mode=0o700)
    write_state(phase='stopping', message='等待当前操作结束并停止服务')
    # Generous grace period; all data writers stop before backup.
    # Pulling can take a long time on a small host. Re-check immediately
    # before entering the recovery block so a newly active operation is
    # rejected without running the recovery ``compose up`` path.
    assert_no_active_operations(data_dir)
    try:
        compose(current['image'], 'stop', '-t', '180', *SERVICES)
        shutil.copytree(data_dir, backup / 'data', ignore=shutil.ignore_patterns('backups', 'snapshots'))
        shutil.copy2(current_file, backup / 'installed.json')
        web_file = ROOT / 'web-installed.json'
        if web_file.is_file():
            shutil.copy2(web_file, backup / 'web-installed.json')
        write_state(phase='installing', message='正在启动新版本')
        compose(release['image'], 'up', '-d', '--no-build', *SERVICES)
        ready(target)
        verify_runtime(target, release['image'], SERVICES)
        temp = ROOT / 'installed.tmp'
        temp.write_text(json.dumps(release))
        temp.replace(current_file)
        web_temp = ROOT / 'web-installed.tmp'
        web_temp.write_text(json.dumps(release))
        web_temp.replace(ROOT / 'web-installed.json')
        write_state(phase='complete', message='更新完成', installed=target)
    except Exception:
        write_state(phase='rollback', message='更新未通过检查，正在恢复原版本')
        # Same schema only: retain new run records to avoid replaying a send.
        # Never automatically overwrite the live database with an older backup.
        compose(current['image'], 'up', '-d', '--no-build', *SERVICES)
        ready(current['version'])
        verify_runtime(current['version'], current['image'], SERVICES)
        write_state(phase='failed', message='更新失败，已恢复原版本')
        raise


def install_web(target, operation_id=''):
    """Update only the React/API web container.

    Auth, worker and monitor keep their current image and process state. This
    is suitable for presentation/API-only releases; schema or worker changes
    must still use the full ``install`` path.
    """
    release = latest(force=True)
    current_file = ROOT / 'installed.json'
    current = json.loads(current_file.read_text())
    web_file = ROOT / 'web-installed.json'
    web_current = json.loads(web_file.read_text()) if web_file.is_file() else current
    if release['version'] != target or version(target) <= version(web_current['version']):
        raise ValueError('版本已变化或不高于当前版本，请重新检查更新')
    if current.get('schema') != release['schema'] or web_current.get('schema') != release['schema']:
        raise ValueError('数据结构不兼容')
    if not re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', web_current.get('image', '')):
        raise ValueError('当前 Web 安装记录不是本仓库的固定镜像')
    write_state(phase='web_pulling', message='正在下载 Web 版本', operation_id=operation_id)
    compose(release['image'], 'pull', *WEB_SERVICES)
    try:
        write_state(phase='web_installing', message='正在切换 Web 服务，发送服务保持运行')
        compose(release['image'], 'up', '-d', '--no-build', *WEB_SERVICES)
        ready(target)
        verify_runtime(target, release['image'], WEB_SERVICES)
        temp = ROOT / 'web-installed.tmp'
        temp.write_text(json.dumps(release))
        temp.replace(ROOT / 'web-installed.json')
        write_state(phase='web_complete', message='Web 更新完成，发送服务未重启', web_installed=target)
    except Exception:
        write_state(phase='web_failed', message='Web 更新未通过健康检查')
        raise


def rollback(target='', operation_id=''):
    current_file = ROOT / 'installed.json'
    current = json.loads(current_file.read_text())
    candidates = rollback_versions(current['version'])
    if target:
        selected = next((item for item in candidates if item['version'] == target), None)
    else:
        selected = candidates[0] if candidates else None
    if selected is None:
        raise ValueError('没有可用的安全回滚版本')
    if (current.get('schema') != selected.get('schema', 1)
            or not re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', current.get('image', ''))):
        raise ValueError('当前安装记录不是本仓库的固定镜像')
    data_dir = resolve_data_dir()
    assert_no_active_operations(data_dir)
    write_state(phase='rollback_pulling', message='正在下载回滚版本', rollback_version=selected['version'], operation_id=operation_id)
    compose(selected['image'], 'pull', *SERVICES)
    # As with a normal install, do not stop services if a send or login
    # operation began while the rollback image was downloading. Keep this
    # outside the recovery block so rejection cannot trigger ``compose up``.
    assert_no_active_operations(data_dir)
    try:
        write_state(phase='rollback', message='正在切换回滚版本')
        compose(current['image'], 'stop', '-t', '180', *SERVICES)
        compose(selected['image'], 'up', '-d', '--no-build', *SERVICES)
        ready(selected['version'])
        verify_runtime(selected['version'], selected['image'], SERVICES)
        temp = ROOT / 'installed.tmp'
        record = {'version': selected['version'], 'schema': 1, 'image': selected['image']}
        temp.write_text(json.dumps(record), encoding='utf-8')
        temp.replace(current_file)
        web_temp = ROOT / 'web-installed.tmp'
        web_temp.write_text(json.dumps(record), encoding='utf-8')
        web_temp.replace(ROOT / 'web-installed.json')
        write_state(phase='rollback_complete', message='回滚完成', installed=selected['version'])
    except Exception:
        write_state(phase='rollback_failed', message='回滚未通过健康检查，正在恢复当前版本')
        compose(current['image'], 'up', '-d', '--no-build', *SERVICES)
        ready(current['version'])
        verify_runtime(current['version'], current['image'], SERVICES)
        raise


def main():
    QUEUE.mkdir(exist_ok=True)
    with (ROOT / 'updater.lock').open('w') as lock:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        write_state(phase='idle', message='更新服务已连接')
        while True:
            request = QUEUE / 'requests' / 'request.json'
            if request.exists():
                try:
                    # Keep file until complete so duplicate clicks are rejected.
                    data = json.loads(request.read_text())
                    if time.time() - data['requested_at'] > 300:
                        raise ValueError('请求已过期，请重试')
                    operation_id = str(data.get('operation_id') or '')
                    write_state(operation_id=operation_id, requested_at=data.get('requested_at'))
                    if data['action'] == 'check':
                        item = latest()
                        current = json.loads((ROOT / 'installed.json').read_text())
                        web_file = ROOT / 'web-installed.json'
                        web_current = json.loads(web_file.read_text()) if web_file.is_file() else current
                        runtime = runtime_report(
                            web_current.get('version'),
                            {service: (web_current if service == 'spark-web' else current).get('image')
                             for service in SERVICES},
                        )
                        write_state(phase='checked', latest=item['version'], message='版本检查完成', **item,
                                    rollback_versions=rollback_versions(current['version']),
                                    runtime=runtime,
                                    runtime_drift=runtime.get('drift', True))
                    elif data['action'] == 'install':
                        install(data['version'], operation_id)
                    elif data['action'] == 'install-web':
                        install_web(data['version'], operation_id)
                    elif data['action'] == 'rollback':
                        rollback(data.get('version', ''), operation_id)
                    else:
                        raise ValueError('不支持的操作')
                except Exception as error:
                    # Never expose command output or GitHub credentials to the UI.
                    detail = str(error).strip().replace("\n", " ")[:240]
                    write_state(
                        phase='failed',
                        message='操作未完成，请检查更新服务日志和发行配置',
                        error_code=type(error).__name__.lower(),
                        error_detail=detail or '未提供详细错误',
                    )
                finally:
                    request.unlink(missing_ok=True)
            write_state()
            time.sleep(5)


if __name__ == '__main__':
    main()
