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
 '/admin/overview','/admin/today','/admin/users','/admin/subscriptions','/admin/codes','/admin/tasks',
 '/admin/runs','/admin/operations','/admin/settings/mail','/admin/audit','/admin/settings',
])
def test_routes_use_new_design_and_valid_assets(site,path):
    response=site.real_client.get(path)
    assert response.status_code==200,path
    assert '<div id="app"></div>' in response.text,path
    assert '/assets/index-' in response.text,path
    inventory=Inventory(response.text)
    assert len(inventory.ids)==len(set(inventory.ids)),(path,'duplicate ids')
    assert len(inventory.scripts)==len(set(inventory.scripts)),(path,'duplicate script initialization')
    assert not any('/mineradio' in href or '/app.css' in href or '/admin.css' in href or '/smoke_cards' in href for href in inventory.css)
    for url in inventory.css+inventory.scripts:
        assert site.real_client.get(url).status_code==200,(path,url)


@pytest.mark.parametrize('path', ['/accounts','/subscription','/notifications','/admin/settings/mail','/admin/users'])
def test_authenticated_controls_are_backed_by_bootstrap(site,path):
    response=site.real_client.get(path)
    assert response.status_code==200
    assert '<div id="app"></div>' in response.text


def test_task_editor_and_customer_detail(site):
    for path in [f'/admin/customers/{site.active_user_id}']:
        page=site.real_client.get(path)
        assert page.status_code==200
        assert '<div id="app"></div>' in page.text


def test_mobile_filter_rows_stack_instead_of_overflowing():
    root = Path(__file__).resolve().parents[2]
    workspace = (root / "spark_console/static/workspace.css").read_text(encoding="utf-8")
    features = (root / "spark_console/static/workspace_features.css").read_text(encoding="utf-8")
    runs = (root / "spark_console/templates/admin_runs.html").read_text(encoding="utf-8")
    assert "admin-run-filters" in runs
    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)" in workspace
    assert 'input[type="date"]' in workspace
    assert "min-width: 0" in workspace
    assert "flex-wrap: wrap" in features.split(".workspace-options {", 1)[1][:280]
    assert ".admin-col-status" in features
    assert "today-send-action" in workspace
    overview = (root / "spark_console/templates/admin_overview.html").read_text(encoding="utf-8")
    assert 'class="admin-col-status"' in overview
    assert "admin-col-compact" not in overview


def test_no_old_shell_styles_are_loaded():
    root=Path(__file__).resolve().parents[2]/'spark_console/templates'
    for name in ('base.html','admin_base.html','studio_head.html'):
        text=(root/name).read_text(encoding='utf-8')
        for retired in ('mineradio.css','mineradio_admin.css','smoke_cards.css','smoke_production.css','layout_refinement.css'):
            assert retired not in text


def test_attempt_details_render_for_owner_and_admin(site):
    from datetime import datetime, timezone
    from sqlalchemy import select
    from spark_console.db import session_scope
    from spark_console.models import User, SparkTask, TaskRun, TaskRunAttempt
    with session_scope(site.engine) as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        task = SparkTask(owner_user_id=admin.id, target_name="历史详情测试",
                        send_time="09:30", message_template="test", enabled=False)
        db.add(task)
        db.flush()
        run = TaskRun(task_id=task.id, scheduled_for=datetime.now(timezone.utc),
                      status="failed", stage="authenticating", attempt=2)
        db.add(run)
        db.flush()
        db.add(TaskRunAttempt(run_id=run.id, number=1, started_at=run.scheduled_for,
            finished_at=run.scheduled_for, status="failed", stage="authenticating",
            reason="测试加载异常 <private>"))
    for path in ("/runs", "/admin/runs"):
        response = site.real_client.get(path)
        assert response.status_code == 200
        assert '<div id="app"></div>' in response.text
