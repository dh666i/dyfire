from __future__ import annotations

import hashlib
import math
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from spark_console.crypto import CookieCipher
from spark_console.models import (
    DouyinAccount,
    DouyinLoginAction,
    DouyinLoginChallenge,
    DouyinLoginInput,
    DouyinLoginSession,
    ScanStatus,
    utc_now,
)
from spark_console.services import Conflict, NotFound, ValidationError


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_VIEW_PNG_BYTES = 4 * 1024 * 1024
# Backwards-compatible export name. The payload is now a full 1280x720 browser
# frame rather than a cropped QR element, so the previous 1 MiB ceiling was too
# small for valid PNG screenshots.
MAX_QR_PNG_BYTES = MAX_VIEW_PNG_BYTES
OTP_LIFETIME = timedelta(minutes=2)
OTP_PREFIX = b"douyin-otp-v1:"
# DouyinLoginInput is also used for the password field on the real login
# page. Keep the legacy OTP prefix while tagging new non-OTP values separately.
TEXT_PREFIX = b"douyin-text-v2:"
TEXT_MIN_LENGTH = 1
TEXT_MAX_LENGTH = 256
DELIVERY_QUEUED = "queued"
DELIVERY_CLAIMED = "claimed"
DELIVERY_DELIVERED = "delivered"
DELIVERY_UNCERTAIN = "uncertain"
DELIVERY_FAILED = "failed"
FINAL_DELIVERY_STATUSES = frozenset(
    {DELIVERY_DELIVERED, DELIVERY_UNCERTAIN, DELIVERY_FAILED}
)
NEEDS_VERIFICATION = "needs_verification"
NEEDS_APP_VERIFICATION = "needs_app_verification"
SMS_VERIFICATION = "sms"
APP_VERIFICATION = "app"
ACTIVE_VERIFICATION_CODES = frozenset(
    {NEEDS_VERIFICATION, NEEDS_APP_VERIFICATION}
)

ACTIVE_STATUSES = frozenset(
    {
        ScanStatus.QUEUED,
        ScanStatus.LOADING_QR,
        ScanStatus.AWAITING_SCAN,
        ScanStatus.CONFIRMING,
    }
)
TERMINAL_STATUSES = frozenset(
    {
        ScanStatus.SUCCEEDED,
        ScanStatus.FAILED,
        ScanStatus.EXPIRED,
        ScanStatus.CANCELLED,
    }
)
ALLOWED = {
    ScanStatus.QUEUED: {
        ScanStatus.LOADING_QR,
        ScanStatus.CANCELLED,
        ScanStatus.EXPIRED,
        ScanStatus.FAILED,
    },
    ScanStatus.LOADING_QR: {
        ScanStatus.AWAITING_SCAN,
        ScanStatus.CANCELLED,
        ScanStatus.EXPIRED,
        ScanStatus.FAILED,
    },
    ScanStatus.AWAITING_SCAN: {
        ScanStatus.CONFIRMING,
        ScanStatus.SUCCEEDED,
        ScanStatus.CANCELLED,
        ScanStatus.EXPIRED,
        ScanStatus.FAILED,
    },
    ScanStatus.CONFIRMING: {
        ScanStatus.SUCCEEDED,
        ScanStatus.CANCELLED,
        ScanStatus.EXPIRED,
        ScanStatus.FAILED,
    },
}

FAILURE_CODES = frozenset(
    {
        "qr_load_failed",
        "login_timeout",
        "cancelled",
        "verification_required",
        "subscription_inactive",
        "account_limit_reached",
        "credential_invalid",
        "account_identity_mismatch",
        "account_already_bound",
        "automation_failed",
    }
)

STATUS_MESSAGES = {
    ScanStatus.QUEUED: "等待云端通道",
    ScanStatus.LOADING_QR: "正在加载二维码",
    ScanStatus.AWAITING_SCAN: "请使用抖音 App 扫码并在手机确认",
    ScanStatus.CONFIRMING: "已扫码；确认后正在抓取并加密保存登录凭证",
    ScanStatus.SUCCEEDED: "绑定成功",
    ScanStatus.FAILED: "绑定失败，请重试",
    ScanStatus.EXPIRED: "扫码已超时，请重试",
    ScanStatus.CANCELLED: "扫码已取消",
}
ERROR_MESSAGES = {
    "qr_load_failed": "二维码加载失败，请重试",
    "login_timeout": "扫码已超时，请重试",
    "cancelled": "扫码已取消",
    "verification_required": "抖音要求额外验证，请稍后重试",
    "subscription_inactive": "会员未开通或已过期，请开通后重试",
    "account_limit_reached": "抖音账号额度已用完，请升级套餐或删除旧账号",
    "credential_invalid": "登录凭证无效，请重试",
    "account_identity_mismatch": "扫码登录的抖音账号与所选账号不一致，请使用正确账号重试",
    "account_already_bound": "该抖音账号已经绑定，无需重复添加",
    "automation_failed": "自动化登录失败，请重试",
    NEEDS_VERIFICATION: "请在云端抖音页面完成短信验证",
    NEEDS_APP_VERIFICATION: "请在云端抖音页面完成额外验证",
}


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def scan_slot_for_owner(owner_id: str) -> str:
    return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:16]


def browser_view_revision(png: bytes | None) -> str | None:
    """Return a short content revision for a browser frame.

    The revision contains no page data and is safe to expose to the owning
    client. It lets the HTTP layer use conditional requests so an unchanged
    screenshot is not downloaded and swapped into the DOM repeatedly.
    """

    if not isinstance(png, bytes) or not png.startswith(PNG_SIGNATURE):
        return None
    return hashlib.sha256(png).hexdigest()[:24]


def is_safe_browser_text(value: object) -> bool:
    """Validate a value before it is queued for a remote text input.

    Douyin passwords may contain Unicode letters, digits, punctuation, and
    spaces. ``str.isprintable`` excludes control characters such as newlines
    and escape sequences while preserving the exact value supplied by the
    user. The explicit length cap prevents an authenticated user from using
    the encrypted interaction queue for unbounded payloads.
    """

    return bool(
        isinstance(value, str)
        and TEXT_MIN_LENGTH <= len(value) <= TEXT_MAX_LENGTH
        and value.isprintable()
    )


def normalize_interaction_request_id(value: object) -> str:
    """Return a canonical UUID used only to deduplicate one client action."""

    if not isinstance(value, str):
        raise ValidationError("invalid_request_id")
    candidate = value.strip()
    try:
        parsed = uuid.UUID(candidate)
    except (AttributeError, ValueError):
        raise ValidationError("invalid_request_id") from None
    canonical = str(parsed)
    if candidate.lower() != canonical:
        raise ValidationError("invalid_request_id")
    return canonical


class ScanSessionService:
    def __init__(
        self,
        session: Session,
        now=utc_now,
        lifetime: timedelta = timedelta(minutes=5),
        queue_lifetime: timedelta = timedelta(minutes=30),
    ):
        self.session = session
        self.now = now
        self.lifetime = lifetime
        self.queue_lifetime = queue_lifetime

    def start(
        self, owner_id: str, account_id: str | None = None
    ) -> DouyinLoginSession:
        current = self._now()
        if account_id is not None:
            account = self.session.scalar(
                select(DouyinAccount).where(
                    DouyinAccount.id == account_id,
                    DouyinAccount.owner_user_id == owner_id,
                )
            )
            if account is None:
                raise NotFound("account not found")
        if self.active_owned(owner_id) is not None:
            raise Conflict("slot_busy")
        waiting=self.session.scalar(select(func.count()).select_from(DouyinLoginSession).where(DouyinLoginSession.status.in_(ACTIVE_STATUSES),DouyinLoginSession.expires_at>current)) or 0
        if waiting>=20:raise Conflict('queue_full')
        scan = DouyinLoginSession(
            owner_user_id=owner_id,
            account_id=account_id,
            slot=scan_slot_for_owner(owner_id),
            status=ScanStatus.QUEUED,
            expires_at=current + self.queue_lifetime,
            created_at=current,
            updated_at=current,
        )
        try:
            with self.session.begin_nested():
                self.session.add(scan)
                self.session.flush()
        except IntegrityError:
            raise Conflict("slot_busy") from None
        return scan

    def start_recovery(self, owner_id: str, account_id: str) -> DouyinLoginSession:
        current = self._now()
        account = self.session.scalar(select(DouyinAccount).where(
            DouyinAccount.id == account_id,
            DouyinAccount.owner_user_id == owner_id,
        ))
        if account is None:
            raise NotFound("account not found")
        if self.active_owned(owner_id) is not None:
            raise Conflict("slot_busy")
        scan = DouyinLoginSession(
            owner_user_id=owner_id,
            account_id=account_id,
            mode="recovery",
            slot=scan_slot_for_owner(owner_id),
            status=ScanStatus.QUEUED,
            expires_at=current + self.queue_lifetime,
            created_at=current,
            updated_at=current,
        )
        try:
            with self.session.begin_nested():
                self.session.add(scan)
                self.session.flush()
        except IntegrityError:
            raise Conflict("slot_busy") from None
        return scan

    def active_owned(self, owner_id: str) -> DouyinLoginSession | None:
        return self.session.scalar(
            select(DouyinLoginSession)
            .where(
                DouyinLoginSession.owner_user_id == owner_id,
                DouyinLoginSession.slot.is_not(None),
                DouyinLoginSession.status.in_(ACTIVE_STATUSES),
            )
            .order_by(DouyinLoginSession.created_at.desc())
            .limit(1)
        )

    def claim_next(self) -> DouyinLoginSession | None:
        from spark_console.models import AccountOperation, TaskRun, SparkTask
        from sqlalchemy import delete
        from spark_console.services.account_health import acquire
        self.session.execute(delete(AccountOperation).where(AccountOperation.expires_at <= self._now()))
        candidates = self.session.scalars(
            select(DouyinLoginSession)
            .where(
                DouyinLoginSession.slot.is_not(None),
                DouyinLoginSession.status == ScanStatus.QUEUED,
            )
            .order_by(DouyinLoginSession.created_at, DouyinLoginSession.id)
            .limit(100)
        ).all()
        scan = None
        for candidate in candidates:
            if candidate.expires_at.replace(tzinfo=timezone.utc) <= self._now():
                self._expire_one(candidate,self._now());continue
            if candidate.account_id:
                if self.session.scalar(select(TaskRun.id).join(SparkTask).where(
                    SparkTask.douyin_account_id == candidate.account_id, TaskRun.status == 'running',
                    TaskRun.lease_until > self._now()).limit(1)):
                    continue
                if not acquire(self.session, candidate.account_id, candidate.id, 'login', self._now(), int(self.lifetime.total_seconds())+60):
                    continue
            scan=candidate;break
        if scan is None:return None
        slot = scan.slot
        if slot is None:
            return None
        current = self._now()
        result = self.session.execute(
            update(DouyinLoginSession)
            .where(
                DouyinLoginSession.id == scan.id,
                DouyinLoginSession.slot == slot,
                DouyinLoginSession.status == ScanStatus.QUEUED,
                DouyinLoginSession.expires_at > current,
            )
            .values(
                status=ScanStatus.LOADING_QR,
                expires_at=current + self.lifetime,
                updated_at=current,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            if scan.account_id:
                from spark_console.services.account_health import release
                release(self.session, scan.account_id, scan.id)
            self._expire_one(scan, current)
            return None
        return self._reload(scan.id)

    def publish_qr(self, scan_id: str, png: bytes) -> DouyinLoginSession:
        self._validate_png(png)
        scan = self._get(scan_id)
        return self._cas_transition(
            scan,
            ScanStatus.AWAITING_SCAN,
            qr_png=png,
        )

    def publish_view(self, scan_id: str, png: bytes) -> DouyinLoginSession:
        self._validate_png(png)
        scan = self._get(scan_id)
        current = self._now()
        slot = scan.slot
        if slot is None or self._status(scan) not in ACTIVE_STATUSES:
            raise Conflict("transition_conflict")
        if _aware(scan.expires_at) <= current:
            self._expire_one(scan, current)
            raise Conflict("transition_conflict")
        if scan.qr_png == png:
            return scan
        result = self.session.execute(
            update(DouyinLoginSession)
            .where(
                DouyinLoginSession.id == scan.id,
                DouyinLoginSession.slot == slot,
                DouyinLoginSession.status.in_(ACTIVE_STATUSES),
                DouyinLoginSession.expires_at > current,
            )
            .values(qr_png=png)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self._expire_one(scan, current)
            raise Conflict("transition_conflict")
        return self._reload(scan.id)

    def queue_click(
        self, owner_id: str, scan_id: str, x: float, y: float
    ) -> DouyinLoginAction:
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, (int, float))
            or not isinstance(y, (int, float))
            or not math.isfinite(x)
            or not math.isfinite(y)
            or not (0 <= x <= 1)
            or not (0 <= y <= 1)
        ):
            raise ValidationError("invalid_interaction")
        scan = self.get_owned(owner_id, scan_id)
        current = self._now()
        if scan.slot is None or self._status(scan) not in ACTIVE_STATUSES:
            raise Conflict("scan_not_active")
        if _aware(scan.expires_at) <= current:
            self._expire_one(scan, current)
            raise Conflict("scan_not_active")
        action = DouyinLoginAction(
            scan_id=scan.id,
            kind="click",
            x_million=round(x * 1_000_000),
            y_million=round(y * 1_000_000),
            created_at=current,
        )
        self.session.add(action)
        self.session.flush()
        return action

    def queue_text(
        self,
        owner_id: str,
        scan_id: str,
        value: str,
        cipher: CookieCipher,
        request_id: str | None = None,
    ) -> DouyinLoginInput:
        canonical_request_id = normalize_interaction_request_id(
            request_id if request_id is not None else str(uuid.uuid4())
        )
        scan = self.get_owned(owner_id, scan_id)
        existing = self._input_by_request_id(scan.id, canonical_request_id)
        if existing is not None:
            return existing
        if self._active_text_input(scan.id) is not None:
            raise Conflict("interaction_in_progress")
        if not is_safe_browser_text(value):
            raise ValidationError("invalid_interaction")
        current = self._now()
        if scan.slot is None or self._status(scan) not in ACTIVE_STATUSES:
            raise Conflict("scan_not_active")
        if _aware(scan.expires_at) <= current:
            self._expire_one(scan, current)
            raise Conflict("scan_not_active")
        prefix = (
            OTP_PREFIX
            if value.isdigit() and 4 <= len(value) <= 8
            else TEXT_PREFIX
        )
        plaintext = bytearray(prefix + value.encode("utf-8"))
        try:
            encrypted = cipher.encrypt(bytes(plaintext))
        finally:
            plaintext[:] = b"\0" * len(plaintext)
            plaintext.clear()
        pending = DouyinLoginInput(
            scan_id=scan.id,
            request_id=canonical_request_id,
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            delivery_status=DELIVERY_QUEUED,
            created_at=current,
        )
        try:
            with self.session.begin_nested():
                self.session.add(pending)
                self.session.flush()
        except IntegrityError:
            # A retried HTTP request may race the original transaction. The
            # database uniqueness constraint is the final authority: return
            # the already accepted interaction without creating a second
            # encrypted payload.
            existing = self._input_by_request_id(scan.id, canonical_request_id)
            if existing is not None:
                return existing
            if self._active_text_input(scan.id) is not None:
                raise Conflict("interaction_in_progress") from None
            raise
        self._clear_pending_inputs(
            scan.id,
            current,
            exclude_input_id=pending.id,
        )
        return pending

    def get_text_interaction_by_request_owned(
        self,
        owner_id: str,
        scan_id: str,
        request_id: str,
    ) -> DouyinLoginInput | None:
        """Find an accepted request without changing its delivery state."""

        canonical_request_id = normalize_interaction_request_id(request_id)
        scan = self.get_owned(owner_id, scan_id)
        return self._input_by_request_id(scan.id, canonical_request_id)

    def active_text_interaction_owned(
        self,
        owner_id: str,
        scan_id: str,
    ) -> DouyinLoginInput | None:
        scan = self.get_owned(owner_id, scan_id)
        return self._active_text_input(scan.id)

    def get_text_interaction_owned(
        self,
        owner_id: str,
        scan_id: str,
        interaction_id: int,
    ) -> DouyinLoginInput:
        self.get_owned(owner_id, scan_id)
        if isinstance(interaction_id, bool) or not isinstance(interaction_id, int):
            raise NotFound("interaction not found")
        pending = self.session.scalar(
            select(DouyinLoginInput).where(
                DouyinLoginInput.id == interaction_id,
                DouyinLoginInput.scan_id == scan_id,
            )
        )
        if pending is None:
            raise NotFound("interaction not found")
        return pending

    def public_text_interaction(
        self, pending: DouyinLoginInput
    ) -> dict[str, object]:
        public_status = (
            "processing"
            if pending.delivery_status == DELIVERY_CLAIMED
            else pending.delivery_status
        )
        return {
            "interaction_id": pending.id,
            "request_id": pending.request_id,
            "status": public_status,
            "delivery_status": pending.delivery_status,
            "session_text_attempt_number": self._text_attempt_number(pending),
            "attempted_at": self._public_datetime(pending.attempted_at),
            "finished_at": self._public_datetime(pending.finished_at),
            "status_url": (
                f"/accounts/scan/{pending.scan_id}/interactions/{pending.id}"
            ),
        }

    def claim_interaction(
        self, scan_id: str, cipher: CookieCipher | None = None
    ) -> dict[str, object] | None:
        current = self._now()
        scan = self.session.get(DouyinLoginSession, scan_id)
        action_allowed = bool(
            scan is not None
            and scan.slot is not None
            and self._status(scan) in ACTIVE_STATUSES
            and _aware(scan.expires_at) > current
        )

        # Browser actions are intentionally claimed before encrypted text so a
        # click that focuses the real Douyin field is delivered before the OTP
        # or password.
        if action_allowed:
            action = self.session.scalar(
                select(DouyinLoginAction)
                .where(
                    DouyinLoginAction.scan_id == scan_id,
                    DouyinLoginAction.consumed_at.is_(None),
                )
                .order_by(DouyinLoginAction.id)
                .limit(1)
            )
            if action is not None:
                action.consumed_at = current
                self.session.flush()
                result = {"kind": action.kind}
                if action.kind == "click":
                    result.update(
                        x=action.x_million / 1_000_000,
                        y=action.y_million / 1_000_000,
                    )
                return result

        # The cloud page is the source of truth for the current verification
        # step. It may ask for an OTP or password before the scanner can
        # reliably classify the form, so an owner may queue safe text during
        # any live scan.
        text_allowed = bool(cipher is not None and action_allowed)
        if cipher is not None and not text_allowed:
            self._clear_pending_inputs(
                scan_id, current, finalize_claimed=True
            )
        if text_allowed:
            self._clear_expired_inputs(scan_id, current)
            pending = self.session.scalar(
                select(DouyinLoginInput)
                .where(
                    DouyinLoginInput.scan_id == scan_id,
                    DouyinLoginInput.delivery_status == DELIVERY_QUEUED,
                    DouyinLoginInput.consumed_at.is_(None),
                    DouyinLoginInput.ciphertext.is_not(None),
                    DouyinLoginInput.nonce.is_not(None),
                    DouyinLoginInput.created_at > current - OTP_LIFETIME,
                )
                .order_by(DouyinLoginInput.id)
                .limit(1)
            )
            if (
                pending is not None
                and pending.ciphertext is not None
                and pending.nonce is not None
            ):
                encrypted = bytearray(pending.ciphertext)
                nonce = bytearray(pending.nonce)
                claimed = self.session.execute(
                    update(DouyinLoginInput)
                    .where(
                        DouyinLoginInput.id == pending.id,
                        DouyinLoginInput.delivery_status == DELIVERY_QUEUED,
                        DouyinLoginInput.consumed_at.is_(None),
                        DouyinLoginInput.ciphertext.is_not(None),
                        DouyinLoginInput.nonce.is_not(None),
                    )
                    .values(
                        ciphertext=None,
                        nonce=None,
                        consumed_at=current,
                        delivery_status=DELIVERY_CLAIMED,
                        attempted_at=current,
                        finished_at=None,
                    )
                    .execution_options(synchronize_session=False)
                )
                if claimed.rowcount != 1:
                    encrypted[:] = b"\0" * len(encrypted)
                    encrypted.clear()
                    nonce[:] = b"\0" * len(nonce)
                    nonce.clear()
                    return None
                set_committed_value(pending, "ciphertext", None)
                set_committed_value(pending, "nonce", None)
                set_committed_value(pending, "consumed_at", current)
                set_committed_value(
                    pending, "delivery_status", DELIVERY_CLAIMED
                )
                set_committed_value(pending, "attempted_at", current)
                set_committed_value(pending, "finished_at", None)
                try:
                    try:
                        decrypted = cipher.decrypt(bytes(encrypted), bytes(nonce))
                    except Exception:
                        self._finish_claimed_input(
                            pending, DELIVERY_FAILED, current
                        )
                        return None
                finally:
                    encrypted[:] = b"\0" * len(encrypted)
                    encrypted.clear()
                    nonce[:] = b"\0" * len(nonce)
                    nonce.clear()

                plaintext = bytearray(decrypted)
                del decrypted
                try:
                    if plaintext.startswith(OTP_PREFIX):
                        value = plaintext[len(OTP_PREFIX) :].decode("ascii")
                        if not value.isdigit() or not 4 <= len(value) <= 8:
                            self._finish_claimed_input(
                                pending, DELIVERY_FAILED, current
                            )
                            return None
                    elif plaintext.startswith(TEXT_PREFIX):
                        value = plaintext[len(TEXT_PREFIX) :].decode("utf-8")
                        if not is_safe_browser_text(value):
                            self._finish_claimed_input(
                                pending, DELIVERY_FAILED, current
                            )
                            return None
                    else:
                        # Rows written by the pre-prefix OTP implementation
                        # contained only the numeric code. Accept that legacy
                        # shape for one read, then consume and erase it just
                        # like a versioned payload.
                        value = plaintext.decode("ascii")
                        if not value.isdigit() or not 4 <= len(value) <= 8:
                            self._finish_claimed_input(
                                pending, DELIVERY_FAILED, current
                            )
                            return None
                except UnicodeDecodeError:
                    self._finish_claimed_input(
                        pending, DELIVERY_FAILED, current
                    )
                    return None
                finally:
                    plaintext[:] = b"\0" * len(plaintext)
                    plaintext.clear()
                return {
                    "kind": "text",
                    "text": value,
                    "interaction_id": pending.id,
                    "request_id": pending.request_id,
                    "session_text_attempt_number": self._text_attempt_number(
                        pending
                    ),
                }

        return None

    def mark_text_interaction_delivered(
        self, scan_id: str, interaction_id: int
    ) -> DouyinLoginInput:
        return self._finish_text_interaction(
            scan_id, interaction_id, DELIVERY_DELIVERED
        )

    def mark_text_interaction_uncertain(
        self, scan_id: str, interaction_id: int
    ) -> DouyinLoginInput:
        return self._finish_text_interaction(
            scan_id, interaction_id, DELIVERY_UNCERTAIN
        )

    def mark_text_interaction_failed(
        self, scan_id: str, interaction_id: int
    ) -> DouyinLoginInput:
        return self._finish_text_interaction(
            scan_id, interaction_id, DELIVERY_FAILED
        )

    def mark_needs_verification(
        self,
        scan_id: str,
        details: dict[str, object] | bool | None = None,
    ) -> DouyinLoginSession:
        scan = self._get(scan_id)
        slot = scan.slot
        if slot is None:
            raise Conflict("transition_conflict")
        current = self._now()
        verification_kind = self._requested_verification_kind(details)
        error_code = (
            NEEDS_VERIFICATION
            if verification_kind == SMS_VERIFICATION
            else NEEDS_APP_VERIFICATION
        )
        result = self.session.execute(
            update(DouyinLoginSession)
            .where(
                DouyinLoginSession.id == scan.id,
                DouyinLoginSession.slot == slot,
                DouyinLoginSession.status.in_(
                    (ScanStatus.LOADING_QR, ScanStatus.AWAITING_SCAN, ScanStatus.CONFIRMING)
                ),
                DouyinLoginSession.expires_at > current,
            )
            .values(
                status=ScanStatus.CONFIRMING,
                error_code=error_code,
                updated_at=current,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            self._update_verification_challenge(
                scan.id, details, current, verification_kind
            )
            return self._reload(scan.id)
        # Repeated verification observations are expected while the real
        # cloud page remains open; make this idempotent for the same session.
        persisted = self._reload(scan.id)
        if (
            persisted.slot == slot
            and self._status(persisted) == ScanStatus.CONFIRMING
            and persisted.error_code == error_code
        ):
            self._update_verification_challenge(
                scan.id, details, current, verification_kind
            )
            return persisted
        expired = self._expire_one(scan, current)
        if expired is not None:
            return expired
        raise Conflict("transition_conflict")

    def mark_confirming(self, scan_id: str) -> DouyinLoginSession:
        scan = self._get(scan_id)
        return self._cas_transition(scan, ScanStatus.CONFIRMING)

    def complete(self, scan_id: str, account_id: str) -> DouyinLoginSession:
        scan = self._get(scan_id)
        return self._cas_terminal(
            scan,
            ScanStatus.SUCCEEDED,
            account_id=account_id,
        )

    def fail(self, scan_id: str, code: str) -> DouyinLoginSession:
        if code not in FAILURE_CODES:
            raise ValidationError("invalid_error_code")
        scan = self._get(scan_id)
        return self._cas_terminal(scan, ScanStatus.FAILED, error_code=code)

    def cancel_owned(self, owner_id: str, scan_id: str) -> DouyinLoginSession:
        scan = self.get_owned(owner_id, scan_id)
        return self._cas_terminal(
            scan,
            ScanStatus.CANCELLED,
            error_code="cancelled",
        )

    def get_owned(self, owner_id: str, scan_id: str) -> DouyinLoginSession:
        scan = self.session.scalar(
            select(DouyinLoginSession).where(
                DouyinLoginSession.id == scan_id,
                DouyinLoginSession.owner_user_id == owner_id,
            )
        )
        if scan is None:
            raise NotFound("scan session not found")
        return scan

    def expire_stale(self) -> int:
        current = self._now()
        scan_ids = tuple(
            self.session.scalars(
                select(DouyinLoginSession.id).where(
                    DouyinLoginSession.slot.is_not(None),
                    DouyinLoginSession.status.in_(ACTIVE_STATUSES),
                    DouyinLoginSession.expires_at <= current,
                )
            )
        )
        result = self.session.execute(
            update(DouyinLoginSession)
            .where(
                DouyinLoginSession.slot.is_not(None),
                DouyinLoginSession.status.in_(ACTIVE_STATUSES),
                DouyinLoginSession.expires_at <= current,
            )
            .values(
                status=ScanStatus.EXPIRED,
                slot=None,
                qr_png=None,
                account_id=None,
                error_code="login_timeout",
                finished_at=current,
                updated_at=current,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount:
            self._clear_pending_inputs_for_scans(
                scan_ids, current, finalize_claimed=True
            )
            self._clear_pending_actions_for_scans(scan_ids, current)
            self._clear_verification_challenges(scan_ids)
            self._reload_cached_scans()
        return result.rowcount

    def fail_abandoned_browser_sessions(self) -> int:
        current = self._now()
        abandoned_statuses = (
            ScanStatus.LOADING_QR,
            ScanStatus.AWAITING_SCAN,
            ScanStatus.CONFIRMING,
        )
        scan_ids = tuple(
            self.session.scalars(
                select(DouyinLoginSession.id).where(
                    DouyinLoginSession.slot.is_not(None),
                    DouyinLoginSession.status.in_(abandoned_statuses),
                )
            )
        )
        result = self.session.execute(
            update(DouyinLoginSession)
            .where(
                DouyinLoginSession.slot.is_not(None),
                DouyinLoginSession.status.in_(abandoned_statuses),
            )
            .values(
                status=ScanStatus.FAILED,
                slot=None,
                qr_png=None,
                account_id=None,
                error_code="automation_failed",
                finished_at=current,
                updated_at=current,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount:
            self._clear_pending_inputs_for_scans(
                scan_ids, current, finalize_claimed=True
            )
            self._clear_pending_actions_for_scans(scan_ids, current)
            self._clear_verification_challenges(scan_ids)
            self._reload_cached_scans()
        return result.rowcount

    def public_status(
        self, scan: DouyinLoginSession, now: datetime | None = None
    ) -> dict[str, object]:
        status = self._status(scan)
        current = _aware(now) if now is not None else self._now()
        if status in TERMINAL_STATUSES:
            remaining_seconds = 0
        else:
            remaining_seconds = max(
                0,
                math.ceil((_aware(scan.expires_at) - current).total_seconds()),
            )
        needs_verification = (
            status in ACTIVE_STATUSES
            and scan.error_code in ACTIVE_VERIFICATION_CODES
        )
        verification_kind = (
            self._verification_kind_for_error(scan.error_code)
            if needs_verification
            else None
        )
        challenge = (
            self.session.get(DouyinLoginChallenge, scan.id)
            if verification_kind == SMS_VERIFICATION
            else None
        )
        resend_after_seconds = None
        can_resend = False
        resend_pending = bool(challenge is not None and challenge.resend_pending)
        if challenge is not None:
            available_at = challenge.resend_available_at
            if available_at is not None and not resend_pending:
                resend_after_seconds = max(
                    0,
                    math.ceil((_aware(available_at) - current).total_seconds()),
                )
            can_resend = not resend_pending and (
                challenge.can_resend or resend_after_seconds == 0
            )
        verification_sent = bool(
            challenge is not None and challenge.verification_sent
        )
        if challenge is None:
            send_status = None
        elif verification_sent:
            send_status = "sent"
        elif resend_pending:
            send_status = "sending"
        elif can_resend:
            send_status = "manual_required"
        else:
            send_status = "manual_required"
        public_error = scan.error_code
        if (
            public_error is not None
            and public_error not in FAILURE_CODES
            and not needs_verification
        ):
            public_error = "automation_failed"
        message = ERROR_MESSAGES.get(public_error, STATUS_MESSAGES[status])
        if verification_kind == SMS_VERIFICATION:
            message = ERROR_MESSAGES[NEEDS_VERIFICATION]
        return {
            "id": scan.id,
            "status": status.value,
            "remaining_seconds": remaining_seconds,
            "error": public_error,
            "message": message,
            "account_id": scan.account_id,
            "needs_verification": needs_verification,
            "verification_kind": verification_kind,
            "masked_phone": challenge.masked_phone if challenge is not None else None,
            "verification_sent": verification_sent,
            "send_status": send_status,
            "can_retry_send": bool(
                challenge is not None
                and not verification_sent
                and can_resend
            ),
            "resend_pending": resend_pending,
            "can_resend": can_resend,
            "resend_after_seconds": resend_after_seconds,
            "frame_revision": browser_view_revision(scan.qr_png),
        }

    def _update_verification_challenge(
        self,
        scan_id: str,
        details: dict[str, object] | bool | None,
        current: datetime,
        verification_kind: str,
    ) -> None:
        payload = details if isinstance(details, dict) else {}
        if verification_kind != SMS_VERIFICATION:
            self._clear_verification_challenges((scan_id,))
            self.session.flush()
            return

        challenge = self.session.get(DouyinLoginChallenge, scan_id)
        if challenge is None:
            challenge = DouyinLoginChallenge(scan_id=scan_id, updated_at=current)
            self.session.add(challenge)
        challenge.resend_pending = False

        if "masked_phone" in payload:
            value = payload.get("masked_phone")
            if isinstance(value, str):
                value = value.strip()
                if value and len(value) <= 64 and value.isprintable():
                    challenge.masked_phone = value
        if "verification_sent" in payload:
            challenge.verification_sent = bool(
                challenge.verification_sent
                or payload.get("verification_sent") is True
            )
        if "can_resend" in payload:
            challenge.can_resend = payload.get("can_resend") is True

        raw_seconds = payload.get("resend_after_seconds")
        seconds = None
        if isinstance(raw_seconds, (int, float)) and not isinstance(raw_seconds, bool):
            seconds = max(0, min(300, math.ceil(raw_seconds)))
        if challenge.can_resend:
            challenge.resend_available_at = current
        elif seconds is not None:
            challenge.resend_available_at = current + timedelta(seconds=seconds)
        challenge.updated_at = current
        self.session.flush()

    @staticmethod
    def _requested_verification_kind(
        details: dict[str, object] | bool | None,
    ) -> str:
        if not isinstance(details, dict):
            return SMS_VERIFICATION
        value = details.get("kind", details.get("verification_kind"))
        if value is None:
            return SMS_VERIFICATION
        if isinstance(value, str) and value.strip().lower() == SMS_VERIFICATION:
            return SMS_VERIFICATION
        return APP_VERIFICATION

    @staticmethod
    def _verification_kind_for_error(error_code: str | None) -> str | None:
        if error_code == NEEDS_VERIFICATION:
            return SMS_VERIFICATION
        if error_code == NEEDS_APP_VERIFICATION:
            return APP_VERIFICATION
        return None

    def _get(self, scan_id: str) -> DouyinLoginSession:
        scan = self.session.get(DouyinLoginSession, scan_id)
        if scan is None:
            raise NotFound("scan session not found")
        return scan

    @staticmethod
    def _validate_png(png: bytes) -> None:
        if (
            not isinstance(png, bytes)
            or not png.startswith(PNG_SIGNATURE)
            or len(png) > MAX_VIEW_PNG_BYTES
        ):
            raise ValidationError("invalid_qr_png")

    def _cas_transition(
        self,
        scan: DouyinLoginSession,
        target: ScanStatus,
        **values,
    ) -> DouyinLoginSession:
        current = self._now()
        slot = scan.slot
        if slot is None:
            raise Conflict("transition_conflict")
        sources = tuple(
            source for source, targets in ALLOWED.items() if target in targets
        )
        result = self.session.execute(
            update(DouyinLoginSession)
            .where(
                DouyinLoginSession.id == scan.id,
                DouyinLoginSession.slot == slot,
                DouyinLoginSession.status.in_(sources),
                DouyinLoginSession.expires_at > current,
            )
            .values(status=target, updated_at=current, **values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            return self._reload(scan.id)
        expired = self._expire_one(scan, current)
        if expired is not None:
            return expired
        persisted = self._reload(scan.id)
        persisted_status = self._status(persisted)
        if (
            persisted.slot is not None
            and persisted_status in ACTIVE_STATUSES
            and target not in ALLOWED.get(persisted_status, set())
        ):
            raise Conflict("invalid_transition")
        raise Conflict("transition_conflict")

    def _cas_terminal(
        self,
        scan: DouyinLoginSession,
        target: ScanStatus,
        *,
        account_id: str | None = None,
        error_code: str | None = None,
    ) -> DouyinLoginSession:
        if target not in TERMINAL_STATUSES:
            raise ValueError("terminal status required")
        current = self._now()
        if account_id is None and getattr(scan, "mode", "login") == "recovery":
            account_id = scan.account_id
        terminal = self._cas_transition(
            scan,
            target,
            slot=None,
            qr_png=None,
            account_id=account_id,
            error_code=error_code,
            finished_at=current,
        )
        self._clear_pending_inputs(
            scan.id, current, finalize_claimed=True
        )
        self._clear_pending_actions_for_scans((scan.id,), current)
        self._clear_verification_challenges((scan.id,))
        return terminal

    def _expire_one(
        self, scan: DouyinLoginSession, current: datetime
    ) -> DouyinLoginSession | None:
        slot = scan.slot
        if slot is None:
            return None
        result = self.session.execute(
            update(DouyinLoginSession)
            .where(
                DouyinLoginSession.id == scan.id,
                DouyinLoginSession.slot == slot,
                DouyinLoginSession.status.in_(ACTIVE_STATUSES),
                DouyinLoginSession.expires_at <= current,
            )
            .values(
                status=ScanStatus.EXPIRED,
                slot=None,
                qr_png=None,
                account_id=None,
                error_code="login_timeout",
                finished_at=current,
                updated_at=current,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            return None
        self._clear_pending_inputs(
            scan.id, current, finalize_claimed=True
        )
        self._clear_pending_actions_for_scans((scan.id,), current)
        self._clear_verification_challenges((scan.id,))
        return self._reload(scan.id)

    def _clear_verification_challenges(self, scan_ids: tuple[str, ...]) -> None:
        if not scan_ids:
            return
        self.session.execute(
            delete(DouyinLoginChallenge).where(
                DouyinLoginChallenge.scan_id.in_(scan_ids)
            )
        )

    def _clear_pending_actions_for_scans(
        self, scan_ids: tuple[str, ...], current: datetime
    ) -> None:
        if not scan_ids:
            return
        result = self.session.execute(
            update(DouyinLoginAction)
            .where(
                DouyinLoginAction.scan_id.in_(scan_ids),
                DouyinLoginAction.consumed_at.is_(None),
            )
            .values(consumed_at=current)
            .execution_options(synchronize_session="fetch")
        )
        if result.rowcount:
            self._reload_cached_actions(scan_ids)

    def _reload_cached_actions(self, scan_ids: tuple[str, ...]) -> None:
        actions = self.session.scalars(
            select(DouyinLoginAction)
            .where(DouyinLoginAction.scan_id.in_(scan_ids))
            .execution_options(populate_existing=True)
        ).all()
        for action in actions:
            for attribute in ("created_at", "consumed_at"):
                value = getattr(action, attribute)
                if value is not None and value.tzinfo is None:
                    set_committed_value(action, attribute, _aware(value))

    def _clear_expired_inputs(self, scan_id: str, current: datetime) -> None:
        result = self.session.execute(
            update(DouyinLoginInput)
            .where(
                DouyinLoginInput.scan_id == scan_id,
                DouyinLoginInput.delivery_status == DELIVERY_QUEUED,
                DouyinLoginInput.consumed_at.is_(None),
                DouyinLoginInput.created_at <= current - OTP_LIFETIME,
            )
            .values(
                ciphertext=None,
                nonce=None,
                consumed_at=current,
                delivery_status=DELIVERY_FAILED,
                finished_at=current,
            )
            .execution_options(synchronize_session="fetch")
        )
        if result.rowcount:
            self._reload_cached_inputs((scan_id,))

    def _clear_pending_inputs(
        self,
        scan_id: str,
        current: datetime | None = None,
        *,
        exclude_input_id: int | None = None,
        finalize_claimed: bool = False,
    ) -> None:
        self._clear_pending_inputs_for_scans(
            (scan_id,),
            current if current is not None else self._now(),
            exclude_input_id=exclude_input_id,
            finalize_claimed=finalize_claimed,
        )

    def _clear_pending_inputs_for_scans(
        self,
        scan_ids: tuple[str, ...],
        current: datetime,
        *,
        exclude_input_id: int | None = None,
        finalize_claimed: bool = False,
    ) -> None:
        if not scan_ids:
            return
        queued_conditions = [
            DouyinLoginInput.scan_id.in_(scan_ids),
            DouyinLoginInput.delivery_status == DELIVERY_QUEUED,
            or_(
                DouyinLoginInput.ciphertext.is_not(None),
                DouyinLoginInput.nonce.is_not(None),
                DouyinLoginInput.consumed_at.is_(None),
            ),
        ]
        if exclude_input_id is not None:
            queued_conditions.append(DouyinLoginInput.id != exclude_input_id)
        result = self.session.execute(
            update(DouyinLoginInput)
            .where(*queued_conditions)
            .values(
                ciphertext=None,
                nonce=None,
                consumed_at=current,
                delivery_status=DELIVERY_FAILED,
                finished_at=current,
            )
            .execution_options(synchronize_session="fetch")
        )
        changed = bool(result.rowcount)
        if finalize_claimed:
            claimed = self.session.execute(
                update(DouyinLoginInput)
                .where(
                    DouyinLoginInput.scan_id.in_(scan_ids),
                    DouyinLoginInput.delivery_status == DELIVERY_CLAIMED,
                )
                .values(
                    delivery_status=DELIVERY_UNCERTAIN,
                    finished_at=current,
                )
                .execution_options(synchronize_session="fetch")
            )
            changed = changed or bool(claimed.rowcount)
        if changed:
            self._reload_cached_inputs(scan_ids)

    def _reload_cached_inputs(self, scan_ids: tuple[str, ...]) -> None:
        inputs = self.session.scalars(
            select(DouyinLoginInput)
            .where(DouyinLoginInput.scan_id.in_(scan_ids))
            .execution_options(populate_existing=True)
        ).all()
        for pending in inputs:
            for attribute in (
                "created_at",
                "consumed_at",
                "attempted_at",
                "finished_at",
            ):
                value = getattr(pending, attribute)
                if value is not None and value.tzinfo is None:
                    set_committed_value(pending, attribute, _aware(value))

    def _input_by_request_id(
        self, scan_id: str, request_id: str
    ) -> DouyinLoginInput | None:
        return self.session.scalar(
            select(DouyinLoginInput).where(
                DouyinLoginInput.scan_id == scan_id,
                DouyinLoginInput.request_id == request_id,
            )
        )

    def _active_text_input(
        self, scan_id: str
    ) -> DouyinLoginInput | None:
        return self.session.scalar(
            select(DouyinLoginInput)
            .where(
                DouyinLoginInput.scan_id == scan_id,
                DouyinLoginInput.delivery_status.in_(
                    (DELIVERY_QUEUED, DELIVERY_CLAIMED)
                ),
            )
            .order_by(DouyinLoginInput.id)
            .limit(1)
        )

    def _finish_text_interaction(
        self,
        scan_id: str,
        interaction_id: int,
        delivery_status: str,
    ) -> DouyinLoginInput:
        if delivery_status not in FINAL_DELIVERY_STATUSES:
            raise ValueError("final delivery status required")
        if isinstance(interaction_id, bool) or not isinstance(interaction_id, int):
            raise NotFound("interaction not found")
        pending = self.session.scalar(
            select(DouyinLoginInput).where(
                DouyinLoginInput.id == interaction_id,
                DouyinLoginInput.scan_id == scan_id,
            )
        )
        if pending is None:
            raise NotFound("interaction not found")
        if pending.delivery_status == delivery_status:
            return pending
        if pending.delivery_status != DELIVERY_CLAIMED:
            raise Conflict("interaction_state_conflict")
        current = self._now()
        result = self.session.execute(
            update(DouyinLoginInput)
            .where(
                DouyinLoginInput.id == pending.id,
                DouyinLoginInput.scan_id == scan_id,
                DouyinLoginInput.delivery_status == DELIVERY_CLAIMED,
            )
            .values(delivery_status=delivery_status, finished_at=current)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            persisted = self._reload_input(pending.id)
            if persisted.delivery_status == delivery_status:
                return persisted
            raise Conflict("interaction_state_conflict")
        set_committed_value(pending, "delivery_status", delivery_status)
        set_committed_value(pending, "finished_at", current)
        return pending

    def _finish_claimed_input(
        self,
        pending: DouyinLoginInput,
        delivery_status: str,
        current: datetime,
    ) -> None:
        self.session.execute(
            update(DouyinLoginInput)
            .where(
                DouyinLoginInput.id == pending.id,
                DouyinLoginInput.delivery_status == DELIVERY_CLAIMED,
            )
            .values(delivery_status=delivery_status, finished_at=current)
            .execution_options(synchronize_session=False)
        )
        set_committed_value(pending, "delivery_status", delivery_status)
        set_committed_value(pending, "finished_at", current)

    def _text_attempt_number(self, pending: DouyinLoginInput) -> int:
        return int(
            self.session.scalar(
                select(func.count(DouyinLoginInput.id)).where(
                    DouyinLoginInput.scan_id == pending.scan_id,
                    DouyinLoginInput.id <= pending.id,
                )
            )
            or 0
        )

    def _reload_input(self, interaction_id: int) -> DouyinLoginInput:
        pending = self.session.scalar(
            select(DouyinLoginInput)
            .where(DouyinLoginInput.id == interaction_id)
            .execution_options(populate_existing=True)
        )
        if pending is None:
            raise NotFound("interaction not found")
        for attribute in (
            "created_at",
            "consumed_at",
            "attempted_at",
            "finished_at",
        ):
            value = getattr(pending, attribute)
            if value is not None and value.tzinfo is None:
                set_committed_value(pending, attribute, _aware(value))
        return pending

    @staticmethod
    def _public_datetime(value: datetime | None) -> str | None:
        return _aware(value).isoformat() if value is not None else None

    def _reload(self, scan_id: str) -> DouyinLoginSession:
        scan = self.session.scalar(
            select(DouyinLoginSession)
            .where(DouyinLoginSession.id == scan_id)
            .execution_options(populate_existing=True)
        )
        if scan is None:
            raise NotFound("scan session not found")
        for attribute in ("expires_at", "created_at", "updated_at", "finished_at"):
            value = getattr(scan, attribute)
            if value is not None and value.tzinfo is None:
                set_committed_value(scan, attribute, _aware(value))
        return scan

    def _reload_cached_scans(self) -> None:
        for instance in list(self.session.identity_map.values()):
            if isinstance(instance, DouyinLoginSession):
                self._reload(instance.id)

    def _now(self) -> datetime:
        return _aware(self.now())

    @staticmethod
    def _status(scan: DouyinLoginSession) -> ScanStatus:
        return ScanStatus(scan.status)
