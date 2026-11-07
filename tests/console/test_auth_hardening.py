from unittest.mock import patch
from dataclasses import replace
from fastapi.testclient import TestClient
import pytest
from spark_console.db import session_scope
from spark_console.models import User
from tests.console.test_web_fixture import UserFixture
from spark_console.web.app import create_app

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


def test_public_origin_login_works_through_bridge_with_internal_host(fixture):
    settings=replace(fixture.settings,public_origin='https://dyfire.cn')
    with TestClient(create_app(settings,fixture.engine),base_url='http://dyfire-origin.internal') as client:
        response=client.post('/login',data={'username':'friend','password':'Temporary-123!'},
            headers={'Origin':'https://dyfire.cn','Accept':'text/html'},follow_redirects=False)
        assert response.status_code==303 and response.headers['location']=='/dashboard'
        assert client.cookies.get('spark_session')
        # Internal bridge names and arbitrary Forwarded-Host values must not
        # grant another origin permission to log the browser in.
        original=client.cookies.get('spark_session')
        for origin in ('https://dyfire-origin.internal','https://other.example'):
            denied=client.post('/login',data={'username':'friend','password':'Temporary-123!'},
                headers={'Origin':origin,'X-Forwarded-Host':'dyfire.cn'},follow_redirects=False)
            assert denied.status_code==403
        assert original==client.cookies.get('spark_session')


def test_rejected_browser_form_returns_to_styled_login_without_json_screen(fixture):
    response=fixture.client.post('/login',data={'username':'friend','password':'unused'},
        headers={'Origin':'https://other.example','Accept':'text/html'},follow_redirects=False)
    assert response.status_code==303 and response.headers['location'].startswith('/login?error=')
    shell=fixture.client.get(response.headers['location'])
    assert shell.status_code==200 and '<div id="app"></div>' in shell.text


@pytest.mark.parametrize('path',['/register','/forgot-password','/forgot-password/reset'])
def test_all_auth_forms_use_the_configured_public_origin(fixture,path):
    settings=replace(fixture.settings,public_origin='https://dyfire.cn')
    with TestClient(create_app(settings,fixture.engine),base_url='http://internal.invalid') as client:
        body={'username':'friend','password':'Temporary-123!','email':'fixture@example.com','code':'wrong-code'}
        result=client.post(path,data=body,headers={'Origin':'https://dyfire.cn'},follow_redirects=False)
        assert result.status_code!=403
        denied=client.post(path,data=body,headers={'Origin':'https://other.example'},follow_redirects=False)
        assert denied.status_code==403


@pytest.mark.parametrize('origin',['https://dyfire.cn/path','https://user@dyfire.cn','https://dyfire.cn:bad','null','https://dyfire.cn/?x=1'])
def test_invalid_canonical_origin_is_rejected_at_startup(fixture,origin):
    with pytest.raises(ValueError,match='public origin'):
        replace(fixture.settings,public_origin=origin)
