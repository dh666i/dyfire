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
RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_BYTES = 8
RECOVERY_CODE_DIGITS = RECOVERY_CODE_BYTES * 2
RECOVERY_KDF_ITERATIONS = 120_000


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


def normalize_recovery_code(value: object) -> str | None:
    """Normalize a printable recovery code without accepting extra data."""
    if not isinstance(value, str):
        return None
    normalized = "".join(value.split()).replace("-", "").upper()
    if len(normalized) != RECOVERY_CODE_DIGITS:
        return None
    if any(character not in "0123456789ABCDEF" for character in normalized):
        return None
    return normalized


def _format_recovery_code(normalized: str) -> str:
    return "-".join(
        normalized[index : index + 4]
        for index in range(0, RECOVERY_CODE_DIGITS, 4)
    )


def _recovery_digest(normalized: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        normalized.encode("ascii"),
        salt,
        RECOVERY_KDF_ITERATIONS,
    ).hex()


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[tuple[str, bytes, str]]:
    """Return ``(display, salt, digest)`` tuples for one recovery set."""
    if count <= 0 or count > 20:
        raise ValueError("recovery code count must be between 1 and 20")
    normalized_codes: set[str] = set()
    result: list[tuple[str, bytes, str]] = []
    while len(result) < count:
        normalized = secrets.token_hex(RECOVERY_CODE_BYTES).upper()
        if normalized in normalized_codes:
            continue
        normalized_codes.add(normalized)
        salt = secrets.token_bytes(16)
        result.append((_format_recovery_code(normalized), salt, _recovery_digest(normalized, salt)))
    return result


def recovery_code_matches(value: object, digest: str, salt: bytes) -> bool:
    normalized = normalize_recovery_code(value)
    if normalized is None or not isinstance(digest, str) or not isinstance(salt, bytes):
        return False
    return hmac.compare_digest(_recovery_digest(normalized, salt), digest)


def consume_recovery_code(db, user_id: str, value: object, *, now=None) -> bool:
    """Atomically consume one unused recovery code for ``user_id``.

    Callers must invoke this inside the existing request transaction.  The
    row lock plus the used-at update ensures a concurrent retry cannot consume
    the same code twice.
    """
    from sqlalchemy import select
    from spark_console.models import AdminTotpRecoveryCode, utc_now

    normalized = normalize_recovery_code(value)
    if normalized is None:
        return False
    rows = db.scalars(
        select(AdminTotpRecoveryCode)
        .where(
            AdminTotpRecoveryCode.user_id == user_id,
            AdminTotpRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    ).all()
    for row in rows:
        if hmac.compare_digest(_recovery_digest(normalized, row.salt), row.code_digest):
            row.used_at = now or utc_now()
            return True
    return False
