"""Durable, host-owned deployment journal. Never restores application data."""
import json
import os
from pathlib import Path
import re
import time

TERMINAL = {'complete', 'failed', 'rolled_back', 'recovery_failed'}


def atomic_text(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(mode)
    temporary.replace(path)
    if os.name != 'nt':
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def atomic_json(path, value, mode=0o600):
    atomic_text(path, json.dumps(value, ensure_ascii=False), mode)


class Journal:
    def __init__(self, root):
        self.path = Path(root) / 'updates' / 'transaction.json'

    def read(self):
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or not re.fullmatch(r'[a-f0-9]{32}', str(value.get('operation_id', ''))):
            raise ValueError('更新事务记录无效，保持当前服务并检查部署记录')
        return value

    def accept(self, request, now=None):
        now = time.time() if now is None else now
        operation = str(request.get('operation_id', ''))
        if not re.fullmatch(r'[a-f0-9]{32}', operation):
            raise ValueError('更新操作编号无效')
        existing = self.read()
        if existing.get('operation_id') == operation:
            if existing.get('action') != request.get('action') or existing.get('version') != request.get('version', ''):
                raise ValueError('更新操作内容与已接受请求不一致')
            return existing
        if existing and existing.get('stage') not in TERMINAL:
            raise ValueError('上一更新事务尚未恢复，暂不能接受新操作')
        requested_at = request.get('requested_at')
        if not isinstance(requested_at, (int, float)) or not -30 <= now - requested_at <= 300:
            raise ValueError('请求已过期，请重试')
        value = {'operation_id': operation, 'action': request.get('action'),
                 'version': request.get('version', ''), 'requested_at': requested_at,
                 'accepted_at': now, 'stage': 'accepted'}
        atomic_json(self.path, value)
        return value

    def update(self, **fields):
        value = self.read()
        if not value:
            raise ValueError('尚未接受更新事务')
        value.update(fields, updated_at=time.time())
        atomic_json(self.path, value)
        return value

    def checkpoint(self, operation_id, **fields):
        # Direct host function calls in tests/initial setup need no fake journal.
        value = self.read()
        if value and value['operation_id'] == operation_id:
            return self.update(**fields)
        return value
