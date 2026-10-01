"""Offline SQLite-to-PostgreSQL copy for a maintenance-window cutover.

This is an initial-copy tool, not live replication. Stop every application and
worker writer before ``--apply``; the source is opened read-only and held in a
single SQLite snapshot while rows are copied. The PostgreSQL target must be
empty. A failed import rolls back as one target transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import Column, DateTime, Engine, Integer, MetaData, String, Table, func, inspect, select, text
from sqlalchemy.engine import URL, Connection, create_engine

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
    TaskRun,
)


CURRENT_SQLITE_SCHEMA_VERSION = SQLITE_SCHEMA_VERSION
DEFAULT_BATCH_SIZE = 1000
MAX_BATCH_SIZE = 10000
SCHEMA_HISTORY = Table(
    "schema_migrations",
    MetaData(),
    Column("version", Integer, primary_key=True, autoincrement=False),
    Column("name", String(128), nullable=False),
    Column("applied_at", DateTime(timezone=True), nullable=False),
)


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
        destination.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        _assert_empty_target(destination)
        metadata.create_all(destination)

        for table in metadata.sorted_tables:
            copied = _copy_table(source, destination, table, batch_size=batch_size)
            if copied != expected_counts[table.name]:
                raise RuntimeError(
                    f"source row count changed during copy for {table.name}: "
                    f"expected {expected_counts[table.name]}, copied {copied}"
                )

        SCHEMA_HISTORY.create(destination, checkfirst=True)
        history_count = _copy_table(source, destination, SCHEMA_HISTORY, batch_size=batch_size)
        _synchronize_postgres_sequences(destination)
        for table in [*metadata.sorted_tables, SCHEMA_HISTORY]:
            expected_count = (
                history_count if table is SCHEMA_HISTORY else expected_counts[table.name]
            )
            copied_count = _count(
                destination,
                select(func.count()).select_from(table),
            )
            if copied_count != expected_count:
                raise RuntimeError(
                    f"target row count mismatch for {table.name}: "
                    f"expected {expected_count}, got {copied_count}"
                )
            if _table_checksum(source, table, batch_size=batch_size) != _table_checksum(
                destination, table, batch_size=batch_size
            ):
                raise RuntimeError(f"target row content mismatch for {table.name}")


def _copy_table(
    source: Connection, destination: Connection, table: Table, *, batch_size: int
) -> int:
    copied = 0
    with source.execute(select(table).execution_options(yield_per=batch_size)) as rows:
        for batch in rows.partitions(batch_size):
            records = [_normalize_record(table, dict(row._mapping)) for row in batch]
            destination.execute(table.insert(), records)
            copied += len(records)
    return copied


def _table_checksum(connection: Connection, table: Table, *, batch_size: int) -> str:
    """Compare all values with bounded memory and dialect-independent ordering."""
    ordering = [
        column.collate("C" if connection.dialect.name == "postgresql" else "BINARY")
        if isinstance(column.type, String) else column
        for column in table.primary_key.columns
    ]
    checksum = hashlib.sha256()
    statement = select(table).order_by(*ordering).execution_options(yield_per=batch_size)
    with connection.execute(statement) as rows:
        for batch in rows.partitions(batch_size):
            for row in batch:
                record = _normalize_record(table, dict(row._mapping))
                values = []
                for column in table.columns:
                    value = record[column.name]
                    if isinstance(value, datetime):
                        value = ["datetime", value.isoformat(timespec="microseconds")]
                    elif isinstance(value, (bytes, bytearray, memoryview)):
                        value = ["binary", bytes(value).hex()]
                    values.append(value)
                encoded = json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode("ascii")
                checksum.update(len(encoded).to_bytes(8, "big"))
                checksum.update(encoded)
    return checksum.hexdigest()


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


def _synchronize_postgres_sequences(connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    preparer = connection.dialect.identifier_preparer
    for table in Base.metadata.sorted_tables:
        for column in table.primary_key.columns:
            if not column.autoincrement or not isinstance(column.type, Integer):
                continue
            table_name = preparer.format_table(table)
            sequence = connection.scalar(
                text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                {"table_name": table_name, "column_name": column.name},
            )
            if sequence is None:
                continue
            connection.execute(
                text(
                    "SELECT setval(CAST(:sequence AS regclass), "
                    f"GREATEST(COALESCE((SELECT MAX({preparer.quote(column.name)}) "
                    f"FROM {table_name}), 1), 1), "
                    f"EXISTS(SELECT 1 FROM {table_name}))"
                ),
                {"sequence": sequence},
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
            else:
                # A preflight must prove the eventual destination is usable;
                # this read-only check never creates tables or changes state.
                with target.connect() as destination:
                    _assert_empty_target(destination)
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
