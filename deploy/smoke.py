"""Isolated component smoke: temporary database or local browser content only."""
import argparse
import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from spark_console.version import VERSION


def web_smoke():
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from spark_console.config import Settings
    from spark_console.db import create_engine_for, create_schema, session_scope
    from spark_console.models import ContactDirectoryJob, DouyinAccount, DouyinLoginSession, User
    from spark_console.security import SessionService
    from spark_console.web.app import create_app
    # Import the other lightweight service as part of this same dependency check.
    import spark_console.ops_history

    with TemporaryDirectory() as folder:
        root = Path(folder)
        cookie = root / 'cookie.key'; cookie.write_bytes(b'c' * 32)
        session = root / 'session.key'; session.write_bytes(b's' * 32)
        settings = Settings(root, f'sqlite:///{root}/spark.db', cookie, session)
        engine = create_engine_for(settings); create_schema(engine)
        with session_scope(engine) as db:
            user = User(username='isolated-smoke-admin', password_hash='not-a-login-password', role='admin', must_change_password=False)
            db.add(user); db.flush()
            raw, record = SessionService(b's' * 32).create_record(user.id)
            db.add(record)
            csrf = record.csrf_token
            account = DouyinAccount(owner_user_id=user.id, display_name='Isolated smoke', encrypted_cookies=b'not-live-cookies', cookie_nonce=b'not-live-nonce', validation_state='valid')
            db.add(account); db.flush(); account_id = account.id
        with TestClient(create_app(settings, engine), base_url='https://testserver') as client:
            assert client.get('/health/ready').status_code == 200
            assert client.get('/api/version').json()['version'] == VERSION
            assert client.get('/studio').status_code == 200
            assert client.get('/api/studio').status_code == 401
            client.cookies.set('spark_session', raw)
            assert client.get('/api/studio').status_code == 200
            scan = client.post('/accounts/scan', data={'csrf_token': csrf})
            assert scan.status_code == 201, scan.text
            sync = client.post(f'/accounts/{account_id}/directory-jobs', data={'csrf_token': csrf})
            assert sync.status_code == 202, sync.text
            with session_scope(engine) as db:
                assert db.scalar(select(DouyinLoginSession)) is not None
                job = db.scalar(select(ContactDirectoryJob))
                assert job is not None and job.status == 'queued'
        engine.dispose()


def browser_smoke():
    # Import execution entry points without starting their loops.
    import spark_console.auth_worker
    import spark_console.worker
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
        page = browser.new_page()
        page.set_content('<p>browser-ready</p>')
        assert page.inner_text('p') == 'browser-ready'
        browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--component', choices=['web', 'browser'], default=os.environ.get('DYFIRE_RUNTIME_COMPONENT', 'web'))
    args = parser.parse_args()
    if args.component == 'web':
        assert importlib.util.find_spec('playwright') is None, 'Web image must not install Playwright'
        assert not Path('/ms-playwright').exists(), 'Web image must not contain Chromium'
        web_smoke()
    else:
        browser_smoke()
    print(f'{args.component} component smoke passed ({VERSION})')


if __name__ == '__main__':
    main()
