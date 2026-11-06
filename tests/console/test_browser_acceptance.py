"""Real Chromium against an isolated server/database. No Douyin/SMTP workers."""
import os
from pathlib import Path
import socket
import threading
import time
from datetime import datetime, timezone

import pytest
import uvicorn
from playwright.sync_api import sync_playwright
from sqlalchemy import select

from spark_console.db import session_scope
from spark_console.models import User, DouyinAccount, SparkTask, TaskRun
from spark_console.security import SessionService
from tests.console.test_web_fixture import UserFixture


@pytest.fixture(scope='module')
def browser_site():
    fixture=UserFixture();fixture.setUp()
    tokens={}
    with session_scope(fixture.engine) as db:
        user=db.get(User,fixture.uid);user.must_change_password=False
        admin=User(username='browser-admin',password_hash='unused',role='admin',must_change_password=False)
        db.add(admin);db.flush()
        for role,record in [('user',user),('admin',admin)]:
            token,session=SessionService(fixture.settings.session_key_file.read_bytes()).create_record(record.id)
            db.add(session);tokens[role]=token
        account=DouyinAccount(owner_user_id=user.id,display_name='验收账号',encrypted_cookies=b'isolated',cookie_nonce=b'isolated',validation_state='verification')
        db.add(account);db.flush()
        task=SparkTask(owner_user_id=user.id,douyin_account_id=account.id,target_name='验收好友',send_time='09:30',message_kind='douyin_sticker',message_template='续火花',enabled=True)
        db.add(task);db.flush()
        now=datetime.now(timezone.utc)
        db.add(TaskRun(task_id=task.id,scheduled_for=now,status='failed',stage='authenticating',error_code='account_verification_required',error_summary='需要本人验证',finished_at=now))
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(fixture.client.app,log_level='error',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    deadline=time.monotonic()+10
    while not server.started and time.monotonic()<deadline:time.sleep(.02)
    assert server.started
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            server.should_exit=True;thread.join(10);fixture.tearDown()
            if os.environ.get('DYFIRE_REQUIRE_BROWSER')=='1':pytest.fail('Chromium is required in CI')
            pytest.skip('Chromium not installed; browser gate is required on CI')
        browser=playwright.chromium.launch(headless=True)
        try:yield browser,f'http://127.0.0.1:{port}',tokens
        finally:
            browser.close();server.should_exit=True;thread.join(10);sock.close();fixture.tearDown()


@pytest.mark.parametrize('width',[360,390,430,1440])
@pytest.mark.parametrize('theme',['dark','light'])
@pytest.mark.parametrize('role',['user','admin'])
def test_routes_both_themes_and_mobile_sizes(browser_site,width,theme,role):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce')
    context.add_init_script(f"localStorage.setItem('dyfire-theme','{theme}')")
    context.add_cookies([{'name':'spark_session','value':tokens[role],'url':base}])
    page=context.new_page();errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    routes=['/dashboard','/studio','/accounts','/runs','/subscription','/account-center','/notifications','/help']
    if role=='admin':routes+=['/admin/overview','/admin/users','/admin/codes','/admin/runs','/admin/operations','/admin/settings','/admin/settings/mail']
    try:
        for route in routes:
            page.goto(base+route,wait_until='networkidle')
            page.locator('.fire-main').wait_for()
            assert page.locator('.loading-screen').count()==0,route
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'),f'{role} {theme} {width} {route} overflows'
            assert not errors,errors
        if role=='user':
            response=context.request.get(base+'/api/admin/bootstrap')
            assert response.status in (403,404)
    finally:context.close()


def test_login_has_no_extra_administrator_verification(browser_site):
    browser,base,_tokens=browser_site
    context=browser.new_context(viewport={'width':390,'height':844})
    try:
        page=context.new_page();page.goto(base+'/login',wait_until='networkidle')
        assert page.get_by_label('登录账号').count()==1
        assert page.get_by_text('验证器',exact=False).count()==0
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    finally:context.close()
