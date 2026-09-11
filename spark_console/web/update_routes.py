"""Admin-only file queue. The web process never executes deployment commands."""
import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from spark_console.db import session_scope
from spark_console.services.audits import AuditService
from spark_console.version import VERSION


def build_update_router(engine, auth):
    router = APIRouter()

    def directory():
        value = os.environ.get('DYFIRE_UPDATE_DIR')
        if not value:
            raise HTTPException(503, '尚未配置更新服务')
        return Path(value)

    @router.get('/api/admin/updates')
    def status(request: Request):
        with session_scope(engine) as db:
            auth.admin_context(request, db)
        result = {'current': VERSION, 'configured': bool(os.environ.get('DYFIRE_UPDATE_DIR'))}
        if result['configured']:
            try:
                state = json.loads((directory() / 'status.json').read_text())
                result.update(state)
                result['online'] = time.time() - state.get('heartbeat', 0) < 90
            except (OSError, ValueError):
                result['online'] = False
        return result

    @router.post('/api/admin/updates/{action}')
    async def queue(action: str, request: Request):
        if action not in ('check', 'install'):
            raise HTTPException(404)
        body = await request.json()
        with session_scope(engine) as db:
            user, record, _ = auth.admin_context(request, db)
            auth.csrf(record, request.headers.get('X-CSRF-Token', ''))
            target = body.get('version', '')
            if action == 'install':
                import re
                if not isinstance(target, str) or not re.fullmatch(r'\d+\.\d+\.\d+', target):
                    raise HTTPException(400, '请选择有效的发行版本')
            payload = {'action': action, 'version': target, 'requested_at': time.time()}
            try:
                with (directory() / 'requests' / 'request.json').open('x', encoding='utf-8') as stream:
                    json.dump(payload, stream)
            except FileExistsError:
                raise HTTPException(409, '已有更新操作正在等待处理')
            AuditService(db).write(user.id, 'system.update.' + action, 'release', detail=target)
        return {'accepted': True}

    return router
