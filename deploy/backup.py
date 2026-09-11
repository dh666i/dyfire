"""Host-only consistent SQLite snapshot and root-private recovery materials."""
import fcntl,json,os,shutil,sqlite3,subprocess,tempfile
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path('/opt/dyfire-backups')
STATUS=Path('/opt/dyfire-web-updater/updates/backup-status.json')
APP=Path('/opt/henng-spark-console')

def main():
    os.umask(0o077);ROOT.mkdir(mode=0o700,exist_ok=True)
    with (ROOT/'lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        at=datetime.now(timezone.utc).isoformat()
        try:
            if shutil.disk_usage(ROOT).free<2*1024**3:raise RuntimeError('磁盘可用空间不足 2GB，备份暂停，请清理或扩容')
            info=json.loads(subprocess.check_output(['docker','inspect','douyin-spark-console-spark-web-1']))[0]
            mount=next(m for m in info['Mounts'] if m['Destination']=='/data')
            source=Path(mount['Source'])/'spark.db'
            if not source.is_file():raise RuntimeError('数据库路径不存在')
            folder=Path(tempfile.mkdtemp(prefix='snapshot-',dir=ROOT))
            with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=20) as src,sqlite3.connect(folder/'spark.db') as dst:
                src.backup(dst,pages=256,sleep=.1)
                if dst.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('数据库完整性检查未通过')
            # Separate root-private key directory; never expose it through Web.
            shutil.copytree(APP/'secrets',folder/'secrets')
            for name in ('.env.console','compose.console.yml','VERSION'):
                shutil.copy2(APP/name,folder/name)
            (folder/'manifest.json').write_text(json.dumps({'at':at,'image':info['Image'],'verified':'sqlite quick_check'}))
            result={'status':'success','at':at,'message':'数据库一致性备份及恢复材料已保存；仅服务器管理员可访问'}
        except Exception as error:
            result={'status':'failed','at':at,'message':str(error) if isinstance(error,RuntimeError) else '备份失败，请检查服务器备份日志'}
            raise
        finally:
            temp=STATUS.with_suffix('.tmp');temp.write_text(json.dumps(result,ensure_ascii=False));temp.chmod(0o644);temp.replace(STATUS)
if __name__=='__main__':main()
