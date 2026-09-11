"""One-time host integration. Preserves the current compose services and volumes."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import yaml

root=Path('/opt/dyfire-web-updater')
source=Path(__file__).resolve().parent
root.mkdir(exist_ok=True,mode=0o700)
for name in ('git_web_updater.py','Dockerfile.fast','Dockerfile.fast.dockerignore'):
    shutil.copy2(source/name,root/name)
with urllib.request.urlopen('https://api.github.com/meta',timeout=20) as response:
    keys=json.load(response)['ssh_keys']
(root/'known_hosts').write_text(''.join('github.com '+key+'\n' for key in keys))
env=__import__('os').environ.copy()
env['GIT_SSH_COMMAND']='ssh -p 443 -o Hostname=ssh.github.com -o HostKeyAlias=github.com -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 -i /opt/dyfire-web-updater/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/opt/dyfire-web-updater/known_hosts'
if not (root/'repository').exists():
    subprocess.run(['git','clone','--bare','git@github.com:dh666i/dyfire.git',str(root/'repository')],env=env,check=True)
queue=root/'updates';queue.mkdir(exist_ok=True,mode=0o755)
requests=queue/'requests';requests.mkdir(exist_ok=True,mode=0o700)
__import__('os').chown(requests,10001,10001)
compose=Path('/opt/henng-spark-console/compose.console.yml')
backup=root/'compose.before-updater.yml'
if not backup.exists():shutil.copy2(compose,backup)
data=yaml.safe_load(compose.read_text())
web=data['services']['spark-web']
web['environment']=dict(web.get('environment',{}),DYFIRE_UPDATE_DIR='/run/dyfire-updates')
mounts=list(web.get('volumes',[]))
for value in (str(queue)+':/run/dyfire-updates:ro',str(requests)+':/run/dyfire-updates/requests:rw'):
    if value not in mounts:mounts.append(value)
web['volumes']=mounts
compose.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False))
subprocess.run(['docker','compose','--env-file','/opt/henng-spark-console/.env.console','-f',str(compose),'config','--quiet'],check=True)
(root/'installed.json').write_text(json.dumps({'version':'0.1.5','commit':sys.argv[1],'image':'dyfire:0.1.5'}))
unit='''[Unit]
Description=Dyfire GitHub Web updater
After=network-online.target docker.service
[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/dyfire-web-updater/git_web_updater.py
Restart=on-failure
RestartSec=10
UMask=0022
[Install]
WantedBy=multi-user.target
'''
Path('/etc/systemd/system/dyfire-web-updater.service').write_text(unit)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','dyfire-web-updater'],check=True)
