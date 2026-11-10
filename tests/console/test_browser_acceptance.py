"""Real Chromium against an isolated server/database. No Douyin/SMTP workers."""
import os
import re
from pathlib import Path
import socket
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import uvicorn
from playwright.sync_api import sync_playwright
from playwright.sync_api import expect
from sqlalchemy import select

from spark_console.db import session_scope
from spark_console.models import User, UserSubscription, DouyinAccount, SparkTask, TaskRun
from spark_console.security import SessionService
from spark_console.crypto import CookieCipher
from spark_console.services.subscriptions import SubscriptionService
from spark_console.services.audits import AuditService
from tests.console.test_web_fixture import UserFixture
from spark_console.web.app import create_app


def _capture_site_detail(page,name,*,full_page=False):
    """Review artifact only; never alter CSS, animation state or assertions."""
    output=Path('.artifacts/ui-site-unification/screenshots')
    output.mkdir(parents=True,exist_ok=True)
    theme=page.evaluate("document.documentElement.dataset.theme || localStorage.getItem('dyfire-theme') || 'default'")
    width=page.viewport_size['width']
    page.screenshot(path=str(output/f'{name}-{theme}-{width}.png'),full_page=full_page)


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
        # Password-reset checks must never revoke the shared ordinary user's
        # session. Give management mutations their own customer and resources.
        managed=User(username='workspace-check',password_hash='unused',role='user',must_change_password=False)
        db.add(managed);db.flush();tokens['workspace_user_id']=managed.id
        db.add(UserSubscription(user_id=managed.id,valid_from=now-timedelta(days=1),valid_until=now+timedelta(days=30),max_accounts=2,max_tasks=10))
        managed_account=DouyinAccount(owner_user_id=managed.id,display_name='管理验收账号',encrypted_cookies=b'isolated',cookie_nonce=b'isolated',validation_state='valid')
        db.add(managed_account);db.flush()
        managed_task=SparkTask(owner_user_id=managed.id,douyin_account_id=managed_account.id,target_name='管理验收好友',send_time='09:40',message_kind='douyin_sticker',message_template='续火花',enabled=True)
        db.add(managed_task);db.flush();tokens['workspace_task_id']=managed_task.id
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


@pytest.mark.parametrize('width',[360,390,430,768,1280,1440,1920])
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
            if width in (390,1440):
                output=Path('.artifacts/ui-quality-implementation/screenshots');output.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(output/f'{role}-{theme}-{width}-{route.strip("/").replace("/","-")}.png'))
        if role=='user':
            response=context.request.get(base+'/api/admin/bootstrap')
            assert response.status in (403,404)
        # Read-only visual inventory for secondary pages. The route/error/
        # accessibility assertions above remain unchanged.
        if width in (390,1440):
            detail_routes=['/attention'] if role=='user' else [
                '/admin/runs?view=today','/admin/operations?view=diagnostics',
                '/admin/operations?view=audit','/admin/operations?view=business']
            for route in detail_routes:
                page.goto(base+route,wait_until='networkidle')
                page.locator('.fire-main').wait_for()
                name=role+'-'+route.strip('/').replace('/','-').replace('?','-').replace('=','-')
                _capture_site_detail(page,name,full_page=True)
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
    # Public forms are absent from the authenticated route matrix. Capture
    # them separately without registration, mail delivery, or login writes.
    for width in (390,1440):
        for theme in ('dark','light'):
            context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce')
            context.add_init_script(f"localStorage.setItem('dyfire-theme','{theme}')")
            try:
                page=context.new_page()
                for route in ('/login','/register','/forgot-password'):
                    page.goto(base+route,wait_until='networkidle')
                    page.locator('.fire-auth-card').wait_for()
                    _capture_site_detail(page,'public-'+route.strip('/'),full_page=True)
            finally:context.close()


@pytest.mark.parametrize('width',[360,390,430])
@pytest.mark.parametrize('theme',['dark','light'])
def test_mobile_customer_identity_remains_readable(browser_site,width,theme):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce')
    context.add_init_script(f"localStorage.setItem('dyfire-theme','{theme}')")
    context.add_cookies([{'name':'spark_session','value':tokens['admin'],'url':base}])
    try:
        page=context.new_page();page.goto(base+'/admin/users',wait_until='networkidle')
        row=page.locator('.user-workbench-row').filter(has_text='friend').first
        expect(row).to_be_visible()
        identity=row.locator('.row-primary').bounding_box()
        resources=row.locator('.row-secondary').bounding_box()
        assert identity['width']>=190,identity
        assert resources['width']>=140,resources
        for button in row.locator('.row-actions button').all():
            assert button.bounding_box()['height']>=44
        page.get_by_role('button',name='管理员，打开账户菜单').click()
        menu=page.get_by_role('navigation',name='全部页面与账户操作')
        for label in ['首页','续火任务','抖音账号']:
            expect(menu.get_by_role('link',name=label,exact=True)).to_be_visible()
        menu_box=menu.bounding_box();dock_box=page.get_by_role('navigation',name='移动端主导航').bounding_box()
        assert menu_box['y']+menu_box['height']<dock_box['y']-8
        logout=menu.get_by_role('button',name='退出登录',exact=True)
        logout.scroll_into_view_if_needed();expect(logout).to_be_in_viewport()
        _capture_site_detail(page,'admin-mobile-account-menu')
        page.keyboard.press('Escape');expect(menu).to_have_count(0)
    finally:context.close()


@pytest.mark.parametrize('width',[390,1440])
@pytest.mark.parametrize('theme',['dark','light'])
def test_task_editor_toast_and_nested_select_behavior(browser_site,width,theme):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':900})
    context.add_init_script(f"localStorage.setItem('dyfire-theme','{theme}')")
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    context.tracing.start(screenshots=True,snapshots=True)
    try:
        page=context.new_page();page.goto(base+'/studio',wait_until='networkidle')
        for button in page.locator('.task-row > .row-actions > button').all():
            box=button.bounding_box();assert box['height']>=44 and box['width']>=44
        page.get_by_role('button',name=re.compile('编辑.*验收好友')).first.click()
        dialog=page.get_by_role('dialog',name='编辑 验收好友')
        expect(dialog).to_be_visible();assert dialog.bounding_box()['width']<=540
        _capture_site_detail(page,'task-edit')
        select=dialog.locator('select');select.click()
        expect(page.get_by_role('listbox')).to_be_visible()
        _capture_site_detail(page,'task-edit-message-select')
        page.keyboard.press('Escape');expect(page.get_by_role('listbox')).to_have_count(0)
        expect(dialog).to_be_visible()
        dialog.locator('input[type=time]').fill('11:35')
        # Diagnostics only: observe the original pointer/animation sequence
        # without changing timing, hover positions, assertions or application.
        page.evaluate("""() => {
          const events = [];
          let pointer = null;
          const describe = element => element ? {
            tag:element.tagName, className:String(element.className || ''),
            role:element.getAttribute?.('role'), label:element.getAttribute?.('aria-label'),
            toast:!!element.closest?.('[data-sonner-toast]')
          } : null;
          const snapshot = (kind,event) => {
            if (event?.clientX !== undefined) pointer = {x:event.clientX,y:event.clientY};
            const toasts = [...document.querySelectorAll('[data-sonner-toast]')].map(element => {
              const r = element.getBoundingClientRect();
              const style=getComputedStyle(element);
              return {rect:{x:r.x,y:r.y,width:r.width,height:r.height},
                hover:element.matches(':hover'), mounted:element.dataset.mounted,
                removed:element.dataset.removed, expanded:element.dataset.expanded,
                opacity:style.opacity,transform:style.transform,
                paused:element.dataset.feedbackPaused,
                active:element.contains(document.activeElement)};
            });
            events.push({kind,at:performance.now(),pointer,toasts,
              target:describe(event?.target),relatedTarget:describe(event?.relatedTarget),
              bodyOverflow:getComputedStyle(document.body).overflow,
              activeElement:describe(document.activeElement),
              elementsFromPoint:pointer?document.elementsFromPoint(pointer.x,pointer.y).slice(0,6).map(describe):[]});
          };
          for(const type of ['pointerover','pointerout','pointermove','focusin','focusout'])
            document.addEventListener(type,event=>snapshot(type,event),true);
          window.originalToastDiagnostics=events;
          window.captureOriginalToastDiagnostic=snapshot;
          snapshot('start');
          setInterval(()=>snapshot('sample'),100);
        }""")
        dialog.get_by_role('button',name='保存任务',exact=True).click()
        toast=page.locator('[data-sonner-toast]').filter(has_text='任务已保存')
        expect(toast).to_be_visible()
        page.evaluate('() => captureOriginalToastDiagnostic("before-hover")')
        toast.hover();page.evaluate('() => captureOriginalToastDiagnostic("after-hover")');page.wait_for_timeout(4400);expect(toast).to_be_visible()
        close=toast.get_by_role('button',name='关闭提示')
        expect(close).to_be_visible()
        toast_box=toast.bounding_box();close_box=close.bounding_box()
        message_box=toast.locator('[data-content]').bounding_box()
        assert close_box['width']>=44 and close_box['height']>=44,close_box
        assert close_box['x']>=toast_box['x']-1 and close_box['y']>=toast_box['y']-1
        assert close_box['x']+close_box['width']<=toast_box['x']+toast_box['width']+1
        assert close_box['y']+close_box['height']<=toast_box['y']+toast_box['height']+1
        assert message_box['x']+message_box['width']<=close_box['x']+1
        assert abs((close_box['y']+close_box['height']/2)-(toast_box['y']+toast_box['height']/2))<=2
        output=Path('.artifacts/ui-controls-refinement/screenshots');output.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(output/f'toast-{theme}-{width}.png'))
        close.click();expect(toast).to_have_count(0)
    finally:
        output=Path('.artifacts/ui-toast-hover');output.mkdir(parents=True,exist_ok=True)
        diagnostic_name=f'original-{theme}-{width}'
        try:
            (output/f'{diagnostic_name}.json').write_text(
                page.evaluate('() => JSON.stringify(window.originalToastDiagnostics || [],null,2)'),encoding='utf-8')
            page.screenshot(path=str(output/f'{diagnostic_name}.png'),timeout=5000)
        finally:
            try:context.tracing.stop(path=str(output/f'{diagnostic_name}.zip'))
            finally:context.close()


def test_heatmap_has_one_floating_tooltip_without_layout_shift(browser_site):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':1440,'height':1000})
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        page=context.new_page();page.goto(base+'/dashboard',wait_until='networkidle')
        cells=page.locator('.fire-heat-day');assert cells.count()>=365
        cell=cells.nth(200);cell.scroll_into_view_if_needed()
        before=page.locator('.fire-heat-card').bounding_box()
        cell.hover();expect(page.get_by_role('tooltip')).to_be_visible()
        assert cell.get_attribute('title') is None
        after=page.locator('.fire-heat-card').bounding_box()
        assert before['height']==after['height']
        assert page.locator('[role=tooltip]').count()==1
        cell.focus();page.keyboard.press('ArrowRight')
        expect(cells.nth(207)).to_be_focused()
        page.keyboard.press('Escape');expect(page.get_by_role('tooltip')).to_have_count(0)
        # A pending reveal can move another day beneath the stationary mouse;
        # dismissal must survive it, then reopen for intentional interaction.
        page.wait_for_timeout(400);expect(page.get_by_role('tooltip')).to_have_count(0)
        cells.nth(214).hover();expect(page.get_by_role('tooltip')).to_be_visible()
        page.keyboard.press('Escape');expect(page.get_by_role('tooltip')).to_have_count(0)
        page.keyboard.press('ArrowRight');expect(cells.nth(214)).to_be_focused()
        expect(page.get_by_role('tooltip')).to_be_visible()
    finally:context.close()


def test_cloud_refresh_keeps_last_frame_and_blocks_stale_clicks(browser_site):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        page=context.new_page();interactions=[]
        state={'id':'buffer-test','mode':'recovery','status':'confirming','remaining_seconds':180,'frame_revision':'good','needs_verification':True,'verification_kind':'app'}
        page.route('**/accounts/recovery',lambda route:route.fulfill(json=state))
        page.route('**/accounts/scan/buffer-test',lambda route:route.fulfill(json=state))
        def frame(route):
            if 'revision=good' in route.request.url:
                route.fulfill(content_type='image/svg+xml',body='<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><rect width="1280" height="720" fill="white"/></svg>')
            else:route.fulfill(status=503,body='unavailable')
        page.route('**/accounts/scan/buffer-test/qr?*',frame)
        page.route('**/accounts/scan/buffer-test/interact',lambda route:(interactions.append(route.request.post_data),route.fulfill(json={'status':'delivered'})))
        page.goto(base+'/accounts',wait_until='networkidle');page.get_by_role('button',name='打开验证',exact=True).first.click()
        modal=page.get_by_role('dialog',name='复核原抖音账号')
        expect(modal.locator('.scan-stage')).to_have_class(re.compile('frame-ready'))
        expect(modal.locator('.scan-frame-image[aria-hidden=false]')).to_have_attribute('src',re.compile('revision=good'))
        state['frame_revision']='unavailable'
        expect(modal.locator('.cloud-frame-refresh')).to_be_visible(timeout=6000)
        image=modal.locator('.scan-frame-image[aria-hidden=false]')
        expect(image).to_have_attribute('src',re.compile('revision=good'))
        assert image.evaluate('(el)=>el.complete && el.naturalWidth===1280')
        image.click();assert not interactions
        expect(modal.locator('.scan-loading-overlay')).not_to_be_visible()
        _capture_site_detail(page,'account-recovery-stale-frame')
    finally:context.close()


def test_auth_switch_keeps_touch_keyboard_closed_and_animates(browser_site):
    browser,base,_=browser_site
    context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
    try:
        page=context.new_page();page.goto(base+'/login',wait_until='networkidle')
        assert page.evaluate('document.activeElement.tagName')!='INPUT'
        before=page.locator('.auth-form-motion').bounding_box()['height']
        page.locator('.fire-auth-tabs').get_by_role('link',name='注册',exact=True).click()
        expect(page.get_by_role('heading',name='创建账号')).to_be_visible()
        assert page.locator('.auth-form-motion').evaluate('(el)=>el.getAnimations().some(a=>a.effect.getTiming().duration===240)')
        page.wait_for_timeout(300)
        assert page.locator('.auth-form-motion').bounding_box()['height']>before
        assert page.evaluate('document.activeElement.tagName')!='INPUT'
        _capture_site_detail(page,'public-registration-after-transition')
    finally:context.close()


@pytest.mark.parametrize('motion',['reduce','no-preference'])
def test_editor_motion_obeys_system_preference(browser_site,motion):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':1280,'height':900},reduced_motion=motion)
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        page=context.new_page();page.goto(base+'/studio',wait_until='networkidle')
        page.get_by_role('button',name=re.compile('编辑.*验收好友')).first.click()
        dialog=page.get_by_role('dialog',name='编辑 验收好友')
        # Dialog entry/exit now uses interruptible CSS transitions. Verify the
        # effective motion duration, independent of keyframes vs transitions.
        duration=dialog.evaluate('''el=>Math.max(...[getComputedStyle(el).animationDuration,getComputedStyle(el).transitionDuration].flatMap(v=>v.split(',').map(s=>parseFloat(s))))''')
        assert duration<=.001 if motion=='reduce' else .15<=duration<=.28
        dialog.get_by_role('button',name='关闭',exact=True).click();expect(dialog).to_have_count(0)
        expect(page.get_by_role('button',name=re.compile('编辑.*验收好友')).first).to_be_focused()
    finally:context.close()


@pytest.mark.parametrize('width',[390,1440])
@pytest.mark.parametrize('theme',['dark','light'])
def test_quality_account_select_and_friend_panel(browser_site,width,theme):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':844},has_touch=width<700,reduced_motion='reduce')
    context.add_init_script(f"localStorage.setItem('dyfire-theme','{theme}')")
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        value=context.request.get(base+'/api/app/bootstrap').json()
        value['membership'].update(max_tasks=10,can_add_task=True)
        value['resource_limits'].update(tasks_max=10,tasks_enabled_total=1)
        account=value['accounts'][0]
        value['accounts'].append({**account,'id':'another-account','name':'第二个测试账号'})
        page=context.new_page();errors=[];reads=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('**/api/app/bootstrap',lambda route:route.fulfill(json=value))
        def contacts(route):
            reads.append(route.request.url)
            route.fulfill(json={'items':[{'name':'新好友','sec_uid':'new-contact'}, {'name':'用于检查手机端换行与阅读的长名称好友','sec_uid':'long-contact'}], 'total':2, 'has_more':False, 'can_sync_more':False})
        page.route('**/accounts/*/conversations?*',contacts)
        page.goto(base+'/studio',wait_until='networkidle')
        account_control=page.get_by_role('combobox',name='发送账号')
        account_control.click();expect(page.get_by_role('listbox')).to_be_visible()
        _capture_site_detail(page,'task-create-account-select')
        page.get_by_role('option',name='第二个测试账号',exact=True).click()
        expect(account_control).to_contain_text('第二个测试账号')
        account_control.focus();page.keyboard.press('ArrowDown');expect(page.get_by_role('listbox')).to_be_visible()
        page.keyboard.press('Escape');expect(page.get_by_role('listbox')).to_have_count(0)
        expect(account_control).to_be_focused()
        trigger=page.locator('.friend-trigger');trigger.click()
        panel=page.get_by_role('dialog',name='选择目标好友',exact=True)
        expect(panel).to_be_visible();expect(panel.get_by_text('新好友',exact=True)).to_be_visible()
        assert reads and '/another-account/conversations' in reads[-1]
        if width<700:
            expect(page.locator('.quality-sheet')).to_be_visible()
            assert not page.get_by_label('搜索已同步好友').evaluate('(el)=>el===document.activeElement')
        panel.get_by_label('搜索已同步好友').fill('新好友')
        checkbox=panel.get_by_role('checkbox')
        expect(checkbox).not_to_be_checked()
        checkbox.focus();page.keyboard.press('Space')
        expect(checkbox).to_be_checked();expect(checkbox).to_be_focused()
        page.keyboard.press('Space');expect(checkbox).not_to_be_checked()
        panel.get_by_text('新好友',exact=True).click();expect(checkbox).to_be_checked()
        done=panel.get_by_role('button',name='完成选择')
        expect(done).to_be_enabled()
        assert done.bounding_box()['y']+done.bounding_box()['height']<=844
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        output=Path('.artifacts/ui-controls-refinement/screenshots');output.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(output/f'friend-panel-{theme}-{width}.png'))
        done.click();expect(panel).to_have_count(0);expect(trigger).to_contain_text('新好友')
        _capture_site_detail(page,'task-create-friend-selected')
        assert errors==[],errors
    finally:context.close()


def test_async_button_retains_width_and_rejects_duplicate_click(browser_site):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':1280,'height':900})
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        page=context.new_page();pending=[]
        page.route('**/api/studio/tasks/*/enabled',lambda route:pending.append(route))
        page.goto(base+'/studio',wait_until='networkidle')
        button=page.locator('.task-row').first.get_by_role('button',name=re.compile('暂停|恢复')).first
        before=button.bounding_box()['width'];button.click()
        expect(button).to_have_attribute('aria-busy','true');expect(button).to_be_disabled()
        assert abs(before-button.bounding_box()['width'])<1
        button.evaluate("el=>el.click()")
        assert len(pending)==1
        pending[0].fulfill(status=503,json={'detail':'测试暂时不可用，请稍后重试'})
        expect(button).to_be_enabled();expect(page.get_by_text('测试暂时不可用，请稍后重试',exact=True)).to_be_visible()
    finally:context.close()


def test_customer_table_columns_align_with_headings(browser_site):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':1280,'height':900},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['admin'],'url':base}])
    try:
        page=context.new_page();page.goto(base+'/admin/users',wait_until='networkidle')
        widths=page.evaluate("""() => ['.user-workbench-head','.user-workbench-row'].map(selector=>getComputedStyle(document.querySelector(selector)).gridTemplateColumns)""")
        assert widths[0]==widths[1],widths
    finally:context.close()


def test_browser_back_restores_task_list_position(browser_site):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':1440,'height':900},reduced_motion='reduce',bypass_csp=True)
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        value=context.request.get(base+'/api/app/bootstrap').json()
        template=value['tasks'][0]
        value['tasks']=[{**template,'id':f'scroll-task-{index}','name':f'位置好友 {index}'} for index in range(50)]
        value['tasks_pagination'].update(total=50,has_next=False)
        page=context.new_page()
        page.route('**/api/app/bootstrap',lambda route:route.fulfill(json=value))
        page.goto(base+'/studio',wait_until='networkidle')
        page.mouse.wheel(0,900)
        page.wait_for_function('window.scrollY > 600')
        position=page.evaluate('window.scrollY')
        page.get_by_role('radio',name='抖音账号',exact=True).click()
        page.wait_for_url('**/accounts')
        page.wait_for_function('window.scrollY === 0')
        page.go_back();page.wait_for_url('**/studio')
        page.wait_for_function('(position)=>Math.abs(window.scrollY-position)<2',arg=position)
    finally:context.close()


def test_theme_switch_updates_native_form_controls(browser_site):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
    context.add_init_script("localStorage.setItem('dyfire-theme','dark')")
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        page=context.new_page();page.goto(base+'/studio',wait_until='networkidle')
        page.get_by_role('button',name='切换主题',exact=True).click()
        page.wait_for_function("getComputedStyle(document.documentElement).colorScheme === 'light'")
        page.get_by_role('button',name='切换主题',exact=True).click()
        page.wait_for_function("getComputedStyle(document.documentElement).colorScheme === 'dark'")
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
        _capture_site_detail(page,'task-edit-discard-confirmation')
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
        original_time=editor.locator('input[type=time]').input_value()
        editor.locator('input[type=time]').fill('10:45')
        preview=editor.locator('.admin-change-preview')
        expect(preview).to_contain_text('每日发送时间')
        expect(preview).to_contain_text(f'{original_time} → 10:45')
        _capture_site_detail(page,'admin-task-change-preview')
        editor.get_by_role('button',name='保存任务',exact=True).click()
        confirm=page.get_by_role('dialog',name='确认保存任务修改')
        expect(confirm.locator('.modal-dialog-body')).to_contain_text(f'每日发送时间：{original_time} → 10:45')
        _capture_site_detail(page,'admin-task-change-confirmation')
        confirm.get_by_role('button',name='确认保存',exact=True).click()
        expect(editor).to_have_count(0)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    finally:context.close()


@pytest.mark.parametrize('width',[390,1440])
@pytest.mark.parametrize('theme',['dark','light'])
def test_admin_customer_management_keeps_actions_and_feedback_in_workspace(browser_site,width,theme):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce')
    context.add_init_script(f"localStorage.setItem('dyfire-theme','{theme}')")
    context.add_cookies([{'name':'spark_session','value':tokens['admin'],'url':base}])
    user_id=tokens['workspace_user_id']
    workspace_url=f'{base}/api/admin/users/{user_id}/workspace'
    try:
        page=context.new_page();page.goto(base+'/admin/users',wait_until='networkidle')
        page.locator('.user-workbench-row').filter(has_text='workspace-check').get_by_role('button',name='编辑',exact=True).click()
        workspace=page.get_by_role('dialog',name=re.compile('workspace-check'))
        expect(workspace).to_be_visible()
        workspace.get_by_text('管理',exact=True).click()
        output=Path('.artifacts/admin-workspace-refinement/screenshots');output.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(output/f'admin-manage-top-{theme}-{width}.png'))
        note=workspace.get_by_label('管理员备注',exact=True)
        note_text=f'回归备注 {theme} {width}'
        note.fill(note_text)
        with page.expect_response(lambda response:response.url.endswith(f'/api/admin/users/{user_id}/note') and response.request.method=='POST') as saved_note:
            workspace.get_by_role('button',name='保存备注',exact=True).click()
        assert saved_note.value.ok
        expect(workspace.get_by_text('用户备注已保存',exact=True)).to_be_visible()
        assert context.request.get(workspace_url).json()['user']['admin_note']==note_text

        account_limit=workspace.get_by_label('账号额度',exact=True)
        task_limit=workspace.get_by_label('任务额度',exact=True)
        old_accounts=int(account_limit.input_value());old_tasks=int(task_limit.input_value())
        account_limit.fill(str(old_accounts+1));task_limit.fill(str(old_tasks+1))
        workspace.get_by_role('button',name='保存额度',exact=True).click()
        confirmation=page.get_by_role('dialog',name='确认调整客户额度')
        expect(confirmation).to_contain_text(f'任务额度：{old_tasks} → {old_tasks+1}')
        _capture_site_detail(page,'admin-quota-change-confirmation')
        with page.expect_response(lambda response:response.url.endswith(f'/api/admin/subscriptions/{user_id}/limits') and response.request.method=='POST') as saved_limits:
            confirmation.get_by_role('button',name='确认调整',exact=True).click()
        assert saved_limits.value.ok
        expect(workspace.get_by_text(f'额度已更新：{old_accounts+1} 个账号 / {old_tasks+1} 个任务',exact=True)).to_be_visible()
        membership=context.request.get(workspace_url).json()['membership']
        assert membership['max_accounts']==old_accounts+1 and membership['max_tasks']==old_tasks+1
        expect(workspace.get_by_role('button',name='保存额度',exact=True)).to_be_disabled()

        with page.expect_response(lambda response:response.url.endswith(f'/api/admin/users/{user_id}/reset-password') and response.request.method=='POST') as reset_response:
            workspace.get_by_role('button',name='重置密码',exact=True).click()
        assert reset_response.value.ok
        temporary=reset_response.value.json()['temporary_password']
        expect(workspace).to_contain_text(temporary)
        page.screenshot(path=str(output/f'admin-manage-password-result-{theme}-{width}.png'))
        # Administrators must be able to retrieve the result without closing
        # the editor or hunting for feedback behind its backdrop.
        expect(workspace.get_by_text(re.compile(re.escape(temporary))).first).to_be_in_viewport()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert workspace.evaluate('(el)=>el.scrollWidth <= el.clientWidth + 1')
        for label in ['保存备注','保存额度','重置密码']:
            button=workspace.get_by_role('button',name=label,exact=True)
            button.scroll_into_view_if_needed();expect(button).to_be_in_viewport()
            assert button.bounding_box()['height']>=44
        note.scroll_into_view_if_needed()
        page.screenshot(path=str(output/f'admin-manage-note-{theme}-{width}.png'))
        workspace.get_by_role('button',name='撤销订阅',exact=True).scroll_into_view_if_needed()
        page.screenshot(path=str(output/f'admin-manage-bottom-{theme}-{width}.png'))
    finally:context.close()


def test_admin_customer_workspace_protects_unsaved_changes_and_returns_from_task_editor(browser_site):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['admin'],'url':base}])
    try:
        page=context.new_page();page.goto(base+'/admin/users',wait_until='networkidle')
        page.locator('.user-workbench-row').filter(has_text='workspace-check').get_by_role('button',name='编辑',exact=True).click()
        workspace=page.get_by_role('dialog',name=re.compile('workspace-check'))
        workspace.get_by_text('管理',exact=True).click()
        note=workspace.get_by_label('管理员备注',exact=True)
        original_note=note.input_value();draft=original_note+' 未保存草稿'
        note.fill(draft)
        workspace.get_by_role('button',name='关闭',exact=True).first.click()
        confirmation=page.get_by_role('dialog',name='放弃未保存的修改？')
        expect(confirmation).to_be_visible()
        confirmation.get_by_role('button',name='继续编辑',exact=True).click()
        expect(note).to_have_value(draft)
        # Switching between overview and management must preserve the draft.
        workspace.get_by_text('概览',exact=True).click()
        workspace.get_by_text('管理',exact=True).click()
        expect(note).to_have_value(draft)
        note.fill(original_note)
        limits=workspace.get_by_label('任务额度',exact=True)
        original_limit=limits.input_value();limits.fill(str(int(original_limit)+1))
        workspace.get_by_role('button',name='关闭',exact=True).first.click()
        expect(confirmation).to_be_visible()
        confirmation.get_by_role('button',name='继续编辑',exact=True).click()
        expect(limits).to_have_value(str(int(original_limit)+1))
        limits.fill(original_limit)
        workspace.get_by_text('概览',exact=True).click()
        workspace.locator('.customer-task').filter(has_text='管理验收好友').get_by_role('button',name='编辑',exact=True).click()
        editor=page.get_by_role('dialog',name='编辑 管理验收好友')
        expect(editor).to_be_visible()
        time_field=editor.locator('input[type=time]')
        original_time=time_field.input_value();new_time='10:55' if original_time!='10:55' else '11:05'
        time_field.fill(new_time)
        editor.get_by_role('button',name='保存任务',exact=True).click()
        page.get_by_role('dialog',name='确认保存任务修改').get_by_role('button',name='确认保存',exact=True).click()
        expect(editor).to_have_count(0)
        expect(workspace).to_be_visible()
        expect(workspace.locator('.customer-task').filter(has_text='管理验收好友')).to_contain_text(new_time)
        assert page.evaluate('document.body.style.overflow')=='hidden'
        workspace.get_by_role('button',name='关闭',exact=True).first.click()
        expect(workspace).to_have_count(0)
        assert page.evaluate('document.body.style.overflow')!='hidden'
    finally:context.close()


@pytest.mark.parametrize('width',[390,1440])
def test_admin_sees_full_redemption_code_and_copies_without_extra_request(browser_site,width):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':900},permissions=['clipboard-read','clipboard-write'])
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
        record=page.locator('.redemption-record-row').filter(has_text=code)
        assert record.evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        boxes=record.locator(':scope > [data-label]').evaluate_all('(els)=>els.map(el=>{const r=el.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}})')
        for index,a in enumerate(boxes):
            for b in boxes[index+1:]:
                assert a['right']<=b['left']+1 or b['right']<=a['left']+1 or a['bottom']<=b['top']+1 or b['bottom']<=a['top']+1,'Redemption information overlaps'
        output=Path('.artifacts/ui-site-design-review/screenshots');output.mkdir(parents=True,exist_ok=True)
        record.evaluate("el=>el.scrollIntoView({block:'center',behavior:'instant'})")
        record.screenshot(path=str(output/f'redemption-record-{width}.png'))
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
        _capture_site_detail(page,'account-recovery-frame')
        frame=modal.locator('.scan-stage').bounding_box()
        tools_summary=modal.locator('.cloud-tools > summary').first.bounding_box()
        assert tools_summary['y']>=frame['y']+frame['height']-1,(frame,tools_summary)
        submit=modal.get_by_role('button',name='提交验证',exact=True)
        submit.scroll_into_view_if_needed()
        assert submit.evaluate("""el=>{
          const r=el.getBoundingClientRect();
          return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));
        }"""),'Verification submit is obscured by another control'
        _capture_site_detail(page,'account-recovery-password')
        modal.get_by_role('button',name='放大云端画面').click()
        fullscreen=page.get_by_role('dialog',name='云端验证画面',exact=True)
        expect(fullscreen).to_be_visible()
        expect(fullscreen.locator('input[type=password]')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
        _capture_site_detail(page,'account-recovery-fullscreen')
        fullscreen.get_by_role('button',name='关闭',exact=True).click()
        expect(modal).to_be_visible()
        modal.get_by_role('button',name='结束本次复核').click()
        expect(modal).to_contain_text('未能结束云端会话')
        expect(modal).to_be_visible()
        _capture_site_detail(page,'account-recovery-cancel-error')
        modal.get_by_role('button',name='结束本次复核').click()
        expect(modal).to_have_count(0)
        assert len(cancels)==2
    finally:context.close()


@pytest.mark.parametrize('theme',['light','dark'])
def test_status_labels_keep_readable_contrast_in_both_themes(browser_site,theme):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
    context.add_init_script(f"localStorage.setItem('dyfire-theme','{theme}')")
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        page=context.new_page();page.goto(base+'/studio',wait_until='networkidle')
        page.locator('.task-row .badge.good').wait_for()
        ratios=page.evaluate("""() => {
          const rgb=s=>(s.match(/[\\d.]+/g)||[]).map(Number);
          const lum=c=>c.slice(0,3).map(x=>x/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4).reduce((a,x,i)=>a+x*[.2126,.7152,.0722][i],0);
          const color=getComputedStyle(document.body).backgroundColor;
          return [...document.querySelectorAll('.task-row .badge.good')].map(e=>{
            const a=lum(rgb(getComputedStyle(e).color)),b=lum(rgb(color));return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
          });
        }""")
        assert ratios and min(ratios)>=4.5,(theme,ratios)
    finally:context.close()


@pytest.mark.parametrize('width',[390,1440])
@pytest.mark.parametrize('theme',['light','dark'])
@pytest.mark.parametrize('role',['user','admin'])
def test_accessibility_rules_for_text_forms_and_scroll_regions(browser_site,width,theme,role):
    source=Path('frontend/node_modules/axe-core/axe.min.js')
    assert source.is_file(),'npm ci must install the pinned accessibility checker'
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce',bypass_csp=True)
    context.add_init_script(f"localStorage.setItem('dyfire-theme','{theme}')")
    context.add_cookies([{'name':'spark_session','value':tokens[role],'url':base}])
    routes=['/dashboard','/studio','/accounts','/runs','/subscription','/account-center','/notifications','/help'] if role=='user' else ['/admin/overview','/admin/users','/admin/codes','/admin/runs','/admin/operations','/admin/operations?view=business','/admin/settings','/admin/settings/mail']
    try:
        page=context.new_page()
        for route in routes:
            page.goto(base+route,wait_until='networkidle');page.locator('.fire-main').wait_for()
            page.evaluate(source.read_text(encoding='utf-8'))
            failures=page.evaluate("""async()=>{
              const result=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});
              return result.violations.map(v=>({id:v.id,targets:v.nodes.map(n=>n.target)}));
            }""")
            assert failures==[],(width,theme,role,route,failures)
            output=Path('.artifacts/ui-site-design-review/screenshots');output.mkdir(parents=True,exist_ok=True)
            screenshot_name=route.strip('/').replace('/','-').replace('?','-').replace('=','-')
            page.screenshot(path=str(output/f'{role}-{theme}-{width}-{screenshot_name}.png'))
    finally:context.close()


def test_redemption_checks_entitlement_before_confirming(browser_site):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        page=context.new_page();submitted=[]
        before=context.request.get(base+'/api/subscription').json()['membership']
        page.on('request',lambda request:submitted.append(request.url) if '/api/subscription/redeem' in request.url else None)
        page.goto(base+'/subscription',wait_until='networkidle')
        expect(page.get_by_role('button',name='检查兑换码',exact=True)).to_be_disabled()
        page.get_by_label('兑换码',exact=True).fill(tokens['redemption_code'])
        page.get_by_role('button',name='检查兑换码',exact=True).click()
        expect(page.locator('.subscription-preview')).to_contain_text('10 个任务')
        expect(page.get_by_role('button',name='确认兑换',exact=True)).to_be_enabled()
        assert submitted==[]
        _capture_site_detail(page,'subscription-redemption-preview')
        page.get_by_role('button',name='确认兑换',exact=True).click()
        expect(page.get_by_label('兑换码',exact=True)).to_have_value('')
        expect(page.locator('.subscription-preview')).to_have_count(0)
        assert len(submitted)==1
        membership=context.request.get(base+'/api/subscription').json()['membership']
        assert membership['active'] and membership['max_tasks']==before['max_tasks']
        assert membership['pending_plan']['max_tasks']==10
    finally:context.close()


@pytest.mark.parametrize('width',[390,1440])
@pytest.mark.parametrize('motion',['no-preference','reduce'])
def test_scroll_reveal_keeps_content_readable_and_support_action_reachable(browser_site,width,motion):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':680},reduced_motion=motion)
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(base+'/subscription',wait_until='networkidle')
        support=page.locator('.contact-support-card[data-reveal]')
        expect(support).to_have_count(1)
        assert page.evaluate('document.documentElement.scrollHeight>innerHeight')
        before=page.evaluate('window.scrollY')
        page.mouse.move(width/2,400);page.mouse.wheel(0,320)
        page.wait_for_function('(before)=>window.scrollY>before',arg=before)
        after=page.evaluate('window.scrollY')
        # One wheel gesture should not be converted into a forced page jump.
        assert 0<after-before<=325,(before,after)
        assert page.evaluate("getComputedStyle(document.body).overflow")!='hidden'
        support.get_by_role('button',name='查看二维码',exact=True).scroll_into_view_if_needed()
        page.wait_for_function("""()=>{
          const el=document.querySelector('.contact-support-card[data-reveal]');
          return el&&Number(getComputedStyle(el).opacity)>=.99;
        }""")
        expect(support.get_by_role('heading',name='联系客服开通订阅')).to_be_visible()
        support.get_by_role('button',name='查看二维码',exact=True).click()
        dialog=page.get_by_role('dialog',name='联系客服开通订阅')
        expect(dialog).to_be_visible()
        _capture_site_detail(page,'subscription-contact-'+motion)
        dialog.get_by_role('button',name='关闭',exact=True).click()
        expect(dialog).to_have_count(0)
        # Returning to an already visited section must not strand it hidden.
        page.mouse.wheel(0,-5000);page.wait_for_function('()=>window.scrollY===0')
        support.scroll_into_view_if_needed()
        page.wait_for_function("()=>Number(getComputedStyle(document.querySelector('.contact-support-card[data-reveal]')).opacity)>=.99")
        expect(support.get_by_role('button',name='查看二维码',exact=True)).to_be_in_viewport()
        if motion=='reduce':
            assert support.evaluate("el=>getComputedStyle(el).transform") in ('none','matrix(1, 0, 0, 1, 0, 0)')
        output=Path('.artifacts/ui-motion-refinement/screenshots');output.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(output/f'scroll-reveal-{motion}-{width}.png'))
        assert errors==[],errors
    finally:context.close()


@pytest.mark.parametrize('width',[390,1440])
def test_admin_note_enter_save_and_animated_tabs_preserve_draft(browser_site,width):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':844},reduced_motion='no-preference')
    context.add_cookies([{'name':'spark_session','value':tokens['admin'],'url':base}])
    user_id=tokens['workspace_user_id']
    endpoint=f'/api/admin/users/{user_id}/note'
    try:
        page=context.new_page();writes=[]
        page.on('request',lambda request:writes.append(request.post_data) if request.url.endswith(endpoint) and request.method=='POST' else None)
        page.goto(base+'/admin/users',wait_until='networkidle')
        page.locator('.user-workbench-row').filter(has_text='workspace-check').get_by_role('button',name='编辑',exact=True).click()
        workspace=page.get_by_role('dialog',name=re.compile('workspace-check'))
        workspace.get_by_role('button',name='管理',exact=True).click()
        note=workspace.get_by_label('管理员备注',exact=True)
        draft=f'动效回归 {width} · 保留草稿'
        note.fill(draft)
        workspace.get_by_role('button',name='概览',exact=True).click()
        workspace.get_by_role('button',name='管理',exact=True).click()
        expect(note).to_have_value(draft)
        assert writes==[],'Switching tabs must not submit the note'
        with page.expect_response(lambda response:response.url.endswith(endpoint) and response.request.method=='POST') as saved:
            note.press('Enter')
        assert saved.value.ok
        expect(workspace.get_by_text('用户备注已保存',exact=True)).to_be_visible()
        assert len(writes)==1
        assert context.request.get(base+f'/api/admin/users/{user_id}/workspace').json()['user']['admin_note']==draft
        expect(workspace.get_by_role('button',name='保存备注',exact=True)).to_be_disabled()
        note.fill(draft+' 未保存')
        expect(workspace.get_by_text('用户备注已保存',exact=True)).to_have_count(0)
        workspace.get_by_role('button',name='关闭',exact=True).first.click()
        confirmation=page.get_by_role('dialog',name='放弃未保存的修改？')
        expect(confirmation).to_be_visible()
        confirmation.get_by_role('button',name='继续编辑',exact=True).click()
        expect(note).to_have_value(draft+' 未保存')
        expect(confirmation).to_have_count(0)
        expect(workspace).to_be_visible()
        note.scroll_into_view_if_needed()
        output=Path('.artifacts/ui-motion-refinement/screenshots');output.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(output/f'admin-note-motion-{width}.png'))
        workspace.get_by_role('button',name='关闭',exact=True).first.click()
        page.get_by_role('dialog',name='放弃未保存的修改？').get_by_role('button',name='放弃修改',exact=True).click()
        expect(workspace).to_have_count(0)
        assert len(writes)==1
        assert context.request.get(base+f'/api/admin/users/{user_id}/workspace').json()['user']['admin_note']==draft
    finally:context.close()


@pytest.mark.parametrize('motion',['no-preference','reduce'])
def test_run_disclosure_reopens_without_clipping_or_losing_keyboard_access(browser_site,motion):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':390,'height':844},reduced_motion=motion)
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        page=context.new_page();page.goto(base+'/runs',wait_until='networkidle')
        row=page.locator('.run-row[role=button]').filter(has_text='验收好友').first
        expect(row).to_have_attribute('aria-expanded','false')
        detail=row.locator('.run-detail');row.focus();page.keyboard.press('Enter')
        expect(row).to_have_attribute('aria-expanded','true')
        expect(detail).to_contain_text('执行阶段：')
        expect(detail).to_be_visible()
        page.wait_for_function("""id=>{
          const el=document.getElementById(id);
          return el&&el.clientHeight>=el.scrollHeight-1&&el.clientHeight>0;
        }""",arg=row.get_attribute('aria-controls'))
        # Reverse the close while it is in flight; its old completion callback
        # must not hide the newly opened content or steal keyboard focus.
        page.keyboard.press('Space');page.keyboard.press('Enter')
        expect(row).to_have_attribute('aria-expanded','true')
        expect(row).to_be_focused()
        expect(detail).to_be_visible()
        page.wait_for_function("""id=>{
          const el=document.getElementById(id);
          return el&&el.clientHeight>=el.scrollHeight-1&&Number(getComputedStyle(el).opacity)>=.99;
        }""",arg=row.get_attribute('aria-controls'))
        detail.scroll_into_view_if_needed()
        output=Path('.artifacts/ui-motion-refinement/screenshots');output.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(output/f'run-disclosure-{motion}-390.png'))
        row.focus();page.keyboard.press('Space')
        expect(row).to_have_attribute('aria-expanded','false');expect(detail).to_be_hidden()
        expect(row).to_be_focused()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    finally:context.close()


@pytest.mark.parametrize('motion',['no-preference','reduce'])
def test_help_details_keyboard_and_rapid_toggle_keep_content_actionable(browser_site,motion):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':390,'height':844},reduced_motion=motion)
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        page=context.new_page();page.goto(base+'/help',wait_until='networkidle')
        details=page.locator('details').filter(has=page.locator('summary').filter(has_text='反馈问题时需要提供什么？'))
        summary=details.locator('summary')
        body=details.locator('.animated-details-body')
        summary.scroll_into_view_if_needed();summary.focus();page.keyboard.press('Enter')
        expect(summary).to_have_attribute('aria-expanded','true')
        expect(body).to_be_visible();expect(body).to_contain_text('诊断编号')
        page.keyboard.press('Enter');page.keyboard.press('Enter')
        expect(summary).to_have_attribute('aria-expanded','true')
        expect(summary).to_be_focused()
        page.wait_for_function("""()=>{
          const el=[...document.querySelectorAll('details')].find(el=>el.querySelector('summary')?.textContent.includes('反馈问题时需要提供什么'));
          return el&&el.open&&el.clientHeight>=el.scrollHeight-1;
        }""")
        expect(body.get_by_role('link',name='查看待处理事项',exact=True)).to_be_visible()
        summary.focus();page.keyboard.press('Enter')
        expect(summary).to_have_attribute('aria-expanded','false');expect(body).to_be_hidden()
        page.keyboard.press('Enter');expect(summary).to_have_attribute('aria-expanded','true')
        action=body.get_by_role('link',name='查看待处理事项',exact=True)
        action.scroll_into_view_if_needed();expect(action).to_be_in_viewport()
        output=Path('.artifacts/ui-motion-refinement/screenshots');output.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(output/f'help-details-{motion}-390.png'))
        action.click();page.wait_for_url('**/attention')
        expect(page.get_by_role('heading',name='待处理事项',exact=True)).to_be_visible()
    finally:context.close()


def _context_navigation_payload(context,base):
    """Isolated response data: no account binding, worker or live sends."""
    value=context.request.get(base+'/api/app/bootstrap').json()
    template=value['accounts'][0]
    first={**template,'id':'context-account-a','name':'第一验收账号',
           'validation_state':'valid','incident':None,
           'health':{'state':'valid','label':'账号正常','blocked':False}}
    second={**template,'id':'context-account-b','name':'第二个用于检查移动端长名称的验收账号',
            'validation_state':'valid','incident':None,
            'health':{'state':'valid','label':'账号正常','blocked':False}}
    task=value['tasks'][0]
    value['accounts']=[first,second]
    value['tasks']=[
        {**task,'id':'context-task-a1','accountId':first['id'],'name':'第一账号好友甲'},
        {**task,'id':'context-task-a2','accountId':first['id'],'name':'第一账号好友乙'},
        {**task,'id':'context-task-b1','accountId':second['id'],'name':'第二账号好友'},
    ]
    value['membership'].update(max_accounts=3,max_tasks=10,can_add_task=True,can_add_account=True)
    value['resource_limits'].update(tasks_max=10,tasks_total=3,tasks_enabled_total=3,
        tasks_paused_total=0,task_counts_by_account={first['id']:2,second['id']:1})
    value['tasks_pagination'].update(total=3,has_next=False)
    value['runs']=[]
    return value


def _route_context_task_pages(page,tasks,page_size=20):
    reads=[]
    def read(route):
        query=parse_qs(urlparse(route.request.url).query)
        account_id=query.get('account_id',[''])[0]
        page_number=int(query.get('page',['1'])[0])
        reads.append((account_id,page_number))
        selected=[task for task in tasks if not account_id or task['accountId']==account_id]
        start=(page_number-1)*page_size
        route.fulfill(json={'items':selected[start:start+page_size],
            'pagination':{'page':page_number,'page_size':page_size,'total':len(selected),
                'pages':max(1,(len(selected)+page_size-1)//page_size),
                'has_next':start+page_size<len(selected),'has_previous':page_number>1}})
    page.route('**/api/app/tasks?*',read)
    return reads


def _route_execution_pages(page,records,page_size=12):
    reads=[]
    def read(route):
        query=parse_qs(urlparse(route.request.url).query)
        status=query.get('status',[''])[0]
        search=query.get('q',[''])[0]
        page_number=int(query.get('page',['1'])[0])
        reads.append((status,search,page_number))
        selected=[row for row in records if (not status or row['status']==status)
            and (not search or search.casefold() in row['name'].casefold())]
        start=(page_number-1)*page_size
        route.fulfill(json={'items':selected[start:start+page_size],
            'page':page_number,'total':len(selected),
            'pages':max(1,(len(selected)+page_size-1)//page_size)})
    page.route('**/api/studio/runs?*',read)
    return reads


@pytest.mark.parametrize('width',[390,1440])
@pytest.mark.parametrize('origin',['dashboard','accounts'])
def test_account_task_context_filters_rows_without_following_composer_changes(browser_site,width,origin):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        value=_context_navigation_payload(context,base)
        first,second=value['accounts']
        page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('**/api/app/bootstrap',lambda route:route.fulfill(json=value))
        task_reads=_route_context_task_pages(page,value['tasks'])
        page.goto(base+'/'+origin,wait_until='networkidle')
        if origin=='dashboard':
            page.get_by_role('button',name=f"查看 {second['name']} 的任务",exact=True).click()
        else:
            card=page.locator('.account-card').filter(has=page.get_by_role('heading',name=second['name'],exact=True))
            card.get_by_role('button',name=re.compile('^查看任务')).click()
        page.wait_for_url('**/studio?account='+second['id'])
        banner=page.locator('.task-context')
        expect(banner).to_be_visible();expect(banner).to_contain_text(second['name'])
        rows=page.locator('.task-row')
        expect(rows).to_have_count(1);expect(rows).to_contain_text('第二账号好友')
        assert task_reads==[(second['id'],1)],task_reads
        composer=page.get_by_role('combobox',name='发送账号',exact=True)
        expect(composer).to_contain_text(second['name'])
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        banner_box=banner.bounding_box()
        assert banner_box['x']>=0 and banner_box['x']+banner_box['width']<=width+1,banner_box
        _capture_site_detail(page,'task-account-context-'+origin)

        # A creation choice must never silently change which existing tasks
        # the account-context link asked to inspect.
        composer.click();page.get_by_role('option',name=first['name'],exact=True).click()
        expect(composer).to_contain_text(first['name'])
        expect(rows).to_have_count(1);expect(rows).to_contain_text('第二账号好友')
        expect(banner).to_contain_text(second['name'])
        assert parse_qs(urlparse(page.url).query).get('account')==[second['id']]

        banner.get_by_role('button',name='查看全部任务',exact=True).click()
        expect(banner).to_have_count(0);expect(rows).to_have_count(3)
        assert 'account' not in parse_qs(urlparse(page.url).query)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert errors==[],errors
    finally:context.close()


@pytest.mark.parametrize('width',[390,1440])
def test_daily_overview_keeps_unconfirmed_separate_and_preserves_result_navigation(browser_site,width):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        value=_context_navigation_payload(context,base)
        statuses=['success','success','running','submitted_unconfirmed','submitted_unconfirmed','submitted_unconfirmed','failed']
        labels={'success':'已发出','running':'执行中','submitted_unconfirmed':'待核实','failed':'未发出'}
        value['runs']=[{'id':f'daily-run-{index}','taskId':f'daily-task-{index}',
            'status':status,'label':labels[status],'name':f'{labels[status]}验收好友{index}',
            'time':datetime.now(timezone.utc).isoformat(),'stage':'confirming',
            'error':'隔离验收失败原因' if status=='failed' else ''} for index,status in enumerate(statuses)]
        page=context.new_page();reads=[]
        page.route('**/api/app/bootstrap',lambda route:route.fulfill(json=value))
        def runs(route):
            status=parse_qs(urlparse(route.request.url).query).get('status',[''])[0]
            reads.append(status)
            route.fulfill(json={'items':[run for run in value['runs'] if not status or run['status']==status]})
        page.route('**/api/studio/runs?*',runs)
        page.goto(base+'/dashboard',wait_until='networkidle')
        overview=page.locator('.daily-overview');expect(overview).to_be_visible()
        for kind,count in [('success',2),('running',1),('unconfirmed',3),('failed',1)]:
            stat=overview.locator('.daily-stat.is-'+kind)
            # NumberFlow keeps its full digit strip in a shadow DOM. The
            # control's accessible name is the user-facing displayed result.
            label=labels['submitted_unconfirmed' if kind=='unconfirmed' else kind]
            expect(stat).to_have_attribute('aria-label',f'{label} {count} 次')
        expect(overview.get_by_role('img',name='今日记录：2 次已发出，1 次执行中，3 次待核实，1 次未发出',exact=True)).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        overview.locator('.daily-stat.is-failed').click()
        page.wait_for_url('**/runs?status=failed')
        expect(page.get_by_role('radio',name='未发出',exact=True)).to_be_checked()
        expect(page.locator('.run-row')).to_have_count(1)
        expect(page.locator('.run-row')).to_contain_text('未发出验收好友6')
        assert reads and reads[-1]=='failed',reads
        page.reload(wait_until='networkidle')
        expect(page.get_by_role('radio',name='未发出',exact=True)).to_be_checked()
        expect(page.locator('.run-row')).to_have_count(1)
        page.get_by_role('radio',name='待核实',exact=True).click()
        expect(page.locator('.run-row')).to_have_count(3)
        expect(page.get_by_role('radio',name='待核实',exact=True)).to_be_checked()
        assert reads[-1]=='submitted_unconfirmed',reads
        assert parse_qs(urlparse(page.url).query).get('status')==['submitted_unconfirmed']
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    finally:context.close()


def test_result_navigation_ignores_unknown_status_values(browser_site):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        page=context.new_page();reads=[]
        def runs(route):
            reads.append(parse_qs(urlparse(route.request.url).query).get('status',[''])[0])
            route.fulfill(json={'items':[]})
        page.route('**/api/studio/runs?*',runs)
        page.goto(base+'/runs?status=unsupported-status',wait_until='networkidle')
        expect(page.get_by_role('radio',name='全部',exact=True)).to_be_checked()
        assert reads and set(reads)=={''},reads
        expect(page.get_by_role('heading',name='还没有执行记录',exact=True)).to_be_visible()
    finally:context.close()


@pytest.mark.parametrize('width',[390,1440])
def test_empty_homepage_keeps_binding_entry_reachable_without_overflow(browser_site,width):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        value=_context_navigation_payload(context,base)
        value.update(accounts=[],tasks=[],runs=[])
        value['resource_limits'].update(tasks_total=0,tasks_enabled_total=0,task_counts_by_account={})
        value['tasks_pagination'].update(total=0,has_next=False)
        page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('**/api/app/bootstrap',lambda route:route.fulfill(json=value))
        page.goto(base+'/dashboard',wait_until='networkidle')
        page.locator('.fire-main').wait_for()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        action=page.locator('.daily-no-account').get_by_role('button',name='绑定抖音账号',exact=True)
        expect(action).to_be_visible();action.click()
        page.wait_for_url('**/accounts')
        expect(page.get_by_role('button',name='开始绑定',exact=True)).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert errors==[],errors
    finally:context.close()


@pytest.mark.parametrize('width',[390,1440])
def test_admin_row_menu_restores_focus_and_does_not_submit_navigation(browser_site,width):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['admin'],'url':base}])
    try:
        page=context.new_page();writes=[]
        page.on('request',lambda request:writes.append(request.url)
            if request.method not in ('GET','HEAD','OPTIONS') and '/admin/users' in request.url else None)
        page.goto(base+'/admin/users',wait_until='networkidle')
        row=page.locator('.user-workbench-row').filter(has_text='workspace-check')
        trigger=row.get_by_role('button',name='workspace-check 更多操作',exact=True)
        trigger.focus();page.keyboard.press('ArrowDown')
        menu=page.get_by_role('menu');expect(menu).to_be_visible()
        expect(menu.get_by_role('menuitem',name='订阅与额度',exact=True)).to_be_focused()
        expect(menu.get_by_role('menuitem',name='停用用户',exact=True)).to_be_enabled()
        menu_box=menu.bounding_box()
        assert menu_box['x']>=0 and menu_box['x']+menu_box['width']<=width+1,menu_box
        _capture_site_detail(page,'admin-user-row-menu')
        page.keyboard.press('Escape');expect(menu).to_have_count(0);expect(trigger).to_be_focused()
        assert writes==[],writes

        trigger.click();page.get_by_role('menuitem',name='订阅与额度',exact=True).click()
        workspace=page.get_by_role('dialog',name=re.compile('workspace-check'))
        expect(workspace).to_be_visible()
        expect(workspace.get_by_label('任务额度',exact=True)).to_be_visible()
        assert writes==[],'Opening a management section must not renew or toggle the user'
        workspace.get_by_role('button',name='关闭',exact=True).first.click()
        expect(workspace).to_have_count(0)

        page.get_by_role('button',name='browser-admin 更多操作',exact=True).click()
        menu=page.get_by_role('menu');expect(menu).to_be_visible()
        expect(menu.get_by_role('menuitem',name='订阅与额度',exact=True)).to_be_disabled()
        expect(menu.get_by_role('menuitem',name='停用用户',exact=True)).to_be_disabled()
        page.keyboard.press('Escape');expect(menu).to_have_count(0)
        assert writes==[],writes
    finally:context.close()


@pytest.mark.parametrize('motion',['no-preference','reduce'])
def test_account_context_reads_tasks_outside_bootstrap_page_and_scopes_pagination(browser_site,motion):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':390,'height':900},reduced_motion=motion)
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        value=_context_navigation_payload(context,base)
        first,second=value['accounts']
        second_task=value['tasks'][2]
        all_tasks=[value['tasks'][0],second_task,
            {**second_task,'id':'context-task-b2','name':'第二账号的分页好友'}]
        value['tasks']=[all_tasks[0]]
        value['tasks_pagination'].update(page=1,page_size=1,total=3,has_next=True)
        value['resource_limits']['task_counts_by_account']={first['id']:1,second['id']:2}
        page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('**/api/app/bootstrap',lambda route:route.fulfill(json=value))
        reads=_route_context_task_pages(page,all_tasks,page_size=1)
        page.goto(base+'/accounts',wait_until='networkidle')
        card=page.locator('.account-card').filter(has=page.get_by_role('heading',name=second['name'],exact=True))
        card.get_by_role('button',name=re.compile('^查看任务')).click()
        page.wait_for_url('**/studio?account='+second['id'])
        expect(page.locator('.task-context')).to_contain_text(second['name'])
        rows=page.locator('.task-row')
        expect(rows).to_have_count(1);expect(rows).to_contain_text('第二账号好友')
        expect(page.get_by_role('combobox',name='发送账号',exact=True)).to_contain_text(second['name'])
        page.get_by_role('button',name=re.compile('^加载更多任务')).click()
        expect(rows).to_have_count(2)
        expect(rows.filter(has_text='第二账号的分页好友')).to_be_visible()
        assert reads==[(second['id'],1),(second['id'],2)],reads

        page.locator('.task-context').get_by_role('button',name='查看全部任务',exact=True).click()
        expect(page.locator('.task-context')).to_have_count(0)
        expect(rows).to_have_count(1);expect(rows).to_contain_text('第一账号好友甲')
        page.get_by_role('button',name=re.compile('^加载更多任务')).click()
        expect(rows).to_have_count(2)
        assert reads[-1]==('',2),reads
        assert 'account' not in parse_qs(urlparse(page.url).query)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert errors==[],errors
    finally:context.close()


def test_execution_records_read_second_server_page_and_keep_status_on_refresh(browser_site):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':390,'height':900},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        records=[{'id':f'page-run-{index}','name':f'分页验收好友{index:02d}',
            'status':'failed','label':'未发出','stage':'confirming','error':'隔离验收失败',
            'time':'2026-11-10 09:30'} for index in range(13)]
        page=context.new_page();reads=_route_execution_pages(page,records)
        page.goto(base+'/runs?status=failed',wait_until='networkidle')
        rows=page.locator('.run-row')
        expect(rows).to_have_count(12)
        expect(page.locator('.runs-count')).to_have_text('13 条记录')
        expect(page.get_by_role('button',name='上一页',exact=True)).to_be_disabled()
        page.get_by_role('button',name='下一页',exact=True).click()
        expect(rows).to_have_count(1);expect(rows).to_contain_text('分页验收好友12')
        expect(page.locator('.admin-pagination')).to_contain_text('第 2 / 2 页')
        expect(page.get_by_role('button',name='下一页',exact=True)).to_be_disabled()
        assert reads[-1]==('failed','',2),reads
        page.get_by_role('button',name='上一页',exact=True).click()
        expect(rows).to_have_count(12)
        expect(rows.filter(has_text='分页验收好友12')).to_have_count(0)
        assert reads[-1]==('failed','',1),reads
        page.reload(wait_until='networkidle')
        expect(page.get_by_role('radio',name='未发出',exact=True)).to_be_checked()
        expect(rows).to_have_count(12)
        assert reads[-1]==('failed','',1),reads
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    finally:context.close()


def test_execution_search_reaches_records_outside_first_page_and_combines_status(browser_site):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':1440,'height':900},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['user'],'url':base}])
    try:
        records=[{'id':f'search-run-{index}','name':f'第一页好友{index:02d}',
            'status':'success','label':'已发出','stage':'confirmed','error':'',
            'time':'2026-11-10 09:30'} for index in range(12)]
        query='分页外的唯一目标'
        records.extend([
            {'id':'search-failed','name':query,'status':'failed','label':'未发出',
             'stage':'confirming','error':'隔离验收失败','time':'2026-11-10 09:30'},
            {'id':'search-running','name':query+'的执行中记录','status':'running','label':'执行中',
             'stage':'confirming','error':'','time':'2026-11-10 09:31'},
        ])
        page=context.new_page();reads=_route_execution_pages(page,records)
        page.goto(base+'/runs',wait_until='networkidle')
        rows=page.locator('.run-row');expect(rows).to_have_count(12)
        expect(rows.filter(has_text=query)).to_have_count(0)
        # Search while on page two must request page one of the complete
        # server-side result set, not filter the twelve visible records.
        page.get_by_role('button',name='下一页',exact=True).click()
        expect(rows).to_have_count(2)
        search=page.get_by_placeholder('按好友名搜索',exact=True)
        search.fill(query)
        expect(page.locator('.runs-count')).to_have_text('2 条记录')
        expect(rows).to_have_count(2)
        assert reads[-1]==('',query,1),reads
        expect(page.get_by_role('button',name='下一页',exact=True)).to_have_count(0)
        page.get_by_role('radio',name='执行中',exact=True).click()
        expect(rows).to_have_count(1);expect(rows).to_contain_text(query+'的执行中记录')
        expect(search).to_have_value(query)
        assert reads[-1]==('running',query,1),reads
        assert parse_qs(urlparse(page.url).query).get('status')==['running']
        assert parse_qs(urlparse(page.url).query).get('q')==[query]
        page.reload(wait_until='networkidle')
        expect(page.get_by_role('radio',name='执行中',exact=True)).to_be_checked()
        expect(search).to_have_value(query)
        expect(rows).to_have_count(1);expect(rows).to_contain_text(query+'的执行中记录')
        assert reads[-1]==('running',query,1),reads
    finally:context.close()


@pytest.mark.parametrize('width',[1175,1280,1440])
def test_compact_desktop_admin_navigation_and_table_columns_remain_separate(browser_site,width):
    browser,base,tokens=browser_site
    context=browser.new_context(viewport={'width':width,'height':1000},reduced_motion='reduce')
    context.add_cookies([{'name':'spark_session','value':tokens['admin'],'url':base}])
    try:
        page=context.new_page();page.goto(base+'/admin/users',wait_until='networkidle')
        navigation=page.get_by_role('radiogroup',name='管理导航',exact=True)
        expect(navigation).to_be_visible()
        items=navigation.get_by_role('radio')
        expect(items).to_have_count(9)
        text_boxes=[item.locator('.rubber-segment__content').bounding_box() for item in items.all()]
        assert all(box and box['width']>0 and box['height']>0 for box in text_boxes),text_boxes
        for left,right in zip(text_boxes,text_boxes[1:]):
            assert left['x']+left['width']<=right['x']+1,(width,left,right)
        nav_box=navigation.bounding_box()
        assert nav_box['x']>=0 and nav_box['x']+nav_box['width']<=width+1,nav_box
        for selector in ['.fire-nav-brand','.fire-nav-right']:
            utility=page.locator(selector).bounding_box()
            assert utility
            separated=(nav_box['x']+nav_box['width']<=utility['x']+1
                or utility['x']+utility['width']<=nav_box['x']+1
                or nav_box['y']+nav_box['height']<=utility['y']+1
                or utility['y']+utility['height']<=nav_box['y']+1)
            assert separated,(width,selector,nav_box,utility)

        headings=page.locator('.user-workbench-head')
        row=page.locator('.user-workbench-row').filter(has_text='workspace-check')
        expect(row).to_be_visible()
        if width>=1200:
            expect(headings).to_be_visible()
            columns=page.evaluate("""()=>['.user-workbench-head','.user-workbench-row'].map(selector=>{
              const el=document.querySelector(selector),style=getComputedStyle(el),rect=el.getBoundingClientRect();
              return {tracks:style.gridTemplateColumns,gap:style.columnGap,
                start:rect.left+parseFloat(style.borderLeftWidth)+parseFloat(style.paddingLeft)};
            })""")
            assert columns[0]['tracks']==columns[1]['tracks'],columns
            assert columns[0]['gap']==columns[1]['gap'],columns
            assert abs(columns[0]['start']-columns[1]['start'])<=1,columns
            # A matching grid declaration alone misses a child assigned to
            # the wrong track. Check each actual cell against its heading.
            head_cells=headings.locator(':scope > span').all()
            row_cells=row.locator(':scope > *').all()
            assert len(head_cells)==len(row_cells)==6
            for heading,cell in zip(head_cells,row_cells):
                head_box=heading.bounding_box();cell_box=cell.bounding_box()
                assert head_box and cell_box
                assert cell_box['x']>=head_box['x']-1,(width,head_box,cell_box)
                assert cell_box['x']+cell_box['width']<=head_box['x']+head_box['width']+1,(width,head_box,cell_box)
        else:
            # Compact desktops pair identity/status and resources/health on
            # two lines, followed by actions. Only phones stack every field.
            expect(headings).to_be_hidden()
            identity=row.locator('.row-primary').bounding_box()
            status=row.locator('.row-status').bounding_box()
            resources=row.locator('.row-secondary').bounding_box()
            health=row.locator('.admin-user-health').bounding_box()
            actions=row.locator('.row-action-cell').bounding_box()
            assert identity['x']+identity['width']<=status['x']+1,(identity,status)
            assert abs(identity['y']-status['y'])<=1,(identity,status)
            assert resources['y']>=identity['y']+identity['height']-1,(identity,resources)
            assert health['y']>=status['y']+status['height']-1,(status,health)
            assert resources['x']+resources['width']<=health['x']+1,(resources,health)
            assert actions['y']>=max(resources['y']+resources['height'],health['y']+health['height'])-1,(resources,health,actions)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        output=Path('.artifacts/ui-layout-upgrade/screenshots');output.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(output/f'admin-navigation-{width}.png'))
        page.goto(base+'/admin/settings/mail',wait_until='networkidle')
        expect(navigation.get_by_role('radio',name='系统设置',exact=True)).to_be_checked()
        expect(navigation.get_by_role('radio',name='首页',exact=True)).not_to_be_checked()
        for route,activation in [('/notifications','click'),('/account-center','Enter'),('/help','Space')]:
            page.goto(base+route,wait_until='networkidle')
            expect(navigation.locator('[role="radio"][aria-checked="true"]')).to_have_count(0)
            home=navigation.get_by_role('radio',name='首页',exact=True)
            if activation=='click':home.click()
            else:home.press(activation)
            expect(page).to_have_url(base+'/dashboard')
            expect(home).to_be_checked()
    finally:context.close()
