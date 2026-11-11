"""Check maintained runtime contents using local pages, never Douyin accounts."""
import argparse
import importlib.util
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--component', choices=['web', 'browser'], required=True)
args = parser.parse_args()
assert os.getuid() == 10001, 'Runtime must default to the unprivileged spark user'
assert os.environ.get('DYFIRE_RUNTIME_COMPONENT') == args.component
import fastapi, sqlalchemy, argon2, cryptography, httpx, redis, psycopg
if args.component == 'web':
    assert importlib.util.find_spec('playwright') is None
    assert not Path('/ms-playwright').exists()
else:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
        page = browser.new_page()
        page.set_content('<p>runtime-ready</p>')
        assert page.inner_text('p') == 'runtime-ready'
        browser.close()
print(args.component + ' runtime smoke passed')
