from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.orm import Session

from spark_console.config import Settings
from spark_console.models import Base, WorkerLock
from spark_console.task_targets import (
    TARGET_KEY_LENGTH,
    legacy_task_target_key,
    task_target_key,
)


SQLITE_LOCK_TIMEOUT_SECONDS = 10.0


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
    engine = create_engine(
        settings.database_url,
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
    with session_scope(engine) as session:
        if session.get(WorkerLock, 1) is None:
            session.add(WorkerLock(id=1))


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
