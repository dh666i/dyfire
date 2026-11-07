"""Run the actual inspector against an isolated browser adapter, without sends."""
import asyncio
import pytest
from core.session_health import SessionEvidence
from spark_console import read_only_probe


@pytest.mark.parametrize('state',['valid','verification'])
def test_inspector_enforces_session_before_identity_and_only_reads(monkeypatch,state):
    events=[]
    class Locator:
        @property
        def first(self):return self
        async def is_visible(self):return True
    class Page:
        async def goto(self,*args,**kwargs):events.append('navigation')
        async def wait_for_selector(self,*args,**kwargs):events.append('editor')
        def locator(self,*args):return Locator()
        async def evaluate(self,script,*args):
            assert 'innerText' not in script and '.value' not in script
            return True if args else 1
    class Context:
        async def add_cookies(self,value):pass
        async def new_page(self):return Page()
    class Browser:
        async def new_context(self,**kwargs):return Context()
        async def close(self):events.append('closed')
    class Chromium:
        async def launch(self,**kwargs):return Browser()
    class Manager:
        async def __aenter__(self):return type('Playwright',(),{'chromium':Chromium()})()
        async def __aexit__(self,*args):pass
    async def session(page,context,*,timeout):
        events.append('authentication');return SessionEvidence(state,'fixture')
    async def identity(context):events.append('identity');return {'platform_user_id':'fixture-owner'}
    async def target(page,name,**kwargs):
        assert kwargs['target_sec_uid']=='fixture-target';events.append('target')
    monkeypatch.setattr('playwright.async_api.async_playwright',Manager)
    monkeypatch.setattr(read_only_probe,'wait_for_session',session)
    monkeypatch.setattr(read_only_probe,'read_self_profile',identity)
    monkeypatch.setattr(read_only_probe,'select_web_chat_target',target)
    raw=b'[{"name":"sid","value":"isolated","domain":".example.invalid","path":"/"}]'
    result=asyncio.run(read_only_probe.inspect_task(raw,1,'fixture','fixture-target','douyin_sticker',message_id='receipt',expected_user_id='fixture-owner'))
    assert events[-1]=='closed'
    if state=='valid':
        assert 'error_code' not in result,result
        assert result['delivery']=='message_id_found' and result['checks']['sticker_entry']
        assert events==['navigation','authentication','identity','target','editor','closed']
    else:
        assert result['error_code']=='SessionUnavailable'
        assert events==['navigation','authentication','closed']
