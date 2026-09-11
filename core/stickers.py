"""Drive Douyin's first-party web composer for native dynamic stickers."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass

from core.web_chat import CHAT_EDITOR_SELECTOR


DOUYIN_SPARK_STICKER = "续火花"

# Generated class names have changed between Douyin web releases. Semantic
# attributes are preferred, with the known current class names as fallbacks.
STICKER_BUTTON_SELECTORS = (
    'button[aria-label*="表情"]',
    '[role="button"][aria-label*="表情"]',
    '[title*="表情"]',
    'button[aria-label*="emoji" i]',
    '[role="button"][aria-label*="emoji" i]',
    '[class*="messageMsgInputiconAction"]',
    '[class*="emoji"][role="button"]',
)
STICKER_PANEL_SELECTORS = (
    '.componentsemojiemojiPanel',
    '[class*="emojiPanel"]',
    '[class*="EmojiPanel"]',
    '[class*="stickerPanel"]',
    '[class*="StickerPanel"]',
)


class NativeStickerError(RuntimeError):
    """Base error with the send-boundary state attached."""

    def __init__(self, message: str, *, submitted: bool = False):
        super().__init__(message)
        self.submitted = submitted


class NativeStickerPreparationError(NativeStickerError):
    """The sticker was not ready; retrying is safe."""


class NativeStickerSubmissionError(NativeStickerError):
    """The item was selected, but delivery could not be confirmed."""

    def __init__(self, message: str):
        super().__init__(message, submitted=True)


@dataclass(frozen=True)
class NativeStickerSendResult:
    submitted: bool
    confirmed: bool
    reason: str = ""
    message_id: str | None = None


# The snapshot is deliberately based on outgoing message containers rather
# than visible text. A native sticker may have no text node at all.
_SNAPSHOT_SCRIPT = r"""
() => {
  const root = document.querySelector('#sub-app') || document.body;
  if (!root) return [];
  const selector = [
    '[data-message-id]', '[data-msg-id]', '[data-msgid]',
    '[data-client-message-id]', '[data-server-message-id]',
    '[data-e2e*="msg"]', '[class*="message"]', '[class*="Message"]',
    '[class*="bubble"]', '[class*="Bubble"]', 'li[role="listitem"]'
  ].join(',');
  const editors = '[contenteditable="true"], textarea, [class*="chat-input-"], [class*="messageEditor"]';
  const marker = (node) => {
    const values = [];
    for (let current = node, depth = 0; current && current !== root && depth < 8; current = current.parentElement, depth += 1) {
      values.push(current.className || '', current.getAttribute('data-testid') || '',
        current.getAttribute('data-direction') || '', current.getAttribute('data-owner') || '',
        current.getAttribute('data-is-self') || '', current.getAttribute('data-self') || '');
    }
    return values.join(' ').toLowerCase();
  };
  const outgoing = (node) => {
    const value = marker(node);
    if (/(^|[\s_-])(incoming|received|other|left)([\s_-]|$)/.test(value)) return false;
    if (/(^|[\s_-])(outgoing|sent|mine|self|right)([\s_-]|$)/.test(value) || /(is-self|from-me|message-right)/.test(value)) return true;
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    const editor = root.querySelector(editors);
    const reference = editor && editor.getBoundingClientRect().width ? editor.getBoundingClientRect() : root.getBoundingClientRect();
    return rect.left + rect.width * .5 >= reference.left + reference.width * .5;
  };
  const key = (node) => {
    for (let current = node, depth = 0; current && depth < 7; current = current.parentElement, depth += 1) {
      for (const name of ['data-message-id', 'data-msg-id', 'data-msgid', 'data-client-message-id', 'data-server-message-id']) {
        const value = String(current.getAttribute(name) || '').trim();
        if (value && !['0', 'null', 'undefined'].includes(value)) return name + ':' + value;
      }
    }
    const imgs = Array.from(node.querySelectorAll('img')).map((img) => img.src || img.alt || img.title || '').filter(Boolean).join('|');
    const text = String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
    const rect = node.getBoundingClientRect();
    return [node.tagName, node.className || '', imgs, text, Math.round(rect.top / 4)].join(':');
  };
  const result = []; const seen = new Set();
  for (const node of Array.from(root.querySelectorAll(selector))) {
    if (!node || node.closest(editors) || !outgoing(node)) continue;
    const value = key(node);
    if (seen.has(value)) continue;
    seen.add(value);
    result.push({key: value});
  }
  return result;
}
"""

_WAIT_SCRIPT = r"""
({ baseline }) => {
  const root = document.querySelector('#sub-app') || document.body;
  if (!root) return false;
  const selector = '[data-message-id], [data-msg-id], [data-msgid], [data-client-message-id], [data-server-message-id], [data-e2e*="msg"], [class*="message"], [class*="Message"], [class*="bubble"], [class*="Bubble"], li[role="listitem"]';
  const editors = '[contenteditable="true"], textarea, [class*="chat-input-"], [class*="messageEditor"]';
  if (!Array.isArray(baseline)) return false;
  const before = new Set(baseline.map((entry) => entry && entry.key).filter(Boolean));
  const marker = (node) => {
    const values = [];
    for (let current = node, depth = 0; current && current !== root && depth < 8; current = current.parentElement, depth += 1) {
      values.push(current.className || '', current.getAttribute('data-testid') || '', current.getAttribute('data-direction') || '', current.getAttribute('data-owner') || '', current.getAttribute('data-is-self') || '', current.getAttribute('data-self') || '');
    }
    return values.join(' ').toLowerCase();
  };
  const outgoing = (node) => {
    const value = marker(node);
    if (/(^|[\s_-])(incoming|received|other|left)([\s_-]|$)/.test(value)) return false;
    if (/(^|[\s_-])(outgoing|sent|mine|self|right)([\s_-]|$)/.test(value) || /(is-self|from-me|message-right)/.test(value)) return true;
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    const editor = root.querySelector(editors); const reference = editor && editor.getBoundingClientRect().width ? editor.getBoundingClientRect() : root.getBoundingClientRect();
    return rect.left + rect.width * .5 >= reference.left + reference.width * .5;
  };
  const key = (node) => {
    for (let current = node, depth = 0; current && depth < 7; current = current.parentElement, depth += 1) {
      for (const name of ['data-message-id', 'data-msg-id', 'data-msgid', 'data-client-message-id', 'data-server-message-id']) {
        const value = String(current.getAttribute(name) || '').trim();
        if (value && !['0', 'null', 'undefined'].includes(value)) return name + ':' + value;
      }
    }
    const imgs = Array.from(node.querySelectorAll('img')).map((img) => img.src || img.alt || img.title || '').filter(Boolean).join('|');
    const text = String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim(); const rect = node.getBoundingClientRect();
    return [node.tagName, node.className || '', imgs, text, Math.round(rect.top / 4)].join(':');
  };
  for (const node of Array.from(root.querySelectorAll(selector))) {
    if (!node || node.closest(editors) || !outgoing(node)) continue;
    const current = key(node);
    if (!before.has(current)) return {source: 'dom', reason: 'new_outgoing_sticker', messageId: current};
  }
  return false;
}
"""


async def _first_visible(page, selectors):
    for selector in selectors:
        try:
            candidate = page.locator(selector).first
            if await candidate.count() and await candidate.is_visible():
                return candidate
        except Exception:
            continue
    raise NativeStickerPreparationError("未找到抖音表情按钮")


async def _wait_panel(page, name: str, timeout: int = 8_000):
    deadline = asyncio.get_running_loop().time() + timeout / 1000
    while asyncio.get_running_loop().time() < deadline:
        for selector in STICKER_PANEL_SELECTORS:
            try:
                panels = page.locator(selector)
                for index in range(await panels.count()):
                    panel = panels.nth(index)
                    if await panel.is_visible():
                        return panel
            except Exception:
                continue
        await page.wait_for_timeout(100)
    raise NativeStickerPreparationError(f"抖音表情面板未打开，无法选择“{name}”")


def _css_quote(value: str) -> str:
    return str(value).replace('\\', '\\\\').replace('"', '\\"')


async def _find_item(panel, name: str):
    escaped = _css_quote(name)
    selectors = (
        f'[aria-label="{escaped}"]', f'[title="{escaped}"]', f'[alt="{escaped}"]',
        f'[data-emoji-name="{escaped}"]', f'[data-sticker-name="{escaped}"]',
    )
    for selector in selectors:
        try:
            candidates = panel.locator(selector)
            for index in range(await candidates.count()):
                candidate = candidates.nth(index)
                if await candidate.is_visible():
                    return candidate
        except Exception:
            continue
    try:
        items = panel.locator('.emojiEmojiItememojiItem')
        for index in range(await items.count()):
            candidate = items.nth(index)
            if not await candidate.is_visible():
                continue
            description = candidate.locator('[class*="emojiItemDesc"], [class*="EmojiItemDesc"], [data-emoji-name]').first
            if await description.count() and (await description.inner_text()).strip() == name:
                return candidate
    except Exception:
        pass
    for role in ('img', 'button'):
        try:
            candidate = panel.get_by_role(role, name=name, exact=True)
            for index in range(await candidate.count()):
                item = candidate.nth(index)
                if await item.is_visible():
                    return item
        except Exception:
            continue
    try:
        candidate = panel.get_by_text(name, exact=True)
        for index in range(await candidate.count()):
            item = candidate.nth(index)
            if await item.is_visible():
                return item
    except Exception:
        pass
    return None


async def _editor_has_payload(page) -> bool:
    try:
        return bool(await page.evaluate("""(selector) => { const e = document.querySelector(selector); return Boolean(e && (String(e.innerHTML || '').trim() || String(e.innerText || e.textContent || '').trim() || e.querySelector('img,[data-emoji],[data-sticker]'))); }""", CHAT_EDITOR_SELECTOR))
    except Exception:
        return False


async def _confirm(page, baseline, timeout: int):
    try:
        handle = await page.wait_for_function(_WAIT_SCRIPT, arg={"baseline": baseline}, timeout=timeout)
        value = handle
        json_value = getattr(handle, 'json_value', None)
        if callable(json_value):
            value = json_value()
            if inspect.isawaitable(value):
                value = await value
        if isinstance(value, dict) and value.get('source') == 'dom':
            return value
    except Exception as error:
        raise NativeStickerSubmissionError("原生续火花已进入发送步骤，但页面未能确认是否送达") from error
    raise NativeStickerSubmissionError("原生续火花已进入发送步骤，但页面未返回有效确认")


async def send_douyin_sticker(page, sticker_name: str = DOUYIN_SPARK_STICKER, *, on_submitted=None, timeout: int = 45_000) -> NativeStickerSendResult:
    """Select the named native sticker and send it through the web composer."""
    name = str(sticker_name or '').strip()
    if not name:
        raise NativeStickerPreparationError("原生表情名称不能为空")
    try:
        baseline = await page.evaluate(_SNAPSHOT_SCRIPT)
    except Exception:
        # A missing baseline must never be treated as an empty chat. Otherwise
        # any pre-existing outgoing bubble could be mistaken for this send.
        baseline = None
    button = await _first_visible(page, STICKER_BUTTON_SELECTORS)
    try:
        await button.click(force=True)
    except Exception as error:
        raise NativeStickerPreparationError("无法打开抖音表情面板") from error
    panel = await _wait_panel(page, name)
    item = await _find_item(panel, name)
    if item is None:
        raise NativeStickerPreparationError(f"在抖音表情面板中找不到“{name}”")
    try:
        await item.click(force=True)
    except Exception as error:
        # Selecting an item is still a preparation step. In particular, a
        # detached/closed panel means the click was not dispatched; do not
        # report a submitted run or consume the worker's retry boundary.
        raise NativeStickerPreparationError("抖音表情面板已关闭，未能选择原生续火花") from error

    # Douyin's current composer stages the native item and sends it with Enter.
    # A few builds send immediately on item click; pressing Enter against the
    # now-empty composer is a no-op there. Either way the durable checkpoint is
    # recorded only after the item click succeeded and immediately before the
    # actual keyboard send action.
    if on_submitted is not None:
        try:
            result = on_submitted()
            if inspect.isawaitable(result):
                await result
        except Exception as error:
            raise NativeStickerPreparationError("发送前安全记录失败，本次未执行发送") from error
    try:
        await page.locator(CHAT_EDITOR_SELECTOR).first.press('Enter')
    except Exception as error:
        raise NativeStickerSubmissionError("原生续火花已进入发送步骤，但发送按键操作中断") from error
    try:
        await page.keyboard.press('Escape')
    except Exception:
        pass
    evidence = await _confirm(page, baseline, timeout)
    return NativeStickerSendResult(True, True, str(evidence.get('reason') or 'new_outgoing_sticker'), str(evidence.get('messageId') or '') or None)
