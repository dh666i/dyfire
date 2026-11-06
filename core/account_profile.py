"""Validated self-account response; never return session keys or contact details."""
import re

from core.avatars import avatar_from_user_info

ACCOUNT_INFO_URL = "https://www.douyin.com/passport/account/info/v2/?aid=6383"


def normalize_platform_user_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    if text in ("", "0") or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", text):
        return None
    return text


def authenticated_user_id(body):
    if not isinstance(body, dict) or body.get("message") != "success":
        return None
    data = body.get("data")
    if not isinstance(data, dict) or data.get("is_visitor_account") is True:
        return None
    # Live passport responses omit error_code on success. Older responses
    # include 0; an explicit failure code must never be ignored.
    for source in (body, data):
        if "error_code" in source:
            code = source["error_code"]
            if not ((type(code) is int and code == 0) or (type(code) is str and code == "0")):
                return None
    uid = normalize_platform_user_id(data.get("user_id"))
    uid_str = normalize_platform_user_id(data.get("user_id_str"))
    if "user_id" in data and data["user_id"] is not None and uid is None:
        return None
    if "user_id_str" in data and data["user_id_str"] is not None and uid_str is None:
        return None
    if uid is not None and uid_str is not None and uid != uid_str:
        return None
    return uid_str or uid


def profile_from_response(body):
    uid = authenticated_user_id(body)
    if uid is None:
        return {}
    data = body["data"]
    return {
        "platform_user_id": uid,
        "nickname": str(data.get("name") or data.get("nickname") or "").strip()[:256],
        "unique_id": str(data.get("unique_id") or "").strip()[:128],
        "avatar_url": avatar_from_user_info(data),
    }


async def read_self_profile(context):
    try:
        response = await context.request.get(ACCOUNT_INFO_URL, timeout=5000)
        if not 200 <= response.status < 300:
            return {}
        return profile_from_response(await response.json())
    except Exception:
        return {}


async def probe_saved_profile(storage_state):
    """Read the identity from original credentials before any login interaction."""
    from playwright.async_api import async_playwright
    from types import SimpleNamespace
    async with async_playwright() as playwright:
        request = await playwright.request.new_context(storage_state=storage_state)
        try:
            return await read_self_profile(SimpleNamespace(request=request))
        finally:
            await request.dispose()
