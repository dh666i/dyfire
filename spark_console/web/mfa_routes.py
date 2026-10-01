"""Optional administrator TOTP enrollment endpoints.

These endpoints prepare a credential without changing the existing password
login or session behavior. Enforcement is intentionally a separate rollout.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from spark_console.crypto import SecretCipher
from spark_console.db import session_scope
from spark_console.mfa import (
    consume_recovery_code,
    encode_secret,
    generate_recovery_codes,
    generate_secret,
    provisioning_uri,
    verify_code,
)
from spark_console.models import AdminTotpCredential, AdminTotpRecoveryCode, utc_now
from spark_console.rate_limit import FailedAttemptLimiter
from spark_console.security import PasswordService
from spark_console.services.audits import AuditService
from spark_console.web.auth import WebAuth
from spark_console.web.registration_routes import registration_client_key


def build_mfa_router(
    engine,
    auth: WebAuth,
    passwords: PasswordService,
    limiter: FailedAttemptLimiter,
    cipher: SecretCipher,
) -> APIRouter:
    router = APIRouter()

    async def _json(request: Request) -> dict:
        try:
            value = await request.json()
        except (TypeError, ValueError):
            raise HTTPException(400, "请求格式无效")
        if not isinstance(value, dict):
            raise HTTPException(400, "请求格式无效")
        return value

    def _admin(request: Request, db):
        user, record = auth.current(request, db)
        if user.role != "admin":
            raise HTTPException(404)
        return user, record

    def _password_ok(user, password: object) -> bool:
        return isinstance(password, str) and passwords.verify(
            user.password_hash, password
        )

    def _replace_recovery_codes(db, user_id: str) -> list[str]:
        """Replace the complete set in the same transaction as enrollment."""
        db.query(AdminTotpRecoveryCode).filter(
            AdminTotpRecoveryCode.user_id == user_id
        ).delete(synchronize_session=False)
        plaintext: list[str] = []
        for display, salt, digest in generate_recovery_codes():
            db.add(
                AdminTotpRecoveryCode(
                    user_id=user_id,
                    code_digest=digest,
                    salt=salt,
                )
            )
            plaintext.append(display)
        return plaintext

    @router.get("/api/auth/mfa/totp")
    def totp_status(request: Request):
        with session_scope(engine) as db:
            user, _record = _admin(request, db)
            credential = db.get(AdminTotpCredential, user.id)
            remaining_count = len(
                db.scalars(
                    select(AdminTotpRecoveryCode.id).where(
                        AdminTotpRecoveryCode.user_id == user.id,
                        AdminTotpRecoveryCode.used_at.is_(None),
                    )
                ).all()
            )
            return {
                "configured": credential is not None,
                "enabled": bool(credential and credential.enabled),
                "pending": bool(credential and not credential.enabled),
                "confirmed_at": (
                    credential.confirmed_at.isoformat()
                    if credential and credential.confirmed_at
                    else None
                ),
                "recovery_codes_remaining": remaining_count,
            }

    @router.post("/api/auth/mfa/totp/setup")
    async def totp_setup(request: Request):
        values = await _json(request)
        with session_scope(engine) as db:
            user, record = _admin(request, db)
            auth.csrf(record, values.get("csrf_token", ""))
            key = f"totp-password:{user.id}:{registration_client_key(request)}"
            if not limiter.allow(key):
                raise HTTPException(429, "验证尝试过多，请稍后再试")
            if not _password_ok(user, values.get("password")):
                limiter.record_failure(key)
                AuditService(db).write(user.id, "auth.totp_password_failed", "user", user.id)
                return JSONResponse(
                    {"ok": False, "detail": "管理员密码验证失败"}, status_code=403
                )
            limiter.clear(key)
            existing = db.scalar(
                select(AdminTotpCredential)
                .where(AdminTotpCredential.user_id == user.id)
                .with_for_update()
            )
            if existing is not None:
                raise HTTPException(409, "管理员验证器已配置，请先停用后重新设置")
            secret = generate_secret()
            sealed = cipher.encrypt(secret)
            db.add(
                AdminTotpCredential(
                    user_id=user.id,
                    ciphertext=sealed.ciphertext,
                    nonce=sealed.nonce,
                    enabled=False,
                )
            )
            AuditService(db).write(user.id, "auth.totp_setup_started", "user", user.id)
            # The plaintext secret is deliberately returned only at enrollment;
            # it is never persisted or included in a status response.
            return {
                "ok": True,
                "secret": encode_secret(secret),
                "otpauth_uri": provisioning_uri(secret, username=user.username),
                "requires_confirmation": True,
            }

    @router.post("/api/auth/mfa/totp/confirm")
    async def totp_confirm(request: Request):
        values = await _json(request)
        with session_scope(engine) as db:
            user, record = _admin(request, db)
            auth.csrf(record, values.get("csrf_token", ""))
            credential = db.scalar(
                select(AdminTotpCredential)
                .where(AdminTotpCredential.user_id == user.id)
                .with_for_update()
            )
            if credential is None or credential.enabled:
                raise HTTPException(409, "没有待确认的管理员验证器")
            key = f"totp:{user.id}:{registration_client_key(request)}"
            if not limiter.allow(key):
                raise HTTPException(429, "验证尝试过多，请稍后再试")
            try:
                secret = cipher.decrypt(credential.ciphertext, credential.nonce)
            except Exception as error:
                raise HTTPException(500, "管理员验证器密钥无法读取") from error
            counter = verify_code(
                secret,
                values.get("code"),
                last_used_counter=credential.last_used_counter,
            )
            if counter is None:
                limiter.record_failure(key)
                AuditService(db).write(user.id, "auth.totp_confirm_failed", "user", user.id)
                # Return a client error while keeping the audit event commitable.
                return JSONResponse(
                    {"ok": False, "detail": "验证码无效或已使用"}, status_code=403
                )
            limiter.clear(key)
            credential.enabled = True
            credential.confirmed_at = utc_now()
            credential.last_used_counter = counter
            recovery_codes = _replace_recovery_codes(db, user.id)
            AuditService(db).write(user.id, "auth.totp_enabled", "user", user.id)
            return {
                "ok": True,
                "enabled": True,
                "recovery_codes": recovery_codes,
                "recovery_codes_remaining": len(recovery_codes),
            }

    @router.post("/api/auth/mfa/totp/recovery-codes/regenerate")
    async def recovery_codes_regenerate(request: Request):
        """Rotate the one-time recovery set after two-factor confirmation."""
        values = await _json(request)
        with session_scope(engine) as db:
            user, record = _admin(request, db)
            auth.csrf(record, values.get("csrf_token", ""))
            credential = db.scalar(
                select(AdminTotpCredential)
                .where(
                    AdminTotpCredential.user_id == user.id,
                    AdminTotpCredential.enabled.is_(True),
                )
                .with_for_update()
            )
            if credential is None:
                raise HTTPException(409, "请先启用管理员验证器")
            key = f"totp-recovery-regenerate:{user.id}:{registration_client_key(request)}"
            if not limiter.allow(key):
                raise HTTPException(429, "验证尝试过多，请稍后再试")
            if not _password_ok(user, values.get("password")):
                limiter.record_failure(key)
                AuditService(db).write(user.id, "auth.totp_recovery_regenerate_failed", "user", user.id)
                return JSONResponse(
                    {"ok": False, "detail": "管理员密码验证失败"}, status_code=403
                )
            try:
                secret = cipher.decrypt(credential.ciphertext, credential.nonce)
            except Exception as error:
                raise HTTPException(500, "管理员验证器密钥无法读取") from error
            counter = verify_code(
                secret,
                values.get("code"),
                last_used_counter=credential.last_used_counter,
            )
            if counter is None:
                limiter.record_failure(key)
                AuditService(db).write(user.id, "auth.totp_recovery_regenerate_failed", "user", user.id)
                return JSONResponse(
                    {"ok": False, "detail": "需要有效的管理员验证码"}, status_code=403
                )
            limiter.clear(key)
            credential.last_used_counter = counter
            recovery_codes = _replace_recovery_codes(db, user.id)
            AuditService(db).write(user.id, "auth.totp_recovery_codes_regenerated", "user", user.id)
            return {
                "ok": True,
                "recovery_codes": recovery_codes,
                "recovery_codes_remaining": len(recovery_codes),
            }

    @router.post("/api/auth/mfa/totp/recovery-codes/consume")
    async def recovery_code_consume(request: Request):
        """Consume a recovery code for a future MFA challenge flow.

        This endpoint is deliberately separate from login: enabling recovery
        storage must not silently alter existing sessions or authentication.
        """
        values = await _json(request)
        with session_scope(engine) as db:
            user, record = _admin(request, db)
            auth.csrf(record, values.get("csrf_token", ""))
            credential = db.get(AdminTotpCredential, user.id)
            if credential is None or not credential.enabled:
                raise HTTPException(409, "请先启用管理员验证器")
            key = f"totp-recovery-consume:{user.id}:{registration_client_key(request)}"
            if not limiter.allow(key):
                raise HTTPException(429, "验证尝试过多，请稍后再试")
            if not consume_recovery_code(db, user.id, values.get("code")):
                limiter.record_failure(key)
                AuditService(db).write(user.id, "auth.totp_recovery_code_failed", "user", user.id)
                return JSONResponse(
                    {"ok": False, "detail": "恢复码无效或已使用"}, status_code=403
                )
            limiter.clear(key)
            AuditService(db).write(user.id, "auth.totp_recovery_code_used", "user", user.id)
            remaining = len(
                db.scalars(
                    select(AdminTotpRecoveryCode.id).where(
                        AdminTotpRecoveryCode.user_id == user.id,
                        AdminTotpRecoveryCode.used_at.is_(None),
                    )
                ).all()
            )
            return {"ok": True, "recovery_codes_remaining": remaining}

    @router.post("/api/auth/mfa/totp/disable")
    async def totp_disable(request: Request):
        values = await _json(request)
        with session_scope(engine) as db:
            user, record = _admin(request, db)
            auth.csrf(record, values.get("csrf_token", ""))
            key = f"totp-password:{user.id}:{registration_client_key(request)}"
            if not limiter.allow(key):
                raise HTTPException(429, "验证尝试过多，请稍后再试")
            if not _password_ok(user, values.get("password")):
                limiter.record_failure(key)
                AuditService(db).write(user.id, "auth.totp_password_failed", "user", user.id)
                return JSONResponse(
                    {"ok": False, "detail": "管理员密码验证失败"}, status_code=403
                )
            limiter.clear(key)
            credential = db.scalar(
                select(AdminTotpCredential)
                .where(AdminTotpCredential.user_id == user.id)
                .with_for_update()
            )
            if credential is None:
                return {"ok": True, "enabled": False}
            try:
                secret = cipher.decrypt(credential.ciphertext, credential.nonce)
            except Exception as error:
                raise HTTPException(500, "管理员验证器密钥无法读取") from error
            if credential.enabled:
                totp_key = f"totp:{user.id}:{registration_client_key(request)}"
                if not limiter.allow(totp_key):
                    raise HTTPException(429, "验证尝试过多，请稍后再试")
                counter = verify_code(secret, values.get("code"), last_used_counter=credential.last_used_counter)
                if counter is None:
                    limiter.record_failure(totp_key)
                    AuditService(db).write(user.id, "auth.totp_disable_failed", "user", user.id)
                    return JSONResponse(
                        {"ok": False, "detail": "需要有效的管理员验证码"}, status_code=403
                    )
                limiter.clear(totp_key)
                credential.last_used_counter = counter
            db.delete(credential)
            db.query(AdminTotpRecoveryCode).filter(
                AdminTotpRecoveryCode.user_id == user.id
            ).delete(synchronize_session=False)
            AuditService(db).write(user.id, "auth.totp_disabled", "user", user.id)
            return {"ok": True, "enabled": False}

    return router
