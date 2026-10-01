import hashlib
import sqlite3
from pathlib import Path

import pytest


def _module():
    from deploy import recovery_drill

    return recovery_drill


def _database(path: Path, key: bytes) -> None:
    from spark_console.crypto import CookieCipher

    encrypted = CookieCipher(key).encrypt(b'{"sessionid":"preserved"}')
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL);
            CREATE TABLE douyin_accounts (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL REFERENCES users(id),
                encrypted_cookies BLOB NOT NULL,
                cookie_nonce BLOB NOT NULL
            );
            CREATE TABLE spark_tasks (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL REFERENCES users(id),
                target_name TEXT NOT NULL
            );
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            """
        )
        db.execute("INSERT INTO users VALUES ('u1', 'owner')")
        db.execute(
            "INSERT INTO douyin_accounts VALUES (?, ?, ?, ?)",
            ("a1", "u1", encrypted.ciphertext, encrypted.nonce),
        )
        db.execute("INSERT INTO spark_tasks VALUES ('t1', 'u1', '好友')")
        db.execute("INSERT INTO schema_migrations VALUES (15, 'audit_request_context')")


def test_restore_drill_refuses_missing_key_without_touching_source(tmp_path):
    module = _module()
    source = tmp_path / "backup.db"
    key = b"k" * 32
    _database(source, key)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    key_file = tmp_path / "cookie.key"
    key_file.write_bytes(b"wrong")

    with pytest.raises(module.RecoveryDrillError, match="Cookie 密钥必须"):
        module.run(source, cookie_key=key_file)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_restore_drill_checks_cookie_and_preserves_source(tmp_path):
    module = _module()
    source = tmp_path / "backup.db"
    key_file = tmp_path / "cookie.key"
    key = b"k" * 32
    key_file.write_bytes(key)
    _database(source, key)
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    report = module.run(source, cookie_key=key_file)

    assert report["ok"] is True
    assert report["integrity"] == {
        "quick_check": "ok",
        "integrity_check": "ok",
        "foreign_key_errors": 0,
    }
    assert report["cookie_check"] == {"provided": True, "checked": 1}
    assert report["schema_migrations"] == [
        {"version": 15, "name": "audit_request_context"}
    ]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_restore_drill_can_retain_only_a_new_output_path(tmp_path):
    module = _module()
    source = tmp_path / "backup.db"
    key_file = tmp_path / "cookie.key"
    output = tmp_path / "isolated" / "restored.db"
    key_file.write_bytes(b"k" * 32)
    _database(source, b"k" * 32)

    report = module.run(source, output=output, cookie_key=key_file)

    assert report["restored"] == str(output.resolve())
    assert output.is_file()
    with sqlite3.connect(output) as db:
        assert db.execute("SELECT target_name FROM spark_tasks").fetchone() == ("好友",)


def test_restore_drill_rejects_foreign_key_corruption(tmp_path):
    module = _module()
    source = tmp_path / "backup.db"
    _database(source, b"k" * 32)
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("UPDATE spark_tasks SET owner_user_id='missing'")

    with pytest.raises(module.RecoveryDrillError, match="完整性"):
        module.run(source)


def test_restore_drill_rejects_symlink_source_and_output(tmp_path):
    module = _module()
    source = tmp_path / "backup.db"
    _database(source, b"k" * 32)
    source_link = tmp_path / "backup-link.db"
    output_target = tmp_path / "output.db"
    output_link = tmp_path / "output-link.db"
    source_link.symlink_to(source)
    output_link.symlink_to(output_target)

    with pytest.raises(module.RecoveryDrillError, match="符号链接"):
        module.run(source_link)
    with pytest.raises(module.RecoveryDrillError, match="符号链接"):
        module.run(source, output=output_link)
