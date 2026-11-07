from __future__ import annotations

import json
from core.avatars import safe_avatar_url

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from core.account_profile import normalize_platform_user_id

from spark_console.credentials import CredentialError, CredentialPayload
from spark_console.crypto import CookieCipher
from spark_console.models import (
    DouyinAccount,
    DouyinAccountIdentity,
    DouyinContactIdentity,
    DouyinContactAvatar,
    DouyinConversation,
    DouyinLoginSession,
    ScanStatus,
    SparkTask,
    TaskRun,
    utc_now,
)
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.audits import AuditService


class AccountIdentityMismatch(Conflict):
    pass


class AccountIdentityAlreadyBound(Conflict):
    pass


class AccountReloginActive(Conflict):
    pass


class AccountIdentityUnavailable(Conflict):
    pass


class AccountService:
    # Account selectors are embedded in legacy edit forms. Keep the initial
    # projection bounded; the account management surface has its own API.
    LIST_PAGE_SIZE = 100

    def __init__(self, session: Session, cipher: CookieCipher, audit: AuditService):
        self.session = session
        self.cipher = cipher
        self.audit = audit

    def create(self, owner_id: str, display_name: str, cookies: bytes | str) -> DouyinAccount:
        name = self._validated_display_name(display_name)
        raw = cookies.encode("utf-8") if isinstance(cookies, str) else cookies
        try:
            parsed = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            raise ValidationError("Cookie 必须是有效的 JSON") from error
        if not isinstance(parsed, list) or not parsed:
            raise ValidationError("Cookie JSON 必须是非空数组")
        sealed = self.cipher.encrypt(raw)
        account = DouyinAccount(
            owner_user_id=owner_id,
            display_name=name,
            encrypted_cookies=sealed.ciphertext,
            cookie_nonce=sealed.nonce,
        )
        self.session.add(account)
        self.session.flush()
        self.audit.write(owner_id, "account.created", "douyin_account", account.id)
        return account

    def create_from_storage_state(
        self,
        owner_id: str,
        display_name: str,
        storage_state: dict,
        douyin_unique_id: str | None = None,
        conversation_names=(),
        contact_identities=(),
        *,
        platform_user_id: str | None = None,
    ) -> DouyinAccount:
        name = self._validated_display_name(display_name)
        normalized_unique_id = self._normalized_unique_id(douyin_unique_id)
        uid = normalize_platform_user_id(platform_user_id)
        if platform_user_id is not None and uid is None:
            raise AccountIdentityUnavailable("无法确认抖音账号身份，原登录资料未改动")
        if uid is None:
            self._ensure_identity_available(normalized_unique_id)

        sealed = self._seal_storage_state(storage_state)
        account = DouyinAccount(
            owner_user_id=owner_id,
            display_name=name,
            encrypted_cookies=sealed.ciphertext,
            cookie_nonce=sealed.nonce,
            cookie_version=2,
            validation_state="valid",
            last_verified_at=utc_now(),
        )
        self.session.add(account)
        self.session.flush()
        self.session.add(
            DouyinAccountIdentity(
                account_id=account.id, douyin_unique_id=normalized_unique_id,
            )
        )
        self.session.flush()
        if uid is not None:
            self._claim_platform_identity(account.id, uid)
        seen = set()
        for display_name in conversation_names:
            normalized_name = str(display_name).strip()
            if not normalized_name or normalized_name in seen:
                continue
            seen.add(normalized_name)
            self.session.add(
                DouyinConversation(
                    account_id=account.id,
                    display_name=normalized_name[:256],
                )
            )
        for identity in contact_identities:
            sec_uid = str(identity.sec_uid).strip()
            if not sec_uid:
                continue
            self.session.add(
                DouyinContactIdentity(
                    account_id=account.id,
                    sec_uid=sec_uid[:256],
                    short_id=_limited(identity.short_id, 64),
                    unique_id=_limited(identity.unique_id, 128),
                    nickname=_limited(identity.nickname, 256),
                    remark_name=_limited(identity.remark_name, 256),
                )
            )
        self.sync_contact_avatars(account.id, {
            identity.sec_uid: getattr(identity, "avatar_url", None) for identity in contact_identities
        })
        self.audit.write(owner_id, "account.created", "douyin_account", account.id)
        return account

    def sync_contact_avatars(self, account_id: str, avatars: dict) -> int:
        if not avatars:
            return 0
        known = set(self.session.scalars(select(DouyinContactIdentity.sec_uid).where(
            DouyinContactIdentity.account_id == account_id
        )))
        saved = 0
        for sec_uid, value in avatars.items():
            url = safe_avatar_url(value)
            if not url or sec_uid not in known:
                continue
            record = self.session.get(DouyinContactAvatar, (account_id, sec_uid))
            if record is None:
                self.session.add(DouyinContactAvatar(account_id=account_id, sec_uid=sec_uid, avatar_url=url))
            else:
                record.avatar_url = url
                record.updated_at = utc_now()
            saved += 1
        return saved

    def record_verified(
        self,
        account_id: str,
        *,
        expected_ciphertext: bytes,
        expected_nonce: bytes,
        expected_version: int,
        storage_state: dict,
    ) -> bool:
        """Refresh credentials without overwriting a concurrent re-login."""
        sealed = self._seal_storage_state(storage_state)
        verified_at = utc_now()
        result = self.session.execute(
            update(DouyinAccount)
            .where(
                DouyinAccount.id == account_id,
                DouyinAccount.encrypted_cookies == expected_ciphertext,
                DouyinAccount.cookie_nonce == expected_nonce,
                DouyinAccount.cookie_version == expected_version,
            )
            .values(
                encrypted_cookies=sealed.ciphertext,
                cookie_nonce=sealed.nonce,
                cookie_version=2,
                validation_state="valid",
                last_verified_at=verified_at,
                updated_at=verified_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            from spark_console.services.incidents import observe
            observe(self.session, account_id, 'valid', now=verified_at)
            return True
        return False

    def mark_invalid(self, account_id: str, actor_id: str | None = None) -> int:
        account = self.session.get(DouyinAccount, account_id)
        if account is None:
            raise NotFound("account not found")
        account.validation_state = "invalid"
        from spark_console.services.incidents import observe
        observe(self.session, account_id, 'invalid')
        self.audit.write(
            actor_id or account.owner_user_id,
            "account.credentials_invalid",
            "douyin_account",
            account_id,
        )
        return 0

    def mark_invalid_if_current(
        self,
        account_id: str,
        *,
        expected_ciphertext: bytes,
        expected_nonce: bytes,
        expected_version: int,
        actor_id: str,
    ) -> bool:
        """Invalidate only the credential snapshot that produced the failure."""
        invalidated = self.session.execute(
            update(DouyinAccount)
            .where(
                DouyinAccount.id == account_id,
                DouyinAccount.encrypted_cookies == expected_ciphertext,
                DouyinAccount.cookie_nonce == expected_nonce,
                DouyinAccount.cookie_version == expected_version,
            )
            .values(validation_state="invalid")
            .execution_options(synchronize_session=False)
        )
        if invalidated.rowcount != 1:
            return False
        from spark_console.services.incidents import observe
        observe(self.session, account_id, 'invalid')
        self.audit.write(
            actor_id,
            "account.credentials_invalid",
            "douyin_account",
            account_id,
        )
        return True

    def reauthenticate_owned(
        self,
        owner_id: str,
        account_id: str,
        storage_state: dict,
        douyin_unique_id: str | None = None,
        *,
        platform_user_id: str | None = None,
    ) -> DouyinAccount:
        account = self.get_owned(owner_id, account_id)
        identity = self.session.get(DouyinAccountIdentity, account.id)
        normalized_unique_id = self._normalized_unique_id(douyin_unique_id)
        uid = normalize_platform_user_id(platform_user_id)
        expected_uid = identity.douyin_user_id if identity is not None else None
        if uid is None or expected_uid is None:
            raise AccountIdentityUnavailable("无法确认原抖音账号身份，原登录资料未改动，请稍后重试")
        if expected_uid != uid:
            raise AccountIdentityMismatch(
                "扫码登录的抖音账号与所选账号不一致"
            )
        self._claim_platform_identity(account.id, uid)
        sealed = self._seal_storage_state(storage_state)
        account.encrypted_cookies = sealed.ciphertext
        account.cookie_nonce = sealed.nonce
        account.cookie_version = 2
        account.validation_state = "valid"
        account.last_verified_at = utc_now()
        from spark_console.services.account_health import recovered
        recovered(self.session, account)
        if normalized_unique_id is not None:
            identity.douyin_unique_id = normalized_unique_id
        self.session.flush()
        self.audit.write(
            owner_id, "account.reauthenticated", "douyin_account", account.id
        )
        return account

    def record_identity_if_current(self, account_id, expected, platform_user_id):
        """Enroll a legacy identity only from its unchanged original credentials."""
        uid = normalize_platform_user_id(platform_user_id)
        if uid is None:
            raise AccountIdentityUnavailable("无法确认原抖音账号身份，原登录资料未改动，请稍后重试")
        matched = self.session.execute(update(DouyinAccount).where(
            DouyinAccount.id == account_id,
            DouyinAccount.encrypted_cookies == expected[0],
            DouyinAccount.cookie_nonce == expected[1],
            DouyinAccount.cookie_version == expected[2],
        ).values(updated_at=DouyinAccount.updated_at))
        if matched.rowcount != 1:
            raise Conflict("账号登录资料已更新，请重新打开验证")
        self._claim_platform_identity(account_id, uid)

    def _claim_platform_identity(self, account_id, uid):
        identity = self.session.get(DouyinAccountIdentity, account_id)
        if identity is not None and identity.douyin_user_id not in (None, uid):
            raise AccountIdentityMismatch("当前抖音账号与原账号不一致，原资料未改动")
        if self.session.scalar(select(DouyinAccountIdentity.account_id).where(
            DouyinAccountIdentity.douyin_user_id == uid,
            DouyinAccountIdentity.account_id != account_id,
        ).limit(1)) is not None:
            raise AccountIdentityAlreadyBound("该抖音账号已经绑定")
        try:
            with self.session.begin_nested():
                if identity is None:
                    identity = DouyinAccountIdentity(account_id=account_id)
                    self.session.add(identity)
                identity.douyin_user_id = uid
                self.session.flush()
        except IntegrityError:
            raise AccountIdentityAlreadyBound("该抖音账号已经绑定") from None

    def require_legacy_identities_ready(self):
        # Protect accounts currently providing service during cutover. Dormant
        # legacy records are preserved; the real send path verifies their
        # identity before any future message can be submitted.
        pending = self.session.scalar(select(DouyinAccount.id).outerjoin(
            DouyinAccountIdentity, DouyinAccountIdentity.account_id == DouyinAccount.id
        ).where(
            DouyinAccountIdentity.douyin_user_id.is_(None),
            exists().where(SparkTask.douyin_account_id == DouyinAccount.id, SparkTask.enabled.is_(True)),
        ).limit(1))
        if pending is not None:
            raise AccountIdentityUnavailable("账号身份核对暂未完成，请稍后重试或联系客服")

    def _ensure_identity_available(
        self,
        douyin_unique_id: str | None,
        *,
        excluding_account_id: str | None = None,
    ) -> None:
        if douyin_unique_id is None:
            return
        statement = select(DouyinAccountIdentity.account_id).where(
            DouyinAccountIdentity.douyin_unique_id == douyin_unique_id
        )
        if excluding_account_id is not None:
            statement = statement.where(
                DouyinAccountIdentity.account_id != excluding_account_id
            )
        if self.session.scalar(statement.limit(1)) is not None:
            raise AccountIdentityAlreadyBound("该抖音账号已经绑定")

    @staticmethod
    def _normalized_unique_id(value: str | None) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        if len(text) > 64:
            raise ValidationError("抖音号不能超过 64 个字符")
        return text

    def rename_owned(
        self, owner_id: str, account_id: str, display_name: str
    ) -> DouyinAccount:
        account = self.get_owned(owner_id, account_id)
        account.display_name = self._validated_display_name(display_name)
        from spark_console.models import DouyinAccountProfile
        profile = self.session.get(DouyinAccountProfile, account.id)
        if profile is None:
            profile = DouyinAccountProfile(account_id=account.id)
            self.session.add(profile)
        profile.custom_remark = True
        self.session.flush()
        self.audit.write(owner_id, "account.renamed", "douyin_account", account.id)
        return account

    def get_owned(self, owner_id: str, account_id: str) -> DouyinAccount:
        account = self.session.scalar(
            select(DouyinAccount).where(
                DouyinAccount.id == account_id,
                DouyinAccount.owner_user_id == owner_id,
            )
        )
        if account is None:
            raise NotFound("account not found")
        return account

    def list_owned(self, owner_id: str) -> list[dict[str, str]]:
        accounts = self.session.scalars(
            select(DouyinAccount)
            .where(DouyinAccount.owner_user_id == owner_id)
            .order_by(DouyinAccount.created_at)
            .limit(self.LIST_PAGE_SIZE)
        ).all()
        return [
            {"id": item.id, "display_name": item.display_name, "validation_state": item.validation_state}
            for item in accounts
        ]

    def decrypt_for_worker(self, account_id: str) -> bytearray:
        account = self.session.get(DouyinAccount, account_id)
        if account is None:
            raise NotFound("account not found")
        return bytearray(self.cipher.decrypt(account.encrypted_cookies, account.cookie_nonce))

    def delete_owned(self, owner_id: str, account_id: str) -> None:
        account = self.get_owned(owner_id, account_id)
        relogin = self.session.scalar(
            select(DouyinLoginSession.id)
            .where(
                DouyinLoginSession.account_id == account.id,
                DouyinLoginSession.slot.is_not(None),
                DouyinLoginSession.status.in_(
                    (
                        ScanStatus.QUEUED,
                        ScanStatus.LOADING_QR,
                        ScanStatus.AWAITING_SCAN,
                        ScanStatus.CONFIRMING,
                    )
                ),
            )
            .limit(1)
        )
        if relogin is not None:
            raise AccountReloginActive(
                "该账号正在重新登录，请先完成或取消登录后再删除"
            )
        running = self.session.scalar(
            select(TaskRun.id)
            .join(SparkTask, SparkTask.id == TaskRun.task_id)
            .where(
                SparkTask.douyin_account_id == account.id,
                TaskRun.status == "running",
            )
            .limit(1)
        )
        if running is not None:
            raise Conflict("该账号有任务正在执行，请完成后再删除")
        self.session.execute(
            update(SparkTask)
            .where(SparkTask.douyin_account_id == account.id)
            .values(enabled=False, douyin_account_id=None)
        )
        account.encrypted_cookies = b""
        account.cookie_nonce = b""
        self.session.flush()
        self.session.delete(account)
        self.audit.write(owner_id, "account.deleted", "douyin_account", account_id)

    def _seal_storage_state(self, storage_state: dict):
        envelope = {"version": 2, "storage_state": storage_state}
        try:
            raw = json.dumps(
                envelope, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            CredentialPayload.parse(raw, 2)
        except (CredentialError, TypeError, ValueError, UnicodeEncodeError):
            raise ValidationError("浏览器凭据格式无效") from None
        return self.cipher.encrypt(raw)

    @staticmethod
    def _validated_display_name(display_name: str) -> str:
        name = display_name.strip()
        if not name or len(name) > 64:
            raise ValidationError("账号名称须为 1–64 个字符")
        return name


def _limited(value, length: int) -> str | None:
    text = str(value or "").strip()
    return text[:length] or None
