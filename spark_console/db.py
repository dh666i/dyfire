from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy import select, update

from spark_console.config import Settings
from spark_console.models import Base, DouyinAccount, WorkerLock
from spark_console.worker_sharding import account_dispatch_key
from spark_console.task_targets import (
    TARGET_KEY_LENGTH,
    legacy_task_target_key,
    task_target_key,
)


SQLITE_LOCK_TIMEOUT_SECONDS = 10.0
SQLITE_SCHEMA_VERSION = 13


def _enable_sqlite_wal(cursor) -> None:
    deadline = time.monotonic() + SQLITE_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            mode = cursor.execute("PRAGMA journal_mode").fetchone()[0].lower()
            if mode not in {"wal", "memory"}:
                cursor.execute("PRAGMA journal_mode=WAL").fetchone()
            return
        except sqlite3.OperationalError as error:
            message = str(error).lower()
            if "locked" not in message and "busy" not in message:
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def create_engine_for(settings: Settings) -> Engine:
    engine_options = {}
    if not settings.database_url.startswith("sqlite"):
        engine_options.update(
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            pool_recycle=settings.database_pool_recycle_seconds,
            pool_pre_ping=True,
        )
    engine = create_engine(
        settings.database_url,
        **engine_options,
        connect_args={
            "check_same_thread": False,
            "timeout": SQLITE_LOCK_TIMEOUT_SECONDS,
        }
        if settings.database_url.startswith("sqlite")
        else {},
    )
    if settings.database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def set_sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(
                    f"PRAGMA busy_timeout={int(SQLITE_LOCK_TIMEOUT_SECONDS * 1000)}"
                )
                _enable_sqlite_wal(cursor)
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()
    return engine


def create_schema(engine: Engine) -> None:
    """Create all declared tables additively for fresh and existing databases."""
    if engine.dialect.name == "sqlite":
        _initialize_sqlite_schema(engine)
        return

    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    account_columns = {column["name"] for column in inspector.get_columns("douyin_accounts")}
    if "dispatch_key" not in account_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE douyin_accounts ADD COLUMN dispatch_key BIGINT"))
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_douyin_accounts_dispatch_key "
            "ON douyin_accounts (dispatch_key)"
        ))
    _assert_postgres_schema_complete(engine)
    with session_scope(engine) as session:
        if session.get(WorkerLock, 1) is None:
            session.add(WorkerLock(id=1))
        for account in session.scalars(
            select(DouyinAccount).where(DouyinAccount.dispatch_key.is_(None))
        ).all():
            account.dispatch_key = account_dispatch_key(account.id)


def _assert_postgres_schema_complete(engine: Engine) -> None:
    """Fail closed when an existing PostgreSQL schema predates a column.

    ``create_all`` is intentionally additive for new databases but does not
    alter existing tables. Continuing with a partially migrated schema creates
    delayed runtime failures, so require an explicit reviewed migration before
    serving traffic.
    """
    inspector = inspect(engine)
    missing: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name not in actual:
                missing.append(f"{table_name}.{column.name}")
    if missing:
        raise RuntimeError(
            "PostgreSQL schema is incomplete; run the reviewed migration before "
            "starting Dyfire: " + ", ".join(sorted(missing)[:20])
        )
    required_indexes = {
        "uq_douyin_login_inputs_scan_active",
        "ix_douyin_accounts_dispatch_key",
        "ix_task_runs_status_finished",
        "ix_mail_deliveries_status_updated",
    }
    actual_indexes = {
        index["name"]
        for table_name in inspect(engine).get_table_names()
        for index in inspect(engine).get_indexes(table_name)
    }
    missing_indexes = sorted(required_indexes - actual_indexes)
    if missing_indexes:
        raise RuntimeError(
            "PostgreSQL schema is missing required indexes; run the reviewed "
            "migration before starting Dyfire: " + ", ".join(missing_indexes)
        )


def _initialize_sqlite_schema(engine: Engine) -> None:
    """Serialize SQLite table creation, migrations, and singleton setup."""
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            Base.metadata.create_all(connection)
            _run_sqlite_migrations(connection)
            connection.exec_driver_sql(
                "INSERT OR IGNORE INTO worker_lock "
                "(id, worker_id, lease_until) VALUES (1, NULL, NULL)"
            )
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()


def _run_sqlite_migrations(connection: Connection) -> None:
    migrations = (
        (1, "task_runs_reliability", _upgrade_sqlite_task_runs),
        (2, "login_input_delivery_receipts", _upgrade_sqlite_login_inputs),
        (3, "normalize_submitted_runs", _normalize_sqlite_submitted_runs),
        (4, "spark_task_message_kind", _upgrade_sqlite_task_message_kind),
        (5, "spark_task_target_key", _upgrade_sqlite_task_target_key),
        (6, "normalize_spark_task_target_keys", _normalize_sqlite_task_target_keys),
        (7, "login_session_mode", _upgrade_sqlite_login_session_mode),
        (8, "user_admin_note", _upgrade_sqlite_user_admin_note),
        (9, "redemption_code_retrieval", _upgrade_sqlite_redemption_code_retrieval),
        (10, "scale_query_indexes", _upgrade_sqlite_scale_query_indexes),
        (11, "account_dispatch_shards", _upgrade_sqlite_account_dispatch_shards),
        (12, "retention_query_indexes", _upgrade_sqlite_retention_query_indexes),
        (13, "account_queue_query_indexes", _upgrade_sqlite_account_queue_query_indexes),
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, "
        "name VARCHAR(128) NOT NULL, "
        "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    applied_versions = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT version FROM schema_migrations"
        ).fetchall()
    }
    for version, name, migrate in migrations:
        if version in applied_versions:
            continue
        migrate(connection)
        connection.exec_driver_sql(
            "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
            (version, name),
        )


def _upgrade_sqlite_login_session_mode(connection: Connection) -> None:
    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(douyin_login_sessions)"
        ).fetchall()
    }
    if "mode" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE douyin_login_sessions ADD COLUMN mode VARCHAR(16) NOT NULL DEFAULT 'login'"
        )


def _upgrade_sqlite_user_admin_note(connection: Connection) -> None:
    """Add the private administrator note without replacing an existing DB."""
    columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(users)").fetchall()
    }
    if "admin_note" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN admin_note VARCHAR(500)"
        )


def _upgrade_sqlite_redemption_code_retrieval(connection: Connection) -> None:
    """Persist newly issued redemption codes and remove pre-redemption expiry."""
    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(redemption_codes)"
        ).fetchall()
    }
    if "code" not in columns:
        # Existing codes cannot be reconstructed because prior releases only
        # stored SHA-256 digests.  Keep those rows nullable and fully support
        # retrieval for every code issued after this migration.
        connection.exec_driver_sql(
            "ALTER TABLE redemption_codes ADD COLUMN code VARCHAR(128)"
        )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_redemption_codes_code "
        "ON redemption_codes (code) WHERE code IS NOT NULL"
    )
    # The old table declared expires_at NOT NULL. Keep migration additive by
    # using a far-future sentinel for unredeemed legacy rows; service code
    # treats this as "never expires" until redemption.
    connection.exec_driver_sql(
        "UPDATE redemption_codes SET expires_at = '9999-12-31 23:59:59.999999' "
        "WHERE redeemed_at IS NULL AND revoked_at IS NULL "
        "AND expires_at IS NOT NULL"
    )


def _upgrade_sqlite_scale_query_indexes(connection: Connection) -> None:
    """Add compound indexes used by queue, admin and subscription queries."""
    indexes = (
        ("ix_user_subscriptions_valid_window", "user_subscriptions", "valid_from, valid_until"),
        ("ix_douyin_login_sessions_status_expires", "douyin_login_sessions", "status, expires_at"),
        ("ix_spark_tasks_enabled_next_run", "spark_tasks", "enabled, next_run_at"),
        ("ix_task_runs_status_scheduled", "task_runs", "status, scheduled_for"),
        ("ix_task_runs_task_scheduled_status", "task_runs", "task_id, scheduled_for, status"),
        ("ix_audit_events_created_action", "audit_events", "created_at, action"),
        ("ix_douyin_login_sessions_status_finished", "douyin_login_sessions", "status, finished_at"),
        ("ix_task_runs_status_finished", "task_runs", "status, finished_at"),
        ("ix_mail_deliveries_status_updated", "mail_deliveries", "status, updated_at"),
        ("ix_douyin_login_sessions_account_status_expires", "douyin_login_sessions", "account_id, status, expires_at"),
        ("ix_account_operations_expires", "account_operations", "expires_at"),
        ("ix_contact_directory_jobs_status_created", "contact_directory_jobs", "status, created_at"),
    )
    for name, table, columns in indexes:
        connection.exec_driver_sql(
            f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"
        )


def _upgrade_sqlite_account_dispatch_shards(connection: Connection) -> None:
    """Add and backfill the stable key used by horizontally sharded workers."""
    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(douyin_accounts)"
        ).fetchall()
    }
    if "dispatch_key" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE douyin_accounts ADD COLUMN dispatch_key BIGINT"
        )
    rows = connection.exec_driver_sql(
        "SELECT id FROM douyin_accounts WHERE dispatch_key IS NULL"
    ).fetchall()
    for (account_id,) in rows:
        connection.exec_driver_sql(
            "UPDATE douyin_accounts SET dispatch_key = ? WHERE id = ?",
            (account_dispatch_key(account_id), account_id),
        )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_douyin_accounts_dispatch_key "
        "ON douyin_accounts (dispatch_key)"
    )


def _upgrade_sqlite_retention_query_indexes(connection: Connection) -> None:
    """Index terminal-record timestamps used by bounded retention passes."""
    indexes = (
        ("ix_douyin_login_sessions_status_finished", "douyin_login_sessions", "status, finished_at"),
        ("ix_task_runs_status_finished", "task_runs", "status, finished_at"),
        ("ix_mail_deliveries_status_updated", "mail_deliveries", "status, updated_at"),
    )
    for name, table, columns in indexes:
        connection.exec_driver_sql(
            f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"
        )


def _upgrade_sqlite_account_queue_query_indexes(connection: Connection) -> None:
    indexes = (
        ("ix_douyin_login_sessions_account_status_expires", "douyin_login_sessions", "account_id, status, expires_at"),
        ("ix_account_operations_expires", "account_operations", "expires_at"),
        ("ix_contact_directory_jobs_status_created", "contact_directory_jobs", "status, created_at"),
    )
    for name, table, columns in indexes:
        connection.exec_driver_sql(
            f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"
        )


def _upgrade_sqlite_task_runs(connection: Connection) -> None:
    """Add worker reliability columns without replacing an existing SQLite DB."""
    additions = {
        "worker_id": "VARCHAR(64)",
        "attempt": "INTEGER NOT NULL DEFAULT 1",
        "lease_until": "DATETIME",
        "retry_after": "DATETIME",
        "submitted_at": "DATETIME",
    }
    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(task_runs)"
        ).fetchall()
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.exec_driver_sql(
                f"ALTER TABLE task_runs ADD COLUMN {name} {definition}"
            )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_task_runs_status_lease "
        "ON task_runs (status, lease_until)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_task_runs_task_retry "
        "ON task_runs (task_id, retry_after)"
    )


def _upgrade_sqlite_login_inputs(connection: Connection) -> None:
    """Add idempotency keys and persistent text-delivery receipts."""
    additions = {
        "request_id": "VARCHAR(36)",
        "delivery_status": "VARCHAR(16) NOT NULL DEFAULT 'queued'",
        "attempted_at": "DATETIME",
        "finished_at": "DATETIME",
    }
    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(douyin_login_inputs)"
        ).fetchall()
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.exec_driver_sql(
                f"ALTER TABLE douyin_login_inputs ADD COLUMN {name} {definition}"
            )

    # A legacy consumed row has already left the encrypted queue, but the old
    # schema cannot tell whether the browser accepted it. Preserve that truth
    # as ``uncertain`` instead of presenting it as a newly queued operation.
    connection.exec_driver_sql(
        "UPDATE douyin_login_inputs "
        "SET delivery_status = 'uncertain', "
        "attempted_at = COALESCE(attempted_at, consumed_at), "
        "finished_at = COALESCE(finished_at, consumed_at) "
        "WHERE consumed_at IS NOT NULL AND delivery_status = 'queued'"
    )
    # The pre-idempotency implementation tried to keep one pending row, but a
    # process crash or concurrent HTTP requests could still have left more
    # than one. Retain only the newest encrypted candidate and securely erase
    # older queued payloads before installing the active-row unique index.
    connection.exec_driver_sql(
        "UPDATE douyin_login_inputs "
        "SET ciphertext = NULL, nonce = NULL, "
        "consumed_at = COALESCE(consumed_at, CURRENT_TIMESTAMP), "
        "delivery_status = 'failed', "
        "finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP) "
        "WHERE delivery_status = 'queued' AND id NOT IN ("
        "SELECT MAX(id) FROM douyin_login_inputs "
        "WHERE delivery_status = 'queued' GROUP BY scan_id"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_douyin_login_inputs_scan_request "
        "ON douyin_login_inputs (scan_id, request_id)"
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_douyin_login_inputs_scan_active "
        "ON douyin_login_inputs (scan_id) "
        "WHERE delivery_status IN ('queued', 'claimed')"
    )


def _normalize_sqlite_submitted_runs(connection: Connection) -> None:
    """Normalize legacy submit-boundary rows without creating a resend window."""
    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(task_runs)"
        ).fetchall()
    }
    if "submitted_at" not in columns:
        return

    # Older builds stored this exact outcome as a green success even though
    # the page never observed the outgoing bubble. Keep the historical truth,
    # but present it consistently as a separate non-failure terminal state.
    connection.exec_driver_sql(
        "UPDATE task_runs "
        "SET status = 'submitted_unconfirmed', "
        "submitted_at = COALESCE(submitted_at, finished_at, started_at, scheduled_for) "
        "WHERE status = 'success' "
        "AND stage = 'submitted' "
        "AND error_code = 'delivery_confirmation_unavailable'"
    )
    # A legacy or interrupted row may have crossed the submit boundary before
    # the submitted_at column was persisted. Backfill the fence so no future
    # Worker can reclaim it and send the same message again.
    connection.exec_driver_sql(
        "UPDATE task_runs "
        "SET submitted_at = COALESCE(started_at, scheduled_for, CURRENT_TIMESTAMP) "
        "WHERE stage = 'submitted' AND submitted_at IS NULL"
    )


def _upgrade_sqlite_task_message_kind(connection: Connection) -> None:
    """Add an explicit payload type while preserving every legacy text task."""
    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(spark_tasks)"
        ).fetchall()
    }
    if "message_kind" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE spark_tasks "
            "ADD COLUMN message_kind VARCHAR(24) NOT NULL DEFAULT 'text'"
        )
    connection.exec_driver_sql(
        "UPDATE spark_tasks SET message_kind = 'text' "
        "WHERE message_kind IS NULL OR TRIM(message_kind) = ''"
    )


def _upgrade_sqlite_task_target_key(connection: Connection) -> None:
    """Install stable target uniqueness without discarding legacy schedules."""
    _ensure_sqlite_task_target_key_column(connection)
    _rebuild_sqlite_task_target_keys(connection)


def _normalize_sqlite_task_target_keys(connection: Connection) -> None:
    """Replace pre-hash v5 keys with the current canonical target-key format."""
    _ensure_sqlite_task_target_key_column(connection)
    _rebuild_sqlite_task_target_keys(connection)


def _ensure_sqlite_task_target_key_column(connection: Connection) -> None:
    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(spark_tasks)"
        ).fetchall()
    }
    if "target_key" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE spark_tasks "
            f"ADD COLUMN target_key VARCHAR({TARGET_KEY_LENGTH}) NOT NULL DEFAULT ''"
        )


def _rebuild_sqlite_task_target_keys(connection: Connection) -> None:
    # Old v5 builds stored values such as ``uid:<sec_uid>``. Drop both known
    # uniqueness indexes before rewriting because distinct legacy values can
    # collapse to one canonical SHA-256 key. The surrounding BEGIN IMMEDIATE
    # transaction makes the index replacement atomic for SQLite callers.
    connection.exec_driver_sql("DROP INDEX IF EXISTS uq_enabled_task_schedule")
    connection.exec_driver_sql(
        "DROP INDEX IF EXISTS uq_enabled_task_target_schedule"
    )

    rows = connection.exec_driver_sql(
        "SELECT task.id, task.douyin_account_id, task.target_name, "
        "task.send_time, task.enabled, identity.sec_uid "
        "FROM spark_tasks AS task "
        "LEFT JOIN spark_task_target_identities AS identity "
        "ON identity.task_id = task.id "
        "ORDER BY task.id"
    ).fetchall()
    occupied: set[tuple[str, str, str]] = set()
    for task_id, account_id, target_name, send_time, enabled, sec_uid in rows:
        canonical_key = task_target_key(target_name, sec_uid)
        stored_key = canonical_key
        if enabled and account_id is not None:
            schedule_key = (str(account_id), canonical_key, str(send_time))
            if schedule_key in occupied:
                stored_key = legacy_task_target_key(str(task_id))
            else:
                occupied.add(schedule_key)
        connection.exec_driver_sql(
            "UPDATE spark_tasks SET target_key = ? WHERE id = ?",
            (stored_key, task_id),
        )

    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX uq_enabled_task_target_schedule "
        "ON spark_tasks (douyin_account_id, target_key, send_time) "
        "WHERE enabled = 1"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER IF NOT EXISTS require_spark_task_target_key_insert "
        "BEFORE INSERT ON spark_tasks "
        "WHEN NEW.target_key IS NULL OR TRIM(NEW.target_key) = '' "
        "BEGIN SELECT RAISE(ABORT, 'spark task target_key required'); END"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER IF NOT EXISTS require_spark_task_target_key_update "
        "BEFORE UPDATE OF target_key ON spark_tasks "
        "WHEN NEW.target_key IS NULL OR TRIM(NEW.target_key) = '' "
        "BEGIN SELECT RAISE(ABORT, 'spark task target_key required'); END"
    )


@contextmanager
def session_scope(engine: Engine):
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
