import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from utils.logger import setup_logger
from core.avatars import avatar_from_user_info


WEB_CHAT_URL = "https://www.douyin.com/chat"
CONVERSATION_ITEM_SELECTOR = ".conversationConversationItemwrapper"
CONVERSATION_TITLE_SELECTOR = ".conversationConversationItemtitle"
CHAT_EDITOR_SELECTOR = ".messageEditorimChatEditorContainer"
SEARCH_INPUT_SELECTORS = (
    'input[placeholder="搜索"]',
    'input[placeholder*="搜索"]',
)
# The chat list is virtualized: only a small window of rows is present in the
# DOM at any time. Scroll the owning container instead of assuming the first
# few visible rows are the complete address book.
SCROLL_CONVERSATION_LIST_SCRIPT = r"""() => {
  const row = document.querySelector('.conversationConversationItemwrapper');
  if (!row) return {moved:false, reason:'no_rows'};
  for (let node = row.parentElement; node && node !== document.body; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (node.scrollHeight > node.clientHeight + 4 && /auto|scroll/.test(style.overflowY)) {
      const before = node.scrollTop;
      const step = Math.max(160, Math.floor(node.clientHeight * .82));
      node.scrollTop = Math.min(node.scrollTop + step, node.scrollHeight);
      return {moved: node.scrollTop > before, top: node.scrollTop, height: node.scrollHeight, viewport: node.clientHeight};
    }
  }
  return {moved:false, reason:'no_scroll_container'};
}"""
LOGIN_REQUIRED_TEXTS = (
    "扫码登录",
    "手机号登录",
    "验证码登录",
)

logger = setup_logger(level=logging.DEBUG)


@dataclass(frozen=True)
class DouyinUserIdentity:
    sec_uid: str
    short_id: str | None = None
    unique_id: str | None = None
    nickname: str | None = None
    remark_name: str | None = None
    avatar_url: str | None = None

    @property
    def aliases(self) -> tuple[str, ...]:
        values = (
            self.remark_name,
            self.nickname,
            self.unique_id,
            self.short_id,
        )
        return tuple(dict.fromkeys(value.strip() for value in values if value and value.strip()))


class UserInfoCollector:
    PATH = "/aweme/v1/web/im/user/info"

    def __init__(self):
        self.identities: dict[str, DouyinUserIdentity] = {}
        self._changed = asyncio.Event()
        self._pending: set[asyncio.Task] = set()

    def capture(self, response) -> None:
        if self.PATH not in urlparse(response.url).path:
            return
        task = asyncio.create_task(self.handle_response(response))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def handle_response(self, response) -> None:
        if self.PATH not in urlparse(response.url).path or response.status != 200:
            return
        try:
            body = await response.json()
        except Exception:
            return
        data = body.get("data", ()) if isinstance(body, dict) else ()
        for item in data if isinstance(data, list) else ():
            if not isinstance(item, dict):
                continue
            sec_uid = str(item.get("sec_uid") or "").strip()
            if not sec_uid:
                continue
            self.identities[sec_uid] = DouyinUserIdentity(
                sec_uid=sec_uid,
                short_id=_optional_string(item.get("short_id")),
                unique_id=_optional_string(item.get("unique_id")),
                nickname=_optional_string(item.get("nickname")),
                remark_name=_optional_string(item.get("remark_name")),
                avatar_url=avatar_from_user_info(item) or getattr(self.identities.get(sec_uid), "avatar_url", None),
            )
            self._changed.set()

    def get(self, sec_uid: str) -> DouyinUserIdentity | None:
        return self.identities.get(sec_uid)

    @property
    def avatar_urls(self) -> dict[str, str]:
        return {key: identity.avatar_url for key, identity in self.identities.items() if identity.avatar_url}

    async def wait_for(self, sec_uid: str, timeout: float = 15.0) -> DouyinUserIdentity | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            identity = self.get(sec_uid)
            if identity is not None:
                return identity
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), deadline - loop.time())
            except TimeoutError:
                break
        return self.get(sec_uid)

    async def drain(self) -> tuple[DouyinUserIdentity, ...]:
        if self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)
        return tuple(self.identities.values())


def _optional_string(value) -> str | None:
    text = str(value or "").strip()
    return text or None


class TargetNotFoundError(RuntimeError):
    """Raised when the requested friend is absent from the web chat list."""


class WebChatLoginRequiredError(RuntimeError):
    """Raised when the web chat page requires the account to sign in again."""


def _redirected_away_from_web_chat(url: object) -> bool:
    try:
        parsed = urlparse(str(url or ""))
    except (TypeError, ValueError):
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    return not (
        parsed.hostname.lower() == "www.douyin.com"
        and parsed.path.rstrip("/") == "/chat"
    )


async def _locator_has_visible_match(locator) -> bool:
    try:
        count = await locator.count()
        if count <= 0:
            return False
        all_matches = getattr(locator, "all", None)
        nth_match = getattr(locator, "nth", None)
        if callable(all_matches):
            matches = await all_matches()
        elif callable(nth_match):
            matches = [nth_match(index) for index in range(count)]
        else:
            matches = [getattr(locator, "first", locator)]
        for match in matches:
            is_visible = getattr(match, "is_visible", None)
            if not callable(is_visible) or await is_visible():
                return True
    except Exception:
        # Minimal page doubles and older Playwright-style wrappers may not expose
        # the complete Locator API. A missing signal must not break selection.
        return False
    return False


async def _has_visible_exact_text(page, text: str) -> bool:
    get_by_text = getattr(page, "get_by_text", None)
    if callable(get_by_text):
        try:
            if await _locator_has_visible_match(get_by_text(text, exact=True)):
                return True
        except Exception:
            pass

    locator = getattr(page, "locator", None)
    if callable(locator):
        try:
            return await _locator_has_visible_match(locator(f"text={text}"))
        except Exception:
            pass
    return False


async def ensure_web_chat_authenticated(page) -> None:
    """Raise when navigation or visible login controls show an expired session."""
    if _redirected_away_from_web_chat(getattr(page, "url", "")):
        raise WebChatLoginRequiredError("web chat redirected to a login page")
    for text in LOGIN_REQUIRED_TEXTS:
        if await _has_visible_exact_text(page, text):
            raise WebChatLoginRequiredError(f"web chat shows {text}")


async def list_visible_web_chat_targets(page, timeout=30000):
    """Return unique, visible conversation titles from the signed-in chat list."""
    await page.wait_for_selector(CONVERSATION_ITEM_SELECTOR, timeout=timeout)
    targets = []
    seen = set()
    for item in await page.locator(CONVERSATION_ITEM_SELECTOR).all():
        if hasattr(item, "is_visible") and not await item.is_visible():
            continue
        title = (
            await item.locator(CONVERSATION_TITLE_SELECTOR).inner_text()
        ).strip()
        if title and title not in seen:
            seen.add(title)
            targets.append(title)
    return targets


async def _click_search_result(result) -> None:
    try:
        send_button = result.locator(
            "xpath=ancestor::*[.//*[normalize-space()='发消息']][1]"
            "//*[normalize-space()='发消息' and "
            "not(.//*[normalize-space()='发消息'])]"
        )
        if await send_button.count() > 0 and await send_button.is_visible():
            await send_button.click()
            return
    except (AttributeError, TypeError):
        pass
    try:
        # Search cards are often rendered as a non-clickable nickname span.
        # Clicking the card itself lets Douyin's delegated handler open the
        # thread even when no explicit 发消息 button is present.
        card = result.locator(
            "xpath=ancestor::*[@data-e2e='search-user-item' or "
            "contains(@class, 'search')][1]"
        )
        if await card.count() > 0 and await card.is_visible():
            await card.click()
            return
    except (AttributeError, TypeError):
        pass
    await result.click()


async def _click_first_visible(locator) -> bool:
    """Click the first visible identity-matched node, if the page exposes one."""
    try:
        matches = await locator.all() if hasattr(locator, "all") else [locator.first]
    except Exception:
        return False
    for match in matches:
        try:
            if hasattr(match, "is_visible") and not await match.is_visible():
                continue
            await match.click()
            return True
        except Exception:
            continue
    return False


async def _select_web_chat_target_by_identity(page, sec_uid: str) -> bool:
    """Prefer the stable Douyin identity exposed by conversation/search nodes."""
    value = str(sec_uid or "").strip()
    if not value:
        return False
    # Current web chat builds expose identity on conversation rows, while
    # search cards may expose it on a nested data attribute or profile href.
    # Keep the selectors narrow so an unrelated profile link is never clicked.
    selectors = (
        f'{CONVERSATION_ITEM_SELECTOR}[data-sec-uid="{value}"]',
        f'[data-e2e="search-user-item"][data-sec-uid="{value}"]',
        f'{CONVERSATION_ITEM_SELECTOR}:has([data-sec-uid="{value}"])',
        f'{CONVERSATION_ITEM_SELECTOR}:has(a[href*="{value}"])',
        f'[data-e2e="search-user-item"]:has(a[href*="{value}"])',
    )
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return False
    for selector in selectors:
        try:
            candidate = locator(selector)
            if await candidate.count() and await _click_first_visible_and_wait(page, candidate):
                return True
        except Exception:
            # CSS :has and escaped sec_uid values are not supported by every
            # browser/page double; identity text matching remains available.
            continue
    evaluate = getattr(page, "evaluate", None)
    if callable(evaluate):
        try:
            return bool(
                await evaluate(
                    """(uid) => {
                      const visible = (node) => Boolean(node && node.getClientRects().length);
                      const rows = [...document.querySelectorAll(
                        '.conversationConversationItemwrapper, [data-e2e="search-user-item"]'
                      )].filter(visible);
                      const hasIdentity = (row) => {
                        if (row.getAttribute('data-sec-uid') === uid) return true;
                        return [...row.querySelectorAll('[data-sec-uid]')]
                          .some(node => node.getAttribute('data-sec-uid') === uid)
                          || [...row.querySelectorAll('a[href]')]
                            .some(link => String(link.getAttribute('href') || '').includes(uid));
                      };
                      const row = rows.find(hasIdentity);
                      if (!row) return false;
                      row.scrollIntoView({block: 'nearest'});
                      const target = row.querySelector('button,[role="button"]') || row;
                      target.click();
                      return true;
                    }""",
                    value,
                )
            )
        except Exception:
            pass
    return False


async def _wait_for_visible_search_results(page, candidate, timeout_ms, target_sec_uid=None):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + min(8.0, max(1.0, timeout_ms / 1000))
    while True:
        if target_sec_uid and await _select_web_chat_target_by_identity(page, target_sec_uid):
            return [None]
        exact = page.get_by_text(candidate, exact=True)
        if await exact.count() > 0:
            results = await exact.all() if hasattr(exact, "all") else [exact.first]
            visible = []
            for result in results:
                if hasattr(result, "is_visible") and not await result.is_visible():
                    continue
                visible.append(result)
            if visible:
                return visible
        remaining = deadline - loop.time()
        if remaining <= 0:
            return []
        await asyncio.sleep(min(0.2, remaining))


async def _scroll_conversation_list(page) -> bool:
    """Advance one virtual-list window and report whether rows moved."""
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return False
    try:
        result = await evaluate(SCROLL_CONVERSATION_LIST_SCRIPT)
        return bool(result and result.get("moved")) if isinstance(result, dict) else bool(result)
    except Exception:
        # A DOM shape change should fall through to the dedicated search path;
        # it must never turn a target-selection attempt into a worker crash.
        return False


async def _wait_for_chat_editor(page, timeout_ms: int = 18000) -> bool:
    """Verify a clicked result opened the actual chat, not just a profile card."""
    wait_for_selector = getattr(page, "wait_for_selector", None)
    if not callable(wait_for_selector):
        return True
    try:
        await wait_for_selector(CHAT_EDITOR_SELECTOR, timeout=timeout_ms)
        return True
    except Exception:
        return False


async def _click_conversation_row(page, item) -> bool:
    """Click the actionable part of a virtualized row and verify the thread."""
    candidates = []
    locator = getattr(item, "locator", None)
    if callable(locator):
        for selector in (
            CONVERSATION_TITLE_SELECTOR,
            '[role="button"]',
            'button',
        ):
            try:
                candidate = locator(selector)
                count = getattr(candidate, "count", None)
                if not callable(count) or await count() > 0:
                    candidates.append(candidate.first if hasattr(candidate, "first") else candidate)
            except Exception:
                continue
    candidates.append(item)
    for index, candidate in enumerate(candidates):
        try:
            visible = getattr(candidate, "is_visible", None)
            if callable(visible) and not await visible():
                continue
            await candidate.click()
            if await _wait_for_chat_editor(page, timeout_ms=10000 if index == 0 else 3000):
                return True
        except Exception:
            continue
    return False


async def _click_first_visible_and_wait(page, locator) -> bool:
    """Apply the row click contract to the first visible identity match."""
    try:
        matches = await locator.all() if hasattr(locator, "all") else [locator.first]
    except Exception:
        return False
    for match in matches:
        try:
            visible = getattr(match, "is_visible", None)
            if callable(visible) and not await visible():
                continue
            if await _click_conversation_row(page, match):
                return True
        except Exception:
            continue
    return False


async def select_web_chat_target(page, target, timeout=30000, aliases=(), target_sec_uid=None):
    """Select one exact target, preferring real conversation rows over page text."""
    normalized_target = " ".join(str(target or "").split())
    candidates = tuple(
        dict.fromkeys(
            " ".join(str(value).split())
            for value in (*aliases, normalized_target)
            if value and " ".join(str(value).split())
        )
    )

    # Douyin hydrates the conversation list incrementally. A single DOM read
    # can therefore miss a conversation that is about to appear, especially
    # after another task left the page inside a virtualized thread. Keep the
    # poll bounded and only inspect visible rows so we never click stale DOM.
    wanted = {value.casefold() for value in candidates}
    loop = asyncio.get_running_loop()
    # Give the virtualized list enough time to hydrate, but keep the whole
    # selection bounded. Identity matching below avoids waiting on text when
    # the nickname/remark has changed.
    deadline = loop.time() + max(3.0, min(14.0, timeout / 1000))

    if target_sec_uid and await _select_web_chat_target_by_identity(page, target_sec_uid):
        return normalized_target

    async def choose_visible_row():
        saw_items = False
        scroll_attempts = 0
        stalled = 0
        while True:
            items = await page.locator(CONVERSATION_ITEM_SELECTOR).all()
            saw_items = saw_items or bool(items)
            for item in items:
                if hasattr(item, "is_visible") and not await item.is_visible():
                    continue
                title = (
                    await item.locator(CONVERSATION_TITLE_SELECTOR).inner_text()
                ).strip()
                if title.casefold() in wanted:
                    if await _click_conversation_row(page, item):
                        return title
                    logger.warning(
                        "conversation row clicked but chat editor did not open target=%s",
                        title,
                    )
                    return None
            # If the list is absent entirely, let the dedicated search surface
            # handle the target immediately. Only poll when a virtualized list
            # exists but has not yet exposed the requested row.
            if not saw_items:
                return None
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            if scroll_attempts < 24:
                moved = await _scroll_conversation_list(page)
                if moved:
                    scroll_attempts += 1
                    stalled = 0
                    await asyncio.sleep(min(0.35, remaining))
                    continue
            stalled += 1
            # Give a hydrating list a few short chances before falling back to
            # global search. This avoids turning a delayed first render into a
            # false target-not-found result while keeping the retry bounded.
            if stalled >= 4 or scroll_attempts >= 24:
                return None
            await asyncio.sleep(min(0.25, remaining))

    selected = await choose_visible_row()
    if selected:
        return selected

    for selector in SEARCH_INPUT_SELECTORS:
        try:
            search = page.locator(selector)
            if await search.count() == 0:
                continue
            field = search.first
            for candidate in candidates:
                await field.fill(candidate)
                results = await _wait_for_visible_search_results(
                    page, candidate, timeout, target_sec_uid
                )
                for result in results:
                    if result is None:
                        return candidate
                    await _click_search_result(result)
                    if await _wait_for_chat_editor(page):
                        return candidate
                    logger.warning(
                        "search result clicked but chat editor did not open target=%s",
                        candidate,
                    )
                    break
        except (AttributeError, TypeError):
            # Older page doubles and older layouts have no global search surface.
            break

    await page.wait_for_selector(CONVERSATION_ITEM_SELECTOR, timeout=timeout)
    selected = await choose_visible_row()
    if selected:
        return selected

    raise TargetNotFoundError(f"未在抖音聊天列表中找到好友 {normalized_target}")


async def run_wz_web_chat_probe():
    """Send and verify one WZ message through https://www.douyin.com/chat."""
    from core.browser import get_browser
    from core.msg_builder import build_message
    from core.tasks import (
        capture_message_confirmation_baseline,
        confirm_message_sent,
    )
    from utils.config import get_userData

    users = get_userData()
    if len(users) != 1:
        raise RuntimeError("WZ 单向测试必须且只能包含一个账号")

    user = users[0]
    targets = user.get("targets", [])
    if len(targets) != 1:
        raise RuntimeError("WZ 单向测试必须且只能包含一个目标好友")

    username = user.get("username", "未知用户")
    target = targets[0]
    playwright, browser = await get_browser()
    context = None

    try:
        context = await browser.new_context()
        context.set_default_navigation_timeout(120000)
        context.set_default_timeout(120000)
        await context.add_cookies(user["cookies"])
        page = await context.new_page()
        await page.goto(WEB_CHAT_URL)

        try:
            await select_web_chat_target(page, target)
            await page.wait_for_selector(CHAT_EDITOR_SELECTOR, timeout=30000)
        except Exception:
            os.makedirs(os.path.join("logs", "diagnostics"), exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            await page.screenshot(
                path=os.path.join(
                    "logs", "diagnostics", f"web_chat_probe_{timestamp}.png"
                ),
                full_page=True,
            )
            raise

        chat_input = page.locator(CHAT_EDITOR_SELECTOR).first
        message = build_message()
        lines = message.split("\n")
        for index, line in enumerate(lines):
            await chat_input.type(line)
            if index < len(lines) - 1:
                await chat_input.press("Shift+Enter")

        logger.info(f"账号 {username} 准备通过抖音网页聊天发送消息给 {target}")
        confirmation_baseline = await capture_message_confirmation_baseline(
            page, message
        )
        await chat_input.press("Enter")
        await confirm_message_sent(
            page,
            chat_input,
            message,
            baseline_count=confirmation_baseline,
        )
        logger.info(f"账号 {username} 给好友 {target} 发送消息并确认送达完成")
    finally:
        if context is not None:
            await context.close()
        await browser.close()
        await playwright.stop()
