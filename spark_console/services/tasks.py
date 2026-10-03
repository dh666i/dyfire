from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from spark_console.models import (
    DouyinContactIdentity,
    SparkTask,
    SparkTaskTargetIdentity,
    TaskRun,
)
from spark_console.services import Conflict, NotFound, ValidationError
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.task_targets import (
    TARGET_IDENTITY_LENGTH,
    TARGET_NAME_LENGTH,
    normalize_target_identity,
    normalize_target_name,
    targets_conflict,
    task_target_key,
)


_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
TEXT_MESSAGE_KIND = "text"
DOUYIN_STICKER_MESSAGE_KIND = "douyin_sticker"
DOUYIN_SPARK_STICKER = "续火花"


def _normalize_message_payload(
    message_kind: str, message_template: str
) -> tuple[str, str]:
    kind = str(message_kind or TEXT_MESSAGE_KIND).strip().lower()
    message = str(message_template or "").strip()
    if kind == TEXT_MESSAGE_KIND:
        if not message or len(message) > 500:
            raise ValidationError("消息内容须为 1–500 个字符")
        return kind, message
    if kind == DOUYIN_STICKER_MESSAGE_KIND:
        if message and message != DOUYIN_SPARK_STICKER:
            raise ValidationError("当前仅支持续火花表情")
        return kind, DOUYIN_SPARK_STICKER
    raise ValidationError("不支持的消息类型")


class TaskService:
    def __init__(self, session: Session, accounts: AccountService, audit: AuditService):
        self.session = session
        self.accounts = accounts
        self.audit = audit

    def get_owned(self, owner_id: str, task_id: str) -> SparkTask:
        task = self.session.scalar(
            select(SparkTask).where(
                SparkTask.id == task_id, SparkTask.owner_user_id == owner_id
            )
        )
        if task is None:
            raise NotFound("task not found")
        return task

    def list_owned(self, owner_id: str) -> list[SparkTask]:
        return list(
            self.session.scalars(
                select(SparkTask)
                .where(SparkTask.owner_user_id == owner_id)
                .order_by(SparkTask.send_time, SparkTask.created_at)
            ).all()
        )

    def create(
        self,
        owner_id: str,
        account_id: str,
        target_name: str,
        send_time: str,
        message_template: str,
        target_sec_uid: str | None = None,
        message_kind: str = TEXT_MESSAGE_KIND,
    ) -> SparkTask:
        return self.create_many(
            owner_id,
            account_id,
            ((target_name, target_sec_uid),),
            send_time,
            message_template,
            message_kind=message_kind,
        )[0]

    @staticmethod
    def normalize_targets(
        targets: Iterable[tuple[str, str | None]],
    ) -> list[tuple[str, str]]:
        normalized: list[list[str] | None] = []
        manual_index_by_name: dict[str, int] = {}
        index_by_sec_uid: dict[str, int] = {}
        stable_name_keys: set[str] = set()
        for raw_name, raw_sec_uid in targets:
            target = str(raw_name or "").strip()
            stable_target = normalize_target_identity(raw_sec_uid)
            if not target or len(target) > TARGET_NAME_LENGTH:
                raise ValidationError(
                    f"好友名称须为 1–{TARGET_NAME_LENGTH} 个字符"
                )
            if len(stable_target) > TARGET_IDENTITY_LENGTH:
                raise ValidationError(
                    f"好友身份标识不得超过 {TARGET_IDENTITY_LENGTH} 个字符"
                )

            name_key = normalize_target_name(target)
            if stable_target:
                identity_index = index_by_sec_uid.get(stable_target)
                if identity_index is not None:
                    stable_name_keys.add(name_key)
                    manual_index = manual_index_by_name.pop(name_key, None)
                    if manual_index is not None and manual_index != identity_index:
                        normalized[manual_index] = None
                    continue

                manual_index = manual_index_by_name.pop(name_key, None)
                if manual_index is not None:
                    normalized[manual_index] = [target, stable_target]
                    index = manual_index
                else:
                    index = len(normalized)
                    normalized.append([target, stable_target])
                index_by_sec_uid[stable_target] = index
                stable_name_keys.add(name_key)
                continue

            if name_key in stable_name_keys or name_key in manual_index_by_name:
                continue
            index = len(normalized)
            normalized.append([target, ""])
            manual_index_by_name[name_key] = index

        result = [item for item in normalized if item is not None]
        if not result:
            raise ValidationError("请至少选择一位目标好友")
        return [(target, stable_target) for target, stable_target in result]

    def _active_schedule_conflict(
        self,
        account_id: str,
        send_time: str,
        target_name: str,
        target_sec_uid: str | None,
        *,
        exclude_task_id: str | None = None,
    ) -> bool:
        """Check one schedule identity without materialising the account queue.

        This is deliberately expressed as an existence query.  The previous
        implementation loaded every active task for an account/time slot and
        compared them in Python, so editing or re-enabling one task became
        proportional to the user's complete task backlog.
        """
        normalized_name = normalize_target_name(target_name)
        stable_target = normalize_target_identity(target_sec_uid)
        statement = (
            select(SparkTask.id)
            .outerjoin(
                SparkTaskTargetIdentity,
                SparkTaskTargetIdentity.task_id == SparkTask.id,
            )
            .where(
                SparkTask.douyin_account_id == account_id,
                SparkTask.send_time == send_time,
                SparkTask.enabled.is_(True),
            )
        )
        if exclude_task_id is not None:
            statement = statement.where(SparkTask.id != exclude_task_id)
        if stable_target:
            # A stable identity conflicts only with itself.  Legacy/manual
            # rows without a binding still conflict by display name.
            statement = statement.where(
                or_(
                    SparkTaskTargetIdentity.sec_uid == stable_target,
                    and_(
                        SparkTaskTargetIdentity.sec_uid.is_(None),
                        func.lower(SparkTask.target_name) == normalized_name,
                    ),
                )
            )
        else:
            # Manual targets retain the historical display-name conflict rule,
            # including rows that already carry a stable identity.
            statement = statement.where(
                func.lower(SparkTask.target_name) == normalized_name
            )
        return self.session.scalar(statement.limit(1)) is not None

    @staticmethod
    def _has_target_conflict(
        existing_targets: Iterable[tuple[str, str | None]],
        target_name: str,
        target_sec_uid: str | None,
    ) -> bool:
        return any(
            targets_conflict(
                existing_name,
                existing_sec_uid,
                target_name,
                target_sec_uid,
            )
            for existing_name, existing_sec_uid in existing_targets
        )

    def create_many(
        self,
        owner_id: str,
        account_id: str,
        targets: Iterable[tuple[str, str | None]],
        send_time: str,
        message_template: str,
        message_kind: str = TEXT_MESSAGE_KIND,
    ) -> list[SparkTask]:
        normalized_targets = self.normalize_targets(targets)
        kind, message = _normalize_message_payload(message_kind, message_template)
        if not _TIME_RE.fullmatch(send_time):
            raise ValidationError("发送时间格式必须为 HH:MM")
        account = self.accounts.get_owned(owner_id, account_id)
        if account.validation_state == "invalid":
            raise ValidationError("抖音账号登录状态已失效，请先重新登录")

        for _target, stable_target in normalized_targets:
            if stable_target and self.session.get(
                DouyinContactIdentity, (account.id, stable_target)
            ) is None:
                raise ValidationError("所选好友不属于当前抖音账号")

        # ``target_key`` is the same canonical identity used by the unique
        # schedule index.  Query only candidate keys instead of loading every
        # task for the account and comparing them in Python; large accounts
        # otherwise turn creation into an O(existing_tasks * new_targets)
        # operation.  The identity join remains as a compatibility fallback
        # for rows created before target_key was introduced.
        candidate_keys = {
            task_target_key(target, stable_target)
            for target, stable_target in normalized_targets
        }
        matching_keys = set(
            self.session.scalars(
                select(SparkTask.target_key).where(
                    SparkTask.douyin_account_id == account.id,
                    SparkTask.target_key.in_(candidate_keys),
                )
            ).all()
        )
        if matching_keys:
            raise Conflict("该好友已有续火任务，请编辑或恢复已有任务，无需重复创建")

        # Preserve the legacy rule that a manual name conflicts with a stable
        # identity carrying the same display name (and vice versa).  Restrict
        # this compatibility query to the candidate names so it does not
        # materialise the account's complete task set.
        candidate_names = {
            normalize_target_name(target) for target, _stable in normalized_targets
        }
        name_matches = list(self.session.execute(
            select(SparkTask.target_name, SparkTaskTargetIdentity.sec_uid)
            .outerjoin(
                SparkTaskTargetIdentity,
                SparkTaskTargetIdentity.task_id == SparkTask.id,
            )
            .where(
                SparkTask.douyin_account_id == account.id,
                func.lower(SparkTask.target_name).in_(candidate_names),
            )
        ).all())
        if any(
            self._has_target_conflict(name_matches, target, stable_target)
            for target, stable_target in normalized_targets
        ):
            raise Conflict("该好友已有续火任务，请编辑或恢复已有任务，无需重复创建")

        # A small compatibility check handles legacy rows whose key was not
        # normalised successfully.  It only runs when the indexed lookup is
        # inconclusive and therefore does not affect the common path.
        legacy_targets = list(self.session.execute(
            select(SparkTask.target_name, SparkTaskTargetIdentity.sec_uid)
            .outerjoin(SparkTaskTargetIdentity, SparkTaskTargetIdentity.task_id == SparkTask.id)
            .where(
                SparkTask.douyin_account_id == account.id,
                or_(
                    SparkTask.target_key == "",
                    SparkTask.target_key.like("legacy:%"),
                ),
                or_(
                    func.lower(SparkTask.target_name).in_(candidate_names),
                    SparkTaskTargetIdentity.sec_uid.in_({
                        stable_target
                        for _target, stable_target in normalized_targets
                        if stable_target
                    }),
                ),
            )
        ).all())
        if any(
            self._has_target_conflict(legacy_targets, target, stable_target)
            for target, stable_target in normalized_targets
        ):
            raise Conflict("该好友已有续火任务，请编辑或恢复已有任务，无需重复创建")

        from spark_console.schedule_slots import next_runs, plan as plan_slots

        planned = plan_slots(self.session, account.id, len(normalized_targets), send_time)
        times = list(planned["times"])
        runs = next_runs(times)
        tasks = [
            SparkTask(
                owner_user_id=owner_id,
                douyin_account_id=account.id,
                target_name=target,
                target_key=task_target_key(target, _stable_target),
                send_time=slot,
                message_template=message,
                message_kind=kind,
                enabled=True,
                next_run_at=run_at,
            )
            for (target, _stable_target), slot, run_at in zip(
                normalized_targets, times, runs, strict=True
            )
        ]
        try:
            with self.session.begin_nested():
                self.session.add_all(tasks)
                self.session.flush()
                self.session.add_all(
                    SparkTaskTargetIdentity(task_id=task.id, sec_uid=stable_target)
                    for task, (_target, stable_target) in zip(
                        tasks, normalized_targets, strict=True
                    )
                    if stable_target
                )
                self.session.flush()
        except IntegrityError as error:
            raise Conflict("相同账号、好友和时间的启用任务已存在") from error
        for task in tasks:
            self.audit.write(owner_id, "task.created", "spark_task", task.id)
        return tasks

    def set_enabled_owned(
        self,
        owner_id: str,
        task_id: str,
        enabled: bool,
        *,
        enforce_capacity: bool = True,
    ) -> SparkTask:
        task = self.get_owned(owner_id, task_id)
        if enabled and task.douyin_account_id is None:
            raise ValidationError("账号已删除，无法启用任务")
        enabled_target_key: str | None = None
        if enabled:
            # A paused task remains retained but does not consume quota. The
            # capacity check belongs here as well as at creation so every
            # resume path (API, legacy form and batch actions) has the same
            # rule. Administrators may intentionally repair over-limit users.
            if enforce_capacity and not task.enabled:
                from spark_console.services.subscriptions import SubscriptionService

                SubscriptionService(
                    self.session, self.audit
                ).require_task_capacity(owner_id)
            account = self.accounts.get_owned(owner_id, task.douyin_account_id)
            if account.validation_state == "invalid":
                raise ValidationError("抖音账号登录状态已失效，请先重新登录")
            binding = self.session.get(SparkTaskTargetIdentity, task.id)
            stable_target = binding.sec_uid if binding is not None else ""
            if self._active_schedule_conflict(
                account.id,
                task.send_time,
                task.target_name,
                stable_target,
                exclude_task_id=task.id,
            ):
                raise Conflict("相同账号、好友和时间的启用任务已存在")
            enabled_target_key = task_target_key(task.target_name, stable_target)
        try:
            with self.session.begin_nested():
                if enabled_target_key is not None:
                    task.target_key = enabled_target_key
                task.enabled = enabled
                self.session.flush()
        except IntegrityError as error:
            raise Conflict("相同账号、好友和时间的启用任务已存在") from error
        self.audit.write(owner_id, "task.enabled" if enabled else "task.disabled", "spark_task", task.id)
        return task

    def request_unsubmitted_retry(self, owner_id: str, run_id: str, now: datetime) -> SparkTask:
        run = self.session.get(TaskRun, run_id)
        if run is None:
            raise NotFound("执行记录不存在")
        task = self.get_owned(owner_id, run.task_id)
        if run.submitted_at is not None or run.status in (
            "success",
            "submitted_unconfirmed",
            "running",
        ):
            raise ValidationError("消息可能已发出，不能自动补发")
        if run.status not in ("failed", "skipped"):
            raise ValidationError("当前状态不能补发")
        if not task.enabled:
            raise ValidationError("任务已暂停，请先恢复任务")
        if self.session.scalar(
            select(TaskRun.id)
            .where(
                TaskRun.task_id == task.id,
                TaskRun.status.in_(("running", "retrying")),
            )
            .limit(1)
        ):
            raise Conflict("已有正在执行或等待中的发送")
        next_at = task.next_run_at
        if next_at is not None:
            if next_at.tzinfo is None:
                next_at = next_at.replace(tzinfo=timezone.utc)
            current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
            local_next = next_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%H:%M")
            if next_at <= current + timedelta(minutes=2) and local_next != task.send_time:
                raise Conflict("补发已在排队")
        if not task.douyin_account_id:
            raise ValidationError("发送账号不可用")
        account = self.accounts.get_owned(owner_id, task.douyin_account_id)
        if account.validation_state in ("invalid", "verification"):
            raise ValidationError("请先完成账号验证或重新登录")
        task.next_run_at = now
        self.audit.write(owner_id, "task.retry_requested", "task_run", run.id)
        return task

    def update_owned(
        self,
        owner_id: str,
        task_id: str,
        account_id: str,
        target_name: str,
        send_time: str,
        message_template: str,
        target_sec_uid: str | None = None,
        message_kind: str = TEXT_MESSAGE_KIND,
    ) -> SparkTask:
        task = self.get_owned(owner_id, task_id)
        target = target_name.strip()
        kind, message = _normalize_message_payload(message_kind, message_template)
        if not target or len(target) > TARGET_NAME_LENGTH:
            raise ValidationError(f"好友名称须为 1–{TARGET_NAME_LENGTH} 个字符")
        if not _TIME_RE.fullmatch(send_time):
            raise ValidationError("发送时间格式必须为 HH:MM")
        account = self.accounts.get_owned(owner_id, account_id)
        if task.enabled and account.validation_state == "invalid":
            raise ValidationError("抖音账号登录状态已失效，请先重新登录")
        stable_target = normalize_target_identity(target_sec_uid)
        if len(stable_target) > TARGET_IDENTITY_LENGTH:
            raise ValidationError(
                f"好友身份标识不得超过 {TARGET_IDENTITY_LENGTH} 个字符"
            )
        if stable_target and self.session.get(
            DouyinContactIdentity, (account.id, stable_target)
        ) is None:
            raise ValidationError("所选好友不属于当前抖音账号")
        binding = self.session.get(SparkTaskTargetIdentity, task.id)
        old_stable_target = binding.sec_uid if binding is not None else ""
        old_canonical_key = task_target_key(task.target_name, old_stable_target)
        new_canonical_key = task_target_key(target, stable_target)
        schedule_identity_unchanged = (
            task.douyin_account_id == account.id
            and task.send_time == send_time
            and old_canonical_key == new_canonical_key
        )
        if task.enabled and not schedule_identity_unchanged:
            if self._active_schedule_conflict(
                account.id,
                send_time,
                target,
                stable_target,
                exclude_task_id=task.id,
            ):
                raise Conflict("相同账号、好友和时间的启用任务已存在")

        local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
        hour, minute = map(int, send_time.split(":"))
        candidate = local_now.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= local_now:
            from datetime import timedelta

            candidate += timedelta(days=1)
        try:
            with self.session.begin_nested():
                task.douyin_account_id = account.id
                task.target_name = target
                task.target_key = (
                    task.target_key
                    if schedule_identity_unchanged
                    and task.target_key.startswith("legacy:")
                    else new_canonical_key
                )
                task.send_time = send_time
                task.message_template = message
                task.message_kind = kind
                task.next_run_at = candidate.astimezone(timezone.utc)

                if stable_target:
                    if binding is None:
                        self.session.add(
                            SparkTaskTargetIdentity(
                                task_id=task.id, sec_uid=stable_target
                            )
                        )
                    else:
                        binding.sec_uid = stable_target
                elif binding is not None:
                    self.session.delete(binding)
                self.session.flush()
        except IntegrityError as error:
            raise Conflict("相同账号、好友和时间的启用任务已存在") from error
        self.audit.write(owner_id, "task.updated", "spark_task", task.id)
        return task

    def delete_owned(self, owner_id: str, task_id: str) -> None:
        task = self.get_owned(owner_id, task_id)
        running = self.session.scalar(
            select(TaskRun.id)
            .where(TaskRun.task_id == task.id, TaskRun.status == "running")
            .limit(1)
        )
        if running is not None:
            raise Conflict("任务正在执行，请完成后再删除")
        self.session.delete(task)
        self.audit.write(owner_id, "task.deleted", "spark_task", task_id)
