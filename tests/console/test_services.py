import hashlib
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from core.web_chat import DouyinUserIdentity
from spark_console.crypto import CookieCipher
from spark_console.db import create_schema
from spark_console.models import (
    AuditEvent,
    DouyinAccount,
    DouyinAccountIdentity,
    DouyinContactIdentity,
    DouyinContactAvatar,
    DouyinLoginSession,
    SparkTask,
    SparkTaskTargetIdentity,
    TaskRun,
    User,
)
from spark_console.security import PasswordService
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.accounts import AccountService
from spark_console.services.accounts import (
    AccountIdentityAlreadyBound,
    AccountIdentityMismatch,
)
from spark_console.services.audits import AuditService
from spark_console.services.scan_sessions import ScanSessionService
from spark_console.services.tasks import TaskService
from spark_console.services.users import UserService


def _storage_state(marker: str) -> dict[str, object]:
    return {
        "cookies": [
            {
                "name": "sid",
                "value": marker,
                "domain": ".douyin.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        create_schema(self.engine)
        self.session = Session(self.engine)
        self.audit = AuditService(self.session)
        self.users = UserService(self.session, PasswordService(), self.audit)
        self.accounts = AccountService(
            self.session, CookieCipher(b"c" * 32), self.audit
        )
        self.tasks = TaskService(self.session, self.accounts, self.audit)
        self.owner, _ = self.users.create("friend", "Temporary-123!", "user")
        self.other, _ = self.users.create("other", "Temporary-456!", "user")
        self.account = self.accounts.create(
            self.owner.id, "我的账号", b'[{"name":"sid","value":"secret"}]'
        )
        self.session.flush()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_contact_avatars_are_saved_for_new_binding_and_scoped_to_known_ids(self):
        url = "https://p3.douyinpic.com/avatar.jpeg"
        account = self.accounts.create_from_storage_state(
            self.owner.id, "Avatar account", _storage_state("avatar-test"),
            contact_identities=(DouyinUserIdentity(sec_uid="stable", nickname="Friend", avatar_url=url),),
        )
        self.session.flush()
        self.assertEqual(url, self.session.get(DouyinContactAvatar, (account.id, "stable")).avatar_url)
        self.assertEqual(0, self.accounts.sync_contact_avatars(self.account.id, {"stable": url}))
        self.assertEqual(0, self.accounts.sync_contact_avatars(account.id, {"unknown": url, "stable": "https://evil.test/a"}))
        self.assertEqual(0, self.accounts.sync_contact_avatars(account.id, {"stable": None}))
        updated = "https://p9.byteimg.com/new-avatar.jpeg"
        self.assertEqual(1, self.accounts.sync_contact_avatars(account.id, {"stable": updated}))
        self.session.flush()
        self.assertEqual(updated, self.session.get(DouyinContactAvatar, (account.id, "stable")).avatar_url)
        self.assertIsNone(self.session.get(DouyinContactAvatar, (self.account.id, "stable")))

    def test_user_password_requires_the_shared_policy(self):
        user, supplied = self.users.create(
            "long.password@example.com", "Long-Password-123"
        )

        self.assertEqual("Long-Password-123", supplied)
        self.assertEqual("long.password@example.com", user.username)
        self.assertTrue(PasswordService().verify(user.password_hash, supplied))
        with self.assertRaisesRegex(ValidationError, "密码至少 12 位"):
            self.users.create("short-password", "A1")

    def test_omitted_password_still_generates_a_temporary_password(self):
        user, temporary = self.users.create("generated-password")

        self.assertEqual(18, len(temporary))
        self.assertTrue(PasswordService().verify(user.password_hash, temporary))

    def test_admin_can_promote_user_but_last_admin_cannot_be_demoted(self):
        admin, _ = self.users.create("role-admin", "Temporary-789!", "admin")
        self.users.set_role(admin.id, self.owner.id, "admin")
        self.assertEqual("admin", self.owner.role)
        self.users.set_role(admin.id, self.owner.id, "user")
        self.assertEqual("user", self.owner.role)
        with self.assertRaisesRegex(Conflict, "至少需要保留一名管理员"):
            self.users.set_role("external-actor", admin.id, "user")
        event = self.session.scalar(
            select(AuditEvent).where(AuditEvent.action == "user.role_changed")
        )
        self.assertIsNotNone(event)

    def test_user_cannot_read_or_delete_another_users_task(self):
        task = self.tasks.create(
            self.owner.id, self.account.id, "朋友", "09:00", "今日火花"
        )
        with self.assertRaises(NotFound):
            self.tasks.get_owned(self.other.id, task.id)
        with self.assertRaises(NotFound):
            self.tasks.delete_owned(self.other.id, task.id)

    def test_cookie_is_encrypted_and_never_returned_by_list(self):
        self.session.flush()
        stored = self.session.get(DouyinAccount, self.account.id)
        cookie_marker = b"secret"
        self.assertFalse(cookie_marker in stored.encrypted_cookies)
        public = self.accounts.list_owned(self.owner.id)
        self.assertEqual(1, len(public))
        self.assertEqual({"id", "display_name", "validation_state"}, set(public[0]))
        self.assertEqual(self.account.id, public[0]["id"])
        self.assertEqual("我的账号", public[0]["display_name"])
        self.assertEqual("unknown", public[0]["validation_state"])

    def test_storage_state_is_encrypted_with_identity_and_safe_projection(self):
        state = {
            "cookies": [
                {
                    "name": "sid",
                    "value": "storage-cookie-marker",
                    "domain": ".douyin.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
            ],
            "origins": [
                {
                    "origin": "https://www.douyin.com",
                    "localStorage": [
                        {"name": "session", "value": "local-storage-marker"}
                    ],
                }
            ],
        }

        account = self.accounts.create_from_storage_state(
            self.owner.id, " 扫码账号 ", state, " douyin-123 "
        )
        self.session.flush()

        stored = self.session.get(DouyinAccount, account.id)
        identity = self.session.get(DouyinAccountIdentity, account.id)
        encrypted = stored.encrypted_cookies
        cookie_marker = b"storage-cookie-marker"
        storage_marker = b"local-storage-marker"
        self.assertFalse(cookie_marker in encrypted)
        self.assertFalse(storage_marker in encrypted)
        self.assertEqual(2, stored.cookie_version)
        self.assertEqual("valid", stored.validation_state)
        self.assertIsNotNone(stored.last_verified_at)
        self.assertEqual("douyin-123", identity.douyin_unique_id)
        plaintext = self.accounts.cipher.decrypt(
            stored.encrypted_cookies, stored.cookie_nonce
        )
        self.assertEqual(
            "2275bd33a9235dd8a676e0a62ce3b965a24e5a28054245ec6e85c2c65717ffd1",
            hashlib.sha256(plaintext).hexdigest(),
        )
        projected = next(
            item for item in self.accounts.list_owned(self.owner.id) if item["id"] == account.id
        )
        self.assertEqual(
            {"id", "display_name", "validation_state"}, set(projected)
        )

        audits = " ".join(
            f"{event.action} {event.detail or ''}"
            for event in self.session.scalars(select(AuditEvent)).all()
        )
        self.assertFalse(cookie_marker.decode() in audits)
        self.assertFalse(storage_marker.decode() in audits)

    def test_stable_contact_identity_is_saved_and_bound_to_new_task(self):
        state = {
            "cookies": [
                {
                    "name": "sid",
                    "value": "secret",
                    "domain": ".douyin.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
            ],
            "origins": [],
        }
        account = self.accounts.create_from_storage_state(
            self.owner.id,
            "稳定账号",
            state,
            contact_identities=(
                DouyinUserIdentity(
                    sec_uid="stable-user-id",
                    short_id="123456",
                    unique_id="search-id",
                    nickname="新的昵称",
                    remark_name="我的备注",
                ),
            ),
        )

        task = self.tasks.create(
            self.owner.id,
            account.id,
            "我的备注",
            "09:00",
            "今日火花",
            target_sec_uid="stable-user-id",
        )
        self.session.flush()

        contact = self.session.get(
            DouyinContactIdentity, (account.id, "stable-user-id")
        )
        binding = self.session.get(SparkTaskTargetIdentity, task.id)
        self.assertEqual("新的昵称", contact.nickname)
        self.assertEqual("我的备注", contact.remark_name)
        self.assertEqual("stable-user-id", binding.sec_uid)

    def test_create_many_deduplicates_targets_in_order_and_keeps_identity(self):
        self.session.add_all(
            [
                DouyinContactIdentity(
                    account_id=self.account.id,
                    sec_uid="stable-a",
                    nickname="好友甲",
                ),
                DouyinContactIdentity(
                    account_id=self.account.id,
                    sec_uid="stable-b",
                    nickname="好友乙",
                ),
            ]
        )
        self.session.flush()

        tasks = self.tasks.create_many(
            self.owner.id,
            self.account.id,
            (
                (" 好友甲 ", None),
                ("好友甲", "stable-a"),
                ("好友甲", "stable-a"),
                ("好友乙", "stable-b"),
                ("好友乙的新昵称", "stable-b"),
                ("好友乙的新昵称", None),
            ),
            "09:00",
            "今日火花",
        )
        self.session.flush()

        self.assertEqual(["好友甲", "好友乙"], [task.target_name for task in tasks])
        self.assertEqual(
            ["stable-a", "stable-b"],
            [
                self.session.get(SparkTaskTargetIdentity, task.id).sec_uid
                for task in tasks
            ],
        )
        created_events = self.session.scalars(
            select(AuditEvent).where(AuditEvent.action == "task.created")
        ).all()
        self.assertEqual(2, len(created_events))

    def test_create_many_allows_same_name_for_distinct_stable_identities(self):
        self.session.add_all(
            [
                DouyinContactIdentity(
                    account_id=self.account.id,
                    sec_uid="stable-a",
                    nickname="同名好友",
                ),
                DouyinContactIdentity(
                    account_id=self.account.id,
                    sec_uid="stable-b",
                    nickname="同名好友",
                ),
            ]
        )
        self.session.flush()

        tasks = self.tasks.create_many(
            self.owner.id,
            self.account.id,
            (("同名好友", "stable-a"), ("同名好友", "stable-b")),
            "09:00",
            "今日火花",
        )
        self.session.flush()

        self.assertEqual(2, len(tasks))
        self.assertEqual(["同名好友", "同名好友"], [task.target_name for task in tasks])
        self.assertEqual(2, len({task.target_key for task in tasks}))

    def test_existing_stable_identity_conflicts_after_nickname_change(self):
        self.session.add(
            DouyinContactIdentity(
                account_id=self.account.id,
                sec_uid="stable-contact",
                nickname="旧昵称",
            )
        )
        self.session.flush()
        self.tasks.create(
            self.owner.id,
            self.account.id,
            "旧昵称",
            "09:00",
            "今日火花",
            target_sec_uid="stable-contact",
        )

        with self.assertRaisesRegex(Conflict, "已有续火任务"):
            self.tasks.create(
                self.owner.id,
                self.account.id,
                "新昵称",
                "09:00",
                "今日火花",
                target_sec_uid="stable-contact",
            )

    def test_edit_allows_same_name_for_distinct_identities_but_rejects_same_identity(self):
        self.session.add_all(
            [
                DouyinContactIdentity(
                    account_id=self.account.id,
                    sec_uid="stable-a",
                    nickname="好友甲",
                ),
                DouyinContactIdentity(
                    account_id=self.account.id,
                    sec_uid="stable-b",
                    nickname="好友乙",
                ),
            ]
        )
        self.session.flush()
        first = self.tasks.create(
            self.owner.id,
            self.account.id,
            "好友甲",
            "09:00",
            "今日火花",
            target_sec_uid="stable-a",
        )
        second = self.tasks.create(
            self.owner.id,
            self.account.id,
            "好友乙",
            "09:00",
            "今日火花",
            target_sec_uid="stable-b",
        )

        updated = self.tasks.update_owned(
            self.owner.id,
            second.id,
            self.account.id,
            "好友甲",
            "09:00",
            "新的消息",
            target_sec_uid="stable-b",
        )
        self.assertEqual("好友甲", updated.target_name)
        self.assertNotEqual(first.target_key, updated.target_key)

        with self.assertRaisesRegex(Conflict, "启用任务已存在"):
            self.tasks.update_owned(
                self.owner.id,
                second.id,
                self.account.id,
                "好友甲的新昵称",
                "09:00",
                "新的消息",
                target_sec_uid="stable-a",
            )

    def test_target_name_accepts_256_characters_and_rejects_257(self):
        accepted = "友" * 256
        task = self.tasks.create(
            self.owner.id,
            self.account.id,
            accepted,
            "09:00",
            "今日火花",
        )
        self.assertEqual(accepted, task.target_name)

        with self.assertRaisesRegex(ValidationError, "1–256"):
            self.tasks.create(
                self.owner.id,
                self.account.id,
                "友" * 257,
                "10:00",
                "今日火花",
            )

    def test_target_identity_accepts_256_characters_and_rejects_257(self):
        accepted = "s" * 256
        self.session.add(
            DouyinContactIdentity(
                account_id=self.account.id,
                sec_uid=accepted,
                nickname="边界好友",
            )
        )
        self.session.flush()
        task = self.tasks.create(
            self.owner.id,
            self.account.id,
            "边界好友",
            "09:00",
            "今日火花",
            target_sec_uid=accepted,
        )
        binding = self.session.get(SparkTaskTargetIdentity, task.id)
        self.assertEqual(accepted, binding.sec_uid)

        with self.assertRaisesRegex(ValidationError, "不得超过 256"):
            self.tasks.create(
                self.owner.id,
                self.account.id,
                "超长身份好友",
                "10:00",
                "今日火花",
                target_sec_uid="s" * 257,
            )
        with self.assertRaisesRegex(ValidationError, "不得超过 256"):
            self.tasks.update_owned(
                self.owner.id,
                task.id,
                self.account.id,
                "边界好友",
                "09:00",
                "今日火花",
                target_sec_uid="s" * 257,
            )

    def test_create_many_prevalidates_every_identity_without_partial_creation(self):
        self.session.add(
            DouyinContactIdentity(
                account_id=self.account.id,
                sec_uid="valid-contact",
                nickname="有效好友",
            )
        )
        self.session.flush()

        with self.assertRaisesRegex(ValidationError, "不属于当前抖音账号"):
            self.tasks.create_many(
                self.owner.id,
                self.account.id,
                (
                    ("有效好友", "valid-contact"),
                    ("无效好友", "missing-contact"),
                ),
                "09:00",
                "今日火花",
            )

        self.assertEqual(
            [],
            self.session.scalars(
                select(SparkTask).where(SparkTask.owner_user_id == self.owner.id)
            ).all(),
        )

    def test_create_many_existing_conflict_does_not_create_other_targets(self):
        existing = self.tasks.create(
            self.owner.id, self.account.id, "已有好友", "09:00", "今日火花"
        )
        self.session.flush()

        with self.assertRaisesRegex(Conflict, "已有续火任务"):
            self.tasks.create_many(
                self.owner.id,
                self.account.id,
                (("新好友", None), ("已有好友", None)),
                "09:00",
                "今日火花",
            )

        tasks = self.session.scalars(
            select(SparkTask).where(SparkTask.owner_user_id == self.owner.id)
        ).all()
        self.assertEqual([existing.id], [task.id for task in tasks])

    def test_refreshed_storage_state_uses_cas_and_cannot_overwrite_relogin(self):
        account = self.accounts.create_from_storage_state(
            self.owner.id, "健康账号", _storage_state("initial-marker")
        )
        self.session.flush()
        initial = (
            bytes(account.encrypted_cookies),
            bytes(account.cookie_nonce),
            account.cookie_version,
        )

        self.assertTrue(
            self.accounts.record_verified(
                account.id,
                expected_ciphertext=initial[0],
                expected_nonce=initial[1],
                expected_version=initial[2],
                storage_state=_storage_state("refreshed-marker"),
            )
        )
        self.session.expire_all()
        refreshed = self.session.get(DouyinAccount, account.id)
        refreshed_snapshot = (
            bytes(refreshed.encrypted_cookies),
            bytes(refreshed.cookie_nonce),
            refreshed.cookie_version,
        )
        plaintext = self.accounts.cipher.decrypt(
            refreshed.encrypted_cookies, refreshed.cookie_nonce
        )
        self.assertIn(b"refreshed-marker", plaintext)
        self.assertEqual("valid", refreshed.validation_state)
        self.assertIsNotNone(refreshed.last_verified_at)

        self.accounts.reauthenticate_owned(
            self.owner.id,
            account.id,
            _storage_state("newer-relogin-marker"),
        )
        self.session.flush()
        self.assertFalse(
            self.accounts.record_verified(
                account.id,
                expected_ciphertext=refreshed_snapshot[0],
                expected_nonce=refreshed_snapshot[1],
                expected_version=refreshed_snapshot[2],
                storage_state=_storage_state("stale-worker-marker"),
            )
        )
        self.session.expire_all()
        current = self.session.get(DouyinAccount, account.id)
        plaintext = self.accounts.cipher.decrypt(
            current.encrypted_cookies, current.cookie_nonce
        )
        self.assertIn(b"newer-relogin-marker", plaintext)
        self.assertNotIn(b"stale-worker-marker", plaintext)

    def test_relogin_rejects_a_different_or_unverifiable_douyin_identity(self):
        account = self.accounts.create_from_storage_state(
            self.owner.id,
            "固定身份账号",
            _storage_state("original-identity-marker"),
            "douyin-account-a",
        )
        self.session.flush()
        original_ciphertext = bytes(account.encrypted_cookies)

        for scanned_identity in ("douyin-account-b", None):
            with self.subTest(scanned_identity=scanned_identity):
                with self.assertRaises(AccountIdentityMismatch):
                    self.accounts.reauthenticate_owned(
                        self.owner.id,
                        account.id,
                        _storage_state("wrong-account-marker"),
                        scanned_identity,
                    )
                self.session.expire(account)
                self.assertEqual(original_ciphertext, account.encrypted_cookies)

    def test_same_douyin_identity_cannot_be_bound_twice(self):
        self.accounts.create_from_storage_state(
            self.owner.id,
            "第一个账号",
            _storage_state("first-identity-marker"),
            "same-douyin-account",
        )
        self.session.flush()

        with self.assertRaises(AccountIdentityAlreadyBound):
            self.accounts.create_from_storage_state(
                self.other.id,
                "重复账号",
                _storage_state("duplicate-identity-marker"),
                "same-douyin-account",
            )

    def test_task_rejects_contact_identity_owned_by_another_account(self):
        other_account = self.accounts.create(
            self.owner.id, "其他账号", b'[{"name":"sid","value":"other"}]'
        )
        self.session.add(
            DouyinContactIdentity(
                account_id=other_account.id,
                sec_uid="other-account-contact",
                nickname="不属于当前账号",
            )
        )
        self.session.flush()

        with self.assertRaises(ValidationError):
            self.tasks.create(
                self.owner.id,
                self.account.id,
                "不属于当前账号",
                "09:00",
                "今日火花",
                target_sec_uid="other-account-contact",
            )

    def test_owner_can_update_task_schedule_message_and_stable_target(self):
        self.session.add(
            DouyinContactIdentity(
                account_id=self.account.id,
                sec_uid="updated-stable-id",
                nickname="更新后的好友",
            )
        )
        self.session.flush()
        task = self.tasks.create(
            self.owner.id, self.account.id, "旧好友", "09:00", "旧消息"
        )
        task.enabled = False

        updated = self.tasks.update_owned(
            self.owner.id,
            task.id,
            self.account.id,
            "更新后的好友",
            "21:30",
            "更新后的消息",
            target_sec_uid="updated-stable-id",
        )
        self.session.flush()

        binding = self.session.get(SparkTaskTargetIdentity, task.id)
        self.assertEqual("更新后的好友", updated.target_name)
        self.assertEqual("21:30", updated.send_time)
        self.assertEqual("更新后的消息", updated.message_template)
        self.assertFalse(updated.enabled)
        self.assertEqual("updated-stable-id", binding.sec_uid)
        local_next = updated.next_run_at.replace(tzinfo=timezone.utc).astimezone(
            ZoneInfo("Asia/Shanghai")
        )
        self.assertEqual((21, 30), (local_next.hour, local_next.minute))

    def test_invalid_account_cannot_create_or_reenable_task(self):
        invalid = self.accounts.create_from_storage_state(
            self.owner.id, "失效账号", _storage_state("expired-marker")
        )
        invalid.validation_state = "invalid"
        self.session.flush()

        with self.assertRaisesRegex(ValidationError, "重新登录"):
            self.tasks.create(
                self.owner.id, invalid.id, "朋友", "11:00", "今日火花"
            )

        task = self.tasks.create(
            self.owner.id, self.account.id, "朋友", "11:00", "今日火花"
        )
        task.enabled = False
        self.account.validation_state = "invalid"
        self.session.flush()
        with self.assertRaisesRegex(ValidationError, "重新登录"):
            self.tasks.set_enabled_owned(self.owner.id, task.id, True)

    def test_running_task_blocks_task_account_and_user_deletion(self):
        task = self.tasks.create(
            self.owner.id, self.account.id, "运行中的朋友", "12:00", "今日火花"
        )
        self.session.flush()
        self.session.add(
            TaskRun(
                task_id=task.id,
                scheduled_for=datetime.now(timezone.utc),
                status="running",
                stage="sending",
            )
        )
        self.session.flush()

        with self.assertRaisesRegex(Conflict, "正在执行"):
            self.tasks.delete_owned(self.owner.id, task.id)
        with self.assertRaisesRegex(Conflict, "正在执行"):
            self.accounts.delete_owned(self.owner.id, self.account.id)
        with self.assertRaisesRegex(Conflict, "正在执行"):
            self.users.delete(self.other.id, self.owner.id, self.owner.username)

    def test_active_relogin_blocks_account_deletion_and_keeps_target(self):
        scan = ScanSessionService(self.session).start(
            self.owner.id, self.account.id
        )

        with self.assertRaisesRegex(Conflict, "重新登录"):
            self.accounts.delete_owned(self.owner.id, self.account.id)

        self.assertIsNotNone(self.session.get(DouyinAccount, self.account.id))
        persisted = self.session.get(DouyinLoginSession, scan.id)
        self.assertEqual(self.account.id, persisted.account_id)
        self.assertIsNotNone(persisted.slot)

    def test_updating_to_manual_target_removes_stale_identity_binding(self):
        self.session.add(
            DouyinContactIdentity(
                account_id=self.account.id,
                sec_uid="old-stable-id",
                nickname="旧好友",
            )
        )
        self.session.flush()
        task = self.tasks.create(
            self.owner.id,
            self.account.id,
            "旧好友",
            "09:00",
            "旧消息",
            target_sec_uid="old-stable-id",
        )

        self.tasks.update_owned(
            self.owner.id,
            task.id,
            self.account.id,
            "手动输入好友",
            "10:15",
            "新消息",
        )
        self.session.flush()

        self.assertIsNone(self.session.get(SparkTaskTargetIdentity, task.id))

    def test_storage_state_rejects_empty_cookies_before_creating_account(self):
        before = len(self.session.scalars(select(DouyinAccount)).all())

        with self.assertRaises(ValidationError):
            self.accounts.create_from_storage_state(
                self.owner.id,
                "扫码账号",
                {"cookies": [], "origins": []},
            )

        self.assertEqual(before, len(self.session.scalars(select(DouyinAccount)).all()))

    def test_storage_state_rejects_playwright_invalid_domain_before_encryption(self):
        before = len(self.session.scalars(select(DouyinAccount)).all())
        invalid_domains = (
            "\u0301a.com",
            "foo%3Abar",
            "@",
            "#",
            "?",
            "999.999.999",
            "256.1.1.1.",
        )

        with patch.object(
            self.accounts.cipher,
            "encrypt",
            side_effect=AssertionError("encryption boundary crossed"),
        ):
            for case, domain in enumerate(invalid_domains):
                with self.subTest(case=case):
                    state = {
                        "cookies": [
                            {
                                "name": "probe",
                                "value": "x",
                                "domain": domain,
                                "path": "/",
                                "expires": -1,
                                "httpOnly": True,
                                "secure": True,
                                "sameSite": "Lax",
                            }
                        ],
                        "origins": [],
                    }
                    with self.assertRaises(ValidationError):
                        self.accounts.create_from_storage_state(
                            self.owner.id, "扫码账号", state
                        )

        after = len(self.session.scalars(select(DouyinAccount)).all())
        self.assertEqual(before, after)

    def test_owner_can_rename_account_but_another_user_cannot(self):
        renamed = self.accounts.rename_owned(
            self.owner.id, self.account.id, " 生活号 "
        )

        self.assertEqual("生活号", renamed.display_name)
        rename_events = self.session.scalars(
            select(AuditEvent).where(AuditEvent.action == "account.renamed")
        ).all()
        self.assertEqual(1, len(rename_events))
        self.assertTrue(all(event.detail is None for event in rename_events))
        with self.assertRaises(NotFound):
            self.accounts.rename_owned(self.other.id, self.account.id, "越权名称")
        with self.assertRaises(ValidationError):
            self.accounts.rename_owned(self.owner.id, self.account.id, " ")

    def test_deleting_account_erases_cookie_and_disables_tasks(self):
        task = self.tasks.create(
            self.owner.id, self.account.id, "朋友", "09:00", "今日火花"
        )
        self.session.flush()
        self.accounts.delete_owned(self.owner.id, self.account.id)
        self.session.flush()
        self.assertIsNone(self.session.get(DouyinAccount, self.account.id))
        retained = self.session.get(SparkTask, task.id)
        self.assertFalse(retained.enabled)
        self.assertIsNone(retained.douyin_account_id)

    def test_validation_rejects_bad_time_and_empty_message(self):
        with self.assertRaises(ValidationError):
            self.tasks.create(self.owner.id, self.account.id, "朋友", "25:00", "x")
        with self.assertRaises(ValidationError):
            self.tasks.create(self.owner.id, self.account.id, "朋友", "09:00", " ")

    def test_native_spark_sticker_task_normalizes_payload(self):
        task = self.tasks.create(
            self.owner.id,
            self.account.id,
            "朋友",
            "09:00",
            "",
            message_kind="douyin_sticker",
        )

        self.assertEqual("douyin_sticker", task.message_kind)
        self.assertEqual("续火花", task.message_template)

    def test_native_spark_sticker_rejects_other_payloads_and_unknown_kinds(self):
        with self.assertRaisesRegex(ValidationError, "仅支持续火花表情"):
            self.tasks.create(
                self.owner.id,
                self.account.id,
                "朋友",
                "09:00",
                "比心",
                message_kind="douyin_sticker",
            )
        with self.assertRaisesRegex(ValidationError, "不支持"):
            self.tasks.create(
                self.owner.id,
                self.account.id,
                "朋友",
                "09:00",
                "消息",
                message_kind="image",
            )

    def test_native_spark_sticker_task_can_be_changed_back_to_text(self):
        task = self.tasks.create(
            self.owner.id,
            self.account.id,
            "朋友",
            "09:00",
            "",
            message_kind="douyin_sticker",
        )
        updated = self.tasks.update_owned(
            self.owner.id,
            task.id,
            self.account.id,
            "朋友",
            "10:00",
            "新的文字",
            message_kind="text",
        )

        self.assertEqual("text", updated.message_kind)
        self.assertEqual("新的文字", updated.message_template)

    def test_audit_records_never_contain_cookie_payload(self):
        events = self.session.scalars(select(AuditEvent)).all()
        self.assertTrue(events)
        serialized = " ".join((e.detail or "") + e.action for e in events)
        cookie_marker = "secret"
        self.assertFalse(cookie_marker in serialized)


if __name__ == "__main__":
    unittest.main()
