"""Bounded, conservative data-retention maintenance.

The retention command is intentionally separate from workers and web requests.
It can be run from a maintenance container or a scheduled host job.  A dry
run is the default; applying a policy requires ``dry_run=False`` explicitly.
Only records with an unambiguous terminal state are eligible for deletion.
In particular, queued/running/retrying work and submitted-but-unconfirmed
records are never selected by this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from spark_console.models import (
    AccountIncident,
    AuditEvent,
    DouyinLoginAction,
    DouyinLoginChallenge,
    DouyinLoginInput,
    DouyinLoginSession,
    DouyinStreakObservation,
    MailDelivery,
    TaskRun,
    TaskRunAttempt,
    TaskRunEvent,
    TaskHealthRetry,
    WebSession,
    utc_now,
)


# These statuses are deliberately expressed as strings.  They match the
# persisted values even when an older database predates the current enum.
TERMINAL_RUN_STATUSES = frozenset({"success", "failed", "skipped"})
PROTECTED_RUN_STATUSES = frozenset(
    {"queued", "running", "retrying", "submitted_unconfirmed"}
)
TERMINAL_LOGIN_STATUSES = frozenset(
    {"succeeded", "failed", "expired", "cancelled"}
)
ACTIVE_LOGIN_STATUSES = frozenset(
    {"queued", "loading_qr", "awaiting_scan", "confirming"}
)
TERMINAL_MAIL_STATUSES = frozenset({"accepted", "failed", "cancelled"})
PROTECTED_MAIL_STATUSES = frozenset({"queued", "queued_secure", "sending", "uncertain"})

DEFAULT_RUNS_DAYS = 365
DEFAULT_SESSIONS_DAYS = 30
DEFAULT_MAIL_DAYS = 30
DEFAULT_AUDIT_DAYS = 365
DEFAULT_BATCH_SIZE = 500
MAX_RETENTION_DAYS = 36500  # 100 years; catches accidental timestamps.
# Keep IN-clause deletes below SQLite builds compiled with the historical
# 999-variable limit. One invocation still has a finite, predictable write
# bound across every category.
MAX_BATCH_SIZE = 900
# Stable, application-specific PostgreSQL advisory-lock key.  The lock is
# transaction-scoped, so it is released automatically on commit/rollback.
RETENTION_ADVISORY_LOCK_KEY = 0x445946495245544E


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _positive_int(value: int, *, name: str, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not 1 <= parsed <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return parsed


@dataclass(frozen=True)
class RetentionPolicy:
    """Retention windows in days and the maximum rows handled per batch."""

    runs_days: int = DEFAULT_RUNS_DAYS
    sessions_days: int = DEFAULT_SESSIONS_DAYS
    mail_days: int = DEFAULT_MAIL_DAYS
    audit_days: int = DEFAULT_AUDIT_DAYS
    batch_size: int = DEFAULT_BATCH_SIZE

    def __post_init__(self) -> None:
        for name in ("runs_days", "sessions_days", "mail_days", "audit_days"):
            object.__setattr__(
                self,
                name,
                _positive_int(getattr(self, name), name=f"--{name.replace('_', '-')}", maximum=MAX_RETENTION_DAYS),
            )
        object.__setattr__(
            self,
            "batch_size",
            _positive_int(self.batch_size, name="--batch-size", maximum=MAX_BATCH_SIZE),
        )

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class RetentionCategory:
    """Result for one table family."""

    candidates: int = 0
    deleted: int = 0
    protected: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.candidates - self.deleted)

    def as_dict(self) -> dict[str, int]:
        return {**asdict(self), "remaining": self.remaining}


@dataclass
class RetentionReport:
    """Machine-readable maintenance result."""

    dry_run: bool
    as_of: datetime
    policy: RetentionPolicy
    categories: dict[str, RetentionCategory]
    lock_acquired: bool = True

    @property
    def candidate_count(self) -> int:
        return sum(item.candidates for item in self.categories.values())

    @property
    def deleted_count(self) -> int:
        return sum(item.deleted for item in self.categories.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "as_of": self.as_of.isoformat(),
            "policy": self.policy.as_dict(),
            "categories": {
                name: item.as_dict() for name, item in self.categories.items()
            },
            "candidate_count": self.candidate_count,
            "deleted_count": self.deleted_count,
            "lock_acquired": self.lock_acquired,
        }


def _acquire_retention_lock(db: Session) -> bool:
    """Serialize retention passes without holding an application mutex.

    PostgreSQL uses a transaction-scoped advisory lock and returns immediately
    when another maintenance job is active.  SQLite has no advisory locks;
    the no-op singleton update obtains its writer lock before any candidate is
    read, preventing a second SQLite process from deleting children selected
    by the first process while the transaction is open.
    """
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        return bool(
            db.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": RETENTION_ADVISORY_LOCK_KEY},
            )
        )
    if dialect == "sqlite":
        # create_schema always provisions worker_lock(id=1).  Keep the update
        # value identical so this only claims SQLite's write transaction.
        connection = db.connection()
        previous_timeout = int(connection.exec_driver_sql("PRAGMA busy_timeout").scalar() or 0)
        connection.exec_driver_sql("PRAGMA busy_timeout=0")
        try:
            result = db.execute(
                text("UPDATE worker_lock SET id = id WHERE id = 1")
            )
            return result.rowcount in (1, -1)
        except OperationalError:
            # SQLite marks the failed transaction as rollback-only.  Clear it
            # before returning a report so the caller's session_scope can
            # close normally without raising PendingRollbackError.
            db.rollback()
            return False
        finally:
            try:
                connection.exec_driver_sql(f"PRAGMA busy_timeout={max(0, previous_timeout)}")
            except Exception:
                # SQLite may invalidate the connection after a lock error;
                # the session_scope cleanup will dispose it safely.
                pass
    return True


def _count(db: Session, model: type, *criteria: Any) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(*criteria)) or 0)


def _ids(db: Session, model: type, id_column: Any, *criteria: Any, batch_size: int) -> list[str]:
    statement = (
        select(id_column)
        .select_from(model)
        .where(*criteria)
        .order_by(id_column)
        .limit(batch_size)
    )
    if db.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    return list(db.scalars(statement))


def _delete_one_batch(
    db: Session,
    model: type,
    id_column: Any,
    criteria: tuple[Any, ...],
    *,
    batch_size: int,
    child_models: tuple[tuple[type, Any], ...] = (),
) -> int:
    """Delete one bounded ID batch and return the affected row count.

    Children are removed explicitly before their parent.  This keeps the
    command correct on installations where foreign-key cascade enforcement was
    disabled in a legacy SQLite connection, and makes the deletion intent
    visible in query logs.
    """

    identifiers = _ids(
        db,
        model,
        id_column,
        *criteria,
        batch_size=batch_size,
    )
    if not identifiers:
        return 0
    for child_model, child_column in child_models:
        db.execute(delete(child_model).where(child_column.in_(identifiers)))
    result = db.execute(delete(model).where(id_column.in_(identifiers), *criteria))
    db.flush()
    affected = result.rowcount
    return len(identifiers) if affected is None or affected < 0 else int(affected)


def _run_criteria(cutoff: datetime) -> tuple[Any, ...]:
    return (
        TaskRun.status.in_(TERMINAL_RUN_STATUSES),
        TaskRun.finished_at.is_not(None),
        TaskRun.finished_at <= cutoff,
    )


def _login_criteria(cutoff: datetime) -> tuple[Any, ...]:
    return (
        DouyinLoginSession.status.in_(TERMINAL_LOGIN_STATUSES),
        DouyinLoginSession.finished_at.is_not(None),
        DouyinLoginSession.finished_at <= cutoff,
    )


def _mail_criteria(cutoff: datetime) -> tuple[Any, ...]:
    return (
        MailDelivery.status.in_(TERMINAL_MAIL_STATUSES),
        MailDelivery.updated_at.is_not(None),
        MailDelivery.updated_at <= cutoff,
    )


def _web_session_criteria(cutoff: datetime, now: datetime) -> tuple[Any, ...]:
    # A session must be both older than the retention window and expired now.
    # This protects a long-lived session even if its creation date is old.
    return (
        WebSession.created_at.is_not(None),
        WebSession.created_at <= cutoff,
        WebSession.expires_at <= now,
    )


def _audit_criteria(cutoff: datetime) -> tuple[Any, ...]:
    return (AuditEvent.created_at.is_not(None), AuditEvent.created_at <= cutoff)


def _protected_counts(db: Session, now: datetime) -> dict[str, int]:
    """Return diagnostics for rows intentionally excluded by the policy."""

    return {
        "runs": _count(
            db,
            TaskRun,
            TaskRun.status.in_(PROTECTED_RUN_STATUSES),
        ),
        "login_sessions": _count(
            db,
            DouyinLoginSession,
            DouyinLoginSession.status.in_(ACTIVE_LOGIN_STATUSES),
        ),
        "web_sessions": _count(db, WebSession, WebSession.expires_at > now),
        "mail": _count(
            db,
            MailDelivery,
            MailDelivery.status.in_(PROTECTED_MAIL_STATUSES),
        ),
        "audits": 0,
    }


def retain(
    db: Session,
    policy: RetentionPolicy | None = None,
    *,
    dry_run: bool = True,
    now: datetime | None = None,
) -> RetentionReport:
    """Plan or apply one retention pass.

    ``dry_run`` defaults to ``True`` by design.  Applying rows is explicit and
    still bounded by ``policy.batch_size``.  All timestamps are normalized to
    UTC before calculating cutoffs.  A caller may invoke this from an outer
    transaction; the function flushes each batch but leaves transaction
    ownership to the caller.
    """

    policy = policy or RetentionPolicy()
    as_of = _utc(now)
    if not _acquire_retention_lock(db):
        return RetentionReport(
            dry_run=dry_run,
            as_of=as_of,
            policy=policy,
            categories={
                name: RetentionCategory()
                for name in ("runs", "login_sessions", "web_sessions", "mail", "audits", "account_incidents", "streak_observations")
            },
            lock_acquired=False,
        )
    cutoffs = {
        "runs": as_of - timedelta(days=policy.runs_days),
        "sessions": as_of - timedelta(days=policy.sessions_days),
        "mail": as_of - timedelta(days=policy.mail_days),
        "audits": as_of - timedelta(days=policy.audit_days),
    }
    run_criteria = _run_criteria(cutoffs["runs"])
    login_criteria = _login_criteria(cutoffs["sessions"])
    web_criteria = _web_session_criteria(cutoffs["sessions"], as_of)
    mail_criteria = _mail_criteria(cutoffs["mail"])
    audit_criteria = _audit_criteria(cutoffs["audits"])
    incident_criteria = (AccountIncident.resolved_at.is_not(None), AccountIncident.resolved_at <= cutoffs['runs'])
    streak_criteria = (DouyinStreakObservation.observed_at <= as_of - timedelta(days=365),)

    categories = {
        "runs": RetentionCategory(candidates=_count(db, TaskRun, *run_criteria)),
        "login_sessions": RetentionCategory(
            candidates=_count(db, DouyinLoginSession, *login_criteria)
        ),
        "web_sessions": RetentionCategory(
            candidates=_count(db, WebSession, *web_criteria)
        ),
        "mail": RetentionCategory(candidates=_count(db, MailDelivery, *mail_criteria)),
        "audits": RetentionCategory(candidates=_count(db, AuditEvent, *audit_criteria)),
        "account_incidents": RetentionCategory(candidates=_count(db, AccountIncident, *incident_criteria),
            protected=_count(db,AccountIncident,AccountIncident.resolved_at.is_(None))),
        "streak_observations": RetentionCategory(candidates=_count(db, DouyinStreakObservation, *streak_criteria)),
    }
    protected = _protected_counts(db, as_of)
    for name, count in protected.items():
        if name in categories:
            categories[name].protected = count

    if not dry_run:
        categories["runs"].deleted = _delete_one_batch(
            db,
            TaskRun,
            TaskRun.id,
            run_criteria,
            batch_size=policy.batch_size,
            child_models=(
                (TaskRunEvent, TaskRunEvent.run_id),
                (TaskRunAttempt, TaskRunAttempt.run_id),
                (TaskHealthRetry, TaskHealthRetry.run_id),
            ),
        )
        categories["login_sessions"].deleted = _delete_one_batch(
            db,
            DouyinLoginSession,
            DouyinLoginSession.id,
            login_criteria,
            batch_size=policy.batch_size,
            child_models=(
                (DouyinLoginChallenge, DouyinLoginChallenge.scan_id),
                (DouyinLoginAction, DouyinLoginAction.scan_id),
                (DouyinLoginInput, DouyinLoginInput.scan_id),
            ),
        )
        categories["web_sessions"].deleted = _delete_one_batch(
            db,
            WebSession,
            WebSession.id,
            web_criteria,
            batch_size=policy.batch_size,
        )
        categories["mail"].deleted = _delete_one_batch(
            db,
            MailDelivery,
            MailDelivery.id,
            mail_criteria,
            batch_size=policy.batch_size,
        )
        categories["audits"].deleted = _delete_one_batch(
            db,
            AuditEvent,
            AuditEvent.id,
            audit_criteria,
            batch_size=policy.batch_size,
        )
        categories['account_incidents'].deleted = _delete_one_batch(db,AccountIncident,AccountIncident.id,
            incident_criteria,batch_size=policy.batch_size)
        categories['streak_observations'].deleted = _delete_one_batch(
            db, DouyinStreakObservation, DouyinStreakObservation.id,
            streak_criteria, batch_size=policy.batch_size,
        )
        if sum(item.deleted for item in categories.values()):
            db.add(
                AuditEvent(
                    actor_user_id=None,
                    action="retention.applied",
                    resource_type="maintenance",
                    detail=(
                        f"runs={categories['runs'].deleted};"
                        f"login_sessions={categories['login_sessions'].deleted};"
                        f"web_sessions={categories['web_sessions'].deleted};"
                        f"mail={categories['mail'].deleted};"
                        f"audits={categories['audits'].deleted}"
                    )[:240],
                    # Keep the maintenance audit's own timestamp real even
                    # when ``--as-of`` is used for a historical drill.
                    created_at=utc_now(),
                )
            )
            db.flush()

    return RetentionReport(
        dry_run=dry_run,
        as_of=as_of,
        policy=policy,
        categories=categories,
        lock_acquired=True,
    )
