from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from spark_console.models import User, WebSession
from spark_console.security import SessionService
from spark_console.web.membership import membership_summary


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class WebAuth:
    def __init__(self, sessions: SessionService):
        self.sessions = sessions

    def current(
        self, request: Request, db: Session, allow_change: bool = False
    ) -> tuple[User, WebSession]:
        raw = request.cookies.get("spark_session")
        if not raw or len(raw) > 256 or not raw.isascii():
            raise HTTPException(401)
        record = db.scalar(
            select(WebSession).where(WebSession.token_hash == self.sessions.token_hash(raw))
        )
        if record is None or _aware(record.expires_at) <= datetime.now(timezone.utc):
            raise HTTPException(401)
        user = db.get(User, record.user_id)
        if user is None or user.status != "active":
            raise HTTPException(401)
        if user.must_change_password and not allow_change:
            raise HTTPException(409, "password-change-required")
        return user, record

    def csrf(self, record: WebSession, supplied: str) -> None:
        # JSON clients can send null, numbers, or objects.  Reject those as a
        # normal CSRF failure instead of letting compare_digest raise TypeError
        # and turning an invalid request into a 500 response.
        if not isinstance(supplied, str) or not supplied or not secrets.compare_digest(record.csrf_token, supplied):
            raise HTTPException(403, "CSRF validation failed")

    def user_context(
        self, request: Request, db: Session
    ) -> tuple[User, WebSession, dict]:
        user, record = self.current(request, db)
        return user, record, {
            "user": user,
            "csrf_token": record.csrf_token,
            "is_admin": user.role == "admin",
            "membership": membership_summary(db, user),
        }

    def admin_context(
        self, request: Request, db: Session
    ) -> tuple[User, WebSession, dict]:
        user, record = self.current(request, db)
        if user.role != "admin":
            raise HTTPException(404)
        return user, record, {
            "user": user,
            "csrf_token": record.csrf_token,
            "is_admin": True,
            "membership": membership_summary(db, user),
        }
