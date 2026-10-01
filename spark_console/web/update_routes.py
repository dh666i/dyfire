"""Admin-only file queue. The web process never executes deployment commands."""
import json
import os
import re
import time
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from spark_console.db import session_scope
from spark_console.services.audits import AuditService
from spark_console.version import VERSION


def build_update_router(engine, auth):
    router = APIRouter()

    def newer(left, right):
        """Compare the three-part versions used by release tags."""
        try:
            a = tuple(int(part) for part in str(left).split('.'))
            b = tuple(int(part) for part in str(right).split('.'))
        except (TypeError, ValueError):
            return False
        return len(a) == len(b) == 3 and a > b

    def directory():
        value = os.environ.get('DYFIRE_UPDATE_DIR')
        if not value:
            raise HTTPException(503, '尚未配置更新服务')
        root = Path(value)
        if not (root / 'requests').is_dir():
            raise HTTPException(503, '更新服务目录未完成初始化')
        return root

    @router.get('/api/admin/updates')
    def status(request: Request):
        with session_scope(engine) as db:
            auth.admin_context(request, db)
        result = {
            'current': VERSION,
            'configured': bool(os.environ.get('DYFIRE_UPDATE_DIR')),
            'online': False,
            'available': None,
            'update_available': False,
            'release': {},
            'rollback_versions': [],
        }
        if result['configured']:
            try:
                state = json.loads((directory() / 'status.json').read_text())
                # status.json is host-owned. Copy only the fields in the public
                # contract so stale or unknown updater fields cannot change the
                # API shape or overwrite server-derived values.
                for key in (
                    'phase', 'message', 'heartbeat', 'latest', 'release_version',
                    'release_name', 'release_body', 'release_url',
                    'release_published_at', 'installed', 'web_installed',
                    'operation_id', 'requested_at', 'rollback_version', 'error_code',
                    'error_detail',
                ):
                    if key in state:
                        result[key] = state[key]
                result['update_available'] = newer(state.get('latest'), result['current'])
                result['available'] = state.get('latest') if result['update_available'] else None
                result['release'] = {
                    key: state.get(key)
                    for key in ('release_name', 'release_body', 'release_url', 'release_published_at')
                    if state.get(key)
                }
                if isinstance(state.get('rollback_versions'), list):
                    result['rollback_versions'] = [
                        {
                            key: item.get(key)
                            for key in ('version', 'url', 'published_at')
                            if item.get(key)
                        }
                        for item in state['rollback_versions']
                        if isinstance(item, dict) and re.fullmatch(r'\d+\.\d+\.\d+', str(item.get('version', '')))
                    ]
                try:
                    web = json.loads((directory().parent / 'web-installed.json').read_text())
                    result['web_current'] = web.get('version')
                except (OSError, ValueError):
                    result['web_current'] = result['current']
                result['online'] = time.time() - state.get('heartbeat', 0) < 90
            except (OSError, ValueError, HTTPException):
                result['online'] = False
        return result

    @router.post('/api/admin/updates/{action}')
    async def queue(action: str, request: Request):
        if action not in ('check', 'install', 'install-web', 'rollback'):
            raise HTTPException(404)
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            raise HTTPException(400, '请求格式无效')
        if not isinstance(body, dict):
            raise HTTPException(400, '请求格式无效')
        with session_scope(engine) as db:
            user, record, _ = auth.admin_context(request, db)
            auth.csrf(record, request.headers.get('X-CSRF-Token', ''))
            if action in ('install', 'install-web', 'rollback'):
                auth.require_elevated(record)
            target = body.get('version', '')
            if target is None:
                target = ''
            if not isinstance(target, str):
                raise HTTPException(400, '请选择有效的发行版本')
            if action in ('install', 'install-web') and not re.fullmatch(r'\d+\.\d+\.\d+', target):
                raise HTTPException(400, '请选择有效的发行版本')
            if action == 'rollback' and target and not re.fullmatch(r'\d+\.\d+\.\d+', target):
                    raise HTTPException(400, '请选择有效的发行版本')
            operation_id = uuid4().hex
            payload = {
                'action': action,
                'version': target,
                'operation_id': operation_id,
                'requested_at': time.time(),
            }
            requests = directory() / 'requests'
            pending = requests / (operation_id + '.tmp')
            try:
                with pending.open('x', encoding='utf-8') as stream:
                    json.dump(payload, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Atomic no-overwrite publish; updater cannot see partial JSON.
                os.link(pending, requests / 'request.json')
            except FileExistsError:
                raise HTTPException(409, '已有更新操作正在等待处理')
            finally:
                pending.unlink(missing_ok=True)
            AuditService(db).write(
                user.id,
                'system.update.' + action,
                'release',
                detail=f"{target or 'automatic'} · operation {operation_id[:8]}",
            )
        return {'accepted': True, 'operation_id': operation_id}

    return router
