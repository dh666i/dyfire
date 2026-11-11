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
try:
    from deploy.update_transaction import Journal, atomic_json, atomic_text, TERMINAL
except ModuleNotFoundError:
    from update_transaction import Journal, atomic_json, atomic_text, TERMINAL

ROOT = Path(os.environ.get('DYFIRE_DEPLOY_DIR', '/opt/dyfire')).resolve()
DATA_DIR = Path(os.environ.get('DYFIRE_DATA_DIR', ROOT / 'data')).resolve()
QUEUE = ROOT / 'updates'
STATE = {}
REPOSITORY = 'dh666i/dyfire'
IMAGE = 'ghcr.io/dh666i/dyfire'
LEGACY_COMPATIBILITY = {'api': 1, 'browser': 1, 'database': 1}
GITHUB_API = 'https://api.github.com/repos/' + REPOSITORY
SERVICES = ['spark-web', 'spark-auth', 'spark-worker', 'spark-monitor']
WEB_SERVICES = ['spark-web']
COMPOSE_PROJECT = os.environ.get('DYFIRE_COMPOSE_PROJECT', 'dyfire')
RELEASE_CACHE_SECONDS = 20 * 60
COMPOSE_OPERATION_TIMEOUT_SECONDS = 10 * 60
IMAGE_PULL_TIMEOUT_SECONDS = 60 * 60
_RELEASE_CACHE = {}
_LAYER_CACHE = {}


def pinned_image(value):
    return bool(re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', str(value or '')))


def service_images(record):
    components = record.get('components') or {}
    web = (components.get('web') or {}).get('image', record.get('image'))
    browser = (components.get('browser') or {}).get('image', record.get('image'))
    if not pinned_image(web) or not pinned_image(browser):
        raise ValueError('安装记录不是本仓库的固定镜像')
    return {'spark-web': web, 'spark-monitor': web, 'spark-auth': browser, 'spark-worker': browser}


def installed_records():
    current = json.loads((ROOT / 'installed.json').read_text(encoding='utf-8'))
    web_file = ROOT / 'web-installed.json'
    web = json.loads(web_file.read_text(encoding='utf-8')) if web_file.exists() else current
    return current, web


def installed_images(current, web):
    images = service_images(current)
    images['spark-web'] = service_images(web)['spark-web']
    return images


def persist_images(images):
    # Compose invoked manually after an update must use the installed digests.
    # Preserve all non-image configuration, including credentials and profiles.
    names = {'SPARK_IMAGE_NAME': images['spark-web'], 'SPARK_WEB_IMAGE': images['spark-web'],
             'SPARK_BROWSER_IMAGE': images['spark-auth'], 'SPARK_MONITOR_IMAGE': images['spark-monitor']}
    if any(not pinned_image(value) for value in names.values()) or images['spark-worker'] != images['spark-auth']:
        raise ValueError('无法保存无效的组件镜像配置')
    path = ROOT / '.env.console'
    lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
    pattern = re.compile(r'^\s*(?:export\s+)?(' + '|'.join(names) + r')\s*=')
    kept = ''.join(line for line in lines if not pattern.match(line))
    if kept and not kept.endswith('\n'):
        kept += '\n'
    atomic_text(path, kept + ''.join(f'{key}={value}\n' for key, value in names.items()))


def compose_value(record):
    # Keep legacy monolithic installations usable during migration.
    return service_images(record) if record.get('components') else record['image']


def checkpoint(operation_id, **fields):
    return Journal(ROOT).checkpoint(operation_id, **fields)


def release_for_target(target, operation_id=''):
    version(target)
    journal = Journal(ROOT).read()
    if journal.get('operation_id') == operation_id and journal.get('release'):
        release = journal['release']
    else:
        release = _release_from_payload(_cached_github('/releases/tags/v' + target, force=True), resolve_registry=False)
    if not release or release.get('version') != target or not pinned_image(release.get('image')):
        raise ValueError('指定正式发行没有可验证的镜像摘要')
    service_images(release)
    checkpoint(operation_id, release=release)
    return release


def image_layers(image):
    """Read compressed layer descriptors from this repository's fixed digest."""
    if not re.fullmatch(re.escape(IMAGE)+r'@sha256:[a-f0-9]{64}', str(image)):
        raise ValueError('镜像摘要无效')
    if image in _LAYER_CACHE:
        return _LAYER_CACHE[image]
    accept='application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'
    if QUEUE.is_dir():write_state()
    manifest, _ = registry('/v2/'+REPOSITORY+'/manifests/'+image.split('@',1)[1],accept=accept)
    if 'manifests' in manifest:
        import platform
        architecture={'x86_64':'amd64','aarch64':'arm64','AMD64':'amd64'}.get(platform.machine(),platform.machine())
        selected=next((item for item in manifest['manifests'] if item.get('platform',{}).get('os')=='linux' and item['platform'].get('architecture')==architecture),None)
        if selected is None or not re.fullmatch(r'sha256:[a-f0-9]{64}',str(selected.get('digest'))):
            raise ValueError('当前服务器没有匹配的发行镜像')
        if QUEUE.is_dir():write_state()
        manifest,_=registry('/v2/'+REPOSITORY+'/manifests/'+selected['digest'],accept=accept)
    layers=manifest.get('layers')
    if not isinstance(layers,list) or not layers:
        raise ValueError('镜像层信息不可用')
    result={}
    for layer in layers:
        digest,size=layer.get('digest'),layer.get('size')
        if not re.fullmatch(r'sha256:[a-f0-9]{64}',str(digest)) or type(size) is not int or not 0<=size<=10*1024**3:
            raise ValueError('镜像层信息无效')
        result[digest]=size
    _LAYER_CACHE[image]=result
    return result


def download_plan(image,current_image):
    # The running image's layers are definitely present. Other cached layers
    # may make the real transfer smaller, so this is an upper bound, not a
    # claim that the expanded image size will be downloaded four times.
    try:
        layers=image_layers(image)
        present=image_layers(current_image)
        missing={key:size for key,size in layers.items() if key not in present}
        return {'known':True,'compressed_image_bytes':sum(layers.values()),
                'download_upper_bound_bytes':sum(missing.values()),
                'new_layers':len(missing),'reused_layers':len(layers)-len(missing),
                'shared_by_services':len(SERVICES)}
    except Exception:
        # Metadata estimation must never prevent an otherwise valid release.
        return {'known':False}


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
        'health': (state.get('Health') or {}).get('Status'),
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
        if (not item.get('running') or item.get('health', 'healthy') != 'healthy'
                or (expected and not _image_matches(item, expected))):
            report['drift'] = True
    return report


def verify_runtime(expected_version, expected_image, services=None):
    """Strict post-switch verification; raises before install records are written."""
    deadline = time.monotonic() + 90
    while True:
        report = runtime_report(expected_version, expected_image, services)
        if not report['drift']:
            return report
        starting = any(item.get('health') == 'starting' for item in report.get('services', {}).values())
        if not starting or time.monotonic() >= deadline:
            raise RuntimeError('运行容器版本或镜像与部署记录不一致，或服务健康检查未通过')
        write_state()
        time.sleep(2)


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
    result = {
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
    if manifest.get('manifest_version') == 2:
        components = manifest.get('components')
        compatibility = manifest.get('compatibility')
        if (not isinstance(components, dict) or set(components) != {'web', 'browser'}
                or not isinstance(compatibility, dict)
                or manifest.get('schema') != 2
                or any(type(compatibility.get(key)) is not int or compatibility[key] < 1 for key in LEGACY_COMPATIBILITY)):
            return {}
        normalized = {}
        for name, item in components.items():
            if not isinstance(item, dict) or not pinned_image(item.get('image')) or not pinned_image(item.get('index_image')):
                return {}
            normalized[name] = {'image': item['image'], 'index_image': item['index_image']}
        if normalized['web']['image'] != normalized_image:
            return {}
        result.update(manifest_version=2, manifest_schema=2, schema=compatibility['database'],
                      components=normalized, compatibility=compatibility,
                      release_id=release.get('id'), commit=manifest.get('commit'))
    elif manifest.get('manifest_version', 1) != 1:
        return {}
    return result


def write_state(**values):
    STATE.update(values, heartbeat=time.time())
    atomic_json(QUEUE / 'status.json', STATE, mode=0o644)


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
        result.append({**item, 'version': candidate, 'schema': item.get('schema', 1),
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
    if args and args[0]=='pull':
        try:
            from deploy.image_pull import pull
        except ModuleNotFoundError:
            from image_pull import pull
        images = list(dict.fromkeys(image[service] for service in args[1:])) if isinstance(image, dict) else [image]
        for index, selected in enumerate(images):
            try:layers=image_layers(selected)
            except Exception:layers={}
            def publish(value):
                # The byte counters describe this component, not the whole release.
                # Keep the active image visible when the next component starts.
                label = value.get('label', '')
                if len(images) > 1:
                    label = f'组件 {index + 1}/{len(images)} · {label}'
                write_state(pull_progress={**value, 'label': label,
                    'image_index': index + 1, 'image_count': len(images)})
            pull(selected,layers,publish,timeout=IMAGE_PULL_TIMEOUT_SECONDS)
        return
    if isinstance(image, dict):
        env = dict(os.environ, SPARK_WEB_IMAGE=image['spark-web'],
                   SPARK_BROWSER_IMAGE=image['spark-auth'],
                   SPARK_MONITOR_IMAGE=image['spark-monitor'], COMPOSE_PROJECT_NAME=COMPOSE_PROJECT)
    else:
        env = dict(os.environ, SPARK_IMAGE_NAME=image, SPARK_WEB_IMAGE=image,
                   SPARK_BROWSER_IMAGE=image, SPARK_MONITOR_IMAGE=image, COMPOSE_PROJECT_NAME=COMPOSE_PROJECT)
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
                if db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='account_operations'").fetchone()[0]:
                    active += db.execute("SELECT count(*) FROM account_operations WHERE julianday(expires_at) > julianday('now')").fetchone()[0]
                if db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='task_probes'").fetchone()[0]:
                    active += db.execute("SELECT count(*) FROM task_probes WHERE status='running'").fetchone()[0]
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
            if health.get('status') == 'ready' and actual['version'] == expected:
                return
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError('新版本健康检查未通过')


def install(target, operation_id=''):
    release = release_for_target(target, operation_id)
    current_file = ROOT / 'installed.json'
    current, web_current = installed_records()
    if (release['version'] != target or version(target) <= version(current['version'])
            or version(target) < version(web_current['version'])):
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
    old_images = installed_images(current, web_current)
    previous_image = current['image'] if len(set(old_images.values())) == 1 else old_images
    next_images = service_images(release)
    next_image = compose_value(release)
    checkpoint(operation_id, previous=current, previous_web=web_current, stage='pulling')
    plan=combined_download_plan(next_images, old_images)
    write_state(phase='pulling', message='正在下载版本', operation_id=operation_id, download_plan=plan)
    compose(next_image, 'pull', *SERVICES)
    backup = ROOT / 'backups' / time.strftime('%Y%m%d-%H%M%S')
    backup.mkdir(parents=True, mode=0o700)
    write_state(phase='stopping', message='等待当前操作结束并停止服务')
    # Generous grace period; all data writers stop before backup.
    # Pulling can take a long time on a small host. Re-check immediately
    # before entering the recovery block so a newly active operation is
    # rejected without running the recovery ``compose up`` path.
    assert_no_active_operations(data_dir)
    try:
        checkpoint(operation_id, stage='switching')
        compose(previous_image, 'stop', '-t', '180', *SERVICES)
        shutil.copytree(data_dir, backup / 'data', ignore=shutil.ignore_patterns('backups', 'snapshots'))
        shutil.copy2(current_file, backup / 'installed.json')
        web_file = ROOT / 'web-installed.json'
        if web_file.is_file():
            shutil.copy2(web_file, backup / 'web-installed.json')
        try:
            from deploy.snapshot_inventory import seal_snapshot
        except ModuleNotFoundError:
            from snapshot_inventory import seal_snapshot
        seal_snapshot(backup)
        write_state(phase='installing', message='正在启动新版本')
        compose(next_image, 'up', '-d', '--no-build', *SERVICES)
        checkpoint(operation_id, stage='verifying')
        ready(target)
        runtime=verify_runtime(target, next_images, SERVICES)
        persist_images(next_images)
        atomic_json(current_file, release)
        atomic_json(ROOT / 'web-installed.json', release)
        write_state(**component_status(release, release, release))
        write_state(phase='complete', message='更新完成', installed=target,latest=target,**release,runtime=runtime,runtime_drift=False)
        checkpoint(operation_id, stage='complete')
    except Exception:
        write_state(phase='rollback', message='更新未通过检查，正在恢复原版本')
        # Same schema only: retain new run records to avoid replaying a send.
        # Never automatically overwrite the live database with an older backup.
        checkpoint(operation_id, stage='recovering')
        compose(previous_image, 'up', '-d', '--no-build', *SERVICES)
        ready(web_current['version'])
        verify_runtime(web_current['version'], old_images, SERVICES)
        persist_images(old_images)
        atomic_json(current_file, current)
        atomic_json(ROOT / 'web-installed.json', web_current)
        write_state(**component_status(release, current, web_current))
        write_state(phase='failed', message='更新失败，已恢复原版本')
        checkpoint(operation_id, stage='rolled_back')
        raise


def install_web(target, operation_id=''):
    """Update only the React/API web container.

    Auth, worker and monitor keep their current image and process state. This
    is suitable for presentation/API-only releases; schema or worker changes
    must still use the full ``install`` path.
    """
    release = release_for_target(target, operation_id)
    current_file = ROOT / 'installed.json'
    current = json.loads(current_file.read_text())
    web_file = ROOT / 'web-installed.json'
    web_current = json.loads(web_file.read_text()) if web_file.is_file() else current
    if release['version'] != target or version(target) <= version(web_current['version']):
        raise ValueError('版本已变化或不高于当前版本，请重新检查更新')
    if current.get('schema') != release['schema'] or web_current.get('schema') != release['schema']:
        raise ValueError('数据结构不兼容')
    required = release.get('compatibility', LEGACY_COMPATIBILITY)
    installed_compatibility = current.get('compatibility', LEGACY_COMPATIBILITY)
    if required != installed_compatibility:
        raise ValueError('该版本需要同步升级执行服务，请选择完整更新')
    if not re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', web_current.get('image', '')):
        raise ValueError('当前 Web 安装记录不是本仓库的固定镜像')
    checkpoint(operation_id, previous=current, previous_web=web_current, stage='pulling')
    write_state(phase='web_pulling', message='正在下载 Web 版本', operation_id=operation_id,
                download_plan=download_plan(release['image'],web_current['image']))
    compose(release['image'], 'pull', *WEB_SERVICES)
    try:
        from deploy import web_switch
    except ModuleNotFoundError:
        import web_switch
    try:
        checkpoint(operation_id, stage='switching')
        write_state(phase='web_installing', message='正在切换 Web 服务，发送服务保持运行')
        if web_switch.enabled(ROOT):
            def checked_ready(expected):
                ready(expected)
                expected_image = release['image'] if expected == target else web_current['image']
                verify_runtime(expected, expected_image, WEB_SERVICES)
            web_switch.switch(ROOT, release['image'], target, operation_id,
                recreate=lambda image: compose(image, 'up', '-d', '--no-build', '--no-deps', *WEB_SERVICES),
                ready=checked_ready, publish=write_state,
                previous_image=web_current['image'], previous_version=web_current['version'], project=COMPOSE_PROJECT)
        else:
            compose(release['image'], 'up', '-d', '--no-build', *WEB_SERVICES)
        checkpoint(operation_id, stage='verifying')
        ready(target)
        runtime=verify_runtime(target, release['image'], WEB_SERVICES)
        persist_images(installed_images(current, release))
        atomic_json(ROOT / 'web-installed.json', release)
        write_state(**component_status(release, current, release))
        write_state(phase='web_complete', message='Web 更新完成，发送服务未重启', web_installed=target,latest=target,**release,runtime=runtime,runtime_drift=False)
        checkpoint(operation_id, stage='complete')
    except Exception:
        checkpoint(operation_id, stage='recovering')
        write_state(phase='web_rollback', message='Web 验收未通过，正在恢复上一版本')
        if web_switch.enabled(ROOT):
            web_switch.recover(ROOT,
                recreate=lambda image: compose(image, 'up', '-d', '--no-build', '--no-deps', *WEB_SERVICES),
                ready=ready, publish=write_state, project=COMPOSE_PROJECT)
        compose(web_current['image'], 'up', '-d', '--no-build', *WEB_SERVICES)
        ready(web_current['version'])
        verify_runtime(web_current['version'], web_current['image'], WEB_SERVICES)
        persist_images(installed_images(current, web_current))
        atomic_json(ROOT / 'web-installed.json', web_current)
        write_state(**component_status(release, current, web_current))
        write_state(phase='web_failed', message='Web 更新未通过检查，已恢复上一版本', web_installed=web_current['version'])
        checkpoint(operation_id, stage='rolled_back')
        raise


def rollback(target='', operation_id=''):
    current_file = ROOT / 'installed.json'
    current, web_current = installed_records()
    transaction = Journal(ROOT).read()
    if transaction.get('operation_id') == operation_id and transaction.get('release'):
        selected = transaction['release']
    else:
        candidates = rollback_versions(max(current['version'], web_current['version'], key=version))
        selected = next((item for item in candidates if item['version'] == target), None) if target else (candidates[0] if candidates else None)
    if selected is None:
        raise ValueError('没有可用的安全回滚版本')
    if (current.get('schema') != selected.get('schema', 1)
            or not re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', current.get('image', ''))):
        raise ValueError('当前安装记录不是本仓库的固定镜像')
    data_dir = resolve_data_dir()
    assert_no_active_operations(data_dir)
    old_images = installed_images(current, web_current)
    previous_image = current['image'] if len(set(old_images.values())) == 1 else old_images
    next_image = compose_value(selected)
    checkpoint(operation_id, previous=current, previous_web=web_current, release=selected, stage='pulling')
    write_state(phase='rollback_pulling', message='正在下载回滚版本', rollback_version=selected['version'], operation_id=operation_id)
    compose(next_image, 'pull', *SERVICES)
    # As with a normal install, do not stop services if a send or login
    # operation began while the rollback image was downloading. Keep this
    # outside the recovery block so rejection cannot trigger ``compose up``.
    assert_no_active_operations(data_dir)
    try:
        checkpoint(operation_id, stage='switching')
        write_state(phase='rollback', message='正在切换回滚版本')
        compose(previous_image, 'stop', '-t', '180', *SERVICES)
        compose(next_image, 'up', '-d', '--no-build', *SERVICES)
        checkpoint(operation_id, stage='verifying')
        ready(selected['version'])
        verify_runtime(selected['version'], service_images(selected), SERVICES)
        record = {**selected, 'schema': selected.get('schema', 1)}
        persist_images(service_images(record))
        atomic_json(current_file, record)
        atomic_json(ROOT / 'web-installed.json', record)
        write_state(**component_status(current, record, record))
        write_state(phase='rollback_complete', message='回滚完成', installed=selected['version'])
        checkpoint(operation_id, stage='complete')
    except Exception:
        checkpoint(operation_id, stage='recovering')
        write_state(phase='rollback_failed', message='回滚未通过健康检查，正在恢复当前版本')
        compose(previous_image, 'up', '-d', '--no-build', *SERVICES)
        ready(web_current['version'])
        verify_runtime(web_current['version'], old_images, SERVICES)
        persist_images(old_images)
        atomic_json(current_file, current)
        atomic_json(ROOT / 'web-installed.json', web_current)
        checkpoint(operation_id, stage='rolled_back')
        raise


def combined_download_plan(desired, present):
    try:
        wanted = {}
        existing = {}
        for image in set(desired.values()):
            wanted.update(image_layers(image))
        for image in set(present.values()):
            existing.update(image_layers(image))
        missing = {digest: size for digest, size in wanted.items() if digest not in existing}
        return {'known': True, 'compressed_image_bytes': sum(wanted.values()),
                'download_upper_bound_bytes': sum(missing.values()), 'new_layers': len(missing),
                'reused_layers': len(wanted) - len(missing), 'shared_by_services': len(desired)}
    except Exception:
        return {'known': False}


def component_status(item, current, web_current):
    images = installed_images(current, web_current)
    desired = service_images(item)
    compatible = (current.get('schema', 1) == item.get('schema', 1)
                  and current.get('compatibility', LEGACY_COMPATIBILITY)
                  == item.get('compatibility', LEGACY_COMPATIBILITY))
    return dict(component_versions={'web': web_current['version'], 'browser': current['version']},
        component_update_available=any(images[name] != desired[name] for name in SERVICES)
            and version(item['version']) >= version(web_current['version']),
        web_update_available=version(item['version']) > version(web_current['version']),
        web_update_allowed=compatible,
        web_update_reason='' if compatible else '该版本需要同步升级执行服务，请选择完整更新')


def check_updates():
    item = latest()
    current, web_current = installed_records()
    images = installed_images(current, web_current)
    desired = service_images(item)
    runtime = runtime_report(web_current['version'], images)
    write_state(phase='checked', latest=item['version'], message='版本检查完成', **item,
                **component_status(item, current, web_current),
                rollback_versions=rollback_versions(max(current['version'], web_current['version'], key=version)),
                download_plan=combined_download_plan(desired, images),
                web_download_plan=download_plan(desired['spark-web'], images['spark-web']),
                runtime=runtime, runtime_drift=runtime.get('drift', True))


def recover_transaction(journal):
    data = journal.read()
    previous = data['previous']
    previous_web = data.get('previous_web', previous)
    selected = data['release']
    web_only = data['action'] == 'install-web'
    services = WEB_SERVICES if web_only else SERVICES
    images = service_images(selected)
    write_state(phase='recovering', message='正在核对上次更新，恢复已接受的操作', operation_id=data['operation_id'])
    # A crash after a successful switch must not unnecessarily roll back a
    # healthy image. For web handoffs, only a completed handoff can be committed.
    handoff = ROOT / 'updates' / 'web-handoff.json'
    handoff_done = True
    matching_handoff = False
    if web_only and handoff.exists():
        state = json.loads(handoff.read_text(encoding='utf-8'))
        matching_handoff = state.get('operation_id') == data['operation_id']
        handoff_done = not matching_handoff or state.get('phase') == 'complete'
    if handoff_done and data['stage'] != 'recovering':
        try:
            runtime = verify_runtime(selected['version'], images, services)
            ready(selected['version'])
        except Exception:
            pass
        else:
            persist_images(installed_images(previous, selected) if web_only else images)
            if not web_only:
                atomic_json(ROOT / 'installed.json', selected)
            atomic_json(ROOT / 'web-installed.json', selected)
            write_state(**component_status(selected, previous if web_only else selected, selected))
            write_state(phase='web_complete' if web_only else 'complete', message='已确认上次更新成功',
                        web_installed=selected['version'], runtime=runtime, runtime_drift=False)
            journal.update(stage='complete')
            return
    journal.update(stage='recovering')
    old_images = installed_images(previous, previous_web)
    if web_only:
        try:
            from deploy import web_switch
        except ModuleNotFoundError:
            import web_switch
        if matching_handoff and web_switch.enabled(ROOT):
            web_switch.recover(ROOT,
                recreate=lambda image: compose(image, 'up', '-d', '--no-build', '--no-deps', *WEB_SERVICES),
                ready=ready, publish=write_state, project=COMPOSE_PROJECT)
        compose(previous_web['image'], 'up', '-d', '--no-build', *WEB_SERVICES)
    else:
        assert_no_active_operations(resolve_data_dir())
        compose(old_images, 'up', '-d', '--no-build', *SERVICES)
    ready(previous_web['version'])
    verify_runtime(previous_web['version'], old_images, services)
    persist_images(old_images)
    atomic_json(ROOT / 'installed.json', previous)
    atomic_json(ROOT / 'web-installed.json', previous_web)
    write_state(**component_status(selected, previous, previous_web))
    write_state(phase='rolled_back', message='上次更新中断，已恢复原版本', web_installed=previous_web['version'])
    journal.update(stage='rolled_back')


def process_request(data):
    if not isinstance(data, dict) or data.get('action') not in ('check', 'install', 'install-web', 'rollback'):
        raise ValueError('不支持的更新操作')
    journal = Journal(ROOT)
    record = journal.accept(data)
    if record['stage'] in TERMINAL:
        return
    operation_id = record['operation_id']
    write_state(operation_id=operation_id, requested_at=record['requested_at'], error_code=None,
                error_detail=None, pull_progress=None, phase='accepted', message='更新操作已接受')
    try:
        if record['stage'] not in ('accepted', 'pulling'):
            recover_transaction(journal)
            return
        if data['action'] == 'check':
            write_state(phase='checking', message='正在读取发行版本')
            check_updates()
        elif data['action'] == 'install':
            install(data['version'], operation_id)
        elif data['action'] == 'install-web':
            install_web(data['version'], operation_id)
        else:
            rollback(data.get('version', ''), operation_id)
        journal.update(stage='complete')
    except Exception as error:
        record = journal.read()
        if record['stage'] in ('switching', 'verifying', 'recovering'):
            # Keep the journal/request pending; a later loop can finish recovery.
            write_state(phase='recovering', message='更新恢复尚未完成，正在保留原版本并重试',
                        error_code=type(error).__name__.lower(), error_detail='请检查服务健康状态与更新日志')
        else:
            if record['stage'] != 'rolled_back':
                journal.update(stage='failed')
            write_state(phase='rolled_back' if record['stage'] == 'rolled_back' else 'failed',
                        message='更新失败，已恢复原版本' if record['stage'] == 'rolled_back' else '更新未完成，当前服务保持运行',
                        error_code=type(error).__name__.lower(),
                        error_detail=str(error).strip().replace('\n', ' ')[:240])
        raise


def main():
    QUEUE.mkdir(exist_ok=True)
    with (ROOT / 'updater.lock').open('w') as lock:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_file = QUEUE / 'status.json'
        if state_file.exists():
            STATE.update(json.loads(state_file.read_text(encoding='utf-8')))
        else:
            write_state(phase='idle', message='更新服务已连接')
        journal = Journal(ROOT)
        while True:
            request = QUEUE / 'requests' / 'request.json'
            record = journal.read()
            if not request.exists() and record and record.get('stage') not in TERMINAL:
                atomic_json(request, {key: record[key] for key in ('operation_id', 'action', 'version', 'requested_at')})
            if request.exists():
                data = None
                try:
                    data = json.loads(request.read_text(encoding='utf-8'))
                    process_request(data)
                except Exception as error:
                    if not isinstance(data, dict) or not data.get('operation_id') or journal.read().get('operation_id') != data.get('operation_id'):
                        write_state(phase='failed', message='更新请求未被接受', error_code=type(error).__name__.lower(),
                                    error_detail='请求无效、已过期或上次操作尚未恢复')
                finally:
                    current = journal.read()
                    if not isinstance(data, dict) or not data.get('operation_id') or current.get('operation_id') != data.get('operation_id') or current.get('stage') in TERMINAL:
                        request.unlink(missing_ok=True)
            else:
                try:
                    try:
                        from deploy import web_switch
                    except ModuleNotFoundError:
                        import web_switch
                    web_switch.maintenance(ROOT, project=COMPOSE_PROJECT)
                except Exception:
                    # Cleaning up a drained temporary container must not change
                    # the outcome of a completed deployment or stop the daemon.
                    pass
            write_state()
            time.sleep(5)


if __name__ == '__main__':
    main()
