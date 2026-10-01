from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from spark_console.backup import (
    BackupVerificationError,
    build_manifest,
    verify_backup,
    verify_directory,
    write_manifest,
)


def _database(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("create table schema_migrations (version integer primary key, name text)")
        db.execute("insert into schema_migrations values (15, 'audit_request_context')")
        db.execute("create table users (id text primary key)")
        db.execute("insert into users values ('owner')")


def test_backup_manifest_round_trip_and_tamper_detection(tmp_path):
    database = tmp_path / "spark-20261002T010203000000Z.db"
    _database(database)
    manifest = build_manifest(database, created_at="2026-10-02T01:02:03+00:00")
    write_manifest(database, manifest)
    report = verify_backup(database)
    assert report["ok"] is True
    assert report["manifest"]["verified"] is True
    assert report["sqlite"]["schema"] == 15

    with database.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(BackupVerificationError, match="清单校验失败"):
        verify_backup(database)


def test_backup_without_manifest_requires_explicit_legacy_mode(tmp_path):
    database = tmp_path / "spark-20261002T010203000000Z.db"
    _database(database)
    with pytest.raises(BackupVerificationError, match="缺少清单"):
        verify_backup(database)
    legacy = verify_backup(database, require_manifest=False)
    assert legacy["manifest"] == {"present": False, "verified": False}


def test_verify_directory_rejects_legacy_and_orphan_manifests(tmp_path):
    database = tmp_path / "spark-20261002T010203000000Z.db"
    _database(database)
    report = verify_directory(tmp_path)
    assert report["ok"] is False
    assert report["invalid"][0]["backup"] == database.name

    manifest = build_manifest(database)
    write_manifest(database, manifest)
    orphan = tmp_path / "spark-20261001T010203000000Z.db.manifest.json"
    orphan.write_text("{}", encoding="utf-8")
    report = verify_directory(tmp_path)
    assert report["ok"] is False
    assert orphan.name in report["orphan_manifests"]


def test_manifest_is_not_overwritten(tmp_path):
    database = tmp_path / "spark-20261002T010203000000Z.db"
    _database(database)
    write_manifest(database, build_manifest(database))
    with pytest.raises(BackupVerificationError, match="清单已存在"):
        write_manifest(database, build_manifest(database))
