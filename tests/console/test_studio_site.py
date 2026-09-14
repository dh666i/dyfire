"""Every business route must render the new shell, with its real controls intact."""
from html.parser import HTMLParser
from pathlib import Path

import pytest

from . import test_admin_web as admin_fixture


class Inventory(HTMLParser):
    def __init__(self,html):
        super().__init__();self.inputs=set();self.scripts=[];self.ids=[];self.forms=[];self.css=[];self.feed(html)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if 'id' in attrs:self.ids.append(attrs['id'])
        if tag in ('input','select','textarea') and attrs.get('name'):self.inputs.add(attrs['name'])
        if tag=='script' and attrs.get('src'):self.scripts.append(attrs['src'])
        if tag=='link' and attrs.get('rel')=='stylesheet':self.css.append(attrs['href'])
        if tag=='form':self.forms.append(attrs.get('action',''))


@pytest.fixture(scope='module')
def site():
    fixture=admin_fixture.AdminWebTests();fixture.setUp()
    yield fixture
    fixture.tearDown()


@pytest.mark.parametrize('path',[
 '/login','/register','/forgot-password','/dashboard','/accounts','/runs',
 '/subscription','/account-center','/change-password','/notifications','/attention','/help',
 '/admin/overview','/admin/users','/admin/subscriptions','/admin/codes','/admin/tasks',
 '/admin/runs','/admin/operations','/admin/settings/mail','/admin/audit','/admin/settings',
])
def test_routes_use_new_design_and_valid_assets(site,path):
    response=site.real_client.get(path)
    assert response.status_code==200,path
    assert 'data-design="dyfire-studio"' in response.text,path
    assert 'class="studio-header"' in response.text,path
    assert '/static/workspace.css?v=0.2.0' in response.text,path
    assert '/static/studio_tokens.css?v=0.2.0' in response.text,path
    assert '/static/studio_site.css' not in response.text,path
    if path not in ('/login','/register','/forgot-password'):
        assert 'data-workspace-heading' in response.text,path
    inventory=Inventory(response.text)
    assert len(inventory.ids)==len(set(inventory.ids)),(path,'duplicate ids')
    assert len(inventory.scripts)==len(set(inventory.scripts)),(path,'duplicate script initialization')
    assert not any('/mineradio' in href or '/app.css' in href or '/admin.css' in href or '/smoke_cards' in href for href in inventory.css)
    for url in inventory.css+inventory.scripts:
        assert site.real_client.get(url).status_code==200,(path,url)


@pytest.mark.parametrize('path,names,script',[
 ('/login',{'username','password'},'password_toggle.js'),
 ('/register',{'username','password','password_confirmation','invite_code'},'password_toggle.js'),
 ('/accounts',{'csrf_token'},'account_scan.js'),
 ('/subscription',{'csrf_token','code'},'product_ui.js'),
 ('/notifications',{'csrf_token','email','events','quiet_start','quiet_end'},'mail_ui.js'),
 ('/admin/settings/mail',{'csrf_token','host','port','username','password','sender','sender_name','mode','events'},'mail_ui.js'),
 ('/admin/users',{'csrf_token','username'},'admin.js'),
])
def test_real_controls_remain(site,path,names,script):
    inventory=Inventory(site.real_client.get(path).text)
    assert names<=inventory.inputs
    assert any(script in src for src in inventory.scripts)


def test_task_editor_and_customer_detail(site):
    for path in [f'/admin/customers/{site.active_user_id}']:
        page=site.real_client.get(path)
        assert page.status_code==200
        assert 'data-design="dyfire-studio"' in page.text


def test_no_old_shell_styles_are_loaded():
    root=Path(__file__).resolve().parents[2]/'spark_console/templates'
    for name in ('base.html','admin_base.html','studio_head.html'):
        text=(root/name).read_text(encoding='utf-8')
        for retired in ('mineradio.css','mineradio_admin.css','smoke_cards.css','smoke_production.css','layout_refinement.css'):
            assert retired not in text
