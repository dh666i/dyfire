from __future__ import annotations

import hashlib
import secrets
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from spark_console.models import (
    DouyinAccount,
    RedemptionCode,
    SparkTask,
    SubscriptionGrant,
    User,
    UserSubscription,
    utc_now,
)
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.audits import AuditService


DEFAULT_MAX_ACCOUNTS = 1
DEFAULT_MAX_TASKS = 10
# Users can try the core workflow before purchasing. These limits apply only
# when no subscription row exists; expired paid subscriptions remain blocked.
FREE_MAX_ACCOUNTS = 1
FREE_MAX_TASKS = 1
# Keep the unlimited entitlement representable in the existing positive integer
# columns. API/UI helpers render this sentinel as "不限".
UNLIMITED_TASKS = 2_147_483_647
PRICING_PLANS = (
    {
        "id": "starter",
        "name": "基础版",
        "price_yuan": "9.9",
        "duration_days": 30,
        "max_accounts": 1,
        "max_tasks": 5,
        "task_limit_label": "5 个好友任务",
    },
    {
        "id": "standard",
        "name": "标准版",
        "price_yuan": "15",
        "duration_days": 30,
        "max_accounts": 1,
        "max_tasks": 10,
        "task_limit_label": "10 个好友任务",
    },
    {
        "id": "unlimited",
        "name": "不限版",
        "price_yuan": "20",
        "duration_days": 30,
        "max_accounts": 1,
        "max_tasks": UNLIMITED_TASKS,
        "task_limit_label": "不限好友任务",
    },
)
MAX_SUBSCRIPTION_DAYS = 36_500
INVALID_CODE = "兑换码无效或已使用"
INACTIVE_SUBSCRIPTION = "会员未开通或已过期"
NEVER_EXPIRES_AT = datetime.max.replace(tzinfo=timezone.utc)
REDEMPTION_CODE_ALPHABET = string.ascii_uppercase + string.digits
REDEMPTION_CODE_LENGTH = 24


def pricing_plans() -> tuple[dict[str, object], ...]:
    """Return immutable copies so callers cannot mutate billing metadata."""
    return tuple(dict(plan) for plan in PRICING_PLANS)


def pricing_plan(plan_id: str | None) -> dict[str, object] | None:
    if not plan_id:
        return None
    return next((dict(plan) for plan in PRICING_PLANS if plan["id"] == plan_id), None)


def is_unlimited_tasks(max_tasks: int | None) -> bool:
    return isinstance(max_tasks, int) and max_tasks >= UNLIMITED_TASKS


def task_limit_label(max_tasks: int | None) -> str:
    return "不限" if max_tasks is None or is_unlimited_tasks(max_tasks) else str(max_tasks or 0)


def resolve_plan_limits(
    plan_id: str | None,
    *,
    duration_days: int,
    max_accounts: int,
    max_tasks: int,
) -> tuple[int, int, int, str | None]:
    plan = pricing_plan(plan_id)
    if plan is None:
        if plan_id:
            raise ValidationError("会员套餐无效")
        return duration_days, max_accounts, max_tasks, None
    return (
        int(plan["duration_days"]),
        int(plan["max_accounts"]),
        int(plan["max_tasks"]),
        str(plan["id"]),
    )


def _digest(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _positive_int(value: int, message: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValidationError(message)
    return value


def _duration_days(value: int) -> int:
    value = _positive_int(value, "会员天数必须大于 0")
    if value > MAX_SUBSCRIPTION_DAYS:
        raise ValidationError("会员天数不能超过 36500 天")
    return value


def reconcile_subscription(
    session: Session, subscription: UserSubscription, now: datetime
) -> UserSubscription:
    """Apply a queued lower plan once its paid term begins.

    Grant periods form the source of truth for future limits. Superseded grants
    remain in history but no longer participate in the entitlement timeline.
    ``pending_*`` points at the next boundary so reads can reconcile lazily.
    """
    current = _aware(now)
    boundary = subscription.pending_valid_from
    if boundary is None or current < _aware(boundary):
        return subscription
    if current >= _aware(subscription.valid_until):
        subscription.pending_max_accounts = None
        subscription.pending_max_tasks = None
        subscription.pending_valid_from = None
        return subscription

    grant = session.scalar(
        select(SubscriptionGrant)
        .where(
            SubscriptionGrant.user_id == subscription.user_id,
            SubscriptionGrant.valid_from >= _aware(boundary),
            SubscriptionGrant.valid_from <= current,
            SubscriptionGrant.valid_until > current,
            SubscriptionGrant.superseded_at.is_(None),
        )
        .order_by(SubscriptionGrant.valid_from.desc(), SubscriptionGrant.created_at.desc())
    )
    if grant is None:
        # Do not leave a stale pending marker if an old database has a
        # partially migrated grant history. The current entitlement remains
        # valid and no task is removed.
        subscription.pending_max_accounts = None
        subscription.pending_max_tasks = None
        subscription.pending_valid_from = None
        return subscription

    changed = (
        subscription.max_accounts != grant.max_accounts
        or subscription.max_tasks != grant.max_tasks
    )
    subscription.max_accounts = grant.max_accounts
    subscription.max_tasks = grant.max_tasks
    next_grant = _next_distinct_grant(session, subscription, current)
    if next_grant is None:
        subscription.pending_max_accounts = None
        subscription.pending_max_tasks = None
        subscription.pending_valid_from = None
    else:
        subscription.pending_max_accounts = next_grant.max_accounts
        subscription.pending_max_tasks = next_grant.max_tasks
        subscription.pending_valid_from = _aware(next_grant.valid_from)
    if changed:
        subscription.version += 1
        subscription.updated_at = current
    return subscription


def _refresh_pending_subscription(
    session: Session, subscription: UserSubscription, now: datetime
) -> None:
    """Point the compact pending fields at the next append-only grant."""
    current = _aware(now)
    next_grant = _next_distinct_grant(session, subscription, current)
    if next_grant is None:
        subscription.pending_max_accounts = None
        subscription.pending_max_tasks = None
        subscription.pending_valid_from = None
    else:
        subscription.pending_max_accounts = next_grant.max_accounts
        subscription.pending_max_tasks = next_grant.max_tasks
        subscription.pending_valid_from = _aware(next_grant.valid_from)


def _next_distinct_grant(
    session: Session, subscription: UserSubscription, now: datetime
) -> SubscriptionGrant | None:
    current = _aware(now)
    grants = session.scalars(
        select(SubscriptionGrant)
        .where(
            SubscriptionGrant.user_id == subscription.user_id,
            SubscriptionGrant.valid_from > current,
            SubscriptionGrant.valid_until > SubscriptionGrant.valid_from,
            SubscriptionGrant.superseded_at.is_(None),
            or_(
                SubscriptionGrant.max_accounts != subscription.max_accounts,
                SubscriptionGrant.max_tasks != subscription.max_tasks,
            ),
        )
        .order_by(SubscriptionGrant.valid_from.asc(), SubscriptionGrant.created_at.desc())
        .limit(1)
    ).all()
    return grants[0] if grants else None


def _supersede_future_grants(
    session: Session, user_id: str, now: datetime
) -> None:
    current = _aware(now)
    grants = session.scalars(
        select(SubscriptionGrant).where(
            SubscriptionGrant.user_id == user_id,
            SubscriptionGrant.valid_from > current,
            SubscriptionGrant.valid_until > current,
            SubscriptionGrant.superseded_at.is_(None),
        )
    ).all()
    for grant in grants:
        grant.superseded_at = current


class SubscriptionService:
    def __init__(self, session: Session, audit: AuditService, now=utc_now):
        self.session = session
        self.audit = audit
        self.now = now

    def create_redemption_code(
        self,
        actor_id: str,
        *,
        duration_days: int = 30,
        max_accounts: int = DEFAULT_MAX_ACCOUNTS,
        max_tasks: int = DEFAULT_MAX_TASKS,
        plan_id: str | None = None,
        lifetime: timedelta | None = None,
    ) -> tuple[RedemptionCode, str]:
        self._require_admin(actor_id)
        duration_days, max_accounts, max_tasks, resolved_plan = resolve_plan_limits(
            plan_id,
            duration_days=duration_days,
            max_accounts=max_accounts,
            max_tasks=max_tasks,
        )
        duration_days = _duration_days(duration_days)
        max_accounts = _positive_int(max_accounts, "账号上限必须大于 0")
        max_tasks = _positive_int(max_tasks, "任务上限必须大于 0")
        if lifetime is not None and (
            not isinstance(lifetime, timedelta) or lifetime <= timedelta(0)
        ):
            raise ValidationError("兑换码有效期必须大于 0")
        current = self._now()
        # Keep newly issued codes easy to copy and enter: uppercase letters
        # and digits only. Existing rows are never rewritten, so legacy codes
        # containing URL-safe punctuation remain valid.
        plaintext = "".join(
            secrets.choice(REDEMPTION_CODE_ALPHABET)
            for _ in range(REDEMPTION_CODE_LENGTH)
        )
        expires_at = None
        if lifetime is not None:
            try:
                expires_at = current + lifetime
            except OverflowError:
                raise ValidationError("兑换码有效期无效") from None
        else:
            # Existing SQLite tables declare expires_at NOT NULL. Use a
            # stable far-future sentinel for both old and new schemas.
            expires_at = NEVER_EXPIRES_AT
        code = RedemptionCode(
            code=plaintext,
            code_hash=_digest(plaintext),
            duration_days=duration_days,
            max_accounts=max_accounts,
            max_tasks=max_tasks,
            # Codes remain usable indefinitely until redeemed.  ``expires_at``
            # is populated atomically by redeem() with the membership expiry.
            expires_at=expires_at,
            created_by_user_id=actor_id,
            created_at=current,
        )
        self.session.add(code)
        self.session.flush()
        self.audit.write(
            actor_id,
            "redemption_code.created",
            "redemption_code",
            code.id,
            f"{self._detail(duration_days, max_accounts, max_tasks)};"
            f"plan_id={resolved_plan or 'custom'}",
        )
        self.session.flush()
        return code, plaintext

    def redeem(self, user_id: str, plaintext: str) -> UserSubscription:
        user = self._require_user(user_id)
        if user.status != "active" or not isinstance(plaintext, str):
            raise ValidationError(INVALID_CODE)

        code = self.session.scalar(
            select(RedemptionCode).where(
                RedemptionCode.code_hash == _digest(plaintext)
            )
        )
        if code is None:
            raise ValidationError(INVALID_CODE)

        # A browser can lose the response after the transaction has committed
        # and submit the same code again.  Treat that retry as a read-only
        # replay for the user who redeemed it, instead of reporting a false
        # failure.  A missing subscription is intentionally not considered a
        # replay: it indicates an inconsistent historical row and should fail
        # closed rather than granting anything implicitly.
        if code.redeemed_at is not None:
            replay = self._redeem_replay(user_id, code)
            if replay is not None:
                return replay
            raise ValidationError(INVALID_CODE)

        current = self._now()
        result = self.session.execute(
            update(RedemptionCode)
            .where(
                RedemptionCode.id == code.id,
                RedemptionCode.redeemed_at.is_(None),
                RedemptionCode.revoked_at.is_(None),
                (RedemptionCode.expires_at.is_(None))
                | (RedemptionCode.expires_at >= NEVER_EXPIRES_AT)
                | (RedemptionCode.expires_at > current),
            )
            .values(redeemed_by_user_id=user_id, redeemed_at=current)
            .execution_options(synchronize_session="fetch")
        )
        if result.rowcount != 1:
            # Another request may have won the atomic reservation after the
            # initial lookup. Refresh the row and apply the same-user replay
            # rule so a lost response does not become a false error even under
            # concurrent requests.
            self.session.refresh(code)
            replay = self._redeem_replay(user_id, code)
            if replay is not None:
                return replay
            raise ValidationError(INVALID_CODE)

        subscription, grant = self._extend_subscription(
            user_id,
            duration_days=code.duration_days,
            max_accounts=code.max_accounts,
            max_tasks=code.max_tasks,
            source="redemption",
            actor_id=user_id,
            redemption_code_id=code.id,
            current=current,
        )
        # Once redeemed, expose the resulting membership expiry.  If the user
        # already has an active subscription, the grant is appended after its
        # current end and the table should show that final date.
        code.expires_at = _aware(grant.valid_until)
        self.audit.write(
            user_id,
            "subscription.redeemed",
            "user_subscription",
            user_id,
            f"grant_id={grant.id};redemption_code_id={code.id};"
            f"{self._detail(code.duration_days, code.max_accounts, code.max_tasks)}",
        )
        self.session.flush()
        return subscription

    def _redeem_replay(
        self, user_id: str, code: RedemptionCode
    ) -> UserSubscription | None:
        """Return an already-created grant for a safe same-user retry."""
        if code.redeemed_by_user_id != user_id:
            return None
        grant = self.session.scalar(
            select(SubscriptionGrant).where(
                SubscriptionGrant.redemption_code_id == code.id,
                SubscriptionGrant.user_id == user_id,
            )
        )
        if grant is None:
            return None
        subscription = self._get_subscription(user_id)
        if subscription is None:
            return None
        self.audit.write(
            user_id,
            "subscription.redeem_replayed",
            "user_subscription",
            user_id,
            f"redemption_code_id={code.id};grant_id={grant.id}",
        )
        return subscription

    def extend(
        self,
        actor_id: str,
        user_id: str,
        *,
        duration_days: int,
        max_accounts: int | None = None,
        max_tasks: int | None = None,
        plan_id: str | None = None,
    ) -> UserSubscription:
        self._require_admin(actor_id)
        self._require_user(user_id)
        if plan_id:
            duration_days, resolved_accounts, resolved_tasks, _ = resolve_plan_limits(
                plan_id,
                duration_days=duration_days,
                max_accounts=max_accounts or DEFAULT_MAX_ACCOUNTS,
                max_tasks=max_tasks or DEFAULT_MAX_TASKS,
            )
            max_accounts, max_tasks = resolved_accounts, resolved_tasks
        duration_days = _duration_days(duration_days)
        if max_accounts is not None:
            max_accounts = _positive_int(max_accounts, "账号上限必须大于 0")
        if max_tasks is not None:
            max_tasks = _positive_int(max_tasks, "任务上限必须大于 0")

        subscription, grant = self._extend_subscription(
            user_id,
            duration_days=duration_days,
            max_accounts=max_accounts,
            max_tasks=max_tasks,
            source="admin_extend",
            actor_id=actor_id,
            current=self._now(),
        )
        self.audit.write(
            actor_id,
            "subscription.extended",
            "user_subscription",
            user_id,
            f"grant_id={grant.id};"
            f"{self._detail(duration_days, subscription.max_accounts, subscription.max_tasks)}",
        )
        self.session.flush()
        return subscription

    def set_limits(
        self,
        actor_id: str,
        user_id: str,
        *,
        max_accounts: int,
        max_tasks: int,
    ) -> UserSubscription:
        self._require_admin(actor_id)
        self._require_user(user_id)
        max_accounts = _positive_int(max_accounts, "账号上限必须大于 0")
        max_tasks = _positive_int(max_tasks, "任务上限必须大于 0")
        subscription = self._get_subscription(user_id, for_update=True)
        if subscription is None:
            raise NotFound("会员订阅不存在")

        current = self._now()
        reconcile_subscription(self.session, subscription, current)
        _supersede_future_grants(self.session, user_id, current)
        subscription.max_accounts = max_accounts
        subscription.max_tasks = max_tasks
        subscription.pending_max_accounts = None
        subscription.pending_max_tasks = None
        subscription.pending_valid_from = None
        subscription.updated_at = current
        subscription.version += 1
        grant = SubscriptionGrant(
            user_id=user_id,
            source="admin_limits",
            actor_user_id=actor_id,
            duration_days=None,
            valid_from=_aware(subscription.valid_from),
            valid_until=_aware(subscription.valid_until),
            max_accounts=max_accounts,
            max_tasks=max_tasks,
            created_at=current,
        )
        self.session.add(grant)
        self.session.flush()
        _refresh_pending_subscription(self.session, subscription, current)
        self.audit.write(
            actor_id,
            "subscription.limits_updated",
            "user_subscription",
            user_id,
            f"grant_id={grant.id};max_accounts={max_accounts};max_tasks={max_tasks}",
        )
        self.session.flush()
        return subscription

    def revoke_subscription(
        self, actor_id: str, user_id: str
    ) -> UserSubscription:
        self._require_admin(actor_id)
        self._require_user(user_id)
        subscription = self._get_subscription(user_id, for_update=True)
        if subscription is None:
            raise NotFound("会员订阅不存在")

        current = self._now()
        reconcile_subscription(self.session, subscription, current)
        if _aware(subscription.valid_until) <= current:
            raise Conflict("会员订阅已过期或已撤销")
        valid_from = min(_aware(subscription.valid_from), current)
        subscription.valid_from = valid_from
        subscription.valid_until = current
        _supersede_future_grants(self.session, user_id, current)
        subscription.pending_max_accounts = None
        subscription.pending_max_tasks = None
        subscription.pending_valid_from = None
        subscription.updated_at = current
        subscription.version += 1
        grant = SubscriptionGrant(
            user_id=user_id,
            source="admin_revoke",
            actor_user_id=actor_id,
            duration_days=None,
            valid_from=valid_from,
            valid_until=current,
            max_accounts=subscription.max_accounts,
            max_tasks=subscription.max_tasks,
            created_at=current,
        )
        self.session.add(grant)
        self.session.flush()
        self.audit.write(
            actor_id,
            "subscription.revoked",
            "user_subscription",
            user_id,
            f"grant_id={grant.id}",
        )
        self.session.flush()
        return subscription

    def revoke(self, actor_id: str, user_id: str) -> UserSubscription:
        return self.revoke_subscription(actor_id, user_id)

    def revoke_redemption_code(
        self, actor_id: str, redemption_code_id: str
    ) -> RedemptionCode:
        self._require_admin(actor_id)
        code = self.session.get(RedemptionCode, redemption_code_id)
        if code is None:
            raise NotFound("兑换码不存在")

        current = self._now()
        result = self.session.execute(
            update(RedemptionCode)
            .where(
                RedemptionCode.id == redemption_code_id,
                RedemptionCode.redeemed_at.is_(None),
                RedemptionCode.revoked_at.is_(None),
            )
            .values(revoked_at=current)
            .execution_options(synchronize_session="fetch")
        )
        if result.rowcount != 1:
            raise Conflict("兑换码已使用或已撤销")
        self.audit.write(
            actor_id,
            "redemption_code.revoked",
            "redemption_code",
            redemption_code_id,
        )
        self.session.flush()
        self.session.refresh(code)
        return code

    def active(self, user_id: str) -> bool:
        user = self.session.get(User, user_id)
        if user is None or user.status != "active":
            return False
        subscription = self._get_subscription(user_id)
        if subscription is None:
            return False
        current = self._now()
        reconcile_subscription(self.session, subscription, current)
        return (
            _aware(subscription.valid_from)
            <= current
            < _aware(subscription.valid_until)
        )

    def has_task_access(self, user_id: str) -> bool:
        """Whether an active user may execute an existing task.

        ``active`` intentionally remains the paid-membership predicate for
        billing and display. A user without a subscription has one free task.
        """
        user = self.session.get(User, user_id)
        if user is None or user.status != "active":
            return False
        subscription = self._get_subscription(user_id)
        return True if subscription is None else self.active(user_id)

    def require_active(self, user_id: str) -> UserSubscription:
        return self._require_active(user_id)

    def _require_active(
        self, user_id: str, *, for_update: bool = False
    ) -> UserSubscription:
        user = self.session.get(User, user_id)
        subscription = self._get_subscription(user_id, for_update=for_update)
        current = self._now()
        if subscription is not None:
            reconcile_subscription(self.session, subscription, current)
        if (
            user is None
            or user.status != "active"
            or subscription is None
            or _aware(subscription.valid_from) > current
            or _aware(subscription.valid_until) <= current
        ):
            raise ValidationError(INACTIVE_SUBSCRIPTION)
        return subscription

    def require_account_capacity(self, user_id: str) -> UserSubscription | None:
        # Lock the owner row even when there is no subscription row yet. On
        # PostgreSQL a missing subscription row cannot be locked, so without
        # this guard two first-account requests could both pass the free tier.
        locked_user_id = self.session.scalar(
            select(User.id).where(User.id == user_id).with_for_update()
        )
        user = self.session.get(User, locked_user_id) if locked_user_id else None
        if user is None or user.status != "active":
            raise ValidationError(INACTIVE_SUBSCRIPTION)
        subscription = self._get_subscription(user_id, for_update=True)
        count = self.session.scalar(
            select(func.count())
            .select_from(DouyinAccount)
            .where(DouyinAccount.owner_user_id == user_id)
        )
        current = self._now()
        if subscription is None or _aware(subscription.valid_from) > current or _aware(subscription.valid_until) <= current:
            # No active subscription — allow up to the free-tier limit so that
            # new users and users with expired subscriptions can still bind one
            # account without purchasing a plan.
            if subscription is not None:
                reconcile_subscription(self.session, subscription, current)
            if int(count or 0) >= FREE_MAX_ACCOUNTS:
                raise Conflict("抖音账号数量已达免费额度，请开通订阅后继续添加")
            return None
        reconcile_subscription(self.session, subscription, current)
        if int(count or 0) >= subscription.max_accounts:
            raise Conflict("抖音账号数量已达套餐上限")
        return subscription

    def require_task_capacity(
        self, user_id: str, additional_tasks: int = 1
    ) -> UserSubscription | None:
        additional_tasks = _positive_int(
            additional_tasks, "新增任务数量必须大于 0"
        )
        locked_user_id = self.session.scalar(
            select(User.id).where(User.id == user_id).with_for_update()
        )
        user = self.session.get(User, locked_user_id) if locked_user_id else None
        if user is None or user.status != "active":
            raise ValidationError(INACTIVE_SUBSCRIPTION)
        subscription = self._get_subscription(user_id, for_update=True)
        count = self.session.scalar(
            select(func.count())
            .select_from(SparkTask)
            .where(
                SparkTask.owner_user_id == user_id,
                SparkTask.enabled.is_(True),
            )
        )
        current = self._now()
        if subscription is None or _aware(subscription.valid_from) > current or _aware(subscription.valid_until) <= current:
            # No active subscription — allow up to the free-tier limit so that
            # new users and users with expired subscriptions can still run one
            # task without purchasing a plan.
            if subscription is not None:
                reconcile_subscription(self.session, subscription, current)
            if int(count or 0) + additional_tasks > FREE_MAX_TASKS:
                raise Conflict("续火花任务数量已达免费额度，请开通订阅后继续添加")
            return None
        reconcile_subscription(self.session, subscription, current)
        if not is_unlimited_tasks(subscription.max_tasks) and int(count or 0) + additional_tasks > subscription.max_tasks:
            raise Conflict("续火任务数量已达套餐上限")
        return subscription

    def _extend_subscription(
        self,
        user_id: str,
        *,
        duration_days: int,
        max_accounts: int | None,
        max_tasks: int | None,
        source: str,
        actor_id: str,
        current: datetime,
        redemption_code_id: str | None = None,
    ) -> tuple[UserSubscription, SubscriptionGrant]:
        self.session.scalar(
            select(User.id).where(User.id == user_id).with_for_update()
        )
        subscription = self._get_subscription(user_id, for_update=True)
        if subscription is None:
            valid_from = current
            period_start = current
            resolved_accounts = max_accounts or DEFAULT_MAX_ACCOUNTS
            resolved_tasks = max_tasks or DEFAULT_MAX_TASKS
            subscription = UserSubscription(
                user_id=user_id,
                valid_from=valid_from,
                valid_until=period_start + timedelta(days=duration_days),
                max_accounts=resolved_accounts,
                max_tasks=resolved_tasks,
                updated_at=current,
                version=1,
            )
            self.session.add(subscription)
        else:
            persisted_until = _aware(subscription.valid_until)
            reconcile_subscription(self.session, subscription, current)
            # Re-read the effective limits after a boundary transition. The
            # period end is intentionally retained so queued grants continue
            # to append after it.
            persisted_until = _aware(subscription.valid_until)
            period_start = max(current, persisted_until)
            if persisted_until <= current:
                subscription.valid_from = current
                subscription.pending_max_accounts = None
                subscription.pending_max_tasks = None
                subscription.pending_valid_from = None
            resolved_accounts = (
                subscription.max_accounts if max_accounts is None else max_accounts
            )
            resolved_tasks = subscription.max_tasks if max_tasks is None else max_tasks
            subscription.valid_until = period_start + timedelta(days=duration_days)
            active = persisted_until > current
            is_downgrade = active and (
                resolved_accounts < subscription.max_accounts
                or resolved_tasks < subscription.max_tasks
            )
            if not is_downgrade:
                future_lower_grants = self.session.scalars(
                    select(SubscriptionGrant).where(
                        SubscriptionGrant.user_id == user_id,
                        SubscriptionGrant.valid_from > current,
                        SubscriptionGrant.valid_from <= period_start,
                        SubscriptionGrant.valid_until > current,
                        SubscriptionGrant.superseded_at.is_(None),
                        or_(
                            SubscriptionGrant.max_accounts < resolved_accounts,
                            SubscriptionGrant.max_tasks < resolved_tasks,
                        ),
                    )
                ).all()
                for future_grant in future_lower_grants:
                    future_grant.superseded_at = current
                subscription.max_accounts = resolved_accounts
                subscription.max_tasks = resolved_tasks
            subscription.updated_at = current
            subscription.version += 1

        grant = SubscriptionGrant(
            user_id=user_id,
            source=source,
            actor_user_id=actor_id,
            redemption_code_id=redemption_code_id,
            duration_days=duration_days,
            valid_from=period_start,
            valid_until=_aware(subscription.valid_until),
            max_accounts=resolved_accounts,
            max_tasks=resolved_tasks,
            created_at=current,
        )
        self.session.add(grant)
        self.session.flush()
        _refresh_pending_subscription(self.session, subscription, current)
        return subscription, grant

    def _get_subscription(
        self, user_id: str, *, for_update: bool = False
    ) -> UserSubscription | None:
        query = select(UserSubscription).where(UserSubscription.user_id == user_id)
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def _require_admin(self, actor_id: str) -> User:
        actor = self.session.get(User, actor_id)
        if actor is None:
            raise NotFound("管理员不存在")
        if actor.role != "admin" or actor.status != "active":
            raise ValidationError("需要管理员权限")
        return actor

    def _require_user(self, user_id: str) -> User:
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFound("用户不存在")
        return user

    def _now(self) -> datetime:
        return _aware(self.now())

    @staticmethod
    def _detail(duration_days: int, max_accounts: int, max_tasks: int) -> str:
        return (
            f"duration_days={duration_days};max_accounts={max_accounts};"
            f"max_tasks={max_tasks}"
        )
