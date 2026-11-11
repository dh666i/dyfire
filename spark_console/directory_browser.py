"""Read profile and conversation directory. Never enters or submits a message."""
from spark_console.browser_environment import credential_options
import asyncio
from dataclasses import asdict

from core.account_profile import read_self_profile, ACCOUNT_INFO_URL
from core.web_chat import UserInfoCollector, WEB_CHAT_URL, ensure_web_chat_authenticated
from spark_console.credentials import CredentialPayload
from core.session_health import require_session, wait_for_session, inspect_session, SessionUnavailable
from core.streak_protocol import StreakCollector
from core.streak_observation import STREAK_ROWS_SCRIPT, parse_streak_rows

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
    const remark = n.querySelector('[class*="remark"], [class*="Remark"]');
    return {name, remark_name:(remark?.textContent || '').trim(), sec_uid: n.getAttribute('data-sec-uid') || (match && match[1]) || '',
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


async def _capture_streak_window(page, samples, seen):
    try:
        rows = await page.evaluate(STREAK_ROWS_SCRIPT)
    except Exception:
        return
    if not isinstance(rows, list):
        return
    for row in rows[:300]:
        if not isinstance(row, dict):
            continue
        identity = row.get("identity") if isinstance(row.get("identity"), dict) else {}
        signals = row.get("signals") if isinstance(row.get("signals"), list) else []
        key = (
            tuple(sorted(str(value) for value in identity.get("sec_uids", []) if value)),
            tuple(sorted(str(value) for value in identity.get("uids", []) if value)),
            tuple(sorted(str(value) for value in identity.get("conversation_ids", []) if value)),
            tuple(sorted((str(signal.get("kind", "")), str(signal.get("text", "")))
                         for signal in signals if isinstance(signal, dict))),
        )
        if key not in seen and len(samples) < 1000:
            seen.add(key)
            samples.append(row)


async def collect_directory(page, limit=20, query="", streak_samples=None):
    streak_seen = set()
    if query:
        field = page.locator('input[placeholder*="搜索"]:visible').first
        if not await field.count():
            raise ValueError("当前抖音页面未提供好友搜索入口，请使用已同步好友或稍后重试")
        await field.fill(query)
        # Results may arrive after the search debounce and a network round trip.
        # Do not report an unrendered response as a completed empty search.
        for _ in range(20):
            await asyncio.sleep(.5)
            if streak_samples is not None:
                await _capture_streak_window(page, streak_samples, streak_seen)
            rows = await page.evaluate(ROWS_SCRIPT)
            matched = [row for row in rows if query.casefold() in ' '.join(str(row.get(k) or '') for k in ('name','remark_name','nickname')).casefold()]
            if matched:
                return matched[:limit]
        if rows:
            # Some result rows display only the nickname; the captured identity
            # response supplies its remark later during the merge below.
            return rows[:limit]
        raise ValueError('当前抖音页面未读取到匹配结果，无法确认好友不存在。请尝试昵称或抖音号，或同步更多最近会话。')
    else:
        await page.wait_for_selector('.conversationConversationItemwrapper', timeout=45000)
    found = {}
    stalled = 0
    for _ in range(16):
        if streak_samples is not None:
            await _capture_streak_window(page, streak_samples, streak_seen)
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


def _conversation_identity_map(samples, trusted_self_uid, uid_to_sec_uid):
    own_uid = str(trusted_self_uid or "").strip()
    if not own_uid.isascii() or not own_uid.isdecimal() or own_uid == "0":
        return {}
    result = {}
    for sample in samples:
        identity = sample.get("identity") if isinstance(sample, dict) else None
        conversations = identity.get("conversation_ids", []) if isinstance(identity, dict) else []
        if not isinstance(conversations, list):
            continue
        for conversation_id in conversations:
            parts = str(conversation_id).split(":")
            if len(parts) != 4 or parts[:2] != ["0", "1"]:
                continue
            first, second = parts[2:]
            if not first.isascii() or not first.isdecimal() or not second.isascii() or not second.isdecimal():
                continue
            if (first == own_uid) == (second == own_uid):
                continue
            peer_uid = second if first == own_uid else first
            sec_uid = uid_to_sec_uid.get(peer_uid)
            if isinstance(sec_uid, str) and sec_uid:
                previous = result.get(conversation_id)
                result[conversation_id] = sec_uid if previous in (None, sec_uid) else ""
    return {key: value for key, value in result.items() if value}


def _merge_streak_observations(dom_observations, im_observations):
    dom = {item["sec_uid"]: item for item in dom_observations}
    im = {item["sec_uid"]: item for item in im_observations}
    merged = []
    fields = ("days", "state", "recover_current", "recover_total")
    for sec_uid in sorted(dom.keys() | im.keys()):
        dom_item, im_item = dom.get(sec_uid), im.get(sec_uid)
        if dom_item and im_item:
            conflict = any(
                field != "state"
                and dom_item.get(field) is not None
                and im_item.get(field) is not None
                and dom_item[field] != im_item[field]
                for field in fields
            ) or (
                dom_item.get("state") not in (None, "unknown")
                and im_item.get("state") not in (None, "unknown")
                and dom_item["state"] != im_item["state"]
            )
            if conflict:
                continue
            score = lambda item: sum(
                item.get(field) not in (None, "unknown")
                for field in fields
            )
            merged.append(dom_item if score(dom_item) >= score(im_item) else im_item)
        else:
            merged.append(dom_item or im_item)
    return merged


async def read_directory(payload_bytes, version, limit, query):
    from playwright.async_api import async_playwright
    payload = CredentialPayload.parse(bytes(payload_bytes), version)
    collector = UserInfoCollector()
    streak_collector = StreakCollector()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(**credential_options(payload))
            if payload.cookies_to_add():
                await context.add_cookies(payload.cookies_to_add())
            page = await context.new_page()
            page.on("response", collector.capture)
            page.on("request", streak_collector.capture_request)
            page.on("response", streak_collector.capture)
            await page.goto(WEB_CHAT_URL, wait_until="domcontentloaded", timeout=45000)
            # A cold chat page may still be hydrating. Retry once only when
            # evidence is inconclusive, never when login/verification is explicit.
            evidence = await wait_for_session(page, context, timeout=45)
            if evidence.state == 'temporary':
                await page.reload(wait_until='domcontentloaded', timeout=20000)
                evidence = await wait_for_session(page, context, timeout=45)
            if evidence.state != 'valid':
                raise SessionUnavailable(evidence)
            profile = await read_self_profile(context)
            streak_samples = []
            try:
                rows = await collect_directory(page, limit, query, streak_samples)
            except Exception:
                failed = await inspect_session(page, context)
                if failed.state in ('verification','logged_out'):
                    raise SessionUnavailable(failed)
                raise
            # Search replaces the conversation list. Its absence is not logout.
            final = await inspect_session(page, context)
            if final.state in ('verification', 'logged_out'):
                raise SessionUnavailable(final)
            identities = await collector.drain()
            im_streaks = await streak_collector.finalize(profile.get("platform_user_id"))
            conversation_to_sec_uid = _conversation_identity_map(
                streak_samples, profile.get("platform_user_id"), streak_collector.uid_to_sec_uid,
            )
            dom_streaks = parse_streak_rows(
                streak_samples,
                uid_to_sec_uid=streak_collector.uid_to_sec_uid,
                conversation_to_sec_uid=conversation_to_sec_uid,
            )
            streaks = _merge_streak_observations(dom_streaks, im_streaks)
            items = []
            for row in rows:
                candidates = [i for i in identities if (row.get("sec_uid") and i.sec_uid == row["sec_uid"])
                              or (not row.get("sec_uid") and row["name"] in i.aliases)]
                identity = candidates[0] if len(candidates) == 1 else None
                value = asdict(identity) if identity else dict(row)
                # The visible search label can be the user's remark even when
                # the identity endpoint contains only the original nickname.
                value["name"] = row["name"]
                if row.get('remark_name'):
                    value['remark_name'] = row['remark_name']
                if not value.get('avatar_url'):
                    value['avatar_url'] = row.get('avatar_url')
                if query and query.casefold() not in " ".join(str(v or "") for v in (row.get('name'), row.get('remark_name'), value.get("name"), value.get("nickname"), value.get("remark_name"), value.get("unique_id"))).casefold():
                    continue
                items.append(value)
            if query and not items:
                structure = await page.evaluate(r'''(query) => {
          const visible=n=>!!n.getClientRects().length;
          const matches=[...document.querySelectorAll('body *')].filter(n=>visible(n)&&n.children.length===0&&n.textContent.trim()===query).slice(0,4);
          return {inputs:[...document.querySelectorAll('input')].filter(visible).map(n=>({placeholder:n.placeholder,searchValue:n.value===query,cls:n.className})),matches:matches.map(n=>{const chain=[];for(let i=0;n&&i<4;i++,n=n.parentElement)chain.push({tag:n.tagName,cls:n.className,images:n.querySelectorAll('img').length,links:n.querySelectorAll('a').length});return chain;})};
                }''', query)
                import json
                raise ValueError('当前抖音页面搜索结果未识别；结构诊断：'+json.dumps(structure,ensure_ascii=False))
            return {"items": items, "profile": profile, "streaks": streaks,
                    "has_more": len(rows) >= limit and limit < 100}
        finally:
            await browser.close()
