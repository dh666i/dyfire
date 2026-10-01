"""Session step-up authentication for sensitive administrator actions."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from spark_console.db import session_scope
from spark_console.models import utc_now
from spark_console.rate_limit import FailedAttemptLimiter
from spark_console.security import PasswordService
from spark_console.services.audits import AuditService
from spark_console.web.auth import STEP_UP_LIFETIME, WebAuth
from spark_console.web.registration_routes import registration_client_key


def build_elevation_router(
    engine,
    auth: WebAuth,
    passwords: PasswordService,
    limiter: FailedAttemptLimiter,
):
    router = APIRouter()

    @router.post("/api/auth/elevate")
    async def elevate(request: Request):
        try:
            values = await request.json()
        except (TypeError, ValueError):
            raise HTTPException(400, "请求格式无效")
        if not isinstance(values, dict):
            raise HTTPException(400, "请求格式无效")

        with session_scope(engine) as db:
            user, record = auth.current(request, db)
            auth.csrf(record, values.get("csrf_token", ""))
            if user.role != "admin":
                # Do not reveal whether a non-admin account reached an admin
                # step-up endpoint; the account remains authenticated.
                raise HTTPException(403, "管理员权限不足")

            limiter_key = f"{user.id}:{registration_client_key(request)}"
            if not limiter.allow(limiter_key):
                raise HTTPException(429, "验证尝试过多，请稍后再试")
            password = values.get("password")
            if not isinstance(password, str) or not passwords.verify(
                user.password_hash, password
            ):
                limiter.record_failure(limiter_key)
                AuditService(db).write(
                    user.id,
                    "auth.step_up_failed",
                    "web_session",
                    record.id,
                    outcome="failure",
                    error_code="password_invalid",
                )
                # Return normally so the failed-attempt audit is committed;
                # raising here would roll back the transaction.
                return JSONResponse(
                    {"detail": "管理员密码验证失败"}, status_code=403
                )

            limiter.clear(limiter_key)
            now = utc_now()
            record.elevated_until = now + STEP_UP_LIFETIME
            AuditService(db).write(
                user.id,
                "auth.step_up_succeeded",
                "web_session",
                record.id,
            )
            return {
                "ok": True,
                "elevated": True,
                "elevated_until": record.elevated_until.isoformat(),
            }

    return router
