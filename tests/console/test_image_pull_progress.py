import io
import json
import pytest
from deploy.image_pull import Progress, pull


def test_docker_download_events_distinguish_reused_layers_and_never_regress():
    progress=Progress({'sha256:'+'a'*64:100,'sha256:'+'b'*64:400})
    value=progress.update({'id':'a'*12,'status':'Already exists'})
    assert value['resolved_bytes']==100 and value['downloaded_bytes']==0
    value=progress.update({'id':'b'*12,'status':'Downloading','progressDetail':{'current':200,'total':400}})
    assert value['percent']==60 and value['downloaded_bytes']==200
    value=progress.update({'id':'b'*12,'status':'Downloading','progressDetail':{'current':50}})
    assert value['percent']==60
    value=progress.update({'id':'b'*12,'status':'Download complete'})
    assert value['percent']==100 and value['layers_ready']==2


def test_pull_requires_engine_stream_completion_and_keeps_credentials_out_of_progress(monkeypatch):
    from deploy import image_pull
    class Connection:
        def __init__(self,*args,**kwargs):pass
        def request(self,method,path,**kwargs):self.method=method
        def getresponse(self):
            if self.method=='GET':
                stream=io.BytesIO(json.dumps({'Id':'sha256:'+'c'*64}).encode());stream.status=200;return stream
            stream=io.BytesIO(b'{"id":"aaaaaaaaaaaa","status":"Already exists"}\n');stream.status=200;return stream
        def close(self):pass
    monkeypatch.setattr(image_pull,'DockerConnection',Connection)
    monkeypatch.setattr(image_pull,'registry_auth',lambda:{'password':'must-not-leak'})
    values=[];pull(image_pull.IMAGE+'@sha256:'+'c'*64,{'sha256:'+'a'*64:100},values.append,timeout=5)
    assert values[-1]['percent']==100
    assert 'must-not-leak' not in json.dumps(values)


def test_registry_error_never_becomes_completed_progress(monkeypatch):
    from deploy import image_pull
    class Connection:
        def __init__(self,*args,**kwargs):pass
        def request(self,*args,**kwargs):pass
        def getresponse(self):
            stream=io.BytesIO(b'{"error":"private registry error"}\n');stream.status=200;return stream
        def close(self):pass
    monkeypatch.setattr(image_pull,'DockerConnection',Connection);monkeypatch.setattr(image_pull,'registry_auth',lambda:{})
    values=[]
    with pytest.raises(RuntimeError):pull(image_pull.IMAGE+'@sha256:'+'c'*64,{},values.append,timeout=5)
    assert not any(value['percent']==100 for value in values)
