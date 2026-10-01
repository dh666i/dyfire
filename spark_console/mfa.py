"""Small dependency-free TOTP implementation for administrator enrollment.

This module implements the parts of RFC 6238 needed for enrollment and
confirmation. It deliberately does not decide when TOTP is required; that
policy belongs to a separately reviewed authentication rollout.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote


TIME_STEP_SECONDS = 30
CODE_DIGITS = 6
SECRET_BYTES = 20


def generate_secret() -> bytes:
    return secrets.token_bytes(SECRET_BYTES)


def encode_secret(secret: bytes) -> str:
    if not isinstance(secret, bytes) or len(secret) < 16:
        raise ValueError("TOTP secret is too short")
    return base64.b32encode(secret).decode("ascii").rstrip("=")


def _counter_for(now: float | int | None = None) -> int:
    return int((time.time() if now is None else now) // TIME_STEP_SECONDS)


def _code(secret: bytes, counter: int) -> str:
    message = counter.to_bytes(8, "big", signed=False)
    digest = hmac.new(secret, message, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return f"{value % (10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def normalize_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    code = "".join(value.split())
    if len(code) != CODE_DIGITS or not code.isdecimal():
        return None
    return code


def verify_code(
    secret: bytes,
    supplied: object,
    *,
    now: float | int | None = None,
    last_used_counter: int | None = None,
    allowed_drift: int = 1,
) -> int | None:
    """Return the matching time-step, rejecting malformed and replayed codes."""
    code = normalize_code(supplied)
    if code is None or not isinstance(secret, bytes) or not secret:
        return None
    if allowed_drift < 0 or allowed_drift > 2:
        raise ValueError("allowed drift must be between 0 and 2")
    counter = _counter_for(now)
    for candidate in range(counter - allowed_drift, counter + allowed_drift + 1):
        if candidate < 0:
            continue
        if last_used_counter is not None and candidate <= last_used_counter:
            continue
        if hmac.compare_digest(_code(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret: bytes, *, username: str, issuer: str = "Dyfire") -> str:
    """Build a standards-compatible URI for authenticator applications."""
    encoded_secret = encode_secret(secret)
    label = f"{issuer}:{username}"
    return (
        "otpauth://totp/"
        + quote(label, safe="")
        + "?secret="
        + encoded_secret
        + "&issuer="
        + quote(issuer, safe="")
        + f"&algorithm=SHA1&digits={CODE_DIGITS}&period={TIME_STEP_SECONDS}"
    )
