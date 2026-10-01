"""Request correlation and stable metadata for HTTP API failures."""
from __future__ import annotations

import json
import contextvars
import ipaddress
import re
import uuid
from collections.abc import Callable

from starlette.types import Message, Receive, Scope, Send


_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_ERROR_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_STATUS_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "request_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
}
_audit_context: contextvars.ContextVar[dict[str, str | None] | None] = contextvars.ContextVar(
    "spark_audit_request_context", default=None
)


def current_audit_context() -> dict[str, str | None]:
    """Return request metadata for audit writes in this request task."""
    values = _audit_context.get()
    return dict(values) if values is not None else {}


def _client_ip(scope: Scope) -> str | None:
    peer = scope.get("client")
    if not peer or not peer[0]:
        return None
    try:
        address = ipaddress.ip_address(str(peer[0]))
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address)


def _new_request_id() -> str:
    return uuid.uuid4().hex


def _request_id(scope: Scope) -> str:
    """Return a bounded, header-safe ID; discard untrusted values silently."""
    for name, value in scope.get("headers") or ():
        if name.lower() != b"x-request-id":
            continue
        try:
            candidate = value.decode("ascii")
        except UnicodeDecodeError:
            break
        if _REQUEST_ID.fullmatch(candidate):
            return candidate
        break
    return _new_request_id()


def _error_code(status: int, payload: dict) -> str:
    """Choose a stable machine-readable code without replacing old fields."""
    for key in ("error_code", "code", "error"):
        value = payload.get(key)
        if isinstance(value, str) and _ERROR_CODE.fullmatch(value):
            return value
    return _STATUS_ERROR_CODES.get(status, "server_error" if status >= 500 else f"http_{status}")


def _with_request_metadata(body: bytes, status: int, request_id: str) -> bytes:
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return body
    if not isinstance(payload, dict):
        return body
    payload.setdefault("request_id", request_id)
    payload.setdefault("error_code", _error_code(status, payload))
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class RequestCorrelationMiddleware:
    """Attach a request ID to every response and API JSON error.

    Existing response fields are preserved.  Only JSON responses with an HTTP
    error status receive the additive ``request_id`` and ``error_code`` fields.
    """

    def __init__(self, app: Callable):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope)
        audit_context = {"request_id": request_id, "client_ip": _client_ip(scope)}
        state = scope.get("state")
        if not isinstance(state, dict):
            state = {}
            scope["state"] = state
        state["request_id"] = request_id
        state["audit_context"] = audit_context
        context_token = _audit_context.set(audit_context)

        response_start: Message | None = None
        response_body: list[bytes] = []
        response_status = 0
        response_is_json_error = False

        async def send_with_metadata(message: Message) -> None:
            nonlocal response_start, response_status, response_is_json_error
            message_type = message.get("type")
            if message_type == "http.response.start":
                response_status = int(message.get("status", 0))
                headers = list(message.get("headers") or ())
                content_type = ""
                filtered: list[tuple[bytes, bytes]] = []
                for name, value in headers:
                    lower_name = name.lower()
                    if lower_name == b"x-request-id":
                        continue
                    if lower_name == b"content-type":
                        try:
                            content_type = value.decode("latin-1").lower()
                        except UnicodeDecodeError:
                            content_type = ""
                    filtered.append((name, value))
                filtered.append((b"x-request-id", request_id.encode("ascii")))
                response_start = {**message, "headers": filtered}
                response_is_json_error = (
                    response_status >= 400
                    and (
                        content_type.startswith("application/json")
                        or content_type.startswith("application/problem+json")
                        or content_type.endswith("+json")
                    )
                )
                if not response_is_json_error:
                    await send(response_start)
                return

            if message_type != "http.response.body" or response_start is None:
                await send(message)
                return

            if not response_is_json_error:
                await send(message)
                return

            response_body.append(message.get("body") or b"")
            if message.get("more_body", False):
                return

            body = _with_request_metadata(b"".join(response_body), response_status, request_id)
            headers = [
                (name, value)
                for name, value in response_start["headers"]
                if name.lower() != b"content-length"
            ]
            headers.append((b"content-length", str(len(body)).encode("ascii")))
            await send({**response_start, "headers": headers})
            await send({"type": "http.response.body", "body": body})

        try:
            await self.app(scope, receive, send_with_metadata)
        finally:
            _audit_context.reset(context_token)
