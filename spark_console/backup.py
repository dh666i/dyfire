"""SQLite backup bundle manifests and read-only verification.

The database file is the payload and the JSON sidecar is the commit marker.
Operators must verify the sidecar before treating a file in ``backups`` as a
usable backup.  The verifier never opens the source database for writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKUP_NAME = re.compile(r"^spark-\d{8}T\d{6}(?:\d{6})?Z\.db$")
MANIFEST_VERSION = 1


class BackupVerificationError(RuntimeError):
    """The backup payload or its commit manifest cannot be trusted."""


def manifest_path(database: str | Path) -> Path:
    """Return the sidecar path for a backup database."""
    return Path(database).with_suffix(Path(database).suffix + ".manifest.json")


def _regular(path: str | Path, *, label: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink() or not candidate.is_file():
        raise BackupVerificationError(f"{label} 不是普通文件: {candidate}")
    return candidate.resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_checks(path: Path) -> dict[str, Any]:
    # URI mode and query_only protect the source from accidental writes.
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        quick = connection.execute("PRAGMA quick_check").fetchone()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        quick_value = str(quick[0]) if quick else ""
        integrity_value = str(integrity[0]) if integrity else ""
        if quick_value != "ok" or integrity_value != "ok":
            raise BackupVerificationError("备份 SQLite 完整性检查失败")
        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_errors:
            raise BackupVerificationError("备份 SQLite 外键完整性检查失败")
        try:
            schema = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        except sqlite3.OperationalError as error:
            if "no such table" not in str(error).lower():
                raise
            schema = None
        return {
            "quick_check": quick_value,
            "integrity_check": integrity_value,
            "foreign_key_errors": 0,
            "schema": int(schema) if schema is not None else None,
        }
    except sqlite3.Error as error:
        raise BackupVerificationError(f"无法读取备份 SQLite 数据库: {path}") from error
    finally:
        connection.close()


def build_manifest(path: str | Path, *, created_at: str | None = None) -> dict[str, Any]:
    """Build a non-secret manifest for an already completed backup file."""
    database = _regular(path, label="备份")
    if not BACKUP_NAME.fullmatch(database.name):
        raise BackupVerificationError(f"备份文件名不符合受管格式: {database.name}")
    checks = _sqlite_checks(database)
    return {
        "format": MANIFEST_VERSION,
        "backup": database.name,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "bytes": database.stat().st_size,
        "sha256": _sha256(database),
        "sqlite": checks,
    }


def write_manifest(path: str | Path, manifest: dict[str, Any]) -> Path:
    """Atomically publish a manifest after the database payload is complete."""
    database = _regular(path, label="备份")
    target = manifest_path(database)
    if target.exists() or target.is_symlink():
        raise BackupVerificationError(f"备份清单已存在: {target}")
    temporary = target.with_name(target.name + ".tmp")
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(payload)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def verify_backup(path: str | Path, *, require_manifest: bool = True) -> dict[str, Any]:
    """Verify a backup payload and its sidecar, returning a safe report."""
    database = _regular(path, label="备份")
    sidecar = manifest_path(database)
    if sidecar.is_symlink() or not sidecar.is_file():
        if require_manifest:
            raise BackupVerificationError(f"备份缺少清单，不能确认可恢复: {sidecar.name}")
        sqlite_checks = _sqlite_checks(database)
        return {
            "ok": True,
            "manifest": {"present": False, "verified": False},
            "backup": database.name,
            "bytes": database.stat().st_size,
            "sqlite": sqlite_checks,
        }
    try:
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BackupVerificationError(f"备份清单无法读取: {sidecar.name}") from error
    if not isinstance(manifest, dict) or manifest.get("format") != MANIFEST_VERSION:
        raise BackupVerificationError("备份清单格式或版本不受支持")
    expected = {
        "backup": database.name,
        "bytes": database.stat().st_size,
        "sha256": _sha256(database),
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise BackupVerificationError(f"备份清单校验失败: {key}")
    actual_sqlite = _sqlite_checks(database)
    if manifest.get("sqlite") != actual_sqlite:
        raise BackupVerificationError("备份清单中的 SQLite 检查结果不一致")
    return {
        "ok": True,
        "manifest": {"present": True, "verified": True},
        "backup": database.name,
        "manifest_file": sidecar.name,
        "bytes": expected["bytes"],
        "sha256": expected["sha256"],
        "sqlite": actual_sqlite,
        "created_at": manifest.get("created_at"),
    }


def verify_directory(directory: str | Path) -> dict[str, Any]:
    """Verify every managed backup in a directory.

    Legacy payloads without manifests are reported as failures instead of
    being silently counted as usable recovery points.
    """
    raw_root = Path(directory).expanduser()
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise BackupVerificationError(f"备份目录不存在或不是目录: {raw_root}")
    root = raw_root.resolve()
    databases = sorted(
        (item for item in root.iterdir() if item.is_file() and BACKUP_NAME.fullmatch(item.name)),
        key=lambda item: item.name,
        reverse=True,
    )
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    for database in databases:
        try:
            valid.append(verify_backup(database))
        except (OSError, sqlite3.Error, BackupVerificationError) as error:
            invalid.append({"backup": database.name, "error": str(error)})
    orphan_manifests = sorted(
        item.name
        for item in root.iterdir()
        if item.is_file()
        and item.name.endswith(".db.manifest.json")
        and not (root / item.name.removesuffix(".manifest.json")).is_file()
    )
    return {
        "ok": bool(valid) and not invalid and not orphan_manifests,
        "directory": str(root),
        "count": len(databases),
        "valid": valid,
        "invalid": invalid,
        "orphan_manifests": orphan_manifests,
    }
