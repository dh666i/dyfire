"""Read-only by default: propose removal of old deployment snapshots."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import hashlib
import sqlite3
from contextlib import closing


def snapshot_report(path):
    """Read a stopped-writer snapshot with its WAL, without changing content."""
    path=Path(path).resolve(strict=True)
    files={}
    for name in ('spark.db','spark.db-wal'):
        file=path/'data'/name
        if file.is_symlink():raise ValueError('snapshot database must not be a symlink')
        if file.is_file():
            digest=hashlib.sha256()
            with file.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            files['data/'+name]={'bytes':file.stat().st_size,'sha256':digest.hexdigest()}
    if 'data/spark.db' not in files:raise ValueError('snapshot database missing')
    with closing(sqlite3.connect((path/'data/spark.db').as_uri()+'?mode=ro',uri=True)) as db:
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
        foreign_keys=len(db.execute('PRAGMA foreign_key_check').fetchall())
        schema=db.execute('select max(version) from schema_migrations').fetchone()[0] if db.execute("select count(*) from sqlite_master where name='schema_migrations'").fetchone()[0] else None
    if integrity!='ok' or foreign_keys:raise ValueError('snapshot SQLite verification failed')
    return {'format':1,'files':files,'sqlite':{'integrity':'ok','foreign_key_errors':0,'schema':schema}}


def seal_snapshot(path):
    path=Path(path).resolve(strict=True);value=snapshot_report(path)
    tmp=path/'manifest.tmp';tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.chmod(0o600)
    tmp.replace(path/'manifest.json');return value


def verify_snapshot(path):
    path=Path(path).resolve(strict=True)
    try:
        expected=json.loads((path/'manifest.json').read_text())
        actual=snapshot_report(path)
        return {'verified':expected==actual,'legacy':False}
    except FileNotFoundError:return {'verified':False,'legacy':True}
    except (OSError,ValueError,sqlite3.Error):return {'verified':False,'legacy':False}


def inventory(root, *, keep=5, days=14, budget_bytes=2*1024**3, now=None, verify=False):
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
            if verify:entries[-1].update(verify_snapshot(path))
        except (OSError,ValueError,KeyError):continue
    total=sum(item['bytes'] for item in entries)
    candidates=[]
    for index,item in reversed(list(enumerate(entries))):
        if index>=keep and item['age_days']>=days and total>budget_bytes:
            candidates.append(item['name']);total-=item['bytes']
    return {'snapshots':entries,'candidates':candidates,'retained_bytes':total,'over_budget':total>budget_bytes}


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/opt/dyfire'));p.add_argument('--apply',action='store_true')
    p.add_argument('--verify',action='store_true',help='Verify snapshot manifests and SQLite integrity; old snapshots stay marked legacy')
    p.add_argument('--keep',type=int,default=5);p.add_argument('--days',type=int,default=14);p.add_argument('--budget-mb',type=int,default=2048)
    args=p.parse_args()
    if min(args.keep,args.days,args.budget_mb)<1:raise SystemExit('Retention values must be positive')
    root=args.root.resolve();result=inventory(root,keep=args.keep,days=args.days,budget_bytes=args.budget_mb*1024**2,verify=args.verify)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.apply:
        # Applying is a separate, explicit operator action; no timer invokes it.
        for name in result['candidates']:
            target=root/'backups'/name
            if target.is_symlink() or target.resolve().parent!=root/'backups':raise SystemExit('Unsafe snapshot path')
            shutil.rmtree(target)


if __name__=='__main__':main()
