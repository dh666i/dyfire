"""Read profile and conversation directory. Never enters or submits a message."""
import asyncio
from dataclasses import asdict

from core.avatars import avatar_from_user_info
from core.web_chat import UserInfoCollector, WEB_CHAT_URL, ensure_web_chat_authenticated
from spark_console.credentials import CredentialPayload
from core.session_health import require_session

ACCOUNT_INFO_URL = "https://www.douyin.com/passport/account/info/v2/?aid=6383"


async def read_self_profile(context):
    try:
        response = await context.request.get(ACCOUNT_INFO_URL, timeout=5000)
        body = await response.json()
        data = body.get("data", {})
        if body.get("message") != "success" or data.get("error_code") != 0 or not data.get("user_id"):
            return {}
        return {"nickname": str(data.get("name") or data.get("nickname") or "").strip()[:256],
                "unique_id": str(data.get("unique_id") or "").strip()[:128],
                "avatar_url": avatar_from_user_info(data)}
    except Exception:
        return {}


# Only conversation rows and user-result rows with an explicit identity are read.
# Never click a search result, a send button, or the chat editor.
ROWS_SCRIPT = r"""() => {
  const nodes = [...document.querySelectorAll('.conversationConversationItemwrapper, [data-sec-uid], [data-e2e="search-user-item"]')];
  // Search cards in the web messenger expose a profile link and a 发消息 action.
  for (const link of document.querySelectorAll('a[href*="/user/"]')) {
    let card = link;
    for (let depth=0; card && depth<5; depth++,card=card.parentElement) {
      if (card.innerText?.includes('发消息') && card.querySelectorAll('a[href*="/user/"]').length <= 1) {
        nodes.push(card); break;
      }
    }
  }
  return nodes.filter(n => n.getClientRects().length).map(n => {
    const title = n.querySelector('.conversationConversationItemtitle, [class*="nickname"], [class*="Nickname"]');
    const link = n.matches('a[href*="/user/"]') ? n : n.querySelector('a[href*="/user/"]');
    const match = link && link.getAttribute('href').match(/\/user\/([^/?#]+)/);
    const name = (title && title.textContent || n.getAttribute('data-nickname') || link?.innerText || '').trim();
    return {name, sec_uid: n.getAttribute('data-sec-uid') || (match && match[1]) || '',
      avatar_url: n.querySelector('img')?.src || ''};
  }).filter(x => x.name);
}"""
SCROLL_SCRIPT = r"""() => {
 const row = document.querySelector('.conversationConversationItemwrapper');
 if (!row) return false;
 for (let p=row.parentElement; p && p!==document.body; p=p.parentElement) {
   if (p.scrollHeight>p.clientHeight+4 && /auto|scroll/.test(getComputedStyle(p).overflowY)) {
     const before=p.scrollTop; p.scrollTop += Math.max(120,p.clientHeight*.8);
     return p.scrollTop>before;
   }
 }
 return false;
}"""


async def collect_directory(page, limit=20, query=""):
    if query:
        field = page.locator('input[placeholder*="搜索"]:visible').first
        if not await field.count():
            raise ValueError("当前抖音页面未提供好友搜索入口，请使用已同步好友或稍后重试")
        await field.fill(query)
        await asyncio.sleep(1.5)
    else:
        await page.wait_for_selector('.conversationConversationItemwrapper', timeout=15000)
    found = {}
    stalled = 0
    for _ in range(16):
        rows = await page.evaluate(ROWS_SCRIPT)
        before = len(found)
        for row in rows:
            key = row.get("sec_uid") or row["name"]
            found.setdefault(key, row)
        if len(found) >= limit:
            break
        stalled = stalled + 1 if len(found) == before else 0
        if stalled >= 3 or query:
            break
        moved = await page.evaluate(SCROLL_SCRIPT)
        await asyncio.sleep(.65)
        if not moved and stalled >= 1:
            break
    return list(found.values())[:limit]


async def read_directory(payload_bytes, version, limit, query):
    from playwright.async_api import async_playwright
    payload = CredentialPayload.parse(bytes(payload_bytes), version)
    collector = UserInfoCollector()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(**payload.context_options())
            if payload.cookies_to_add():
                await context.add_cookies(payload.cookies_to_add())
            page = await context.new_page()
            page.on("response", collector.capture)
            await page.goto(WEB_CHAT_URL, wait_until="domcontentloaded", timeout=45000)
            await require_session(page, context)
            profile = await read_self_profile(context)
            try:
                rows = await collect_directory(page, limit, query)
            except Exception:
                await require_session(page, context)
                raise
            await require_session(page, context)
            identities = await collector.drain()
            items = []
            for row in rows:
                candidates = [i for i in identities if (row.get("sec_uid") and i.sec_uid == row["sec_uid"])
                              or (not row.get("sec_uid") and row["name"] in i.aliases)]
                identity = candidates[0] if len(candidates) == 1 else None
                value = asdict(identity) if identity else dict(row)
                value["name"] = (identity.remark_name or identity.nickname) if identity else row["name"]
                if not value.get('avatar_url'):
                    value['avatar_url'] = row.get('avatar_url')
                if query and query.casefold() not in " ".join(str(v or "") for v in (value.get("name"), value.get("nickname"), value.get("remark_name"), value.get("unique_id"))).casefold():
                    continue
                items.append(value)
            return {"items": items, "profile": profile, "has_more": len(rows) >= limit and limit < 100}
        finally:
            await browser.close()
