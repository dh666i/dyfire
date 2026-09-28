from __future__ import annotations

import secrets
import string
from datetime import timedelta, timezone

from sqlalchemy import case, or_, select, update
from sqlalchemy.orm import Session

from spark_console.models import DouyinAccount, SparkTask, TaskRun, User, WebSession, utc_now
from spark_console.security import PasswordService
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.audits import AuditService


MAX_FAILED_LOGINS = 5
LOGIN_LOCKOUT_DURATION = timedelta(minutes=15)


def _aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def validate_registration_password(password: str) -> None:
    if (
        not any(character.isalpha() for character in password)
        or not any(character.isdigit() for character in password)
    ):
        raise ValidationError("注册信息无效")


class UserService:
    def __init__(self, session: Session, passwords: PasswordService, audit: AuditService):
        self.session = session
        self.passwords = passwords
        self.audit = audit

    @staticmethod
    def temporary_password() -> str:
        alphabet = string.ascii_letters + string.digits + "!@#$%"
        return "".join(secrets.choice(alphabet) for _ in range(18))

    def create(self, username: str, password: str | None = None, role: str = "user") -> tuple[User, str]:
        name = username.strip().lower()
        if not (3 <= len(name) <= 32) or not all(
            character.isalnum() or character in "@._-" for character in name
        ):
            raise ValidationError(
                "账号须为 3–32 位字母、数字、@、点、下划线或短横线"
            )
        if role not in {"user", "admin"}:
            raise ValidationError("invalid role")
        if self.session.scalar(select(User).where(User.username == name)):
            raise Conflict("用户名已存在")
        temporary = self.temporary_password() if password is None else password
        user = User(
            username=name,
            password_hash=self.passwords.hash(temporary),
            role=role,
            must_change_password=True,
        )
        self.session.add(user)
        self.session.flush()
        self.audit.write(None, "user.created", "user", user.id)
        return user, temporary

    def authenticate(self, username: str, password: str) -> User | None:
        user = self.session.scalar(select(User).where(User.username == username.strip().lower()))
        if user is None or user.status != "active":
            return None
        now = utc_now()
        if user.locked_until is not None:
            if _aware(user.locked_until) > now:
                return None
            # An expired lock starts a fresh attempt window.
            user.failed_login_count = 0
            user.locked_until = None
            self.session.flush()
        if not self.passwords.verify(user.password_hash, password):
            # Increment in SQL so concurrent failures cannot lose updates or
            # race past the lock threshold on databases with row-level locks.
            self.session.execute(
                update(User)
                .where(
                    User.id == user.id,
                    User.status == "active",
                    or_(User.locked_until.is_(None), User.locked_until <= now),
                )
                .values(
                    failed_login_count=User.failed_login_count + 1,
                    locked_until=case(
                        (User.failed_login_count + 1 >= MAX_FAILED_LOGINS,
                         now + LOGIN_LOCKOUT_DURATION),
                        else_=User.locked_until,
                    ),
                )
                .execution_options(synchronize_session=False)
            )
            self.session.refresh(user)
            return None
        user.failed_login_count = 0
        user.locked_until = None
        return user

    def reset_password(self, actor_id: str, user_id: str) -> str:
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFound("user not found")
        temporary = self.temporary_password()
        user.password_hash = self.passwords.hash(temporary)
        user.must_change_password = True
        self.session.query(WebSession).filter(WebSession.user_id == user.id).delete()
        self.audit.write(actor_id, "user.password_reset", "user", user.id)
        return temporary

    def set_disabled(self, actor_id: str, user_id: str, disabled: bool) -> User:
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFound("user not found")
        user.status = "disabled" if disabled else "active"
        if disabled:
            self.session.query(WebSession).filter(WebSession.user_id == user.id).delete()
        self.audit.write(actor_id, "user.disabled" if disabled else "user.enabled", "user", user.id)
        return user

    def delete(self, actor_id: str, user_id: str, confirmation: str) -> None:
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFound("user not found")
        if user.id == actor_id:
            raise ValidationError("不能删除当前管理员账号")
        if confirmation != user.username:
            raise ValidationError("确认用户名不匹配")
        running = self.session.scalar(
            select(TaskRun.id)
            .join(SparkTask, SparkTask.id == TaskRun.task_id)
            .where(
                SparkTask.owner_user_id == user.id,
                TaskRun.status == "running",
            )
            .limit(1)
        )
        if running is not None:
            raise Conflict("该用户有任务正在执行，请完成后再删除")
        self.session.query(SparkTask).filter(SparkTask.owner_user_id == user.id).delete()
        self.session.query(DouyinAccount).filter(DouyinAccount.owner_user_id == user.id).delete()
        self.session.query(WebSession).filter(WebSession.user_id == user.id).delete()
        self.session.delete(user)
        self.audit.write(actor_id, "user.deleted", "user", user_id)
