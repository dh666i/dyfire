from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import create_engine_for, create_schema, session_scope
from spark_console.models import User
from spark_console.retention import RetentionPolicy, retain
from spark_console.data_migration import migrate_sqlite_to_postgres
from spark_console.backup import (
    BackupVerificationError,
    build_manifest,
    manifest_path,
    verify_backup,
    verify_directory,
    write_manifest,
)
from spark_console.security import PasswordService
from spark_console.services.accounts import AccountService
from spark_console.services.audits import AuditService
from spark_console.services.tasks import TaskService
from spark_console.services.users import UserService


def runtime():
    settings = Settings.from_env(os.environ)
    engine = create_engine_for(settings)
    create_schema(engine)
    return settings, engine


def command_create_admin(args) -> int:
    _settings, engine = runtime()
    password = getpass.getpass("管理员密码: ")
    confirmation = getpass.getpass("再次输入: ")
    if password != confirmation:
        raise SystemExit("两次密码不一致")
    with session_scope(engine) as db:
        UserService(db, PasswordService(), AuditService(db)).create(args.username, password, "admin")
    print("管理员已创建；首次登录必须修改密码。")
    return 0


def command_create_user(args) -> int:
    _settings, engine = runtime()
    with session_scope(engine) as db:
        _user, temporary = UserService(db, PasswordService(), AuditService(db)).create(args.username)
    print(f"临时密码（仅显示一次）: {temporary}")
    return 0


def command_import_legacy(args) -> int:
    settings, engine = runtime()
    payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
    accounts_created = tasks_created = 0
    with session_scope(engine) as db:
        owner = db.scalar(select(User).where(User.username == args.owner.lower()))
        if owner is None:
            raise SystemExit("指定用户不存在")
        audit = AuditService(db)
        accounts = AccountService(db, CookieCipher(settings.cookie_key_file.read_bytes()), audit)
        tasks = TaskService(db, accounts, audit)
        for index, item in enumerate(payload, 1):
            account = accounts.create(owner.id, item.get("username") or f"导入账号{index}", json.dumps(item["cookies"], ensure_ascii=False))
            accounts_created += 1
            for target in item.get("targets", []):
                tasks.create(owner.id, account.id, target, args.time, args.message)
                tasks_created += 1
    print(f"导入完成：账号 {accounts_created} 个，任务 {tasks_created} 个。")
    return 0


def command_backup(args) -> int:
    settings, engine = runtime()
    if engine.dialect.name != "sqlite":
        raise SystemExit("backup-db 当前仅支持 SQLite")
    keep = getattr(args, "keep", 7)
    if keep < 1:
        raise SystemExit("--keep 必须大于 0")
    backup_dir = settings.data_dir / "backups"
    destination = backup_dir / (
        f"spark-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.db"
    )
    temporary = destination.with_suffix(".db.tmp")
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    stale_temporary_name = re.compile(
        r"^spark-\d{8}T\d{12}Z\.db\.tmp$"
    )
    stale_before = datetime.now(timezone.utc).timestamp() - 24 * 3600
    for stale in backup_dir.iterdir():
        if (
            stale.is_file()
            and stale_temporary_name.fullmatch(stale.name)
            and stale.stat().st_mtime < stale_before
        ):
            stale.unlink()
    raw = engine.raw_connection()
    target = None
    try:
        try:
            target = sqlite3.connect(temporary)
            raw.driver_connection.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise RuntimeError("SQLite 备份完整性检查失败")
        finally:
            if target is not None:
                target.close()
            raw.close()
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.chmod(destination, 0o600)
    # The manifest is published last and acts as the backup's commit marker.
    # A database without it is never reported as a verified backup.
    try:
        write_manifest(destination, build_manifest(destination))
    except BaseException:
        destination.unlink(missing_ok=True)
        manifest_path(destination).unlink(missing_ok=True)
        raise
    backup_name = re.compile(r"^spark-\d{8}T\d{6}(?:\d{6})?Z\.db$")
    backups = sorted(
        (
            path
            for path in backup_dir.iterdir()
            if path.is_file() and backup_name.fullmatch(path.name)
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    # Keep the newest ``keep`` files as the minimum recovery window.  The
    # database and its manifest are deleted together so an orphaned payload
    # can never be mistaken for a verified backup.
    for expired in backups[keep:]:
        expired.unlink()
        manifest_path(expired).unlink(missing_ok=True)
    print(destination)
    return 0


def command_verify_backup(args) -> int:
    """Verify one backup bundle without opening it for writes."""
    try:
        report = verify_backup(args.backup, require_manifest=not args.allow_legacy)
    except (OSError, sqlite3.Error, BackupVerificationError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


def command_verify_backup_directory(args) -> int:
    try:
        report = verify_directory(args.directory)
    except (OSError, sqlite3.Error, BackupVerificationError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


def _retention_as_of(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise SystemExit("--as-of 必须是 ISO 8601 时间，例如 2026-01-01T00:00:00+00:00") from error
    if parsed.tzinfo is None:
        raise SystemExit("--as-of 必须包含时区，例如 +00:00 或 Z")
    return parsed


def command_retention(args) -> int:
    """Plan (default) or apply bounded retention deletion."""
    try:
        policy = RetentionPolicy(
            runs_days=args.runs_days,
            sessions_days=args.sessions_days,
            mail_days=args.mail_days,
            audit_days=args.audit_days,
            batch_size=args.batch_size,
        )
        as_of = _retention_as_of(args.as_of)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    _settings, engine = runtime()
    with session_scope(engine) as db:
        report = retain(
            db,
            policy,
            dry_run=not args.apply,
            now=as_of,
        )
    print(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


def command_migrate_sqlite(args) -> int:
    """Preflight or apply a one-time SQLite to PostgreSQL copy."""
    settings = Settings.from_env(os.environ)
    source = Path(args.source).expanduser().resolve()
    target_url = args.target or settings.database_url
    if not target_url.startswith("postgresql"):
        raise SystemExit("--target 必须是 postgresql+psycopg://... 连接串")
    from sqlalchemy import create_engine

    target = create_engine(target_url, pool_pre_ping=True)
    try:
        report = migrate_sqlite_to_postgres(
            source,
            target,
            apply=args.apply,
            batch_size=args.batch_size,
            cookie_key=settings.cookie_key_file.read_bytes(),
        )
    finally:
        target.dispose()
    print(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


def command_queue_preflight(args) -> int:
    """Check database claim readiness and optional Redis Stream hand-off."""
    from spark_console.queue_preflight import run_queue_preflight

    settings = Settings.from_env(os.environ)
    report = run_queue_preflight(settings, probe_stream=args.probe_stream)
    print(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if report.passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spark-console")
    commands = parser.add_subparsers(required=True)
    admin = commands.add_parser("create-admin"); admin.add_argument("username"); admin.set_defaults(handler=command_create_admin)
    user = commands.add_parser("create-user"); user.add_argument("username"); user.set_defaults(handler=command_create_user)
    legacy = commands.add_parser("import-legacy"); legacy.add_argument("path"); legacy.add_argument("--owner", required=True); legacy.add_argument("--time", default="09:00"); legacy.add_argument("--message", default="今日火花"); legacy.set_defaults(handler=command_import_legacy)
    backup = commands.add_parser("backup-db")
    backup.add_argument("--keep", type=int, default=7, help="至少保留的最新备份数量")
    backup.set_defaults(handler=command_backup)
    verify = commands.add_parser(
        "verify-backup",
        help="只读校验备份数据库及其清单；默认拒绝没有清单的旧文件",
    )
    verify.add_argument("backup", help="待校验的备份数据库路径")
    verify.add_argument(
        "--allow-legacy",
        action="store_true",
        help="仅为迁移旧备份允许缺少清单；报告仍标记为未验证",
    )
    verify.set_defaults(handler=command_verify_backup)
    verify_dir = commands.add_parser(
        "verify-backups",
        help="只读校验备份目录中的所有数据库、清单和孤立清单",
    )
    verify_dir.add_argument("directory", help="备份目录路径")
    verify_dir.set_defaults(handler=command_verify_backup_directory)
    retention = commands.add_parser(
        "retention",
        help="清理已过期的终态记录；默认只预览，不删除",
    )
    retention.add_argument("--apply", action="store_true", help="确认执行删除")
    retention.add_argument("--runs-days", type=int, default=365, help="执行记录保留天数")
    retention.add_argument("--sessions-days", type=int, default=30, help="登录会话保留天数")
    retention.add_argument("--mail-days", type=int, default=30, help="邮件记录保留天数")
    retention.add_argument("--audit-days", type=int, default=365, help="审计记录保留天数")
    retention.add_argument("--batch-size", type=int, default=500, help="本次每类最多删除的记录数；后续运行继续清理积压")
    retention.add_argument("--as-of", help="按指定 ISO 8601 时间计算，必须带时区")
    retention.set_defaults(handler=command_retention)
    migration = commands.add_parser(
        "migrate-sqlite-postgres",
        help="离线预检或导入 SQLite 到空 PostgreSQL；默认只预检",
    )
    migration.add_argument("source", help="SQLite spark.db 路径")
    migration.add_argument("--target", required=True, help="postgresql+psycopg://... 目标连接串")
    migration.add_argument("--apply", action="store_true", help="明确执行导入")
    migration.add_argument("--batch-size", type=int, default=1000)
    migration.set_defaults(handler=command_migrate_sqlite)
    preflight = commands.add_parser(
        "queue-preflight",
        help="检查数据库领取结构；可选地验证一次隔离 Redis Stream",
    )
    preflight.add_argument(
        "--probe-stream",
        action="store_true",
        help="写入、读取、确认一个随机临时 Stream，然后删除",
    )
    preflight.set_defaults(handler=command_queue_preflight)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
