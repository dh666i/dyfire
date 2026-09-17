"""Evidence-based session checks. No verification submission or message action."""
import asyncio
import os
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit
from core.web_chat import _has_visible_exact_text, _locator_has_visible_match, CONVERSATION_ITEM_SELECTOR, LOGIN_REQUIRED_TEXTS

ACCOUNT_INFO_URL = 'https://www.douyin.com/passport/account/info/v2/?aid=6383'
VERIFY_TEXTS = ('请完成安全验证', '短信验证', '请输入验证码', '请输入登录密码', '验证登录密码', '人脸验证', '请在抖音App完成验证', '操作频繁，请稍后再试')


@dataclass(frozen=True)
class SessionEvidence:
    state: str
    reason: str
    refreshed_storage_state: dict | None = field(default=None, repr=False, compare=False)


class SessionUnavailable(RuntimeError):
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__(evidence.reason)


async def read_login_api(context):
    """Keep diagnostics bounded and never persist response bodies or credentials."""
    status = None
    try:
        response = await context.request.get(ACCOUNT_INFO_URL, timeout=5000)
        status = response.status
        if not isinstance(status, int):
            status = None
        prefix = f'HTTP {status}' if status is not None else 'HTTP 状态不可读'
        if status == 401:
            return 'logged_out', prefix + '，未授权'
        if status is not None and not 200 <= status < 300:
            return 'unknown', prefix + ('，请求受限' if status in (403, 429) else '，请求未成功')
        try:
            body = await response.json()
        except Exception:
            return 'unknown', prefix + '，返回非 JSON 或 JSON 无法解析'
        if not isinstance(body, dict) or not isinstance(body.get('data'), dict):
            return 'unknown', prefix + '，返回结构异常'
        data = body['data']
        code = data.get('error_code')
        code_detail = f'，业务码 {code}' if type(code) is int and abs(code) < 10**12 else '，业务码缺失或格式异常'
        if body.get('message') == 'success' and code == 0 and data.get('user_id'):
            return 'valid', prefix + code_detail
        message = str(data.get('description') or data.get('error_description') or body.get('description') or body.get('message') or '').lower()
        if any(s in message for s in ('未登录', 'not login', 'not logged in', 'login required')):
            return 'logged_out', prefix + code_detail + '，明确未登录'
        return 'unknown', prefix + code_detail + '，未取得有效登录信息'
    except Exception as error:
        category = '请求超时' if isinstance(error, TimeoutError) or type(error).__name__ == 'TimeoutError' else '网络或请求异常'
        return 'unknown', f'{category}（{type(error).__name__[:40]}）'


async def inspect_session(page, context, *, api_result=None):
    parsed = urlsplit(str(getattr(page, 'url', '') or ''))
    path = parsed.path if parsed.path in ('/', '/chat', '/login', '/passport/web/login/') else '其他页面'
    chat_page = parsed.hostname == 'www.douyin.com' and path.rstrip('/') == '/chat'
    surfaces = getattr(page, 'frames', None)
    surfaces = surfaces if isinstance(surfaces, list) and surfaces else [page]
    for surface in surfaces:
        for text in VERIFY_TEXTS:
            if await _has_visible_exact_text(surface, text):
                return SessionEvidence('verification', f'抖音要求验证：{text}；页面 {path}')
        for selector in ('input[placeholder*="登录密码"]:visible', 'input[placeholder*="验证码"]:visible', '[id*="captcha_container"]:visible'):
            try:
                if await _locator_has_visible_match(surface.locator(selector)):
                    return SessionEvidence('verification', f'检测到安全验证控件；页面 {path}')
            except Exception:
                pass
    login = any([await _has_visible_exact_text(page, text) for text in LOGIN_REQUIRED_TEXTS])
    try:
        chat = await _locator_has_visible_match(page.locator(CONVERSATION_ITEM_SELECTOR))
    except Exception:
        chat = False
    api, diagnostic = api_result if api_result is not None else await read_login_api(context)
    if (api == 'valid' and login) or (api == 'logged_out' and chat) or (chat and login):
        return SessionEvidence('temporary', f'接口与页面登录信号不一致；{diagnostic}；页面 {path}')
    if api == 'valid' and chat_page and not login:
        return SessionEvidence('valid', f'登录状态接口确认有效，聊天页可访问；{diagnostic}')
    if chat and not login and chat_page:
        return SessionEvidence('valid', f'聊天页显示已登录会话列表；{diagnostic}')
    if api == 'logged_out' or (login and api != 'valid'):
        return SessionEvidence('logged_out', f'检测到未登录信号；{diagnostic}；登录界面 {login}；页面 {path}')
    return SessionEvidence('temporary', f'未检测到已登录会话列表；{diagnostic}；页面 {path}')


SESSION_WAIT_TIMEOUT = int(os.environ.get('SPARK_SESSION_WAIT_SECONDS', '45'))
SESSION_API_RECHECK_INTERVAL = max(3, int(os.environ.get('SPARK_SESSION_API_RECHECK_SECONDS', '5')))


async def wait_for_session(page, context, *, timeout=SESSION_WAIT_TIMEOUT, interval=.5):
    """Wait for hydrated UI; inspect without clicking and probe API at most twice."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    cached = None
    last_api_check = -SESSION_API_RECHECK_INTERVAL
    evidence = SessionEvidence('temporary', '页面检查未完成')
    async def inspect_cached():
        nonlocal cached, last_api_check
        # Check verification controls before issuing an API request.
        preliminary = await inspect_session(page, context, api_result=('unknown', '尚未检查接口'))
        if preliminary.state == 'verification':
            return preliminary
        elapsed = timeout - max(0, deadline - loop.time())
        if cached is None or elapsed - last_api_check >= SESSION_API_RECHECK_INTERVAL:
            cached = await read_login_api(context)
            last_api_check = elapsed
        return await inspect_session(page, context, api_result=cached)
    while loop.time() < deadline:
        try:
            evidence = await asyncio.wait_for(inspect_cached(), max(.001, deadline-loop.time()))
        except asyncio.TimeoutError:
            break
        if evidence.state in ('valid', 'verification'):
            return evidence
        await asyncio.sleep(min(interval, max(0, deadline-loop.time())))
    # Recheck API once after the UI grace period; do not poll the endpoint.
    try:
        evidence = await asyncio.wait_for(inspect_session(page, context), 6)
    except asyncio.TimeoutError:
        evidence = SessionEvidence('temporary', '最终登录检查超时，无法确认失效')
    if evidence.state in ('valid', 'verification'):
        return evidence
    return replace(evidence, reason=f'已等待页面就绪 {timeout:g} 秒；{evidence.reason}')


async def open_chat_session(page, context):
    """Identical bounded navigation for send and health check; no message actions."""
    from core.web_chat import WEB_CHAT_URL
    loop = asyncio.get_running_loop()
    started = loop.time()
    failures = 0
    def request_failed(_request):
        nonlocal failures
        failures += 1
    page.on("requestfailed", request_failed)
    navigations = 0
    document_status = None
    evidence = SessionEvidence("temporary", "聊天页尚未就绪")
    try:
        for index in range(2):
            navigations += 1
            try:
                response = (await page.goto(WEB_CHAT_URL, wait_until="domcontentloaded", timeout=20000)
                    if index == 0 else await page.reload(wait_until="domcontentloaded", timeout=20000))
                document_status = getattr(response, "status", None)
                evidence = await wait_for_session(page, context)
            except Exception as error:
                # Inspect even after navigation timeout: verification UI may have appeared.
                try:
                    evidence = await asyncio.wait_for(inspect_session(page, context,
                        api_result=("unknown", "导航未完整完成")), 6)
                except asyncio.TimeoutError:
                    evidence = SessionEvidence("temporary", "导航后页面检测超时")
                if evidence.state == "temporary":
                    evidence = replace(evidence, reason=f"聊天页加载未完成（{type(error).__name__[:40]}）")
            if evidence.state != "temporary":
                break
        status = document_status if type(document_status) is int else "未知"
        return replace(evidence, reason=(evidence.reason[:150] +
            f"；页面加载 {navigations} 次，耗时 {loop.time()-started:.1f} 秒，主文档 {status}，网络失败 {failures}"))
    finally:
        page.remove_listener("requestfailed", request_failed)


async def require_session(page, context, *, navigate=False):
    evidence = await open_chat_session(page, context) if navigate else await wait_for_session(page, context)
    if evidence.state != 'valid':
        raise SessionUnavailable(evidence)
    return evidence


async def check_saved_session(raw, version):
    from playwright.async_api import async_playwright
    from spark_console.credentials import CredentialPayload
    from core.web_chat import WEB_CHAT_URL
    payload = CredentialPayload.parse(bytes(raw), version)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(**payload.context_options())
            if payload.cookies_to_add():
                await context.add_cookies(payload.cookies_to_add())
            page = await context.new_page()
            evidence = await open_chat_session(page, context)
            if evidence.state == 'valid':
                try:
                    evidence = replace(evidence, refreshed_storage_state=await context.storage_state())
                except Exception:
                    pass
            return evidence
        finally:
            await browser.close()
