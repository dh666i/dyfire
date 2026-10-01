"""Offline SQLite-to-PostgreSQL copy for a maintenance-window cutover.

This is an initial-copy tool, not live replication. Stop every application and
worker writer before ``--apply``; the source is opened read-only and held in a
single SQLite snapshot while rows are copied. The PostgreSQL target must be
empty. A failed import rolls back as one target transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, Engine, Integer, String, func, inspect, select, text
from sqlalchemy.engine import URL, Connection, create_engine
from sqlalchemy.orm import Session

from spark_console.db import SQLITE_SCHEMA_VERSION
from spark_console.crypto import CookieCipher
from spark_console.models import (
    AccountOperation,
    Base,
    ContactDirectoryJob,
    DouyinLoginAction,
    DouyinLoginSession,
    DouyinLoginInput,
    DouyinAccount,
    MailDelivery,
    SparkTask,
    TaskRun,
    User,
)


CURRENT_SQLITE_SCHEMA_VERSION = SQLITE_SCHEMA_VERSION
DEFAULT_BATCH_SIZE = 1000
MAX_BATCH_SIZE = 10000


@dataclass(frozen=True)
class MigrationReport:
    source: str
    source_schema_version: int
    table_rows: dict[str, int]
    total_rows: int
    active_runs: int
    active_logins: int
    sending_mail: int
    applied: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_schema_version": self.source_schema_version,
            "table_rows": self.table_rows,
            "total_rows": self.total_rows,
            "active_runs": self.active_runs,
            "active_logins": self.active_logins,
            "sending_mail": self.sending_mail,
            "applied": self.applied,
        }


def readonly_sqlite_engine(path: str | Path) -> Engine:
    database = Path(path).expanduser().resolve()
    if not database.is_file():
        raise ValueError(f"SQLite source does not exist: {database}")
    url = URL.create(
        "sqlite+pysqlite",
        database=f"file:{database.as_posix()}",
        query={"mode": "ro", "uri": "true"},
    )
    return create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})


def _count(connection: Connection, statement) -> int:
    return int(connection.scalar(statement) or 0)


def _source_state(
    connection: Connection, *, cookie_key: bytes | None = None
) -> tuple[int, dict[str, int], int, int, int]:
    connection.exec_driver_sql("BEGIN")
    check = connection.exec_driver_sql("PRAGMA integrity_check").scalar()
    if check != "ok":
        raise ValueError(f"SQLite integrity check failed: {check}")
    foreign_keys = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        raise ValueError(f"SQLite foreign-key violations: {len(foreign_keys)}")

    tables = set(inspect(connection).get_table_names())
    required = set(Base.metadata.tables) | {"schema_migrations"}
    missing = sorted(required - tables)
    if missing:
        raise ValueError("SQLite schema is incomplete: " + ", ".join(missing[:20]))
    _verify_source_indexes(connection)

    schema_version = int(
        connection.exec_driver_sql("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").scalar()
        or 0
    )
    if schema_version != CURRENT_SQLITE_SCHEMA_VERSION:
        raise ValueError(
            f"SQLite schema version {schema_version} is not current "
            f"(expected {CURRENT_SQLITE_SCHEMA_VERSION}); upgrade SQLite first"
        )

    rows = {
        table.name: _count(connection, select(func.count()).select_from(table))
        for table in Base.metadata.sorted_tables
    }
    active_runs = _count(
        connection,
        select(func.count()).select_from(TaskRun).where(
            TaskRun.status.in_(("queued", "running", "retrying", "submitted_unconfirmed"))
        ),
    )
    active_logins = _count(
        connection,
        select(func.count()).select_from(DouyinLoginSession).where(
            DouyinLoginSession.status.in_(("queued", "loading_qr", "awaiting_scan", "confirming"))
        ),
    )
    sending_mail = _count(
        connection,
        select(func.count()).select_from(MailDelivery).where(
            MailDelivery.status.in_(("sending", "uncertain"))
        ),
    )
    pending_async = _count(connection, select(func.count()).select_from(ContactDirectoryJob).where(
        ContactDirectoryJob.status.in_(("queued", "running"))
    )) + _count(connection, select(func.count()).select_from(AccountOperation).where(
        AccountOperation.expires_at > datetime.now(timezone.utc)
    )) + _count(connection, select(func.count()).select_from(DouyinLoginInput).where(
        DouyinLoginInput.delivery_status.in_(("queued", "claimed"))
    )) + _count(connection, select(func.count()).select_from(DouyinLoginAction).where(
        DouyinLoginAction.consumed_at.is_(None)
    ))
    if pending_async:
        raise ValueError(f"source has pending asynchronous work: {pending_async}")
    if cookie_key is not None:
        cipher = CookieCipher(cookie_key)
        for encrypted, nonce in connection.execute(
            select(DouyinAccount.encrypted_cookies, DouyinAccount.cookie_nonce)
        ):
            try:
                cipher.decrypt(bytes(encrypted), bytes(nonce))
            except Exception as error:
                raise ValueError(
                    "source contains an account Cookie that cannot be decrypted with the supplied key"
                ) from error
    duplicate_active = connection.exec_driver_sql(
        "SELECT scan_id FROM douyin_login_inputs WHERE delivery_status IN ('queued', 'claimed') "
        "GROUP BY scan_id HAVING COUNT(*) > 1"
    ).fetchall()
    if duplicate_active:
        raise ValueError("source has duplicate active login inputs")
    _validate_source_lengths(connection)
    return schema_version, rows, active_runs, active_logins, sending_mail


def _validate_source_lengths(connection: Connection) -> None:
    inspector = inspect(connection)
    for table_name, table in Base.metadata.tables.items():
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name not in actual or not isinstance(column.type, String) or not column.type.length:
                continue
            primary_key = list(table.primary_key.columns)
            selected = [*primary_key, func.length(column).label("value_length")]
            row = connection.execute(
                select(*selected).where(
                    column.is_not(None), func.length(column) > column.type.length
                ).limit(1)
            ).first()
            if row:
                row_key = ",".join(str(value) for value in row[:-1])
                raise ValueError(
                    f"SQLite value exceeds PostgreSQL limit: {table_name}.{column.name} "
                    f"primary_key={row_key} length={row[-1]} limit={column.type.length}"
                )


def _verify_source_indexes(connection: Connection) -> None:
    inspector = inspect(connection)
    missing = []
    for table_name, table in Base.metadata.tables.items():
        actual = {index.get("name") for index in inspector.get_indexes(table_name)}
        for index in table.indexes:
            if index.name not in actual:
                missing.append(f"{table_name}.{index.name}")
    if missing:
        raise ValueError("SQLite source is missing required indexes: " + ", ".join(sorted(missing)[:20]))


def _assert_idle(active_runs: int, active_logins: int, sending_mail: int) -> None:
    if active_runs or active_logins or sending_mail:
        raise ValueError(
            "source is not idle: "
            f"active_runs={active_runs}, active_logins={active_logins}, "
            f"sending_or_uncertain_mail={sending_mail}"
        )


def _assert_empty_target(connection: Connection) -> None:
    existing = set(inspect(connection).get_table_names())
    unexpected = sorted(existing - set(Base.metadata.tables) - {"schema_migrations"})
    if unexpected:
        raise ValueError("PostgreSQL target is not empty; unexpected tables: " + ", ".join(unexpected[:20]))
    existing_app_tables = sorted(set(Base.metadata.tables) & existing)
    if existing_app_tables:
        raise ValueError(
            "PostgreSQL target already has application tables; use a fresh database: "
            + ", ".join(existing_app_tables[:20])
        )
    if "schema_migrations" in existing and connection.execute(text("SELECT COUNT(*) FROM schema_migrations")).scalar():
        raise ValueError("PostgreSQL target schema_migrations is not empty")


def _copy_snapshot(
    source: Connection,
    target: Engine,
    expected_counts: dict[str, int],
    *,
    batch_size: int,
) -> None:
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")

    metadata = Base.metadata
    with target.begin() as destination:
        _assert_empty_target(destination)
        metadata.create_all(destination)

        for table in metadata.sorted_tables:
            source_rows = source.execution_options(stream_results=True).execute(
                select(table)
            )
            copied = 0
            while True:
                batch = source_rows.fetchmany(batch_size)
                if not batch:
                    break
                records = [
                    _normalize_record(table, dict(row._mapping)) for row in batch
                ]
                destination.execute(table.insert(), records)
                copied += len(records)
            if copied != expected_counts[table.name]:
                raise RuntimeError(
                    f"source row count changed during copy for {table.name}: "
                    f"expected {expected_counts[table.name]}, copied {copied}"
                )

        _create_schema_history(destination)
        _synchronize_postgres_sequences(destination)
        for table in metadata.sorted_tables:
            copied_count = _count(
                destination,
                select(func.count()).select_from(table),
            )
            if copied_count != expected_counts[table.name]:
                raise RuntimeError(
                    f"target row count mismatch for {table.name}: "
                    f"expected {expected_counts[table.name]}, got {copied_count}"
                )


def _assert_empty_target_after_ddl(connection: Connection) -> None:
    # This check is intentionally separate after CREATE TABLE: a pre-existing
    # table that was empty remains safe, while any concurrent target write is
    # detected before source rows are inserted.
    _assert_empty_target(connection)


def _normalize_record(table, record: dict[str, Any]) -> dict[str, Any]:
    """Make SQLite's naive UTC DateTime values explicit for PostgreSQL."""
    for column in table.columns:
        value = record.get(column.name)
        if value is None or not isinstance(column.type, DateTime) or not isinstance(value, datetime):
            continue
        record[column.name] = (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
    return record


def _create_schema_history(connection: Connection) -> None:
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name VARCHAR(128) NOT NULL, "
        "applied_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    ))
    connection.execute(
        text("INSERT INTO schema_migrations (version, name) VALUES (:version, :name)"),
        [{"version": version, "name": name} for version, name in (
            (1, "task_runs_reliability"),
            (2, "login_input_delivery_receipts"),
            (3, "normalize_submitted_runs"),
            (4, "spark_task_message_kind"),
            (5, "spark_task_target_key"),
            (6, "normalize_spark_task_target_keys"),
            (7, "login_session_mode"),
            (8, "user_admin_note"),
            (9, "redemption_code_retrieval"),
            (10, "scale_query_indexes"),
            (11, "account_dispatch_shards"),
            (12, "retention_query_indexes"),
            (13, "account_queue_query_indexes"),
        )],
    )


def _synchronize_postgres_sequences(connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    preparer = connection.dialect.identifier_preparer
    for table in Base.metadata.sorted_tables:
        for column in table.primary_key.columns:
            if not column.autoincrement or not isinstance(column.type, Integer):
                continue
            table_name = f"{table.schema}.{table.name}" if table.schema else table.name
            connection.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence(:table_name, :column_name), "
                    f"COALESCE((SELECT MAX({preparer.quote(column.name)}) "
                    f"FROM {preparer.quote(table.name)}), 1), "
                    f"EXISTS(SELECT 1 FROM {preparer.quote(table.name)}))"
                ),
                {"table_name": table_name, "column_name": column.name},
            )


def migrate_sqlite_to_postgres(
    source_path: str | Path,
    target: Engine,
    *,
    apply: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    cookie_key: bytes | None = None,
) -> MigrationReport:
    if target.dialect.name != "postgresql":
        raise ValueError("migration target must be PostgreSQL")
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")

    source_engine = readonly_sqlite_engine(source_path)
    try:
        with source_engine.connect() as source:
            schema_version, counts, active_runs, active_logins, sending_mail = _source_state(
                source, cookie_key=cookie_key
            )
            _assert_idle(active_runs, active_logins, sending_mail)
            if apply:
                _copy_snapshot(source, target, counts, batch_size=batch_size)
            return MigrationReport(
                source=str(Path(source_path).expanduser().resolve()),
                source_schema_version=schema_version,
                table_rows=counts,
                total_rows=sum(counts.values()),
                active_runs=active_runs,
                active_logins=active_logins,
                sending_mail=sending_mail,
                applied=apply,
            )
    finally:
        source_engine.dispose()
