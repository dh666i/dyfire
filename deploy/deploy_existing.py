"""Deploy only web to the verified existing installation, retaining its volume."""
import json
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

ROOT=Path('/opt/henng-spark-console')
SOURCE=Path(__file__).resolve().parents[1]
COMPOSE=ROOT/'compose.console.yml'
IMAGE='dyfire:0.1.1'


def run(*args):
    return subprocess.check_output(args,text=True).strip()


def main():
    previous=run('docker','inspect','douyin-spark-console-spark-web-1','--format','{{.Image}}')
    backup=ROOT/'source-backups'/('dyfire-0.1.1-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True)
    shutil.copy2(COMPOSE,backup/'compose.console.yml')
    shutil.copytree(ROOT/'spark_console',backup/'spark_console')
    (backup/'image.txt').write_text(previous)
    command=['docker','compose','--env-file',str(ROOT/'.env.console'),'-f',str(COMPOSE)]
    original=COMPOSE.read_text()
    # Existing services, keys, named volume, ports and project name are retained.
    import re
    updated=re.sub(r'\$\{SPARK_IMAGE_NAME:-[^}]+\}', '${SPARK_IMAGE_NAME:-dyfire:0.1.1}', original, count=1)
    if updated==original:raise RuntimeError('Unexpected original image configuration')
    try:
        COMPOSE.write_text(updated)
        subprocess.run(command+['config','--quiet'],check=True)
        subprocess.run(command+['up','-d','--no-build','--no-deps','spark-web'],check=True)
        for attempt in range(30):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8899/api/version',timeout=3) as response:
                    assert json.load(response)['version']=='0.1.1'
                with urllib.request.urlopen('http://127.0.0.1:8899/health/ready',timeout=3) as response:
                    assert response.status==200
                with urllib.request.urlopen('http://127.0.0.1:8899/studio',timeout=3) as response:
                    assert b'/assets/' in response.read()
                break
            except Exception:
                if attempt==29:raise
                time.sleep(2)
        # Keep host sources consistent with the running web version.
        for name in ('web','static','templates'):
            shutil.copytree(SOURCE/'spark_console'/name,ROOT/'spark_console'/name,dirs_exist_ok=True)
        shutil.copy2(SOURCE/'spark_console/version.py',ROOT/'spark_console/version.py')
        shutil.copy2(SOURCE/'VERSION',ROOT/'VERSION')
        shutil.copytree(SOURCE/'frontend/dist',ROOT/'frontend/dist',dirs_exist_ok=True)
        print('Deployed 0.1.1; backup: '+str(backup))
    except Exception:
        COMPOSE.write_text(original)
        # Restore exact original image even if mutable tags changed meanwhile.
        import os
        subprocess.run(command+['up','-d','--no-build','--no-deps','spark-web'],env=dict(os.environ,SPARK_IMAGE_NAME=previous),check=True)
        raise


if __name__=='__main__':main()
