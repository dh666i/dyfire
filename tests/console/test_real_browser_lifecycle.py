import asyncio
import os
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
from scripts.browser_capacity import measure


def test_real_browser_idle_close_and_saved_login_restore_without_external_traffic():
    with sync_playwright() as pw:
        if not Path(pw.chromium.executable_path).exists():
            if os.environ.get('DYFIRE_REQUIRE_BROWSER')=='1':pytest.fail('Chromium required')
            pytest.skip('Chromium not installed')
    report=asyncio.run(measure(slots=1,rounds=1))
    assert report['network']=='blocked'
    assert report['idle_sessions_recycled'] and report['chromium_disconnected']
    assert report['saved_cookie_restored']
    assert report['production_account_capacity'] is None
