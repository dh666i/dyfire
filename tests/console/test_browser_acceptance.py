"""Real Chromium against an isolated server/database. No Douyin/SMTP workers."""
import os
import re
from pathlib import Path
import socket
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone

import pytest
import uvicorn
from playwright.sync_api import sync_playwright
from playwright.sync_api import expect
from sqlalchemy import select

from spark_console.db import session_scope
from spark_console.models import User, DouyinAccount, SparkTask, TaskRun
from spark_console.security import SessionService
from spark_console.crypto import CookieCipher
from spark_console.services.subscriptions import SubscriptionService
from spark_console.services.audits import AuditService
from tests.console.test_web_fixture import UserFixture
from spark_console.web.app import create_app


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
        _row,tokens['redemption_code']=SubscriptionService(db,AuditService(db),cipher=CookieCipher(b'c'*32)).create_redemption_code(admin.id)
        account=DouyinAccount(owner_user_id=user.id,display_name='验收账号',encrypted_cookies=b'isolated',cookie_nonce=b'isolated',validation_state='verification')
        db.add(account);db.flush()
        task=SparkTask(owner_user_id=user.id,douyin_account_id=account.id,target_name='验收好友',send_time='09:30',message_kind='douyin_sticker',message_template='续火花',enabled=True)
        db.add(task);db.flush()
        now=datetime.now(timezone.utc)
        db.add(TaskRun(task_id=task.id,scheduled_for=now,status='failed',stage='authenticating',error_code='account_verification_required',error_summary='需要本人验证',finished_at=now))
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    port=sock.getsockname()[1]
    # HTTP is confined to this isolated loopback fixture. Production cookies
    # stay secure and the public origin is configured independently.
    app=create_app(replace(fixture.settings,secure_cookies=False),fixture.engine)
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
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


def test_browser_submits_login_form_and_receives_authenticated_dashboard(browser_site):
    browser,base,_tokens=browser_site
    context=browser.new_context(viewport={'width':390,'height':844})
    try:
        page=context.new_page();page.goto(base+'/login',wait_until='networkidle')
        page.locator('input[name=username]').fill('friend')
        page.locator('input[name=password]').fill('Temporary-123!')
        page.locator('button[type=submit]').click()
        page.wait_for_url('**/dashboard');page.locator('.fire-main').wait_for()
        assert any(cookie['name']=='spark_session' for cookie in context.cookies())
    finally:context.close()


def test_editing_preserves_unsaved_time_when_close_is_cancelled(browser_site):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        page=context.new_page();page.goto(base+'/studio',wait_until='networkidle')
        page.get_by_role('button',name=re.compile('编辑.*验收好友')).first.click()
        editor=page.get_by_role('dialog',name='编辑 验收好友')
        editor.locator('input[type=time]').fill('10:45')
        editor.get_by_role('button',name='关闭',exact=True).click()
        confirm=page.get_by_role('dialog',name='放弃未保存的修改？')
        expect(confirm).to_be_visible()
        confirm.get_by_role('button',name='继续编辑',exact=True).click()
        expect(editor).to_be_visible();expect(editor.locator('input[type=time]')).to_have_value('10:45')
        editor.get_by_role('button',name='关闭',exact=True).click()
        page.get_by_role('dialog',name='放弃未保存的修改？').get_by_role('button',name='放弃修改',exact=True).click()
        expect(editor).to_have_count(0)
    finally:context.close()


def test_admin_task_edit_shows_before_after_diff_before_save(browser_site):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':1440,'height':900},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['admin'],'url':base}])
    try:
        page=context.new_page()
        group=context.request.get(base+'/api/admin/task-groups?q=friend').json()['items'][0]
        page.goto(base+f"/admin/tasks/groups/{group['owner_id']}/{group['account_id']}",wait_until='networkidle')
        page.locator('.task-detail-row').filter(has_text='验收好友').get_by_role('button',name='编辑',exact=True).click()
        editor=page.get_by_role('dialog',name=re.compile('编辑 验收好友'))
        editor.locator('input[type=time]').fill('10:45')
        preview=editor.locator('.admin-change-preview')
        expect(preview).to_contain_text('每日发送时间')
        expect(preview).to_contain_text('09:30 → 10:45')
        editor.get_by_role('button',name='保存任务',exact=True).click()
        confirm=page.get_by_role('dialog',name='确认保存任务修改')
        expect(confirm.locator('.modal-dialog-body')).to_contain_text('每日发送时间：09:30 → 10:45')
        confirm.get_by_role('button',name='确认保存',exact=True).click()
        expect(editor).to_have_count(0)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    finally:context.close()


def test_admin_sees_full_redemption_code_and_copies_without_extra_request(browser_site):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':1440,'height':900},permissions=['clipboard-read','clipboard-write'])
    context.add_cookies([{'name':'spark_session','value':tokens['admin'],'url':base}])
    try:
        page=context.new_page();extra=[]
        page.on('request',lambda request:extra.append(request.url) if request.url.endswith('/reveal') else None)
        page.goto(base+'/admin/codes',wait_until='networkidle')
        code=tokens['redemption_code'];cell=page.locator('.redemption-code-cell').filter(has=page.get_by_text(code,exact=True))
        expect(cell.get_by_text(code,exact=True)).to_be_visible()
        cell.get_by_role('button',name='复制',exact=True).click()
        expect(cell.get_by_role('button',name='已复制',exact=True)).to_be_visible()
        assert page.evaluate('navigator.clipboard.readText()')==code
        assert extra==[]
    finally:context.close()


@pytest.mark.parametrize('count',[0,1,3,20,100])
@pytest.mark.parametrize('width',[360,1440])
def test_workspace_data_sizes_and_probe_requests_are_bounded(browser_site,count,width):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        value=context.request.get(base+'/api/app/bootstrap').json()
        template=value['tasks'][0]
        value['tasks']=[{**template,'id':f'layout-task-{index}','name':f'布局好友 {index}'} for index in range(count)]
        value['resource_limits'].update(tasks_total=count,tasks_enabled_total=count,tasks_paused_total=0)
        value['tasks_pagination'].update(total=count,has_next=False)
        page=context.new_page();errors=[];probe_reads=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('request',lambda request:probe_reads.append(request.url) if '/checks' in request.url else None)
        page.route('**/api/app/bootstrap',lambda route:route.fulfill(json=value))
        for path in ('/dashboard','/studio'):
            page.goto(base+path,wait_until='networkidle');page.locator('.fire-main').wait_for()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'),f'{path} count={count}, width={width}'
        assert page.locator('.task-row').count()==count
        assert probe_reads==[],'hidden menus must not poll once per task'
        assert errors==[]
    finally:context.close()


@pytest.mark.parametrize('width',[390,1440])
def test_recovery_frame_fullscreen_and_failed_cancel_remains_actionable(browser_site,width):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':844},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        page=context.new_page();requests=[];cancels=[]
        state={'id':'isolated-recovery','mode':'recovery','account_id':'fixture-account',
               'status':'confirming','remaining_seconds':180,'needs_verification':True,
               'verification_kind':'password','frame_revision':'fixture-1'}
        def start(route):
            requests.append(route.request.post_data)
            route.fulfill(json=state)
        def cancel(route):
            cancels.append(route.request.post_data)
            route.fulfill(status=503,json={'detail':'isolated failure'}) if len(cancels)==1 else route.fulfill(json={**state,'status':'cancelled'})
        page.route('**/accounts/recovery',start)
        page.route('**/accounts/scan/isolated-recovery',lambda route:route.fulfill(json=state))
        page.route('**/accounts/scan/isolated-recovery/qr?*',lambda route:route.fulfill(content_type='image/svg+xml',body='<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><rect width="1280" height="720" fill="white"/></svg>'))
        page.route('**/accounts/scan/isolated-recovery/cancel',cancel)
        page.goto(base+'/accounts',wait_until='networkidle')
        page.get_by_role('button',name='打开验证',exact=True).first.click()
        modal=page.get_by_role('dialog',name='复核原抖音账号',exact=True)
        expect(modal).to_be_visible();expect(modal.locator('.scan-stage')).to_have_class(re.compile('frame-ready'))
        assert requests and 'account_id' in requests[0]
        expect(modal.locator('input[type=password]')).to_be_visible()
        modal.get_by_role('button',name='放大云端画面').click()
        fullscreen=page.get_by_role('dialog',name='云端验证画面',exact=True)
        expect(fullscreen).to_be_visible()
        expect(fullscreen.locator('input[type=password]')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
        fullscreen.get_by_role('button',name='关闭',exact=True).click()
        expect(modal).to_be_visible()
        modal.get_by_role('button',name='结束本次复核').click()
        expect(modal).to_contain_text('未能结束云端会话')
        expect(modal).to_be_visible()
        modal.get_by_role('button',name='结束本次复核').click()
        expect(modal).to_have_count(0)
        assert len(cancels)==2
    finally:context.close()


