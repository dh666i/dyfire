from urllib.parse import urlsplit


AVATAR_DOMAINS = ("douyinpic.com", "douyincdn.com", "byteimg.com", "ibytedtos.com", "pstatp.com")


def safe_avatar_url(value) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    value = value.strip()
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        valid_host = any(host == domain or host.endswith("." + domain) for domain in AVATAR_DOMAINS)
        if (parsed.scheme != "https" or not valid_host or parsed.username or parsed.password
                or parsed.port not in (None, 443) or not parsed.path
                or any(ord(char) < 32 for char in value) or "\\" in value):
            return None
    except ValueError:
        return None
    return value


def avatar_from_user_info(item: dict) -> str | None:
    for field in ("avatar_thumb", "avatar_medium", "avatar_larger", "avatar_url", "avatar"):
        value = item.get(field)
        candidates = value.get("url_list", ()) if isinstance(value, dict) else (value,)
        if not isinstance(candidates, (list, tuple)):
            continue
        for candidate in candidates:
            url = safe_avatar_url(candidate)
            if url:
                return url
    return None
