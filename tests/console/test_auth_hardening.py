from unittest.mock import patch
from fastapi.testclient import TestClient
import pytest
from spark_console.db import session_scope
from spark_console.models import User
from tests.console.test_web_fixture import UserFixture

@pytest.fixture
def fixture():
    f=UserFixture();f.setUp()
    with session_scope(f.engine) as db:
        user=db.get(User,f.uid);user.role='admin';user.must_change_password=False
    f.login()
    yield f
    f.tearDown()

def test_admin_password_cannot_be_reset_through_customer_reset_endpoint(fixture):
    f=fixture;csrf=f.client.get('/api/auth/bootstrap').json()['csrf']
    response=f.client.post(f'/api/admin/users/{f.uid}/reset-password',json={'csrf_token':csrf})
    assert response.status_code==400
    assert 'temporary_password' not in response.json()

def test_security_headers_cover_spa_errors_and_malformed_cookies(fixture):
    f=fixture
    for path in ('/login','/api/version','/unknown-route'):
        response=f.client.get(path)
        csp=response.headers['content-security-policy']
        assert "script-src 'self'" in csp and "script-src 'self' 'unsafe-inline'" not in csp
        assert 'strict-transport-security' in response.headers
        assert response.headers.get('x-request-id')
    malformed=f.client.get('/api/auth/bootstrap',headers={'Cookie':'spark_session='+('%FF'*100)})
    assert malformed.status_code==401

def test_cross_origin_login_form_does_not_replace_the_browser_session(fixture):
    f=fixture;original=f.client.cookies.get('spark_session')
    response=f.client.post('/login',data={'username':'friend','password':'Temporary-123!'},headers={'Origin':'https://other.example'},follow_redirects=False)
    assert response.status_code==403
    assert f.client.cookies.get('spark_session')==original

def test_removed_admin_authenticator_and_elevation_routes_are_not_available(fixture):
    f=fixture
    assert f.client.get('/api/auth/mfa/totp').status_code==404
    assert f.client.post('/api/auth/elevate',json={}).status_code==404
    assert 'mfa_enabled' not in f.client.get('/api/auth/bootstrap').json()

@pytest.mark.parametrize('origin',['https://testserver:invalid','https://[broken','null','https://testserver/foreign','https://user@testserver'])
def test_malformed_login_origins_are_denied_without_server_error(fixture,origin):
    response=fixture.client.post('/login',data={'username':'friend','password':'unused'},headers={'Origin':origin},follow_redirects=False)
    assert response.status_code==403
