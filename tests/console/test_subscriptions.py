import hashlib
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from spark_console.crypto import CookieCipher
from spark_console.db import create_schema
from spark_console.models import (
    AuditEvent,
    DouyinAccount,
    RedemptionCode,
    SparkTask,
    SubscriptionGrant,
    User,
    UserSubscription,
)
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.audits import AuditService
from spark_console.services.subscriptions import (
    SubscriptionService,
    UNLIMITED_TASKS,
    pricing_plan,
    pricing_plans,
    task_limit_label,
)
from spark_console.web.membership import membership_summary


def aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class SubscriptionServiceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 30, 6, 0, tzinfo=timezone.utc)
        self.engine = create_engine("sqlite:///:memory:")
        create_schema(self.engine)
        self.session = Session(self.engine)
        self.admin = User(
            username="admin",
            password_hash="hash",
            role="admin",
            must_change_password=False,
        )
        self.user = User(
            username="customer",
            password_hash="hash",
            role="user",
            must_change_password=False,
        )
        self.other = User(
            username="other",
            password_hash="hash",
            role="user",
            must_change_password=False,
        )
        self.session.add_all((self.admin, self.user, self.other))
        self.session.flush()
        self.service = SubscriptionService(
            self.session, AuditService(self.session), now=lambda: self.now, cipher=CookieCipher(b"c"*32)
        )

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def create_code(self, **kwargs):
        return self.service.create_redemption_code(self.admin.id, **kwargs)

    def test_pricing_plans_match_current_entitlements_and_keep_legacy_lookup(self):
        self.assertEqual(
            [(plan["price_yuan"], plan["max_tasks"]) for plan in pricing_plans()],
            [("5", UNLIMITED_TASKS), ("20", UNLIMITED_TASKS)],
        )
        self.assertEqual(30, pricing_plan("monthly")["duration_days"])
        self.assertEqual(180, pricing_plan("half_year")["duration_days"])
        self.assertEqual(5, pricing_plan("starter")["max_tasks"])
        self.assertEqual(10, pricing_plan("standard")["max_tasks"])
        self.assertEqual(UNLIMITED_TASKS, pricing_plan("unlimited")["max_tasks"])
        self.assertEqual("不限", task_limit_label(UNLIMITED_TASKS))

    def test_new_half_year_code_uses_unlimited_tasks_without_rewriting_legacy_limits(self):
        legacy_code, legacy_plaintext = self.create_code(plan_id="starter")
        new_code, new_plaintext = self.create_code(plan_id="half_year")
        self.assertEqual(180, new_code.duration_days)
        self.assertEqual(UNLIMITED_TASKS, new_code.max_tasks)
        self.service.redeem(self.user.id, new_plaintext)
        self.assertEqual(UNLIMITED_TASKS, self.session.get(UserSubscription, self.user.id).max_tasks)

        self.assertEqual(30, legacy_code.duration_days)
        self.assertEqual(5, legacy_code.max_tasks)
        legacy_subscription = self.service.redeem(self.other.id, legacy_plaintext)
        self.assertEqual(5, legacy_subscription.max_tasks)
        self.assertEqual(self.now + timedelta(days=30), aware(legacy_subscription.valid_until))

    def test_unlimited_plan_allows_task_capacity(self):
        self.service.extend(
            self.admin.id,
            self.user.id,
            duration_days=30,
            plan_id="unlimited",
        )
        subscription = self.session.get(UserSubscription, self.user.id)
        self.assertEqual(UNLIMITED_TASKS, subscription.max_tasks)
        account = DouyinAccount(
            owner_user_id=self.user.id,
            display_name="customer-account",
            encrypted_cookies=b"encrypted",
            cookie_nonce=b"nonce",
        )
        self.session.add(account)
        self.session.flush()
        for index in range(3):
            self.session.add(
                SparkTask(
                    owner_user_id=self.user.id,
                    douyin_account_id=account.id,
                    target_name=f"好友{index}",
                    send_time=f"0{index}:00",
                    message_template="今日火花",
                    enabled=True,
                )
            )
        self.session.flush()
        self.assertIsNotNone(self.service.require_task_capacity(self.user.id, 100_000))

    def test_redemption_code_returns_plaintext_once_and_persists_only_sha256(self):
        code, plaintext = self.create_code(
            duration_days=45,
            max_accounts=2,
            max_tasks=12,
            lifetime=timedelta(days=7),
        )

        self.assertGreaterEqual(len(plaintext), 24)
        self.assertRegex(plaintext, r"^[A-Z0-9]{24}$")
        self.assertEqual(
            hashlib.sha256(plaintext.encode("utf-8")).hexdigest(), code.code_hash
        )
        self.assertNotIn("plaintext", RedemptionCode.__table__.columns)
        self.assertNotIn("ciphertext", RedemptionCode.__table__.columns)
        self.assertEqual(self.now + timedelta(days=7), aware(code.expires_at))
        self.assertEqual(45, code.duration_days)
        self.assertIsNotNone(code.id)
        audit_text = " ".join(
            event.detail or ""
            for event in self.session.scalars(select(AuditEvent)).all()
        )
        self.assertNotIn(plaintext, audit_text)
        self.assertIsNone(code.code)
        self.assertEqual(plaintext, self.service.reveal_redemption_code(self.admin.id, code.id))

    def test_new_redemption_code_is_retrievable_and_does_not_expire_before_use(self):
        code, plaintext = self.create_code(duration_days=30)

        self.assertIsNone(code.code)
        self.assertEqual(plaintext, self.service.reveal_redemption_code(self.admin.id, code.id))
        self.assertIsNotNone(code.code_ciphertext)
        self.assertIsNone(code.redeemed_at)
        # The compatibility sentinel is deliberately far in the future for
        # the deployed SQLite schema; it is never treated as a pre-redemption
        # expiry by the service.
        self.now += timedelta(days=3650)
        subscription = self.service.redeem(self.user.id, plaintext)

        self.assertEqual(self.now, aware(code.redeemed_at))
        self.assertEqual(self.now + timedelta(days=30), aware(code.expires_at))
        self.assertEqual(self.now + timedelta(days=30), aware(subscription.valid_until))

    def test_redeem_creates_subscription_and_append_only_grant(self):
        code, plaintext = self.create_code(
            duration_days=30, max_accounts=2, max_tasks=8
        )

        subscription = self.service.redeem(self.user.id, plaintext)

        self.assertEqual(self.now, aware(subscription.valid_from))
        self.assertEqual(self.now + timedelta(days=30), aware(subscription.valid_until))
        self.assertEqual(2, subscription.max_accounts)
        self.assertEqual(8, subscription.max_tasks)
        self.assertEqual(1, subscription.version)
        self.assertEqual(self.user.id, code.redeemed_by_user_id)
        self.assertEqual(self.now, aware(code.redeemed_at))
        grant = self.session.scalar(
            select(SubscriptionGrant).where(
                SubscriptionGrant.redemption_code_id == code.id
            )
        )
        self.assertEqual("redemption", grant.source)
        self.assertEqual(self.user.id, grant.user_id)
        self.assertEqual(self.user.id, grant.actor_user_id)
        self.assertEqual(30, grant.duration_days)
        self.assertEqual(self.now, aware(grant.valid_from))
        self.assertEqual(self.now + timedelta(days=30), aware(grant.valid_until))
        actions = set(self.session.scalars(select(AuditEvent.action)).all())
        self.assertIn("redemption_code.created", actions)
        self.assertIn("subscription.redeemed", actions)

    def test_active_renewal_starts_from_existing_expiry_and_preserves_old_grant(self):
        _first_code, first_plaintext = self.create_code(
            duration_days=30, max_accounts=1, max_tasks=5
        )
        subscription = self.service.redeem(self.user.id, first_plaintext)
        original_expiry = aware(subscription.valid_until)
        first_grant = self.session.scalar(
            select(SubscriptionGrant).where(
                SubscriptionGrant.user_id == self.user.id
            )
        )
        original_grant = (
            first_grant.source,
            aware(first_grant.valid_from),
            aware(first_grant.valid_until),
            first_grant.max_accounts,
            first_grant.max_tasks,
        )
        self.now += timedelta(days=5)
        _second_code, second_plaintext = self.create_code(
            duration_days=10, max_accounts=3, max_tasks=20
        )

        renewed = self.service.redeem(self.user.id, second_plaintext)

        self.assertEqual(original_expiry + timedelta(days=10), aware(renewed.valid_until))
        self.assertEqual(
            datetime(2026, 8, 30, 6, 0, tzinfo=timezone.utc),
            aware(renewed.valid_from),
        )
        self.assertEqual(
            (3, 20, 2),
            (renewed.max_accounts, renewed.max_tasks, renewed.version),
        )
        self.assertEqual(
            original_grant,
            (
                first_grant.source,
                aware(first_grant.valid_from),
                aware(first_grant.valid_until),
                first_grant.max_accounts,
                first_grant.max_tasks,
            ),
        )
        self.assertEqual(
            2,
            len(
                self.session.scalars(
                    select(SubscriptionGrant).where(
                        SubscriptionGrant.user_id == self.user.id
                    )
                ).all()
            ),
        )

    def test_active_downgrade_is_staged_until_current_term_boundary(self):
        _code, current_code = self.create_code(max_tasks=10)
        subscription = self.service.redeem(self.user.id, current_code)
        current_expiry = aware(subscription.valid_until)
        account = DouyinAccount(
            owner_user_id=self.user.id,
            display_name="customer-account",
            encrypted_cookies=b"encrypted",
            cookie_nonce=b"nonce",
        )
        self.session.add(account)
        self.session.flush()
        for index in range(7):
            self.session.add(
                SparkTask(
                    owner_user_id=self.user.id,
                    douyin_account_id=account.id,
                    target_name=f"好友{index}",
                    send_time=f"{index:02}:00",
                    message_template="今日火花",
                    enabled=True,
                )
            )
        self.session.flush()

        self.now += timedelta(days=5)
        _renewal, lower_code = self.create_code(max_tasks=5)
        self.service.redeem(self.user.id, lower_code)

        self.assertEqual(10, subscription.max_tasks)
        self.assertEqual(current_expiry + timedelta(days=30), aware(subscription.valid_until))
        self.assertEqual(5, subscription.pending_max_tasks)
        self.assertEqual(current_expiry, aware(subscription.pending_valid_from))
        before_boundary = membership_summary(self.session, self.user, self.now)
        self.assertEqual(10, before_boundary["max_tasks"])
        self.assertEqual(5, before_boundary["pending_max_tasks"])
        self.assertEqual(7, before_boundary["enabled_task_count"])
        self.assertEqual(0, before_boundary["task_overage"])

        self.now = current_expiry
        at_boundary = membership_summary(self.session, self.user, self.now)
        self.assertEqual(5, at_boundary["max_tasks"])
        self.assertEqual(5, subscription.max_tasks)
        self.assertEqual(2, at_boundary["task_overage"])
        self.assertEqual(7, at_boundary["enabled_task_count"])
        self.assertIsNone(subscription.pending_valid_from)
        self.assertEqual(
            7,
            self.session.scalar(
                select(func.count()).select_from(SparkTask).where(
                    SparkTask.owner_user_id == self.user.id
                )
            ),
        )

    def test_stacked_downgrades_activate_in_paid_period_order(self):
        _code, current_code = self.create_code(max_tasks=10)
        subscription = self.service.redeem(self.user.id, current_code)
        first_boundary = aware(subscription.valid_until)

        self.now += timedelta(days=2)
        _code, first_lower = self.create_code(max_tasks=5)
        self.service.redeem(self.user.id, first_lower)
        second_boundary = aware(subscription.valid_until)

        self.now += timedelta(days=2)
        _code, second_lower = self.create_code(max_tasks=7)
        self.service.redeem(self.user.id, second_lower)

        self.assertEqual(first_boundary, aware(subscription.pending_valid_from))
        self.assertEqual(5, subscription.pending_max_tasks)
        self.now = first_boundary
        self.assertEqual(5, membership_summary(self.session, self.user, self.now)["max_tasks"])
        self.assertEqual(second_boundary, aware(subscription.pending_valid_from))
        self.assertEqual(7, subscription.pending_max_tasks)

        self.now = second_boundary
        self.assertEqual(7, membership_summary(self.session, self.user, self.now)["max_tasks"])
        self.assertIsNone(subscription.pending_valid_from)

    def test_upgrade_supersedes_a_previously_queued_downgrade(self):
        _code, current_code = self.create_code(max_tasks=10)
        subscription = self.service.redeem(self.user.id, current_code)
        first_boundary = aware(subscription.valid_until)

        self.now += timedelta(days=2)
        lower_redemption, lower_code = self.create_code(max_tasks=5)
        self.service.redeem(self.user.id, lower_code)
        old_grant = self.session.scalar(
            select(SubscriptionGrant).where(
                SubscriptionGrant.redemption_code_id == lower_redemption.id
            )
        )
        later_boundary = aware(subscription.valid_until)

        self.now += timedelta(days=2)
        _code, upgrade_code = self.create_code(max_tasks=20)
        self.service.redeem(self.user.id, upgrade_code)

        self.assertEqual(20, subscription.max_tasks)
        self.assertIsNotNone(old_grant.superseded_at)
        self.assertIsNone(subscription.pending_max_tasks)
        self.assertIsNone(subscription.pending_valid_from)
        self.now = first_boundary
        self.assertEqual(20, membership_summary(self.session, self.user, self.now)["max_tasks"])
        self.assertIsNone(subscription.pending_max_tasks)
        self.now = later_boundary
        self.assertEqual(20, membership_summary(self.session, self.user, self.now)["max_tasks"])
        self.assertIsNone(subscription.pending_valid_from)

    def test_active_guard_reconciles_downgrade_at_exact_boundary(self):
        _code, current_code = self.create_code(max_tasks=10)
        subscription = self.service.redeem(self.user.id, current_code)
        boundary = aware(subscription.valid_until)
        self.now += timedelta(days=1)
        _code, lower_code = self.create_code(max_tasks=5)
        self.service.redeem(self.user.id, lower_code)

        self.now = boundary
        current = self.service.require_active(self.user.id)

        self.assertIs(subscription, current)
        self.assertEqual(5, current.max_tasks)
        self.assertIsNone(current.pending_valid_from)

    def test_upgrade_is_immediate_and_same_plan_renewal_keeps_entitlement(self):
        _code, starter = self.create_code(max_tasks=5)
        subscription = self.service.redeem(self.user.id, starter)
        self.now += timedelta(days=1)

        _code, upgrade = self.create_code(max_tasks=10)
        self.service.redeem(self.user.id, upgrade)
        self.assertEqual(10, subscription.max_tasks)
        self.assertIsNone(subscription.pending_max_tasks)
        self.assertIsNone(subscription.pending_valid_from)

        _code, same_plan = self.create_code(max_tasks=10)
        self.service.redeem(self.user.id, same_plan)
        self.assertEqual(10, subscription.max_tasks)
        self.assertIsNone(subscription.pending_max_tasks)

    def test_expired_renewal_restarts_from_now(self):
        _code, plaintext = self.create_code(duration_days=1, max_tasks=20)
        self.service.redeem(self.user.id, plaintext)
        self.now += timedelta(days=2)
        _renewal, renewal_plaintext = self.create_code(duration_days=3, max_tasks=5)

        subscription = self.service.redeem(self.user.id, renewal_plaintext)

        self.assertEqual(self.now, aware(subscription.valid_from))
        self.assertEqual(self.now + timedelta(days=3), aware(subscription.valid_until))
        self.assertEqual(5, subscription.max_tasks)
        self.assertIsNone(subscription.pending_valid_from)

    def test_code_can_be_redeemed_once_and_revoked_code_cannot_be_redeemed(self):
        used_code, used_plaintext = self.create_code()
        self.service.redeem(self.user.id, used_plaintext)

        with self.assertRaisesRegex(ValidationError, "兑换码无效或已使用"):
            self.service.redeem(self.other.id, used_plaintext)
        with self.assertRaisesRegex(Conflict, "已使用或已撤销"):
            self.service.revoke_redemption_code(self.admin.id, used_code.id)

        revoked_code, revoked_plaintext = self.create_code()
        returned = self.service.revoke_redemption_code(self.admin.id, revoked_code.id)
        self.assertIs(returned, revoked_code)
        self.assertEqual(self.now, aware(revoked_code.revoked_at))
        with self.assertRaisesRegex(ValidationError, "兑换码无效或已使用"):
            self.service.redeem(self.other.id, revoked_plaintext)
        with self.assertRaisesRegex(Conflict, "已使用或已撤销"):
            self.service.revoke_redemption_code(self.admin.id, revoked_code.id)
        with self.assertRaises(NotFound):
            self.service.revoke_redemption_code(
                self.admin.id, "00000000-0000-0000-0000-000000000000"
            )

    def test_same_user_redeem_retry_is_read_only_and_does_not_extend_again(self):
        code, plaintext = self.create_code(duration_days=30)

        first = self.service.redeem(self.user.id, plaintext)
        first_expiry = aware(first.valid_until)
        first_version = first.version

        replay = self.service.redeem(self.user.id, plaintext)

        self.assertEqual(first.user_id, replay.user_id)
        self.assertEqual(first_expiry, aware(replay.valid_until))
        self.assertEqual(first_version, replay.version)
        self.assertEqual(
            1,
            self.session.scalar(
                select(func.count()).select_from(SubscriptionGrant).where(
                    SubscriptionGrant.redemption_code_id == code.id
                )
            ),
        )
        self.assertIn(
            "subscription.redeem_replayed",
            set(self.session.scalars(select(AuditEvent.action)).all()),
        )

    def test_expired_code_is_rejected_without_creating_subscription(self):
        _code, plaintext = self.create_code(lifetime=timedelta(minutes=1))
        self.now += timedelta(minutes=1)

        with self.assertRaisesRegex(ValidationError, "兑换码无效或已使用"):
            self.service.redeem(self.user.id, plaintext)

        self.assertIsNone(self.session.get(UserSubscription, self.user.id))
        self.assertEqual([], self.session.scalars(select(SubscriptionGrant)).all())

    def test_admin_can_open_extend_and_set_limits_with_a_grant_per_change(self):
        subscription = self.service.extend(
            self.admin.id,
            self.user.id,
            duration_days=30,
            max_accounts=2,
            max_tasks=10,
        )
        first_expiry = aware(subscription.valid_until)
        self.now += timedelta(days=1)

        subscription = self.service.extend(
            self.admin.id, self.user.id, duration_days=15
        )
        self.assertEqual(first_expiry + timedelta(days=15), aware(subscription.valid_until))
        self.assertEqual(
            (2, 10, 2),
            (
                subscription.max_accounts,
                subscription.max_tasks,
                subscription.version,
            ),
        )
        expiry_before_limits = aware(subscription.valid_until)

        subscription = self.service.set_limits(
            self.admin.id, self.user.id, max_accounts=4, max_tasks=25
        )

        self.assertEqual(expiry_before_limits, aware(subscription.valid_until))
        self.assertEqual(
            (4, 25, 3),
            (
                subscription.max_accounts,
                subscription.max_tasks,
                subscription.version,
            ),
        )
        grants = self.session.scalars(
            select(SubscriptionGrant).where(
                SubscriptionGrant.user_id == self.user.id
            )
        ).all()
        self.assertEqual(
            {"admin_extend", "admin_limits"}, {grant.source for grant in grants}
        )
        self.assertEqual(3, len(grants))
        actions = set(self.session.scalars(select(AuditEvent.action)).all())
        self.assertIn("subscription.extended", actions)
        self.assertIn("subscription.limits_updated", actions)

    def test_non_admin_cannot_manage_codes_or_subscriptions(self):
        code, _plaintext = self.create_code()
        with self.assertRaisesRegex(ValidationError, "需要管理员权限"):
            self.service.create_redemption_code(self.user.id)
        with self.assertRaisesRegex(ValidationError, "需要管理员权限"):
            self.service.extend(
                self.user.id, self.other.id, duration_days=30
            )
        with self.assertRaisesRegex(ValidationError, "需要管理员权限"):
            self.service.revoke_redemption_code(self.user.id, code.id)
        with self.assertRaisesRegex(ValidationError, "需要管理员权限"):
            self.service.set_limits(
                self.user.id, self.other.id, max_accounts=1, max_tasks=1
            )
        with self.assertRaisesRegex(ValidationError, "需要管理员权限"):
            self.service.revoke_subscription(self.user.id, self.other.id)

    def test_admin_revoke_is_immediate_and_appends_a_zero_length_grant(self):
        subscription = self.service.extend(
            self.admin.id,
            self.user.id,
            duration_days=30,
            max_accounts=2,
            max_tasks=10,
        )

        revoked = self.service.revoke(self.admin.id, self.user.id)

        self.assertIs(subscription, revoked)
        self.assertEqual(self.now, aware(revoked.valid_from))
        self.assertEqual(self.now, aware(revoked.valid_until))
        self.assertEqual(2, revoked.version)
        self.assertFalse(self.service.active(self.user.id))
        grant = self.session.scalar(
            select(SubscriptionGrant).where(
                SubscriptionGrant.source == "admin_revoke"
            )
        )
        self.assertEqual(self.now, aware(grant.valid_from))
        self.assertEqual(self.now, aware(grant.valid_until))
        self.assertEqual((2, 10), (grant.max_accounts, grant.max_tasks))
        actions = set(self.session.scalars(select(AuditEvent.action)).all())
        self.assertIn("subscription.revoked", actions)
        with self.assertRaisesRegex(Conflict, "已过期或已撤销"):
            self.service.revoke_subscription(self.admin.id, self.user.id)
        with self.assertRaises(NotFound):
            self.service.revoke_subscription(self.admin.id, self.other.id)

    def test_active_and_require_active_include_user_status_and_exact_expiry(self):
        self.assertFalse(self.service.active(self.user.id))
        with self.assertRaisesRegex(ValidationError, "会员未开通或已过期"):
            self.service.require_active(self.user.id)

        subscription = self.service.extend(
            self.admin.id, self.user.id, duration_days=1
        )
        self.assertTrue(self.service.active(self.user.id))
        self.assertIs(subscription, self.service.require_active(self.user.id))

        self.now = aware(subscription.valid_until)
        self.assertFalse(self.service.active(self.user.id))
        with self.assertRaises(ValidationError):
            self.service.require_active(self.user.id)

        self.now -= timedelta(seconds=1)
        self.user.status = "disabled"
        self.assertFalse(self.service.active(self.user.id))
        with self.assertRaises(ValidationError):
            self.service.require_active(self.user.id)

    def test_account_and_task_capacity_count_only_the_requested_owner(self):
        self.service.extend(
            self.admin.id,
            self.user.id,
            duration_days=30,
            max_accounts=1,
            max_tasks=1,
        )
        self.session.add(
            DouyinAccount(
                owner_user_id=self.other.id,
                display_name="other-account",
                encrypted_cookies=b"encrypted",
                cookie_nonce=b"nonce",
            )
        )
        self.session.flush()
        self.service.require_account_capacity(self.user.id)
        self.service.require_task_capacity(self.user.id)

        account = DouyinAccount(
            owner_user_id=self.user.id,
            display_name="customer-account",
            encrypted_cookies=b"encrypted",
            cookie_nonce=b"nonce",
        )
        self.session.add(account)
        self.session.flush()
        with self.assertRaisesRegex(Conflict, "账号数量已达套餐上限"):
            self.service.require_account_capacity(self.user.id)

        self.session.add(
            SparkTask(
                owner_user_id=self.user.id,
                douyin_account_id=account.id,
                target_name="好友",
                send_time="09:00",
                message_template="今日火花",
                enabled=True,
            )
        )
        self.session.flush()
        with self.assertRaisesRegex(Conflict, "任务数量已达套餐上限"):
            self.service.require_task_capacity(self.user.id)

    def test_unsubscribed_user_gets_one_free_account_and_task(self):
        self.assertFalse(self.service.active(self.user.id))
        self.assertTrue(self.service.has_task_access(self.user.id))
        self.assertIsNone(self.service.require_account_capacity(self.user.id))
        self.assertIsNone(self.service.require_task_capacity(self.user.id))

        account = DouyinAccount(
            owner_user_id=self.user.id,
            display_name="free-account",
            encrypted_cookies=b"encrypted",
            cookie_nonce=b"nonce",
        )
        self.session.add(account)
        self.session.flush()
        self.session.add(
            SparkTask(
                owner_user_id=self.user.id,
                douyin_account_id=account.id,
                target_name="免费好友",
                send_time="09:00",
                message_template="今日火花",
                enabled=True,
            )
        )
        self.session.flush()
        with self.assertRaisesRegex(Conflict, "免费额度"):
            self.service.require_account_capacity(self.user.id)
        with self.assertRaisesRegex(Conflict, "免费额度"):
            self.service.require_task_capacity(self.user.id)

        summary = membership_summary(self.session, self.user, self.now)
        self.assertTrue(summary["free_tier"])
        self.assertEqual(1, summary["max_accounts"])
        self.assertEqual(1, summary["max_tasks"])
        self.assertFalse(summary["can_add_account"])
        self.assertFalse(summary["can_add_task"])

    def test_expired_subscription_falls_back_to_free_tier(self):
        # A user whose paid subscription has expired should still be able to
        # bind one account and run one task, exactly like a brand-new user who
        # has never had a subscription.
        account = DouyinAccount(
            owner_user_id=self.user.id,
            display_name="free-account",
            encrypted_cookies=b"encrypted",
            cookie_nonce=b"nonce",
        )
        self.session.add(account)
        self.session.flush()
        subscription = self.service.extend(self.admin.id, self.user.id, duration_days=1)
        self.now = aware(subscription.valid_until) + timedelta(seconds=1)
        # has_task_access remains true so existing tasks keep running.
        self.assertTrue(self.service.has_task_access(self.user.id))
        # The account slot is already used up (1 of FREE_MAX_ACCOUNTS=1).
        with self.assertRaisesRegex(Conflict, "免费额度"):
            self.service.require_account_capacity(self.user.id)
        # One enabled task is still allowed.
        self.assertIsNone(self.service.require_task_capacity(self.user.id))
        # membership_summary reflects free-tier limits and flags.
        summary = membership_summary(self.session, self.user, self.now)
        self.assertTrue(summary["free_tier"])
        self.assertEqual(1, summary["max_accounts"])
        self.assertEqual(1, summary["max_tasks"])
        self.assertFalse(summary["can_add_account"])  # already at limit
        self.assertTrue(summary["can_add_task"])       # zero tasks enabled yet

    def test_free_execution_slot_is_deterministic_and_preserves_task_rows(self):
        account = DouyinAccount(
            owner_user_id=self.user.id,
            display_name="free-account",
            encrypted_cookies=b"encrypted",
            cookie_nonce=b"nonce",
        )
        self.session.add(account)
        self.session.flush()
        first = SparkTask(
            owner_user_id=self.user.id,
            douyin_account_id=account.id,
            target_name="较早任务",
            send_time="09:00",
            message_template="今日火花",
            enabled=True,
            created_at=self.now - timedelta(minutes=2),
        )
        second = SparkTask(
            owner_user_id=self.user.id,
            douyin_account_id=account.id,
            target_name="较晚任务",
            send_time="09:05",
            message_template="今日火花",
            enabled=True,
            created_at=self.now - timedelta(minutes=1),
        )
        self.session.add_all((first, second))
        self.session.flush()
        self.assertTrue(self.service.can_execute_task(self.user.id, first.id))
        self.assertFalse(self.service.can_execute_task(self.user.id, second.id))
        self.assertTrue(first.enabled)
        self.assertTrue(second.enabled)

    def test_task_capacity_checks_the_full_batch_size(self):
        self.service.extend(
            self.admin.id,
            self.user.id,
            duration_days=30,
            max_accounts=1,
            max_tasks=3,
        )
        account = DouyinAccount(
            owner_user_id=self.user.id,
            display_name="customer-account",
            encrypted_cookies=b"encrypted",
            cookie_nonce=b"nonce",
        )
        self.session.add(account)
        self.session.flush()
        self.session.add(
            SparkTask(
                owner_user_id=self.user.id,
                douyin_account_id=account.id,
                target_name="已有好友",
                send_time="09:00",
                message_template="今日火花",
                enabled=True,
            )
        )
        self.session.flush()

        self.assertIsNotNone(self.service.require_task_capacity(self.user.id, 2))
        with self.assertRaisesRegex(Conflict, "任务数量已达套餐上限"):
            self.service.require_task_capacity(self.user.id, 3)
        with self.assertRaises(ValidationError):
            self.service.require_task_capacity(self.user.id, 0)

    def test_invalid_parameters_and_missing_records_use_service_errors(self):
        for field, kwargs in (
            ("days", {"duration_days": 0}),
            ("days-too-large", {"duration_days": 36_501}),
            ("accounts", {"max_accounts": 0}),
            ("tasks", {"max_tasks": 0}),
            ("lifetime", {"lifetime": timedelta(0)}),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.create_code(**kwargs)
        with self.assertRaises(NotFound):
            self.service.extend(
                self.admin.id,
                "00000000-0000-0000-0000-000000000000",
                duration_days=30,
            )
        with self.assertRaises(NotFound):
            self.service.set_limits(
                self.admin.id, self.other.id, max_accounts=1, max_tasks=1
            )


if __name__ == "__main__":
    unittest.main()
