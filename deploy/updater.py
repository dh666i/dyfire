"""Run on the host under systemd; no Docker socket is exposed to the web service.

Only immutable images from this repository can be installed. Releases with a
different data schema require a separately reviewed migration, not auto-update.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.request

ROOT = Path(os.environ.get('DYFIRE_DEPLOY_DIR', '/opt/dyfire')).resolve()
QUEUE = ROOT / 'updates'
STATE = {}
REPOSITORY = 'dh666i/dyfire'
IMAGE = 'ghcr.io/dh666i/dyfire'
SERVICES = ['spark-web', 'spark-auth', 'spark-worker', 'spark-monitor']
COMPOSE_PROJECT = os.environ.get('DYFIRE_COMPOSE_PROJECT', 'dyfire')


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('发行版本格式无效')
    return tuple(map(int, value.split('.')))


def write_state(**values):
    STATE.update(values, heartbeat=time.time())
    temp = QUEUE / 'status.tmp'
    temp.write_text(json.dumps(STATE, ensure_ascii=False), encoding='utf-8')
    os.chmod(temp, 0o644)
    temp.replace(QUEUE / 'status.json')


def github(path):
    token = Path(os.environ['DYFIRE_GITHUB_TOKEN_FILE']).read_text().strip()
    req = urllib.request.Request('https://api.github.com/' + path, headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'dyfire-updater'})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def latest():
    release = github(f'repos/{REPOSITORY}/releases/latest')
    tag = release['tag_name']
    candidate = tag.removeprefix('v')
    version(candidate)
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('不安装草稿或预发行版本')
    match = re.search(r'<!-- dyfire-manifest\s+(\{[^<>]+\})\s+-->', release.get('body') or '')
    if not match:
        raise ValueError('发行版缺少镜像清单')
    manifest = json.loads(match.group(1))
    if manifest['version'] != candidate or manifest['schema'] != 1:
        raise ValueError('版本或数据结构不兼容，需要人工迁移')
    if not re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', manifest['image']):
        raise ValueError('发行镜像必须是本仓库不可变摘要')
    return manifest


def compose(image, *args):
    env = dict(os.environ, SPARK_IMAGE_NAME=image, COMPOSE_PROJECT_NAME=COMPOSE_PROJECT)
    process = subprocess.Popen(['docker', 'compose', '--project-name', COMPOSE_PROJECT, '--env-file', str(ROOT / '.env.console'),
                    '--file', str(ROOT / 'compose.console.yml'), *args], cwd=ROOT, env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 600
    while process.poll() is None:
        write_state()
        if time.monotonic() > deadline:
            process.kill();process.wait()
            raise TimeoutError('容器操作超时')
        time.sleep(2)
    if process.returncode:
        raise RuntimeError('容器操作失败')


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


def install(target):
    release = latest()  # Re-fetch from trusted origin; never trust status.json.
    current_file = ROOT / 'installed.json'
    current = json.loads(current_file.read_text())
    if release['version'] != target or version(target) <= version(current['version']):
        raise ValueError('版本已变化或不高于当前版本，请重新检查更新')
    if current.get('schema') != release['schema']:
        raise ValueError('数据结构不兼容')
    if not re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', current['image']):
        raise ValueError('当前安装记录不是本仓库的固定镜像')
    data_dir = ROOT / 'data'
    if not data_dir.is_dir() or data_dir.is_symlink():
        raise ValueError('未找到标准安装的数据目录')
    required = sum(p.stat().st_size for p in data_dir.rglob('*') if p.is_file())
    if shutil.disk_usage(ROOT).free < required + 2 * 1024**3:
        raise ValueError('备份空间不足，请清理磁盘后重试')
    import sqlite3
    database = data_dir / 'spark.db'
    if not database.is_file():
        raise ValueError('自动更新仅支持标准 SQLite 数据目录')
    with sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True) as db:
        active = db.execute("SELECT count(*) FROM task_runs WHERE status='running'").fetchone()[0]
        active += db.execute("SELECT count(*) FROM douyin_login_sessions WHERE status NOT IN ('succeeded','failed','expired','cancelled')").fetchone()[0]
    if active:
        raise ValueError('当前仍有扫码或发送操作，请完成后再更新')
    write_state(phase='pulling', message='正在下载版本')
    compose(release['image'], 'pull', *SERVICES)
    backup = ROOT / 'backups' / time.strftime('%Y%m%d-%H%M%S')
    backup.mkdir(parents=True, mode=0o700)
    write_state(phase='stopping', message='等待当前操作结束并停止服务')
    # Generous grace period; all data writers stop before backup.
    try:
        compose(current['image'], 'stop', '-t', '180', *SERVICES)
        # Production installer binds /opt/dyfire/data to /data.
        shutil.copytree(ROOT / 'data', backup / 'data')
        shutil.copy2(current_file, backup / 'installed.json')
        write_state(phase='installing', message='正在启动新版本')
        compose(release['image'], 'up', '-d', '--no-build', *SERVICES)
        ready(target)
        temp = ROOT / 'installed.tmp'
        temp.write_text(json.dumps(release))
        temp.replace(current_file)
        write_state(phase='complete', message='更新完成', installed=target)
    except Exception:
        write_state(phase='rollback', message='更新未通过检查，正在恢复原版本')
        # Same schema only: retain new run records to avoid replaying a send.
        # Never automatically overwrite the live database with an older backup.
        compose(current['image'], 'up', '-d', '--no-build', *SERVICES)
        ready(current['version'])
        write_state(phase='failed', message='更新失败，已恢复原版本')
        raise


def main():
    QUEUE.mkdir(exist_ok=True)
    with (ROOT / 'updater.lock').open('w') as lock:
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
                    if data['action'] == 'check':
                        item = latest()
                        write_state(phase='checked', latest=item['version'], message='版本检查完成')
                    elif data['action'] == 'install':
                        install(data['version'])
                    else:
                        raise ValueError('不支持的操作')
                except Exception:
                    # Never expose command output or GitHub credentials to the UI.
                    write_state(phase='failed', message='操作未完成，请检查更新服务日志和发行配置')
                finally:
                    request.unlink(missing_ok=True)
            write_state()
            time.sleep(5)


if __name__ == '__main__':
    main()
