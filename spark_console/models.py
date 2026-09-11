from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from spark_console.task_targets import (
    TARGET_KEY_LENGTH,
    TARGET_NAME_LENGTH,
    task_target_key_default,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def uuid_string() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class MailSettings(Base):
    __tablename__ = 'mail_settings'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)


class NotificationPolicy(Base):
    __tablename__='notification_policies'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id',ondelete='CASCADE'),primary_key=True)
    events: Mapped[str] = mapped_column(Text,default='account_alert,account_recovered,expiry')
    quiet_start: Mapped[int] = mapped_column(Integer,default=23)
    quiet_end: Mapped[int] = mapped_column(Integer,default=8)
    quiet_enabled: Mapped[bool] = mapped_column(Boolean,default=False)
    interval_minutes: Mapped[int] = mapped_column(Integer,default=60)


class PasswordReset(Base):
    __tablename__='password_resets'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id',ondelete='CASCADE'),primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utc_now)
    attempts: Mapped[int] = mapped_column(Integer,default=0)


class MailPreference(Base):
    __tablename__ = 'mail_preferences'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), default='')
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    code_hash: Mapped[str | None] = mapped_column(String(64))
    code_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class MailAccountState(Base):
    __tablename__ = 'mail_account_states'
    account_id: Mapped[str] = mapped_column(ForeignKey('douyin_accounts.id', ondelete='CASCADE'), primary_key=True)
    state: Mapped[str] = mapped_column(String(24))
    revision: Mapped[int] = mapped_column(Integer, default=1)


class MailDelivery(Base):
    __tablename__ = 'mail_deliveries'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    event_key: Mapped[str] = mapped_column(String(512), unique=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    recipient: Mapped[str] = mapped_column(String(254))
    event: Mapped[str] = mapped_column(String(24))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default='queued', index=True)
    error: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class DouyinAccount(Base):
    __tablename__ = "douyin_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_cookies: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    cookie_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    cookie_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    validation_state: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AccountHealth(Base):
    __tablename__ = "account_health"
    account_id: Mapped[str] = mapped_column(ForeignKey("douyin_accounts.id", ondelete="CASCADE"), primary_key=True)
    reason: Mapped[str | None] = mapped_column(String(240))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    negative_checks: Mapped[int] = mapped_column(Integer, default=0)


class AccountOperation(Base):
    __tablename__ = "account_operations"
    account_id: Mapped[str] = mapped_column(ForeignKey("douyin_accounts.id", ondelete="CASCADE"), primary_key=True)
    token: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DouyinConversation(Base):
    __tablename__ = "douyin_conversations"

    account_id: Mapped[str] = mapped_column(
        ForeignKey("douyin_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    display_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class DouyinAccountProfile(Base):
    __tablename__ = "douyin_account_profiles"
    account_id: Mapped[str] = mapped_column(ForeignKey("douyin_accounts.id", ondelete="CASCADE"), primary_key=True)
    nickname: Mapped[str | None] = mapped_column(String(256))
    avatar_url: Mapped[str | None] = mapped_column(String(2048))
    unique_id: Mapped[str | None] = mapped_column(String(128))
    custom_remark: Mapped[bool] = mapped_column(Boolean, default=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContactDirectoryJob(Base):
    __tablename__ = "contact_directory_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    account_id: Mapped[str] = mapped_column(ForeignKey("douyin_accounts.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(String(100), default="")
    limit: Mapped[int] = mapped_column(Integer, default=20)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    result_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DouyinContactIdentity(Base):
    __tablename__ = "douyin_contact_identities"

    account_id: Mapped[str] = mapped_column(
        ForeignKey("douyin_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    sec_uid: Mapped[str] = mapped_column(String(256), primary_key=True)
    short_id: Mapped[str | None] = mapped_column(String(64))
    unique_id: Mapped[str | None] = mapped_column(String(128))
    nickname: Mapped[str | None] = mapped_column(String(256))
    remark_name: Mapped[str | None] = mapped_column(String(256))
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class DouyinContactAvatar(Base):
    __tablename__ = "douyin_contact_avatars"

    account_id: Mapped[str] = mapped_column(
        ForeignKey("douyin_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    sec_uid: Mapped[str] = mapped_column(String(256), primary_key=True)
    avatar_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ScanStatus(StrEnum):
    QUEUED = "queued"
    LOADING_QR = "loading_qr"
    AWAITING_SCAN = "awaiting_scan"
    CONFIRMING = "confirming"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class InviteCode(Base):
    __tablename__ = "invite_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class InviteCodeSecret(Base):
    __tablename__ = "invite_code_secrets"

    invite_id: Mapped[str] = mapped_column(
        ForeignKey("invite_codes.id", ondelete="CASCADE"), primary_key=True
    )
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"
    __table_args__ = (
        CheckConstraint("valid_until >= valid_from", name="ck_subscription_valid_period"),
        CheckConstraint("max_accounts > 0", name="ck_subscription_max_accounts"),
        CheckConstraint("max_tasks > 0", name="ck_subscription_max_tasks"),
        CheckConstraint("version > 0", name="ck_subscription_version"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_accounts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RedemptionCode(Base):
    __tablename__ = "redemption_codes"
    __table_args__ = (
        CheckConstraint("duration_days > 0", name="ck_redemption_duration_days"),
        CheckConstraint("max_accounts > 0", name="ck_redemption_max_accounts"),
        CheckConstraint("max_tasks > 0", name="ck_redemption_max_tasks"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    max_accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    max_tasks: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    redeemed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SubscriptionGrant(Base):
    __tablename__ = "subscription_grants"
    __table_args__ = (
        CheckConstraint(
            "duration_days IS NULL OR duration_days > 0",
            name="ck_subscription_grant_duration_days",
        ),
        CheckConstraint("valid_until >= valid_from", name="ck_subscription_grant_period"),
        CheckConstraint("max_accounts > 0", name="ck_subscription_grant_max_accounts"),
        CheckConstraint("max_tasks > 0", name="ck_subscription_grant_max_tasks"),
        Index("ix_subscription_grants_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    redemption_code_id: Mapped[str | None] = mapped_column(
        ForeignKey("redemption_codes.id", ondelete="RESTRICT"), unique=True
    )
    duration_days: Mapped[int | None] = mapped_column(Integer)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    max_tasks: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DouyinLoginSession(Base):
    __tablename__ = "douyin_login_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    slot: Mapped[str | None] = mapped_column(String(16), unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=ScanStatus.QUEUED)
    qr_png: Mapped[bytes | None] = mapped_column(LargeBinary)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("douyin_accounts.id", ondelete="SET NULL"))
    error_code: Mapped[str | None] = mapped_column(String(48))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DouyinLoginChallenge(Base):
    __tablename__ = "douyin_login_challenges"

    scan_id: Mapped[str] = mapped_column(
        ForeignKey("douyin_login_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    masked_phone: Mapped[str | None] = mapped_column(String(64))
    verification_sent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    resend_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_resend: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resend_available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DouyinLoginAction(Base):
    __tablename__ = "douyin_login_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(
        ForeignKey("douyin_login_sessions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    x_million: Mapped[int] = mapped_column(Integer, nullable=False)
    y_million: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DouyinLoginInput(Base):
    __tablename__ = "douyin_login_inputs"
    __table_args__ = (
        Index(
            "uq_douyin_login_inputs_scan_request",
            "scan_id",
            "request_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(
        ForeignKey("douyin_login_sessions.id", ondelete="CASCADE"), index=True
    )
    # Legacy rows predate request-level idempotency and therefore keep NULL.
    # Every new browser-text submission receives a canonical UUID and is
    # unique within its scan session.
    request_id: Mapped[str | None] = mapped_column(String(36))
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    delivery_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DouyinAccountIdentity(Base):
    __tablename__ = "douyin_account_identities"

    account_id: Mapped[str] = mapped_column(
        ForeignKey("douyin_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    douyin_unique_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SparkTask(Base):
    __tablename__ = "spark_tasks"
    __table_args__ = (
        Index(
            "uq_enabled_task_target_schedule",
            "douyin_account_id",
            "target_key",
            "send_time",
            unique=True,
            sqlite_where=text("enabled = 1"),
            postgresql_where=text("enabled = true"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    douyin_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("douyin_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    target_name: Mapped[str] = mapped_column(String(TARGET_NAME_LENGTH), nullable=False)
    target_key: Mapped[str] = mapped_column(
        String(TARGET_KEY_LENGTH), nullable=False, default=task_target_key_default
    )
    send_time: Mapped[str] = mapped_column(String(5), nullable=False)
    message_template: Mapped[str] = mapped_column(String(500), nullable=False)
    message_kind: Mapped[str] = mapped_column(
        String(24), nullable=False, default="text", server_default=text("'text'")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class SparkTaskTargetIdentity(Base):
    __tablename__ = "spark_task_target_identities"

    task_id: Mapped[str] = mapped_column(
        ForeignKey("spark_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    sec_uid: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TaskRun(Base):
    __tablename__ = "task_runs"
    __table_args__ = (
        Index("uq_task_scheduled", "task_id", "scheduled_for", unique=True),
        Index("ix_task_runs_status_lease", "status", "lease_until"),
        Index("ix_task_runs_task_retry", "task_id", "retry_after"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    task_id: Mapped[str] = mapped_column(ForeignKey("spark_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    stage: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    worker_id: Mapped[str | None] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(48))
    error_summary: Mapped[str | None] = mapped_column(String(240))
    message_digest: Mapped[str | None] = mapped_column(String(64))


class WebSession(Base):
    __tablename__ = "web_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    elevated_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WorkerLock(Base):
    __tablename__ = "worker_lock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    actor_user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(36))
    detail: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
