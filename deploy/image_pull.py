"""Read Docker Engine pull events; share one immutable image across services."""
import base64
import http.client
import json
import os
from pathlib import Path
import queue
import re
import socket
import threading
import time
from urllib.parse import urlencode

IMAGE='ghcr.io/dh666i/dyfire'


class DockerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout);self.sock.connect('/run/docker.sock')


def registry_auth():
    directory=Path(os.environ.get('DOCKER_CONFIG',str(Path.home()/'.docker')))
    try:settings=json.loads((directory/'config.json').read_text())
    except (OSError,ValueError):return {}
    value=settings.get('auths',{}).get('ghcr.io',{})
    if value.get('identitytoken'):return {'identitytoken':value['identitytoken'],'serveraddress':'ghcr.io'}
    if value.get('auth'):
        user,password=base64.b64decode(value['auth']).decode().split(':',1)
        return {'username':user,'password':password,'serveraddress':'ghcr.io'}
    return {}


class Progress:
    def __init__(self,layers):
        self.sizes={key.removeprefix('sha256:')[:12]:size for key,size in layers.items()}
        self.downloaded={};self.resolved={};self.reused=set();self.status='等待镜像仓库'
        self.extracted={};self.phases=set()
        self.started=time.monotonic();self.last_progress=self.started

    def update(self,event):
        now=time.monotonic();advanced=False
        identifier=str(event.get('id',''))[:12];status=event.get('status')
        valid_identifier=bool(re.fullmatch(r'[a-f0-9]{12}',identifier))
        # Registry metadata is optional. Engine byte counters still show real
        # work when layer sizes could not be obtained before the pull.
        if status in ('Downloading','Extracting') and valid_identifier:
            current=event.get('progressDetail',{}).get('current',0)
            if type(current) is int and current>=0:
                counters=self.downloaded if status=='Downloading' else self.extracted
                if status=='Downloading' and identifier in self.sizes:
                    current=min(self.sizes[identifier],current)
                previous=counters.get(identifier,0)
                advanced=current>previous
                counters[identifier]=max(previous,current)
                if status=='Downloading' and identifier in self.sizes:
                    self.resolved[identifier]=max(self.resolved.get(identifier,0),current)
            self.status='正在下载镜像层' if status=='Downloading' else '正在解压镜像层'
        # Each phase can advance a layer once. Repeated identical byte or
        # status events must not disguise a genuinely stalled Docker stream.
        if valid_identifier and status in ('Pulling fs layer','Waiting','Downloading',
                'Verifying Checksum','Download complete','Extracting','Pull complete','Already exists'):
            phase=(identifier,status)
            if phase not in self.phases:
                self.phases.add(phase);advanced=True
        if status in ('Already exists','Download complete','Pull complete') and identifier in self.sizes:
            self.resolved[identifier]=self.sizes[identifier]
        if status=='Already exists' and valid_identifier:self.reused.add(identifier)
        if advanced:self.last_progress=now
        total=sum(self.sizes.values());resolved=sum(self.resolved.values())
        return {'total_bytes':total,'resolved_bytes':resolved,'downloaded_bytes':sum(self.downloaded.values()),
            'reused_bytes':sum(self.sizes.get(key,0) for key in self.reused),
            'bytes_per_second':round(sum(self.downloaded.values())/max(1,now-self.started)),
            'idle_seconds':round(now-self.last_progress),
            'percent':min(100,round(resolved*100/total,1)) if total else None,
            'layers_ready':sum(self.resolved.get(key,0)==size for key,size in self.sizes.items()),
            'layers_total':len(self.sizes),'label':self.status,'scope':'image layers only; excludes metadata and protocol overhead'}


def _present(image):
    connection=DockerConnection('docker',timeout=30)
    try:
        connection.request('GET','/images/'+image+'/json')
        response=connection.getresponse()
        if response.status!=200:return False
        record=json.loads(response.read(1024*1024))
        return isinstance(record,dict) and (record.get('Id')==image.split('@',1)[1] or image in (record.get('RepoDigests') or []))
    except (OSError,ValueError):return False
    finally:connection.close()


def pull(image,layers,publish,*,timeout=3600):
    if not re.fullmatch(re.escape(IMAGE)+r'@sha256:[a-f0-9]{64}',str(image)):raise ValueError('镜像摘要无效')
    deadline=time.monotonic()+timeout
    for attempt in range(1,4):
        # A previous connection may have ended after Docker completed the
        # image. Verify its fixed digest before starting another pull stream.
        if _present(image):
            total=sum(layers.values())
            publish({'percent':100,'label':'镜像已在本机缓存，正在校验','total_bytes':total,
                     'resolved_bytes':total,'downloaded_bytes':0,'reused_bytes':total,
                     'layers_ready':len(layers),'layers_total':len(layers),'bytes_per_second':0,
                     'idle_seconds':0,'attempt':attempt})
            return
        try:
            return _pull_once(image,layers,lambda state:publish({**state,'attempt':attempt}),timeout=max(1,deadline-time.monotonic()))
        except (OSError,RuntimeError,TimeoutError):
            if attempt==3 or time.monotonic()+2**attempt>=deadline:raise
            publish({'percent':None,'label':'下载连接中断，正在重试；已完成镜像层会保留','attempt':attempt,
                     'layers_total':len(layers),'layers_ready':0,'downloaded_bytes':0})
            time.sleep(2**attempt)


def _pull_once(image,layers,publish,*,timeout=3600):
    if not re.fullmatch(re.escape(IMAGE)+r'@sha256:[a-f0-9]{64}',str(image)):raise ValueError('镜像摘要无效')
    connection=DockerConnection('docker',timeout=timeout)
    updates=queue.Queue(maxsize=64);stop=threading.Event();progress=Progress(layers)
    def reader():
        try:
            auth=base64.urlsafe_b64encode(json.dumps(registry_auth()).encode()).decode()
            connection.request('POST','/images/create?'+urlencode({'fromImage':image}),headers={'X-Registry-Auth':auth,'Content-Length':'0'})
            response=connection.getresponse()
            if response.status!=200:raise RuntimeError('镜像仓库或 Docker Engine 拒绝下载')
            while not stop.is_set():
                line=response.readline(65537)
                if not line:break
                if len(line)>65536:raise RuntimeError('镜像进度数据超长')
                event=json.loads(line)
                while not stop.is_set():
                    try:updates.put(event,timeout=1);break
                    except queue.Full:pass
            updates.put(None,timeout=2)
        except Exception:
            try:updates.put({'error':'镜像下载连接中断或被拒绝，请查看 Docker 日志后重试'},timeout=2)
            except queue.Full:pass
    worker=threading.Thread(target=reader,daemon=True);worker.start()
    deadline=time.monotonic()+timeout;last=0;state=progress.update({})
    try:
        while time.monotonic()<deadline:
            try:event=updates.get(timeout=2)
            except queue.Empty:
                if not worker.is_alive():raise RuntimeError('镜像进度连接已结束，未取得完成确认')
                event={}
            if event is None:
                verification=DockerConnection('docker',timeout=30)
                try:
                    verification.request('GET','/images/'+image+'/json')
                    response=verification.getresponse()
                    if response.status!=200:raise RuntimeError('下载结束后未找到固定摘要镜像，原服务保持运行')
                    record=json.loads(response.read(1024*1024))
                    digest=image.split('@',1)[1]
                    if not isinstance(record,dict) or (record.get('Id')!=digest and image not in (record.get('RepoDigests') or [])):
                        raise RuntimeError('下载镜像与发行摘要不一致，原服务保持运行')
                finally:verification.close()
                publish({**state,'percent':100,'resolved_bytes':sum(layers.values()),
                         'layers_ready':len(layers),'label':'镜像已下载，正在校验'})
                return
            if event.get('error') or event.get('errorDetail'):raise RuntimeError('镜像下载失败，请查看 Docker 日志后重试')
            state=progress.update(event)
            if state['idle_seconds']>=120:
                raise TimeoutError('镜像下载长时间无进展，准备重试')
            if time.monotonic()-last>=2:publish(state);last=time.monotonic()
        raise TimeoutError('镜像下载超时，原服务保持运行')
    finally:
        stop.set()
        active_socket=getattr(connection,'sock',None)
        if active_socket is not None:
            try:active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        connection.close()
        worker.join(timeout=2)
