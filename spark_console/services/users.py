from __future__ import annotations

import re
import secrets
import string
import unicodedata
from datetime import timedelta, timezone

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.orm import Session

from spark_console.models import DouyinAccount, SparkTask, TaskRun, User, WebSession, utc_now
from spark_console.security import PasswordService
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.audits import AuditService


MAX_FAILED_LOGINS = 5
LOGIN_LOCKOUT_DURATION = timedelta(minutes=15)
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
PASSWORD_POLICY_HINT = "密码至少 12 位，且需同时包含字母和数字；请勿使用常见、连续或重复模式"

# Keep this deliberately small and high-signal.  It catches the passwords
# that are routinely sprayed at internet-facing services without pretending
# that a short deny-list is a substitute for breach-password screening.
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password123",
        "password1234",
        "passw0rd",
        "qwerty",
        "qwerty123",
        "123456",
        "123456789",
        "1234567890",
        "123456789012",
        "123123",
        "123123123123",
        "111111",
        "111111111111",
        "000000000000",
        "abc123",
        "admin",
        "admin123",
        "admin123456",
        "letmein",
        "welcome",
        "welcome123",
        "changeme",
        "iloveyou",
        "1q2w3e4r5t",
    }
)


def _password_forms(password: str) -> tuple[str, str]:
    normalized = unicodedata.normalize("NFKC", password).casefold().strip()
    compact = re.sub(r"[^\w]+", "", normalized, flags=re.UNICODE)
    return normalized, compact


_COMMON_PASSWORD_FORMS = frozenset(
    _password_forms(value)[1] for value in _COMMON_PASSWORDS
)
_COMMON_PASSWORD_BASES = ("password", "qwerty", "admin", "welcome", "letmein", "changeme")


def _is_repeated_pattern(value: str) -> bool:
    if len(value) < 12:
        return False
    return any(
        len(value) % width == 0
        and value == value[:width] * (len(value) // width)
        for width in range(1, 5)
    )


def _contains_sequential_run(value: str) -> bool:
    for alphabet in ("0123456789", "abcdefghijklmnopqrstuvwxyz"):
        for width in range(6, len(alphabet) + 1):
            for start in range(len(alphabet) - width + 1):
                run = alphabet[start : start + width]
                if run in value or run[::-1] in value:
                    return True
    return False


def _aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def validate_registration_password(password: str, *, username: str | None = None) -> None:
    """Validate a website password without exposing account-specific details.

    The public error is intentionally the same for every rejected password.
    This keeps registration and recovery responses from becoming a source of
    account or policy probing while still allowing the UI to show the stable
    requirements in its own copy.
    """
    if (
        not isinstance(password, str)
        or len(password) < MIN_PASSWORD_LENGTH
        or len(password) > MAX_PASSWORD_LENGTH
    ):
        raise ValidationError("注册信息无效")
    if (
        not any(character.isalpha() for character in password)
        or not any(character.isdigit() for character in password)
    ):
        raise ValidationError("注册信息无效")
    normalized, compact = _password_forms(password)
    if normalized in _COMMON_PASSWORDS or compact in _COMMON_PASSWORD_FORMS:
        raise ValidationError("注册信息无效")
    # Reject decorated variants such as ``Password123!`` while avoiding a
    # broad substring rule that would reject ordinary passphrases. A common
    # base followed only by a short numeric suffix remains a sprayable secret.
    if any(
        compact.startswith(base)
        and compact[len(base) :].isdigit()
        and 0 < len(compact) - len(base) <= 8
        for base in _COMMON_PASSWORD_BASES
    ):
        raise ValidationError("注册信息无效")
    if (
        _is_repeated_pattern(compact)
        or _contains_sequential_run(compact)
        or len(set(compact)) == 1
    ):
        raise ValidationError("注册信息无效")
    if username:
        username_normalized, username_compact = _password_forms(username)
        if (
            normalized == username_normalized
            or compact == username_compact
            or (username_compact and username_compact in compact)
        ):
            raise ValidationError("注册信息无效")


def _temporary_password() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    # Include each required character class explicitly; random generation
    # should never produce a temporary password that violates our policy.
    characters = [
        secrets.choice(string.ascii_letters),
        secrets.choice(string.digits),
        *(secrets.choice(alphabet) for _ in range(16)),
    ]
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def _validate_explicit_password(password: str, username: str) -> None:
    try:
        validate_registration_password(password, username=username)
    except ValidationError as error:
        raise ValidationError(PASSWORD_POLICY_HINT) from error


class UserService:
    def __init__(self, session: Session, passwords: PasswordService, audit: AuditService):
        self.session = session
        self.passwords = passwords
        self.audit = audit

    @staticmethod
    def temporary_password() -> str:
        return _temporary_password()

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
        if password is not None:
            _validate_explicit_password(temporary, name)
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
            self.passwords.verify_unknown(password)
            return None
        now = utc_now()
        # A correct password can recover from a hostile source's lockout.
        # Source limits still apply, and incorrect attempts never extend an
        # already active target lock. Verify before branching to avoid timing
        # differences between locked and ordinary accounts.
        valid = self.passwords.verify(user.password_hash, password)
        if valid:
            changed = self.session.execute(update(User).where(
                User.id == user.id, User.password_hash == user.password_hash,
                User.status == "active",
            ).values(failed_login_count=0, locked_until=None)).rowcount
            if changed != 1:
                return None
            self.session.refresh(user)
            return user
        if user.locked_until is not None:
            if _aware(user.locked_until) > now:
                return None
            # An expired lock starts a fresh attempt window.
            user.failed_login_count = 0
            user.locked_until = None
            self.session.flush()
        if not valid:
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
        if user.role == "admin":
            raise ValidationError("管理员密码请通过账户安全或密码恢复流程修改")
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

    def set_role(self, actor_id: str, user_id: str, role: str) -> User:
        """Change a user's privilege role with guardrails for admin continuity."""
        if role not in {"user", "admin"}:
            raise ValidationError("不支持的用户角色")
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFound("user not found")
        if user.id == actor_id:
            raise ValidationError("不能修改当前管理员的角色")
        if user.role == role:
            return user
        if user.role == "admin" and role == "user":
            admin_count = int(
                self.session.scalar(
                    select(func.count(User.id)).where(User.role == "admin")
                )
                or 0
            )
            if admin_count <= 1:
                raise Conflict("至少需要保留一名管理员")
        # A role change must invalidate sessions in both directions. A user
        # promoted with an existing cookie must authenticate again before the
        # browser receives administrator privileges.
        self.session.query(WebSession).filter(WebSession.user_id == user.id).delete()
        previous = user.role
        user.role = role
        self.audit.write(
            actor_id,
            "user.role_changed",
            "user",
            user.id,
            f"from={previous};to={role}",
        )
        return user

    def delete(self, actor_id: str, user_id: str, confirmation: str) -> None:
        from spark_console.models import PaymentRecord
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFound("user not found")
        if user.id == actor_id:
            raise ValidationError("不能删除当前管理员账号")
        if confirmation != user.username:
            raise ValidationError("确认用户名不匹配")
        payment_record=self.session.scalar(select(PaymentRecord.id).where(PaymentRecord.user_id==user.id).limit(1))
        if payment_record is not None:
            raise Conflict("该用户已有收款台账记录，请先导出并处理账目后再删除")
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
