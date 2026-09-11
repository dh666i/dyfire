import json
from pathlib import Path
import shutil
import subprocess
import time
import yaml

root=Path('/opt/henng-spark-console')
source=Path(__file__).resolve().parents[1]
version=(source/'VERSION').read_text().strip()
def run(*args):return subprocess.check_output(args,text=True).strip()
compose=['docker','compose','--env-file',str(root/'.env.console'),'-f',str(root/'compose.console.yml')]
old_auth=run('docker','inspect','douyin-spark-console-spark-auth-1','--format','{{.Image}}')
try:
    run('docker','tag',old_auth,'dyfire:directory-auth-base')
except subprocess.CalledProcessError:
    # Existing runtime/dependency hashes must first be compared with the Web
    # base when the original image was removed from the local store.
    run('docker','tag','dyfire:directory-web-base','dyfire:directory-auth-base')
for base,target in [('dyfire:directory-auth-base','dyfire:auth-'+version),('dyfire:directory-web-base','dyfire:web-directory-'+version)]:
    subprocess.run(['docker','build','--build-arg','DYFIRE_BASE_IMAGE='+base,'-f',str(source/'deploy/Dockerfile.directory-fix'),'-t',target,str(source)],check=True)
subprocess.run(['docker','build','--build-arg','DYFIRE_BASE_IMAGE=dyfire:web-directory-'+version,'-f',str(source/'deploy/Dockerfile.fast'),'-t','dyfire:'+version,str(source)],check=True)
check="import sqlite3;d=sqlite3.connect('file:/data/spark.db?mode=ro',uri=True);assert d.execute(\"select count(*) from douyin_login_sessions where status not in ('succeeded','failed','expired','cancelled') and expires_at>datetime('now')\").fetchone()[0]==0;assert d.execute(\"select count(*) from contact_directory_jobs where status='running'\").fetchone()[0]==0"
run('docker','exec','douyin-spark-console-spark-auth-1','python','-c',check)
original=(root/'compose.console.yml').read_text()
backup=root/'source-backups'/('directory-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir()
(backup/'compose.console.yml').write_text(original)
(backup/'auth-image.txt').write_text(old_auth)
data=yaml.safe_load(original);data['services']['spark-auth']['image']='dyfire:auth-'+version
try:
    (root/'compose.console.yml').write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False))
    subprocess.run(compose+['up','-d','--no-deps','--no-build','spark-auth'],check=True)
    for _ in range(30):
        status=run('docker','inspect','douyin-spark-console-spark-auth-1','--format','{{.State.Health.Status}}')
        if status=='healthy':break
        time.sleep(2)
    else:raise RuntimeError('认证服务健康检查失败')
    subprocess.run(['python3',str(source/'deploy/deploy_existing.py')],check=True)
except Exception:
    restored=yaml.safe_load(original);restored['services']['spark-auth']['image']='dyfire:directory-auth-base'
    (root/'compose.console.yml').write_text(yaml.safe_dump(restored,allow_unicode=True,sort_keys=False))
    subprocess.run(compose+['up','-d','--no-deps','--no-build','spark-auth'],check=True)
    raise
print('Directory and web deployed; backup:',backup)
