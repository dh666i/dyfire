"""Read-only by default: propose removal of old deployment snapshots."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil


def inventory(root, *, keep=5, days=14, budget_bytes=2*1024**3, now=None):
    root=Path(root).resolve()
    base=root/'backups'
    if base.is_symlink():raise ValueError('snapshot directory must not be a symlink')
    now=now or datetime.now(timezone.utc).timestamp()
    entries=[]
    for path in sorted(base.glob('*'),reverse=True):
        if not path.is_dir() or path.is_symlink() or not re.fullmatch(r'\d{8}-\d{6}',path.name):continue
        if path.resolve().parent!=base:raise ValueError('snapshot outside backup directory')
        try:
            installed=json.loads((path/'installed.json').read_text())
            if not re.fullmatch(r'ghcr\.io/dh666i/dyfire@sha256:[a-f0-9]{64}',installed['image']):continue
            files=list(path.rglob('*'))
            if any(p.is_symlink() for p in files):continue
            size=sum(p.stat().st_size for p in files if p.is_file())
            if not (path/'data/spark.db').is_file():continue
            entries.append({'name':path.name,'version':installed['version'],'bytes':size,'age_days':round((now-path.stat().st_mtime)/86400,1)})
        except (OSError,ValueError,KeyError):continue
    total=sum(item['bytes'] for item in entries)
    candidates=[]
    for index,item in reversed(list(enumerate(entries))):
        if index>=keep and item['age_days']>=days and total>budget_bytes:
            candidates.append(item['name']);total-=item['bytes']
    return {'snapshots':entries,'candidates':candidates,'retained_bytes':total,'over_budget':total>budget_bytes}


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/opt/dyfire'));p.add_argument('--apply',action='store_true')
    p.add_argument('--keep',type=int,default=5);p.add_argument('--days',type=int,default=14);p.add_argument('--budget-mb',type=int,default=2048)
    args=p.parse_args()
    if min(args.keep,args.days,args.budget_mb)<1:raise SystemExit('Retention values must be positive')
    root=args.root.resolve();result=inventory(root,keep=args.keep,days=args.days,budget_bytes=args.budget_mb*1024**2)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.apply:
        # Applying is a separate, explicit operator action; no timer invokes it.
        for name in result['candidates']:
            target=root/'backups'/name
            if target.is_symlink() or target.resolve().parent!=root/'backups':raise SystemExit('Unsafe snapshot path')
            shutil.rmtree(target)


if __name__=='__main__':main()
