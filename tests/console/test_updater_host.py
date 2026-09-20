import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def host(tmp_path,monkeypatch):
    if sys.platform=='win32':
        monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace())
    spec=importlib.util.spec_from_file_location('dyfire_updater',Path(__file__).resolve().parents[2]/'deploy/updater.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.ROOT=tmp_path;module.QUEUE=tmp_path/'updates';module.QUEUE.mkdir();module.STATE={}
    (tmp_path/'data').mkdir()
    module.DATA_DIR=tmp_path/'data'
    with sqlite3.connect(tmp_path/'data/spark.db') as db:
        db.execute('CREATE TABLE task_runs (status TEXT)')
        db.execute('CREATE TABLE douyin_login_sessions (status TEXT)')
    previous={'version':'0.1.0','schema':1,'image':module.IMAGE+'@sha256:'+'a'*64}
    target={'version':'0.2.0','schema':1,'image':module.IMAGE+'@sha256:'+'b'*64}
    (tmp_path/'installed.json').write_text(json.dumps(previous))
    monkeypatch.setattr(module,'latest',lambda:target)
    monkeypatch.setattr(module.shutil,'disk_usage',lambda _:SimpleNamespace(free=10*1024**3))
    calls=[]
    monkeypatch.setattr(module,'compose',lambda *args:calls.append(args))
    return module,previous,target,calls


def test_rolls_back_image_but_does_not_rewind_database(host,monkeypatch):
    module,previous,target,calls=host
    def readiness(version):
        if version==target['version']:
            # A new process can write before the health check fails.
            with sqlite3.connect(module.ROOT/'data/spark.db') as db:
                db.execute("INSERT INTO task_runs VALUES ('submitted_unconfirmed')")
            raise RuntimeError('failed health')
    monkeypatch.setattr(module,'ready',readiness)
    with pytest.raises(RuntimeError):module.install('0.2.0')
    assert calls[-1][0]==previous['image']
    assert json.loads((module.ROOT/'installed.json').read_text())==previous
    with sqlite3.connect(module.ROOT/'data/spark.db') as db:
        assert db.execute('SELECT count(*) FROM task_runs').fetchone()[0]==1
    assert len(list((module.ROOT/'backups').glob('*/data/spark.db')))==1


def test_busy_accounts_block_update_before_stop(host):
    module,_,_,calls=host
    with sqlite3.connect(module.ROOT/'data/spark.db') as db:
        db.execute("INSERT INTO task_runs VALUES ('running')")
    with pytest.raises(ValueError,match='扫码或发送'):module.install('0.2.0')
    assert calls==[]


def test_success_records_actual_install(host,monkeypatch):
    module,_,target,calls=host
    monkeypatch.setattr(module,'ready',lambda _:None)
    module.install('0.2.0')
    assert json.loads((module.ROOT/'installed.json').read_text())==target
    assert module.STATE['phase']=='complete'


def test_arbitrary_image_rejected(host):
    module,previous,_,calls=host
    previous['image']='attacker/image:latest'
    (module.ROOT/'installed.json').write_text(json.dumps(previous))
    with pytest.raises(ValueError,match='固定镜像'):module.install('0.2.0')
    assert calls==[]
