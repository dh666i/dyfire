from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from spark_console.db import session_scope
from spark_console.crypto import CookieCipher
from spark_console.rate_limit import FailedAttemptLimiter
from spark_console.security import PasswordService
from spark_console.services import Conflict, ValidationError
from spark_console.services.audits import AuditService
from spark_console.services.invites import InviteService
from spark_console.services.users import (
    PASSWORD_POLICY_HINT,
    UserService,
    validate_registration_password,
)
from spark_console.web.auth import WebAuth
from spark_console.models import InviteCode, SparkTask, TaskRun, User


PUBLIC_ERROR = "注册信息无效"


class _RegistrationFormError(Exception):
    """A safe, actionable registration error that may be shown to the user."""

    def __init__(self, message: str, *, preserve_form: bool = True):
        super().__init__(message)
        self.preserve_form = preserve_form


def _public_error(detail: str) -> str:
    if not detail or detail == PUBLIC_ERROR:
        return PUBLIC_ERROR
    return f"{PUBLIC_ERROR}：{detail}"


@dataclass(frozen=True)
class AdminInvite:
    invite: InviteCode
    status: str
    used_by_username: str | None
    can_revoke: bool
    code: str | None


def registration_client_key(request: Request) -> str:
    # X-Real-IP is user-controlled unless the ASGI proxy trust chain is
    # explicitly configured. Use only the actual peer address for limits.
    return request.client.host if request.client is not None else "unknown"


def build_registration_router(
    engine,
    passwords: PasswordService,
    limiter: FailedAttemptLimiter,
    auth: WebAuth,
    page,
    cipher: CookieCipher,
) -> APIRouter:
    router = APIRouter()

    @router.get("/register")
    def register_page(request: Request):
        return page(request, "register.html", title="注册", nav=False)

    @router.post("/register")
    def register(
        request: Request,
        username: str = Form(default=""),
        password: str = Form(default=""),
        password_confirmation: str = Form(default=""),
        invite_code: str = Form(default=""),
    ):
        key = registration_client_key(request)
        form = {
            "username": username.strip().lower(),
            "invite_code": invite_code.strip(),
        }
        if not limiter.allow(key):
            return page(
                request,
                "register.html",
                429,
                title="注册",
                nav=False,
                error=_public_error("注册尝试过多，请稍后再试"),
                form=form,
            )
        try:
            with session_scope(engine) as db:
                try:
                    validate_registration_password(password, username=username)
                except ValidationError as error:
                    raise _RegistrationFormError(PASSWORD_POLICY_HINT) from error
                if password != password_confirmation:
                    raise _RegistrationFormError("两次输入的密码不一致")
                try:
                    user, _ = UserService(db, passwords, AuditService(db)).create(
                        username, password, "user"
                    )
                except ValidationError as error:
                    raise _RegistrationFormError(str(error)) from error
                user.must_change_password = False
                if invite_code.strip():
                    try:
                        InviteService(db, AuditService(db), cipher).consume(
                            invite_code, user.id
                        )
                    except ValidationError as error:
                        raise _RegistrationFormError(
                            PUBLIC_ERROR,
                            preserve_form=False,
                        ) from error
        except _RegistrationFormError as error:
            limiter.record_failure(key)
            return page(
                request,
                "register.html",
                400,
                title="注册",
                nav=False,
                error=_public_error(str(error)),
                form=form if error.preserve_form else None,
            )
        except (Conflict, IntegrityError):
            limiter.record_failure(key)
            return page(
                request,
                "register.html",
                400,
                title="注册",
                nav=False,
                error=_public_error("该登录账号已被注册，请更换后重试"),
                form=form,
            )
        except ValueError:
            limiter.record_failure(key)
            return page(
                request,
                "register.html",
                400,
                title="注册",
                nav=False,
                error=PUBLIC_ERROR,
                form=form,
            )
        limiter.clear(key)
        return RedirectResponse("/login?registered=1", 303)

    @router.post("/admin/invites")
    def create_invite(request: Request, csrf_token: str = Form(default="")):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            InviteService(db, AuditService(db), cipher).create(admin.id)
        return RedirectResponse("/admin/codes", 303)

    @router.post("/admin/invites/{invite_id}/revoke")
    def revoke_invite(
        request: Request, invite_id: str, csrf_token: str = Form(default="")
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                InviteService(db, AuditService(db), cipher).revoke(
                    admin.id, invite_id
                )
            except ValidationError as error:
                raise HTTPException(400, str(error)) from error
        return RedirectResponse("/admin/codes", 303)

    @router.post("/admin/invites/{invite_id}/delete")
    def delete_invite(
        request: Request, invite_id: str, csrf_token: str = Form(default="")
    ):
        with session_scope(engine) as db:
            admin, record, _context = auth.admin_context(request, db)
            auth.csrf(record, csrf_token)
            try:
                InviteService(db, AuditService(db), cipher).delete(
                    admin.id, invite_id
                )
            except ValidationError as error:
                raise HTTPException(404, "邀请码不存在") from error
        return RedirectResponse("/admin/codes", 303)

    return router


def _admin_page_data(db):
    users = db.scalars(select(User).order_by(User.created_at)).all()
    tasks = db.execute(
        select(SparkTask, User)
        .join(User, SparkTask.owner_user_id == User.id)
        .order_by(SparkTask.send_time)
    ).all()
    runs = db.execute(
        select(TaskRun, SparkTask, User)
        .join(SparkTask, TaskRun.task_id == SparkTask.id)
        .join(User, SparkTask.owner_user_id == User.id)
        .order_by(TaskRun.scheduled_for.desc())
        .limit(20)
    ).all()
    return users, tasks, runs


def admin_invite_items(
    db, cipher: CookieCipher | None = None
) -> list[AdminInvite]:
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(InviteCode, User.username)
        .outerjoin(User, InviteCode.used_by_user_id == User.id)
        .order_by(InviteCode.created_at.desc())
    ).all()
    items = []
    service = InviteService(db, AuditService(db), cipher)
    for invite, username in rows:
        expires_at = invite.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if invite.used_at is not None:
            status = "已使用"
        elif invite.revoked_at is not None:
            status = "已撤销"
        elif expires_at <= now:
            status = "已过期"
        else:
            status = "有效"
        items.append(
            AdminInvite(
                invite=invite,
                status=status,
                used_by_username=username,
                can_revoke=status == "有效",
                code=service.reveal(invite.id),
            )
        )
    return items
