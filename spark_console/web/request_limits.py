"""ASGI request size guard used as the application's last request boundary."""
from __future__ import annotations

from collections.abc import Callable

from starlette.types import Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    """Reject HTTP request bodies larger than ``max_bytes``.

    The middleware checks ``Content-Length`` before an endpoint is entered and
    also counts streamed chunks.  The latter matters when a reverse proxy
    strips the header or uses chunked transfer encoding.  Once the limit is
    exceeded, downstream response messages are suppressed and a single 413
    response is emitted after the application unwinds.
    """

    def __init__(self, app: Callable, max_bytes: int):
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self.app = app
        self.max_bytes = max_bytes

    @staticmethod
    async def _too_large(send: Send) -> None:
        body = b'{"detail":"request body too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or ())
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except (TypeError, ValueError):
                declared = None
            if declared is not None and declared > self.max_bytes:
                await self._too_large(send)
                return

        total = 0
        too_large = False

        async def limited_receive() -> Message:
            nonlocal total, too_large
            if too_large:
                return {"type": "http.disconnect"}
            message = await receive()
            if message.get("type") != "http.request":
                return message
            chunk = message.get("body") or b""
            total += len(chunk)
            if total > self.max_bytes:
                too_large = True
                return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not too_large:
                await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            # Request parsers commonly raise ClientDisconnect when the wrapper
            # returns http.disconnect.  The limit response must still win;
            # unrelated application errors continue through normal handling.
            if not too_large:
                raise
        if too_large:
            await self._too_large(send)
