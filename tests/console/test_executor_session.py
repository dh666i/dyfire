import asyncio
from types import ModuleType
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from spark_console.executor import DouyinExecutor
from tests.console.test_credentials import (
    _FakeBrowser,
    _FakeContext,
    _FakePlaywrightManager,
    _fake_core_tasks,
)


class _StartablePlaywrightManager(_FakePlaywrightManager):
    def __init__(self, browser):
        super().__init__(browser)
        self.starts = 0
        self.stops = 0

    async def start(self):
        self.starts += 1
        self.playwright.stop = self._stop
        return self.playwright

    async def _stop(self):
        self.stops += 1


class _RequestContext(_FakeContext):
    def __init__(self):
        super().__init__()
        self.request = self

    async def get(self, *_args, **_kwargs):
        raise TimeoutError("login api unused for reuse test")


class ExecutorSessionTests(IsolatedAsyncioTestCase):
    async def test_same_account_reuses_browser_after_authentication(self):
        from core.web_chat import TargetNotFoundError

        browser = _FakeBrowser()
        browser.context = _RequestContext()
        launches = {"count": 0}

        async def launch_and_return(**_options):
            launches["count"] += 1
            return browser

        manager = _StartablePlaywrightManager(browser)
        manager.playwright.chromium.launch = launch_and_return
        async_api = ModuleType("playwright.async_api")
        async_api.async_playwright = lambda: manager
        playwright = ModuleType("playwright")
        playwright.async_api = async_api

        async def target_not_found(*_args, **_kwargs):
            raise TargetNotFoundError("fixed public mapping")

        raw = b'[{"name":"sid","value":"reuse-marker","domain":".douyin.com","path":"/"}]'
        executor = DouyinExecutor()
        with patch.dict(
            "sys.modules",
            {
                "playwright": playwright,
                "playwright.async_api": async_api,
                "core.tasks": _fake_core_tasks(),
            },
        ), patch("spark_console.executor.select_web_chat_target", target_not_found):
            first = await executor.execute(
                raw, "目标甲", "消息", credential_version=1, session_key="acct-1"
            )
            second = await executor.execute(
                raw, "目标乙", "消息", credential_version=1, session_key="acct-1"
            )
            await executor.close_session()

        self.assertEqual("target_not_found", first.error_code)
        self.assertEqual("target_not_found", second.error_code)
        self.assertEqual(1, launches["count"])
        self.assertEqual(1, browser.close_count)
