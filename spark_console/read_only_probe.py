"""Inspect a task's real account and conversation without composing a message."""
import asyncio,time
from core.web_chat import WEB_CHAT_URL,CHAT_EDITOR_SELECTOR,select_web_chat_target
from core.session_health import wait_for_session,SessionUnavailable
from core.account_profile import read_self_profile
from core.stickers import STICKER_BUTTON_SELECTORS
from spark_console.credentials import CredentialPayload
from spark_console.browser_environment import credential_options,identity
from spark_console.version import VERSION


async def inspect_task(raw,version,target,uid,kind,*,message_id=None,expected_user_id=None):
    from playwright.async_api import async_playwright
    result={'schema':1,'version':VERSION,'environment':identity(),'phases':[],'checks':{},'delivery':'unknown'}
    async def step(name,awaitable):
        start=time.monotonic()
        try:return await awaitable
        finally:result['phases'].append({'stage':name,'seconds':round(time.monotonic()-start,3)})
    payload=CredentialPayload.parse(bytes(raw),version)
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        try:
            context=await browser.new_context(**credential_options(payload))
            if payload.cookies_to_add():await context.add_cookies(payload.cookies_to_add())
            page=await context.new_page()
            await step('navigation',page.goto(WEB_CHAT_URL,wait_until='domcontentloaded',timeout=45000))
            evidence=await step('authentication',wait_for_session(page,context,timeout=45))
            if evidence.state!='valid':raise SessionUnavailable(evidence)
            result['checks']['account']=True
            profile=await step('identity',read_self_profile(context))
            if not profile.get('platform_user_id') or profile['platform_user_id']!=expected_user_id:
                result.update(error_code='account_identity_unavailable',summary='无法确认绑定身份，已停止自检');return result
            result['checks']['identity']=True
            await step('target',select_web_chat_target(page,target,timeout=30000,target_sec_uid=uid or None))
            await step('editor',page.wait_for_selector(CHAT_EDITOR_SELECTOR,timeout=15000))
            result['checks'].update(target=True,stable_target=bool(uid),editor=True)
            if kind=='douyin_sticker':
                visible=False
                for selector in STICKER_BUTTON_SELECTORS:
                    try:
                        if await page.locator(selector).first.is_visible():visible=True;break
                    except Exception:continue
                result['checks']['sticker_entry']=visible
            try:
                result['checks']['indexeddb_database_count']=await page.evaluate('async()=>typeof indexedDB.databases==="function"?(await indexedDB.databases()).length:null')
            except Exception:result['checks']['indexeddb_database_count']=None
            if message_id and uid:
                # No visible text/body is read or returned. Only compare the
                # exact persisted platform message identity in the selected chat.
                found=await page.evaluate('''id=>[...document.querySelectorAll('[data-message-id],[data-msg-id],[data-msgid],[data-server-message-id]')].some(n=>['data-message-id','data-msg-id','data-msgid','data-server-message-id'].some(k=>n.getAttribute(k)===id))''',message_id)
                if found:result['delivery']='message_id_found'
            result['summary']='账号、目标会话和输入入口检查完成，未输入或发送消息'
            if kind=='douyin_sticker' and not result['checks']['sticker_entry']:
                result.update(error_code='sticker_entry_unavailable',summary='已打开目标会话，暂未识别表情入口；未发送消息')
            return result
        except asyncio.CancelledError:raise
        except Exception as error:
            result.update(error_code=type(error).__name__[:48],summary='自检未完成；请结合账号状态和任务设置处理，未发送消息')
            return result
        finally:await browser.close()
