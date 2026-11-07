from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    event,
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
    digest_version: Mapped[int] = mapped_column(Integer, default=2, server_default=text('1'))
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
    code_version: Mapped[int] = mapped_column(Integer, default=2, server_default=text('1'))
    code_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)




class MailAccountState(Base):
    __tablename__ = 'mail_account_states'
    account_id: Mapped[str] = mapped_column(ForeignKey('douyin_accounts.id', ondelete='CASCADE'), primary_key=True)
    state: Mapped[str] = mapped_column(String(24))
    revision: Mapped[int] = mapped_column(Integer, default=1)


class MailDelivery(Base):
    __tablename__ = 'mail_deliveries'
    __table_args__ = (
        Index("ix_mail_deliveries_status_updated", "status", "updated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    event_key: Mapped[str] = mapped_column(String(512), unique=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    recipient: Mapped[str] = mapped_column(String(254))
    event: Mapped[str] = mapped_column(String(24))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    body_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    body_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
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
    admin_note: Mapped[str | None] = mapped_column(String(500))
    # Per-user first-run guide state. These fields are nullable for additive
    # upgrades so existing users keep working without a data rewrite.
    onboarding_dismissed: Mapped[bool | None] = mapped_column(Boolean)
    onboarding_steps: Mapped[str | None] = mapped_column(Text)
    onboarding_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    # Stable hash used by horizontally sharded workers. Nullable keeps the
    # model compatible with databases created before the shard migration.
    dispatch_key: Mapped[int | None] = mapped_column(BigInteger, index=True)


@event.listens_for(DouyinAccount, "before_insert")
def _set_account_dispatch_key(_mapper, _connection, account: DouyinAccount) -> None:
    """Persist the shard key for every newly-created account."""
    from spark_console.worker_sharding import account_dispatch_key

    if account.id is None:
        account.id = uuid_string()
    if account.dispatch_key is None:
        account.dispatch_key = account_dispatch_key(account.id)


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
    __table_args__ = (
        Index("ix_account_operations_expires", "expires_at"),
    )
    account_id: Mapped[str] = mapped_column(ForeignKey("douyin_accounts.id", ondelete="CASCADE"), primary_key=True)
    token: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AccountIncident(Base):
    """One continuing account problem, shared by all affected task runs."""
    __tablename__ = 'account_incidents'
    __table_args__ = (
        Index('uq_account_incidents_open', 'account_id', unique=True,
              sqlite_where=text('resolved_at IS NULL'), postgresql_where=text('resolved_at IS NULL')),
        Index('ix_account_incidents_account_started', 'account_id', 'started_at'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    account_id: Mapped[str] = mapped_column(ForeignKey('douyin_accounts.id', ondelete='CASCADE'), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observations: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    alert_delivery_id: Mapped[str | None] = mapped_column(ForeignKey('mail_deliveries.id',ondelete='SET NULL'))
    alert_queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    __table_args__ = (
        Index("ix_contact_directory_jobs_status_created", "status", "created_at"),
    )
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
        Index("ix_user_subscriptions_valid_window", "valid_from", "valid_until"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_accounts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    # A lower plan selected while the current term is active is staged here
    # and becomes effective at ``pending_valid_from``.  Keeping the current
    # entitlement in max_* preserves existing tasks and execution state until
    # the paid term ends.
    pending_max_accounts: Mapped[int | None] = mapped_column(Integer)
    pending_max_tasks: Mapped[int | None] = mapped_column(Integer)
    pending_valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
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
    # Legacy plaintext is sealed on authorized retrieval or by maintenance.
    # New codes are stored only as a lookup hash plus retrievable ciphertext.
    code: Mapped[str | None] = mapped_column(String(128), unique=True)
    code_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    code_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    max_accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    max_tasks: Mapped[int] = mapped_column(Integer, nullable=False)
    # Before redemption the service stores a far-future sentinel because old
    # SQLite databases declared this column NOT NULL. It is treated as no
    # expiry; once redeemed it records the membership term's final date.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    # Future grants can be superseded by an immediate upgrade. The original
    # grant remains visible for billing/audit history but no longer controls
    # the entitlement timeline.
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DouyinLoginSession(Base):
    __tablename__ = "douyin_login_sessions"
    __table_args__ = (
        Index("ix_douyin_login_sessions_status_expires", "status", "expires_at"),
        Index("ix_douyin_login_sessions_status_finished", "status", "finished_at"),
        Index("ix_douyin_login_sessions_account_status_expires", "account_id", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    slot: Mapped[str | None] = mapped_column(String(16), unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=ScanStatus.QUEUED)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="login")
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
    __table_args__=(Index('uq_login_action_request','scan_id','request_id',unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(
        ForeignKey("douyin_login_sessions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text)
    payload_digest: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(36))
    delivery_status: Mapped[str | None] = mapped_column(String(24))
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
        Index(
            "uq_douyin_login_inputs_scan_active",
            "scan_id",
            unique=True,
            sqlite_where=text("delivery_status IN ('queued', 'claimed')"),
            postgresql_where=text("delivery_status IN ('queued', 'claimed')"),
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
    __table_args__ = (Index("uq_douyin_account_user_id", "douyin_user_id", unique=True),)

    account_id: Mapped[str] = mapped_column(
        ForeignKey("douyin_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    douyin_unique_id: Mapped[str | None] = mapped_column(String(64))
    # Passport user_id is stable. The editable public Douyin ID is display only.
    douyin_user_id: Mapped[str | None] = mapped_column(String(64))
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
        Index("ix_spark_tasks_enabled_next_run", "enabled", "next_run_at"),
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
        Index("ix_task_runs_status_scheduled", "status", "scheduled_for"),
        Index("ix_task_runs_task_scheduled_status", "task_id", "scheduled_for", "status"),
        Index("ix_task_runs_status_finished", "status", "finished_at"),
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
    execution_version: Mapped[str | None] = mapped_column(String(32))
    receipt_json: Mapped[str | None] = mapped_column(Text)


class TaskRunAttempt(Base):
    """One immutable completed attempt; excludes credentials and response bodies."""
    __tablename__ = "task_run_attempts"
    run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.id", ondelete="CASCADE"), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24))
    stage: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str | None] = mapped_column(String(240))


class TaskRunEvent(Base):
    """Immutable stage events used to explain one run without exposing secrets."""
    __tablename__ = "task_run_events"
    __table_args__ = (
        Index("uq_task_run_event_sequence", "run_id", "sequence", unique=True),
        Index("ix_task_run_events_run_at", "run_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(240))


class TaskHealthRetry(Base):
    """Durable, bounded recovery of one unsubmitted scheduled run."""
    __tablename__ = "task_health_retries"

    run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.id", ondelete="CASCADE"), primary_key=True)
    task_revision: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_reason: Mapped[str] = mapped_column(String(240), nullable=False, default="")


class WebSession(Base):
    __tablename__ = "web_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WorkerLock(Base):
    __tablename__ = "worker_lock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_created_action", "created_at", "action"),
        Index("ix_audit_events_request_id", "request_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    actor_user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(36))
    detail: Mapped[str | None] = mapped_column(String(240))
    request_id: Mapped[str | None] = mapped_column(String(64))
    client_ip: Mapped[str | None] = mapped_column(String(45))
    outcome: Mapped[str | None] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Announcement(Base):
    __tablename__ = 'announcements'
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uuid_string)
    title: Mapped[str] = mapped_column(String(160),nullable=False)
    body: Mapped[str] = mapped_column(Text,nullable=False)
    user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey('users.id',ondelete='SET NULL'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,index=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnnouncementRead(Base):
    __tablename__ = 'announcement_reads'
    announcement_id: Mapped[str] = mapped_column(ForeignKey('announcements.id',ondelete='CASCADE'),primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id',ondelete='CASCADE'),primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utc_now)


class PaymentRecord(Base):
    __tablename__ = 'payment_records'
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uuid_string)
    request_id: Mapped[str] = mapped_column(String(36),unique=True,nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64),nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey('users.id',ondelete='SET NULL'))
    source: Mapped[str] = mapped_column(String(24),nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer,nullable=False)
    reference: Mapped[str] = mapped_column(String(160),default='')
    note: Mapped[str] = mapped_column(String(500),default='')
    grant_id: Mapped[str | None] = mapped_column(ForeignKey('subscription_grants.id',ondelete='SET NULL'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utc_now,index=True)


class OperationSettings(Base):
    __tablename__ = 'operation_settings'
    id: Mapped[int] = mapped_column(Integer,primary_key=True,default=1)
    values_json: Mapped[str] = mapped_column(Text,nullable=False,default='{}')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utc_now)


class TaskProbe(Base):
    __tablename__ = 'task_probes'
    __table_args__ = (Index('ix_task_probes_status_created','status','created_at'),)
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uuid_string)
    task_id: Mapped[str] = mapped_column(ForeignKey('spark_tasks.id',ondelete='CASCADE'),index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey('users.id',ondelete='SET NULL'))
    run_id: Mapped[str | None] = mapped_column(ForeignKey('task_runs.id',ondelete='CASCADE'))
    kind: Mapped[str] = mapped_column(String(16),default='selfcheck')
    status: Mapped[str] = mapped_column(String(24),default='queued',index=True)
    result_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


