"""Host-owned updater for the existing installation. Read-only Git deploy key."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import traceback
import urllib.request

ROOT = Path('/opt/dyfire-web-updater')
REPO = ROOT / 'repository'
QUEUE = ROOT / 'updates'
APP = Path('/opt/henng-spark-console')
STATE = {}
ENV = dict(os.environ, GIT_SSH_COMMAND='ssh -p 443 -o Hostname=ssh.github.com -o HostKeyAlias=github.com -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 -i /opt/dyfire-web-updater/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/opt/dyfire-web-updater/known_hosts')

def status(**values):
    STATE.update(values, heartbeat=time.time())
    temp = QUEUE / 'status.tmp'
    temp.write_text(json.dumps(STATE, ensure_ascii=False))
    temp.chmod(0o644)
    temp.replace(QUEUE / 'status.json')

def run(args, timeout=600):
    # Keep heartbeat alive while Git/Docker runs, without exposing logs to Web.
    with tempfile.TemporaryFile() as output:
        p = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT, env=ENV)
        start = time.monotonic()
        while p.poll() is None:
            if time.monotonic() - start > timeout:
                p.kill(); p.wait(); raise RuntimeError('操作超时')
            status(); time.sleep(1)
        output.seek(0); result = output.read().decode(errors='replace')
        if p.returncode: raise RuntimeError(result[-2000:])
        return result.strip()

def git(*args):
    return run(['git', '-C', str(REPO), *args])

def number(value):
    if not re.fullmatch(r'\d+\.\d+\.\d+', value): raise ValueError('版本格式错误')
    return tuple(map(int, value.split('.')))

def latest():
    status(phase='checking', message='正在读取 GitHub 正式版本标签')
    git('fetch', '--tags', 'origin')
    tags = [t for t in git('tag', '--list', 'v*').splitlines() if re.fullmatch(r'v\d+\.\d+\.\d+', t)]
    if not tags: raise ValueError('暂无正式版本标签')
    tag = max(tags, key=lambda t:number(t[1:]))
    sha = git('rev-parse', tag+'^{commit}')
    if git('show', sha+':VERSION').strip() != tag[1:]: raise ValueError('标签与版本不一致')
    return tag[1:], sha

def validate_scope(base, sha):
    git('merge-base', '--is-ancestor', base, sha)
    paths = git('diff', '--name-only', base, sha).splitlines()
    allowed = ('frontend/', 'spark_console/web/', 'spark_console/templates/', 'spark_console/static/', 'docs/', 'tests/', '.github/', 'deploy/')
    if any(p not in ('VERSION','README.md') and not p.startswith(allowed) for p in paths):
        raise ValueError('该版本涉及执行服务或数据结构，需要完整部署')
    entries = git('ls-tree', '-r', sha).splitlines()
    if any(line.startswith(('120000','160000')) for line in entries): raise ValueError('发行包不得包含符号链接或子模块')
    # These are prebuilt and committed with the release, not built by the web user.
    git('cat-file', '-e', sha+':frontend/dist/index.html')

def compose(image, *args):
    return run(['env', 'SPARK_IMAGE_NAME='+image, 'docker', 'compose', '--env-file', str(APP/'.env.console'), '-f', str(APP/'compose.console.yml'), *args])

def ready(version):
    for _ in range(30):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8899/api/version', timeout=3) as r: actual=json.load(r)['version']
            with urllib.request.urlopen('http://127.0.0.1:8899/health/ready', timeout=3) as r: healthy=r.status==200
            if actual==version and healthy:return
        except Exception:pass
        status();time.sleep(2)
    raise RuntimeError('网页健康检查失败')

def install(target):
    version, sha = latest()
    current = json.loads((ROOT/'installed.json').read_text())
    if target != version or number(version)<=number(current['version']): raise ValueError('请重新检查可更新版本')
    validate_scope(current['commit'], sha)
    if shutil.disk_usage(ROOT).free < 2*1024**3: raise ValueError('磁盘空间不足 2 GB')
    source = Path(tempfile.mkdtemp(prefix='release-', dir=ROOT))
    archive = source/'source.tar'
    git('archive', '--format=tar', '--output='+str(archive), sha)
    run(['tar','-xf',str(archive),'-C',str(source)])
    previous = run(['docker','inspect','douyin-spark-console-spark-web-1','--format','{{.Image}}'])
    image='dyfire:web-'+version+'-'+sha[:12]
    base='dyfire:base-'+previous.removeprefix('sha256:')[:16]
    run(['docker','tag',previous,base])
    status(phase='building', message='正在准备新版网页，当前服务继续运行')
    run(['docker','build','--network','host','--build-arg','DYFIRE_BASE_IMAGE='+base,'-f',str(ROOT/'Dockerfile.fast'),'-t',image,str(source)])
    backup=ROOT/'backups'/time.strftime('%Y%m%d-%H%M%S');backup.mkdir(parents=True)
    shutil.copy2(APP/'compose.console.yml',backup/'compose.console.yml')
    shutil.copy2(ROOT/'installed.json',backup/'installed.json')
    (backup/'image.txt').write_text(previous)
    original=(APP/'compose.console.yml').read_text()
    try:
        status(phase='installing', message='正在切换网页服务，登录与发送服务保持运行')
        compose(image,'up','-d','--no-build','--no-deps','spark-web')
        ready(version)
        updated,count=re.subn(r'\$\{SPARK_IMAGE_NAME:-[^}]+\}', '${SPARK_IMAGE_NAME:-'+image+'}', original)
        if count<1:raise ValueError('部署配置格式不匹配')
        (APP/'compose.console.yml').write_text(updated)
        temp=ROOT/'installed.tmp';temp.write_text(json.dumps({'version':version,'commit':sha,'image':image}));temp.replace(ROOT/'installed.json')
        status(phase='complete', message='网页更新完成', installed=version)
    except Exception:
        status(phase='rollback',message='正在恢复原网页版本')
        (APP/'compose.console.yml').write_text(original)
        compose(previous,'up','-d','--no-build','--no-deps','spark-web');ready(current['version'])
        raise

def main():
    with (ROOT/'lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        status(phase='idle',message='GitHub 只读更新服务已连接')
        while True:
            request=QUEUE/'requests/request.json'
            if request.exists():
                try:
                    data=json.loads(request.read_text())
                    if time.time()-data['requested_at']>300:raise ValueError('请求已过期，请重试')
                    if data['action']=='check':
                        version,sha=latest()
                        current=json.loads((ROOT/'installed.json').read_text())
                        validate_scope(current['commit'],sha)
                        status(phase='checked',latest=version,message='已是最新版本' if number(version)<=number(current['version']) else '发现新版本 '+version)
                    elif data['action']=='install':install(data['version'])
                    else:raise ValueError('不支持的操作')
                except Exception as error:
                    traceback.print_exc()
                    status(phase='failed',message=str(error) if isinstance(error,ValueError) else '更新未完成，请查看服务器更新日志', latest='')
                finally:request.unlink(missing_ok=True)
            status();time.sleep(3)

if __name__=='__main__':main()
