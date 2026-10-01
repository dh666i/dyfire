"""Run a read-only, isolated restore drill for a Dyfire SQLite backup.

The drill never opens the source database for writes and never replaces a live
database.  It copies committed pages into a new SQLite file, checks SQLite and
foreign-key integrity, verifies the declared schema, compares deterministic
row fingerprints, and optionally decrypts every stored account cookie with the
deployment cookie key.  The JSON report is suitable for attaching to an
operations record without exposing credentials or cookie contents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TABLES = (
    "users",
    "douyin_accounts",
    "douyin_account_identities",
    "spark_tasks",
    "task_runs",
    "task_run_attempts",
    "task_run_events",
    "douyin_login_sessions",
    "douyin_login_inputs",
    "web_sessions",
    "subscription_grants",
    "redemption_codes",
    "schema_migrations",
)
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class RecoveryDrillError(RuntimeError):
    """An expected restore-drill failure with a safe operator message."""


def _safe_path(value: str | Path, *, must_exist: bool = True) -> Path:
    raw_path = Path(value).expanduser()
    if raw_path.is_symlink():
        raise RecoveryDrillError(f"路径不能是符号链接: {raw_path}")
    path = raw_path.resolve()
    if must_exist and not path.is_file():
        raise RecoveryDrillError(f"备份文件不存在或不是普通文件: {path}")
    return path


def _connect_readonly(path: Path) -> sqlite3.Connection:
    # The URI mode is a second line of defense against accidental source writes.
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _quote_identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise RecoveryDrillError(f"数据库标识符无效: {value}")
    return '"' + value + '"'


def _canonical_value(value: Any) -> bytes:
    if value is None:
        return b"null"
    if isinstance(value, bytes):
        return b"bytes:" + hashlib.sha256(value).hexdigest().encode()
    if isinstance(value, (int, float, str)):
        return (type(value).__name__ + ":" + str(value)).encode("utf-8")
    return (type(value).__name__ + ":" + repr(value)).encode("utf-8")


def _table_fingerprint(connection: sqlite3.Connection, table: str) -> dict[str, Any]:
    quoted = _quote_identifier(table)
    columns = tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({quoted})"))
    if not columns:
        raise RecoveryDrillError(f"备份表缺少列: {table}")
    digest = hashlib.sha256()
    count = 0
    # SQLite has no portable row-value ordering for arbitrary schemas. Ordering
    # by every declared column gives a deterministic comparison for this copy.
    order = ", ".join(_quote_identifier(column) for column in columns)
    select = f"SELECT {', '.join(_quote_identifier(column) for column in columns)} FROM {quoted} ORDER BY {order}"
    for row in connection.execute(select):
        count += 1
        digest.update(b"row\0")
        for value in row:
            encoded = _canonical_value(value)
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
    return {"rows": count, "sha256": digest.hexdigest()}


def _fingerprints(connection: sqlite3.Connection, tables: Iterable[str]) -> dict[str, dict[str, Any]]:
    return {table: _table_fingerprint(connection, table) for table in tables}


def _cookie_check(connection: sqlite3.Connection, key_file: Path | None) -> dict[str, Any]:
    if key_file is None:
        return {"provided": False, "checked": 0}
    try:
        key = key_file.read_bytes()
    except OSError as error:
        raise RecoveryDrillError(f"无法读取 Cookie 密钥: {key_file}") from error
    if len(key) != 32:
        raise RecoveryDrillError("Cookie 密钥必须是 32 字节")
    # Import lazily so the integrity-only drill remains usable with stdlib only.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from spark_console.crypto import CookieCipher

    cipher = CookieCipher(key)
    rows = connection.execute(
        "SELECT encrypted_cookies, cookie_nonce FROM douyin_accounts"
    )
    checked = 0
    for row in rows:
        try:
            plaintext = cipher.decrypt(row[0], row[1])
        except Exception as error:  # cryptography intentionally has varied error types
            raise RecoveryDrillError("存在无法用当前 Cookie 密钥解密的账号凭据") from error
        if not plaintext:
            raise RecoveryDrillError("存在解密后为空的账号凭据")
        checked += 1
    return {"provided": True, "checked": checked}


def _integrity(connection: sqlite3.Connection) -> dict[str, Any]:
    quick = connection.execute("PRAGMA quick_check").fetchone()
    full = connection.execute("PRAGMA integrity_check").fetchone()
    foreign_keys = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()]
    quick_value = str(quick[0]) if quick else ""
    full_value = str(full[0]) if full else ""
    if quick_value != "ok" or full_value != "ok" or foreign_keys:
        raise RecoveryDrillError("恢复副本完整性检查失败")
    return {
        "quick_check": quick_value,
        "integrity_check": full_value,
        "foreign_key_errors": len(foreign_keys),
    }


def _copy_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise RecoveryDrillError(f"恢复目标已存在: {destination}")
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with _connect_readonly(source) as source_db, sqlite3.connect(temporary) as target_db:
            source_db.backup(target_db)
            target_db.execute("PRAGMA journal_mode=DELETE")
            target_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def run(source: str | Path, *, output: str | Path | None = None, cookie_key: str | Path | None = None) -> dict[str, Any]:
    """Restore and validate one backup, returning a non-secret JSON report."""
    source_path = _safe_path(source)
    key_path = _safe_path(cookie_key) if cookie_key else None
    temporary_root: tempfile.TemporaryDirectory[str] | None = None
    destination: Path | None = None
    created_destination = False
    try:
        if output is None:
            temporary_root = tempfile.TemporaryDirectory(prefix="dyfire-recovery-")
            destination = Path(temporary_root.name) / "spark.db"
            retained = False
        else:
            destination = _safe_path(output, must_exist=False)
            retained = True
        _copy_backup(source_path, destination)
        created_destination = True
        with _connect_readonly(source_path) as source_db, _connect_readonly(destination) as restored_db:
            source_tables = _tables(source_db)
            restored_tables = _tables(restored_db)
            if source_tables != restored_tables:
                raise RecoveryDrillError("恢复副本的表集合与源备份不一致")
            tables = tuple(table for table in DEFAULT_TABLES if table in source_tables)
            # Include every application table, including tables introduced by a
            # future schema, while excluding SQLite's internal bookkeeping.
            tables += tuple(table for table in source_tables if table not in tables)
            source_fingerprints = _fingerprints(source_db, tables)
            restored_fingerprints = _fingerprints(restored_db, tables)
            if source_fingerprints != restored_fingerprints:
                raise RecoveryDrillError("恢复副本内容校验不一致")
            integrity = _integrity(restored_db)
            cookie_result = _cookie_check(restored_db, key_path)
            migration_rows = restored_db.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall() if "schema_migrations" in restored_tables else []
            return {
                "ok": True,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "source": str(source_path),
                "restored": str(destination) if retained else "temporary-isolated-copy",
                "bytes": destination.stat().st_size,
                "tables": len(source_tables),
                "fingerprints": source_fingerprints,
                "integrity": integrity,
                "cookie_check": cookie_result,
                "schema_migrations": [dict(row) for row in migration_rows],
            }
    except BaseException:
        # A requested output is still owned by this drill. Never leave an
        # unchecked or partially validated database that operators may mistake
        # for a successful restore.
        if created_destination and retained and destination is not None:
            destination.unlink(missing_ok=True)
        raise
    finally:
        if temporary_root is not None:
            temporary_root.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dyfire-recovery-drill")
    parser.add_argument("backup", help="待验证的 SQLite 备份文件")
    parser.add_argument("--output", help="保留隔离恢复副本的全新路径；默认演练后删除")
    parser.add_argument("--cookie-key", help="Cookie 密钥文件；提供后验证所有账号凭据可解密")
    args = parser.parse_args(argv)
    try:
        report = run(args.backup, output=args.output, cookie_key=args.cookie_key)
    except (OSError, sqlite3.Error, RecoveryDrillError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
