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
        def admin_context(self, request, db):
            if request.headers.get('Role') != 'admin':
                raise HTTPException(404)
            return SimpleNamespace(id='admin'), None, {}
        def csrf(self, record, value):
            if value != 'csrf':
                raise HTTPException(403)
    monkeypatch.setattr(update_routes, 'session_scope', session)
    monkeypatch.setenv('DYFIRE_UPDATE_DIR', str(tmp_path))
    (tmp_path / 'requests').mkdir()
    app = FastAPI()
    app.include_router(update_routes.build_update_router(None, Auth()))
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
    assert json.loads((root/'requests/request.json').read_text())['version']=='0.2.0'


def test_web_only_action_is_available(setup):
    client, root = setup
    headers={'Role':'admin','X-CSRF-Token':'csrf'}
    response = client.post('/api/admin/updates/install-web', json={'version':'0.2.0'}, headers=headers)
    assert response.status_code == 200
    assert json.loads((root/'requests/request.json').read_text())['action'] == 'install-web'


def test_unconfigured_is_explicit(setup, monkeypatch):
    client, _ = setup
    monkeypatch.delenv('DYFIRE_UPDATE_DIR')
    assert client.get('/api/admin/updates',headers={'Role':'admin'}).json()['configured'] is False
    assert client.post('/api/admin/updates/check',json={},headers={'Role':'admin','X-CSRF-Token':'csrf'}).status_code==503
