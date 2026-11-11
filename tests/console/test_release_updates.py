import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from spark_console.web import update_routes


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def session(_):
        yield SimpleNamespace(add=lambda value: None)
    class Auth:
        elevated = True
        def admin_context(self, request, db):
            if request.headers.get('Role') != 'admin':
                raise HTTPException(404)
            return SimpleNamespace(id='admin'), SimpleNamespace(), {}
        def csrf(self, record, value):
            if value != 'csrf':
                raise HTTPException(403)
        def require_elevated(self, record):
            if not self.elevated:
                raise HTTPException(428, '需要重新验证管理员密码')
    monkeypatch.setattr(update_routes, 'session_scope', session)
    monkeypatch.setenv('DYFIRE_UPDATE_DIR', str(tmp_path))
    (tmp_path / 'requests').mkdir()
    app = FastAPI()
    app.state.test_auth = Auth()
    app.include_router(update_routes.build_update_router(None, app.state.test_auth))
    return TestClient(app), tmp_path


def test_role_and_csrf(setup):
    client, root = setup
    assert client.get('/api/admin/updates').status_code == 404
    assert client.post('/api/admin/updates/install',json={'version':'0.2.0'},headers={'Role':'admin'}).status_code == 403
    assert not (root/'requests/request.json').exists()


def test_queue_is_exclusive_and_validates_version(setup):
    client, root = setup
    headers={'Role':'admin','X-CSRF-Token':'csrf'}
    assert client.post('/api/admin/updates/install',json={'version':'$(whoami)'},headers=headers).status_code == 400
    assert client.post('/api/admin/updates/install',json={'version':'0.2.0'},headers=headers).status_code == 200
    assert client.post('/api/admin/updates/check',json={},headers=headers).status_code == 409
    queued = json.loads((root/'requests/request.json').read_text())
    assert queued['version'] == '0.2.0'
    assert len(queued['operation_id']) == 32


@pytest.mark.parametrize('action,version', [
    ('check', ''), ('install', '0.2.0'), ('install-web', '0.2.0'), ('rollback', ''),
])
def test_matching_request_reuses_operation_without_duplicate_audit(setup, monkeypatch, action, version):
    client, root = setup
    audits = []
    monkeypatch.setattr(update_routes.AuditService, 'write', lambda *args, **kwargs: audits.append((args, kwargs)))
    headers = {'Role': 'admin', 'X-CSRF-Token': 'csrf'}
    first = client.post(f'/api/admin/updates/{action}', json={'version': version}, headers=headers)
    path = root / 'requests/request.json'
    original = path.read_bytes()
    second = client.post(f'/api/admin/updates/{action}', json={'version': version}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert second.json() == {'accepted': True, 'operation_id': first.json()['operation_id'], 'reused': True}
    assert path.read_bytes() == original
    assert len(audits) == 1


@pytest.mark.parametrize('action,version', [('install-web', '0.2.0'), ('install', '0.2.1')])
def test_different_operation_never_replaces_pending_request(setup, monkeypatch, action, version):
    client, root = setup
    audits = []
    monkeypatch.setattr(update_routes.AuditService, 'write', lambda *args, **kwargs: audits.append((args, kwargs)))
    headers = {'Role': 'admin', 'X-CSRF-Token': 'csrf'}
    assert client.post('/api/admin/updates/install', json={'version': '0.2.0'}, headers=headers).status_code == 200
    path = root / 'requests/request.json'
    original = path.read_bytes()
    assert client.post(f'/api/admin/updates/{action}', json={'version': version}, headers=headers).status_code == 409
    assert path.read_bytes() == original
    assert len(audits) == 1


@pytest.mark.parametrize('queued', [
    'invalid json', '[]', 'null', '"request"',
    '{"action":"install","version":"0.2.0"}',
    '{"action":"install","version":"0.2.0","operation_id":null}',
    '{"action":"install","version":"0.2.0","operation_id":42}',
    '{"action":"install","version":"0.2.0","operation_id":"  "}',
])
def test_malformed_pending_request_is_conflict_not_server_error(setup, queued):
    client, root = setup
    path = root / 'requests/request.json'
    path.write_text(queued, encoding='utf-8')
    response = client.post('/api/admin/updates/install', json={'version': '0.2.0'},
                           headers={'Role': 'admin', 'X-CSRF-Token': 'csrf'})
    assert response.status_code == 409
    assert path.read_text(encoding='utf-8') == queued
    assert list((root / 'requests').glob('*.tmp')) == []


@pytest.mark.parametrize('competing,status', [
    ({'action': 'install', 'version': '0.2.0', 'operation_id': 'a' * 32}, 200),
    ({'action': 'install', 'version': '0.2.1', 'operation_id': 'b' * 32}, 409),
    ({'action': 'install', 'version': '0.2.0'}, 409),
    ([], 409),
])
def test_atomic_publish_race_reuses_only_complete_matching_request(setup, monkeypatch, competing, status):
    client, root = setup
    audits = []
    monkeypatch.setattr(update_routes.AuditService, 'write', lambda *args, **kwargs: audits.append((args, kwargs)))

    def race(source, destination):
        Path(destination).write_text(json.dumps(competing), encoding='utf-8')
        raise FileExistsError()

    monkeypatch.setattr(update_routes.os, 'link', race)
    response = client.post('/api/admin/updates/install', json={'version': '0.2.0'},
                           headers={'Role': 'admin', 'X-CSRF-Token': 'csrf'})
    assert response.status_code == status
    if status == 200:
        assert response.json() == {'accepted': True, 'operation_id': competing['operation_id'], 'reused': True}
    assert json.loads((root / 'requests/request.json').read_text()) == competing
    assert list((root / 'requests').glob('*.tmp')) == []
    assert audits == []


@pytest.mark.parametrize('headers,status', [
    ({'X-CSRF-Token': 'csrf'}, 404),
    ({'Role': 'user', 'X-CSRF-Token': 'csrf'}, 404),
    ({'Role': 'admin'}, 403),
])
def test_idempotent_request_still_requires_admin_and_csrf(setup, monkeypatch, headers, status):
    client, root = setup
    audits = []
    monkeypatch.setattr(update_routes.AuditService, 'write', lambda *args, **kwargs: audits.append((args, kwargs)))
    first = client.post('/api/admin/updates/check', json={}, headers={'Role': 'admin', 'X-CSRF-Token': 'csrf'})
    path = root / 'requests/request.json'
    original = path.read_bytes()
    response = client.post('/api/admin/updates/check', json={}, headers=headers)
    assert response.status_code == status
    assert first.json()['operation_id'] not in response.text
    assert path.read_bytes() == original
    assert len(audits) == 1


def test_install_does_not_require_current_session_step_up(setup):
    client, root = setup
    client.app.state.test_auth.elevated = False
    response = client.post(
        '/api/admin/updates/install',
        json={'version': '0.2.0'},
        headers={'Role': 'admin', 'X-CSRF-Token': 'csrf'},
    )
    assert response.status_code == 200
    assert json.loads((root / 'requests/request.json').read_text())['action'] == 'install'


def test_web_only_action_is_available(setup):
    client, root = setup
    headers={'Role':'admin','X-CSRF-Token':'csrf'}
    response = client.post('/api/admin/updates/install-web', json={'version':'0.2.0'}, headers=headers)
    assert response.status_code == 200
    assert json.loads((root/'requests/request.json').read_text())['action'] == 'install-web'


def test_rollback_allows_default_candidate_and_rejects_invalid_payload(setup):
    client, root = setup
    headers = {'Role': 'admin', 'X-CSRF-Token': 'csrf'}
    response = client.post('/api/admin/updates/rollback', json={}, headers=headers)
    assert response.status_code == 200
    assert json.loads((root / 'requests/request.json').read_text())['version'] == ''

    (root / 'requests/request.json').unlink()
    response = client.post('/api/admin/updates/check', data='[]', headers=headers)
    assert response.status_code == 400


def test_status_exposes_only_newer_release(setup):
    client, root = setup
    current = tuple(int(part) for part in update_routes.VERSION.split('.'))
    newer = f'{current[0]}.{current[1]}.{current[2] + 1}'
    (root / 'status.json').write_text(json.dumps({
        'heartbeat': 9999999999,
        'latest': newer,
        'release_name': f'Dyfire {newer}',
        'release_body': 'notes',
        'rollback_versions': [
            {'version': '0.2.1', 'url': 'https://example.invalid', 'image': 'secret'},
            {'version': 'not-a-version', 'url': 'ignored'},
        ],
        'unknown': 'not-public',
    }))
    response = client.get('/api/admin/updates', headers={'Role': 'admin'})
    assert response.status_code == 200
    value = response.json()
    assert value['available'] == newer
    assert value['update_available'] is True
    assert value['rollback_versions'] == [{'version': '0.2.1', 'url': 'https://example.invalid'}]
    assert 'unknown' not in value


def test_browser_update_remains_available_when_web_is_current(setup):
    client, root = setup
    (root / 'status.json').write_text(json.dumps({
        'heartbeat': 9999999999,
        'latest': update_routes.VERSION,
        'component_versions': {'web': update_routes.VERSION, 'browser': '0.1.0'},
        'component_update_available': True,
        'web_update_available': False,
        'web_update_allowed': True,
        'web_update_reason': '',
        'web_download_plan': {'compressed_bytes': 0},
    }), encoding='utf-8')
    response = client.get('/api/admin/updates', headers={'Role': 'admin'})
    assert response.status_code == 200
    value = response.json()
    assert value['online'] is True
    assert value['available'] == update_routes.VERSION
    assert value['update_available'] is True
    assert value['component_update_available'] is True
    assert value['component_versions'] == {'web': update_routes.VERSION, 'browser': '0.1.0'}
    assert value['web_update_available'] is False
    assert value['web_update_allowed'] is True
    assert value['web_download_plan'] == {'compressed_bytes': 0}


@pytest.mark.parametrize('state', [[], None, {'heartbeat': []}, {'heartbeat': 'invalid'}])
def test_malformed_status_reports_offline_without_server_error(setup, state):
    client, root = setup
    (root / 'status.json').write_text(json.dumps(state), encoding='utf-8')
    response = client.get('/api/admin/updates', headers={'Role': 'admin'})
    assert response.status_code == 200
    assert response.json()['online'] is False


def test_malformed_web_installed_state_falls_back_to_running_version(setup, monkeypatch):
    client, root = setup
    state_dir = root / 'updater'
    (state_dir / 'requests').mkdir(parents=True)
    monkeypatch.setenv('DYFIRE_UPDATE_DIR', str(state_dir))
    (state_dir / 'status.json').write_text(json.dumps({'heartbeat': 9999999999}), encoding='utf-8')
    (root / 'web-installed.json').write_text('[]', encoding='utf-8')
    response = client.get('/api/admin/updates', headers={'Role': 'admin'})
    assert response.status_code == 200
    assert response.json()['online'] is True
    assert response.json()['web_current'] == update_routes.VERSION


def test_unconfigured_is_explicit(setup, monkeypatch):
    client, _ = setup
    monkeypatch.delenv('DYFIRE_UPDATE_DIR')
    assert client.get('/api/admin/updates',headers={'Role':'admin'}).json()['configured'] is False
    assert client.post('/api/admin/updates/check',json={},headers={'Role':'admin','X-CSRF-Token':'csrf'}).status_code==503
