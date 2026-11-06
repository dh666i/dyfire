"""Install bounded mail/retention jobs; never installs periodic backups."""
import argparse
import os
from pathlib import Path
import subprocess


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    if root != Path('/opt/dyfire'):
        raise SystemExit('Install only from the canonical /opt/dyfire checkout')
    names=['dyfire-mail.service','dyfire-mail.timer','dyfire-retention.service','dyfire-retention.timer']
    for name in names:
        print(f'{root / "deploy" / name} -> /etc/systemd/system/{name}')
    if not args.apply:return
    if os.geteuid()!=0:raise SystemExit('Run installer as root')
    for name in names:
        subprocess.run(['install','-m','0644',str(root/'deploy'/name),'/etc/systemd/system/'+name],check=True)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','--now','dyfire-mail.timer','dyfire-retention.timer'],check=True)
    subprocess.run(['systemctl','start','dyfire-mail.service'],check=True)


if __name__=='__main__':main()
