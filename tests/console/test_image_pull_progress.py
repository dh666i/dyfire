import io
import json
import pytest
from deploy.image_pull import Progress, pull


def test_docker_download_events_distinguish_reused_layers_and_never_regress():
    progress=Progress({'sha256:'+'a'*64:100,'sha256:'+'b'*64:400})
    value=progress.update({'id':'a'*12,'status':'Already exists'})
    assert value['resolved_bytes']==100 and value['downloaded_bytes']==0
    assert value['reused_bytes']==100
    value=progress.update({'id':'b'*12,'status':'Downloading','progressDetail':{'current':200,'total':400}})
    assert value['percent']==60 and value['downloaded_bytes']==200
    value=progress.update({'id':'b'*12,'status':'Downloading','progressDetail':{'current':50}})
    assert value['percent']==60
    value=progress.update({'id':'b'*12,'status':'Download complete'})
    assert value['percent']==100 and value['layers_ready']==2


def test_pull_requires_engine_stream_completion_and_keeps_credentials_out_of_progress(monkeypatch):
    from deploy import image_pull
    requests=[]
    class Connection:
        def __init__(self,*args,**kwargs):pass
        def request(self,method,path,**kwargs):self.method=method;requests.append(method)
        def getresponse(self):
            if self.method=='GET':
                stream=io.BytesIO(json.dumps({'Id':'sha256:'+'c'*64}).encode());stream.status=200;return stream
            stream=io.BytesIO(b'{"id":"aaaaaaaaaaaa","status":"Already exists"}\n');stream.status=200;return stream
        def close(self):pass
    monkeypatch.setattr(image_pull,'DockerConnection',Connection)
    monkeypatch.setattr(image_pull,'_present',lambda image:False)
    monkeypatch.setattr(image_pull,'registry_auth',lambda:{'password':'must-not-leak'})
    values=[];pull(image_pull.IMAGE+'@sha256:'+'c'*64,{'sha256:'+'a'*64:100},values.append,timeout=5)
    assert values[-1]['percent']==100
    assert requests==['POST','GET']
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
    monkeypatch.setattr(image_pull.time,'sleep',lambda seconds:None)
    values=[]
    with pytest.raises(RuntimeError):pull(image_pull.IMAGE+'@sha256:'+'c'*64,{},values.append,timeout=5)
    assert not any(value['percent']==100 for value in values)


def test_active_extraction_keeps_watchdog_alive_without_counting_expanded_bytes_as_download(monkeypatch):
    from deploy import image_pull
    clock=[0.0]
    monkeypatch.setattr(image_pull.time,'monotonic',lambda:clock[0])
    progress=Progress({'sha256:'+'a'*64:100})
    progress.update({'id':'a'*12,'status':'Downloading','progressDetail':{'current':100}})
    clock[0]=90
    progress.update({'id':'a'*12,'status':'Extracting','progressDetail':{'current':300}})
    clock[0]=180
    value=progress.update({'id':'a'*12,'status':'Extracting','progressDetail':{'current':900}})
    assert value['idle_seconds']==0
    assert value['downloaded_bytes']==100
    assert value['label']=='正在解压镜像层'


@pytest.mark.parametrize('status',['Downloading','Extracting','Already exists','Pull complete'])
def test_repeated_progress_cannot_hide_a_stalled_pull(monkeypatch,status):
    from deploy import image_pull
    clock=[0.0]
    monkeypatch.setattr(image_pull.time,'monotonic',lambda:clock[0])
    progress=Progress({'sha256:'+'a'*64:100})
    event={'id':'a'*12,'status':status,'progressDetail':{'current':50}}
    progress.update(event)
    clock[0]=125
    value=progress.update(event)
    assert value['idle_seconds']==125
    if status in ('Downloading','Extracting'):
        value=progress.update({**event,'progressDetail':{'current':10}})
        assert value['idle_seconds']==125


def test_missing_registry_sizes_still_tracks_real_engine_work(monkeypatch):
    from deploy import image_pull
    clock=[0.0]
    monkeypatch.setattr(image_pull.time,'monotonic',lambda:clock[0])
    progress=Progress({})
    clock[0]=90
    value=progress.update({'id':'a'*12,'status':'Downloading','progressDetail':{'current':1024}})
    assert value['downloaded_bytes']==1024 and value['percent'] is None
    clock[0]=180
    value=progress.update({'id':'a'*12,'status':'Extracting','progressDetail':{'current':4096}})
    assert value['idle_seconds']==0 and value['downloaded_bytes']==1024
    clock[0]=305
    assert progress.update({})['idle_seconds']==125


def test_verified_cached_digest_skips_network_pull(monkeypatch):
    from deploy import image_pull
    image=image_pull.IMAGE+'@sha256:'+'c'*64
    monkeypatch.setattr(image_pull,'_present',lambda selected:selected==image)
    monkeypatch.setattr(image_pull,'_pull_once',lambda *a,**k:pytest.fail('Cached image must not be pulled'))
    values=[]
    pull(image,{'sha256:'+'a'*64:100},values.append)
    assert values[-1]['percent']==100
    assert values[-1]['downloaded_bytes']==0 and values[-1]['reused_bytes']==100


def test_disconnected_pull_rechecks_cache_before_retrying(monkeypatch):
    from deploy import image_pull
    checks=iter([False,True]);attempts=[]
    monkeypatch.setattr(image_pull,'_present',lambda image:next(checks))
    monkeypatch.setattr(image_pull.time,'sleep',lambda seconds:None)
    def interrupted(*args,**kwargs):
        attempts.append(True)
        raise OSError('private connection detail')
    monkeypatch.setattr(image_pull,'_pull_once',interrupted)
    values=[]
    pull(image_pull.IMAGE+'@sha256:'+'c'*64,{},values.append)
    assert len(attempts)==1 and values[-1]['attempt']==2 and values[-1]['percent']==100
    assert 'private connection detail' not in json.dumps(values)


def test_retries_are_bounded_and_never_publish_false_completion(monkeypatch):
    from deploy import image_pull
    attempts=[];delays=[]
    monkeypatch.setattr(image_pull,'_present',lambda image:False)
    monkeypatch.setattr(image_pull.time,'sleep',delays.append)
    def interrupted(*args,**kwargs):
        attempts.append(True)
        raise OSError('connection ended')
    monkeypatch.setattr(image_pull,'_pull_once',interrupted)
    values=[]
    with pytest.raises(OSError):
        pull(image_pull.IMAGE+'@sha256:'+'c'*64,{},values.append)
    assert len(attempts)==3 and delays==[2,4]
    assert not any(value['percent']==100 for value in values)


def test_stream_eof_without_the_expected_digest_is_not_success(monkeypatch):
    from deploy import image_pull
    class Connection:
        def __init__(self,*args,**kwargs):pass
        def request(self,method,path,**kwargs):self.method=method
        def getresponse(self):
            body=json.dumps({'Id':'sha256:'+'d'*64}).encode() if self.method=='GET' else b''
            stream=io.BytesIO(body);stream.status=200;return stream
        def close(self):pass
    monkeypatch.setattr(image_pull,'DockerConnection',Connection)
    monkeypatch.setattr(image_pull,'registry_auth',lambda:{})
    monkeypatch.setattr(image_pull,'_present',lambda image:False)
    monkeypatch.setattr(image_pull.time,'sleep',lambda seconds:None)
    values=[]
    with pytest.raises(RuntimeError,match='发行摘要不一致'):
        pull(image_pull.IMAGE+'@sha256:'+'c'*64,{},values.append)
    assert not any(value['percent']==100 for value in values)


def test_engine_repeating_the_same_progress_is_stopped_by_watchdog(monkeypatch):
    from deploy import image_pull
    clock=[0.0]
    def monotonic():
        clock[0]+=10
        return clock[0]
    class Connection:
        def __init__(self,*args,**kwargs):pass
        def request(self,method,path,**kwargs):pass
        def getresponse(self):
            event={'id':'a'*12,'status':'Downloading','progressDetail':{'current':50}}
            stream=io.BytesIO(((json.dumps(event)+'\n')*32).encode());stream.status=200;return stream
        def close(self):pass
    monkeypatch.setattr(image_pull,'DockerConnection',Connection)
    monkeypatch.setattr(image_pull,'registry_auth',lambda:{})
    monkeypatch.setattr(image_pull.time,'monotonic',monotonic)
    values=[]
    with pytest.raises(TimeoutError,match='长时间无进展'):
        image_pull._pull_once(image_pull.IMAGE+'@sha256:'+'c'*64,
                              {'sha256:'+'a'*64:100},values.append,timeout=1000)
    assert not any(value['percent']==100 for value in values)
