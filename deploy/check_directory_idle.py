import sqlite3
from pathlib import Path
paths=list(Path('/data').glob('*.db'))
db=sqlite3.connect('file:'+str(Path('/data/spark.db'))+'?mode=ro',uri=True)
for table in ('contact_directory_jobs','douyin_login_sessions','account_operations'):
    columns=[r[1] for r in db.execute('pragma table_info('+table+')')]
    if not columns: print(table,'missing');continue
    if table=='account_operations':
        print(table,db.execute("select count(*) from account_operations where expires_at>datetime('now')").fetchone()[0])
    elif table=='douyin_login_sessions':
        print(table,db.execute("select count(*) from douyin_login_sessions where status not in ('succeeded','failed','expired','cancelled') and expires_at>datetime('now')").fetchone()[0])
    else:
        print(table,db.execute("select count(*) from contact_directory_jobs where status='running'").fetchone()[0])
        print('recent jobs',db.execute('select status,error,started_at,finished_at from contact_directory_jobs order by created_at desc limit 3').fetchall())
