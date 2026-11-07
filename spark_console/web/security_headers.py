"""Headers for every response, including the React shell and bounded errors."""
import base64
import hashlib
import logging
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlencode

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse, RedirectResponse

logger = logging.getLogger(__name__)
HASHED_ASSET = re.compile(r"-[A-Za-z0-9_-]{8,}\.(?:js|css|woff2?)$")


def same_origin(origin, scheme, host):
    try:
        supplied = urlsplit(origin)
        expected = urlsplit(scheme + '://' + host)
        return (supplied.scheme in ('http', 'https') and
                supplied.scheme == expected.scheme and
                supplied.hostname == expected.hostname and
                supplied.username is None and supplied.password is None and
                supplied.path in ('', '/') and not supplied.query and not supplied.fragment and
                (supplied.port or (443 if supplied.scheme == 'https' else 80)) ==
                (expected.port or (443 if expected.scheme == 'https' else 80)))
    except (ValueError, UnicodeError):
        return False


def inline_script_hashes(path: Path):
    if not path.is_file():
        return []
    html = path.read_text(encoding="utf-8")
    return ["'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"
            for script in re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.S | re.I)
            if script.strip()]


class SecurityHeadersMiddleware:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        root = Path(__file__).resolve().parents[2] / "frontend"
        index = root / "dist/index.html"
        hashes = inline_script_hashes(index if index.exists() else root / "index.html")
        self.csp = (
            "default-src 'self'; base-uri 'self'; object-src 'none'; "
            "frame-ancestors 'none'; form-action 'self'; "
            "img-src 'self' data: blob: https:; font-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' " + " ".join(hashes) + "; connect-src 'self'"
        )

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        started = False
        fallback_id = uuid.uuid4().hex

        async def secured_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                values = {
                    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                    "Referrer-Policy": "strict-origin-when-cross-origin",
                    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                    "Content-Security-Policy": self.csp,
                    "Cross-Origin-Opener-Policy": "same-origin",
                    "Cross-Origin-Resource-Policy": "same-origin",
                    "X-Permitted-Cross-Domain-Policies": "none", "X-Request-ID": fallback_id,
                }
                for key, value in values.items():
                    headers.setdefault(key, value)
                path = scope.get("path", "")
                if path.startswith("/api/"):
                    headers.setdefault("Cache-Control", "no-store")
                elif path.startswith("/assets/") and HASHED_ASSET.search(path) and message["status"] in (200, 304):
                    headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
                elif path.startswith(("/assets/", "/static/")):
                    headers.setdefault("Cache-Control", "no-cache")
                if scope.get("scheme") == "https" and self.settings.hsts_max_age_seconds > 0:
                    hsts = f"max-age={self.settings.hsts_max_age_seconds}"
                    if self.settings.hsts_include_subdomains:
                        hsts += "; includeSubDomains"
                    headers.setdefault("Strict-Transport-Security", hsts)
            await send(message)

        try:
            if scope.get('method') == 'POST' and scope.get('path') in ('/login','/register','/forgot-password','/forgot-password/reset'):
                request_headers = dict(scope.get('headers',[]))
                origin = request_headers.get(b'origin')
                if origin:
                    host = request_headers.get(b'host',b'').decode('latin-1')
                    scheme = scope.get('scheme', 'http')
                    if self.settings.public_origin:
                        canonical = urlsplit(self.settings.public_origin)
                        scheme, host = canonical.scheme, canonical.netloc
                    if not same_origin(origin.decode('latin-1'), scheme, host):
                        detail = '页面来源未通过校验，请从本站重新打开后提交。'
                        if b'text/html' in request_headers.get(b'accept', b''):
                            path = scope['path']
                            query = {'error': detail}
                            if path == '/forgot-password/reset':
                                path = '/forgot-password'; query['sent'] = '1'
                            response = RedirectResponse(path+'?'+urlencode(query), status_code=303)
                        else:
                            response = JSONResponse({'detail':detail}, status_code=403)
                        await response(scope,receive,secured_send)
                        return
            await self.app(scope, receive, secured_send)
        except Exception as error:
            if started:
                raise
            logger.error("request failed type=%s request_id=%s", type(error).__name__, fallback_id)
            response = JSONResponse({"detail": "服务暂时无法处理请求，请稍后重试", "request_id": fallback_id}, status_code=500)
            await response(scope, receive, secured_send)
