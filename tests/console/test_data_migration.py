from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from spark_console.data_migration import (
    CURRENT_SQLITE_SCHEMA_VERSION,
    _normalize_record,
    _source_state,
    readonly_sqlite_engine,
)
from spark_console.crypto import CookieCipher
from spark_console.db import create_schema
from spark_console.models import Base, DouyinAccount, User


def _keys(tmp_path: Path):
    cookie = tmp_path / "cookie.key"
    session = tmp_path / "session.key"
    cookie.write_bytes(b"c" * 32)
    session.write_bytes(b"s" * 32)
    return cookie, session


def test_readonly_source_preflight_requires_current_schema_and_integrity(tmp_path):
    database = tmp_path / "spark.db"
    engine = create_engine(f"sqlite:///{database}")
    create_schema(engine)
    engine.dispose()

    source = readonly_sqlite_engine(database)
    with source.connect() as connection:
        version, counts, active_runs, active_logins, sending_mail = _source_state(connection)
    assert version == CURRENT_SQLITE_SCHEMA_VERSION
    assert counts["users"] == 0
    assert (active_runs, active_logins, sending_mail) == (0, 0, 0)
    with pytest.raises(Exception):
        with source.begin() as connection:
            connection.execute(text("CREATE TABLE should_not_write (id INTEGER)"))
    source.dispose()


def test_migration_normalizes_sqlite_datetimes_without_touching_cookie_bytes(tmp_path):
    cookie, session = _keys(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'spark.db'}")
    create_schema(engine)
    column = Base.metadata.tables["spark_tasks"].c.updated_at
    original = datetime(2026, 1, 1, 8, 0)
    record = {"updated_at": original, "message_template": "早安", "encrypted_cookies": b"cipher"}
    normalized = _normalize_record(type("Table", (), {"columns": [column]})(), record)
    assert normalized["updated_at"] == original.replace(tzinfo=timezone.utc)
    assert normalized["encrypted_cookies"] == b"cipher"
    engine.dispose()


def test_source_preflight_verifies_existing_account_cookie_with_same_key(tmp_path):
    cookie_key, _session_key = _keys(tmp_path)
    database = tmp_path / "accounts.db"
    engine = create_engine(f"sqlite:///{database}")
    create_schema(engine)
    sealed = CookieCipher(cookie_key.read_bytes()).encrypt(b'[{"name":"sid","value":"keep"}]')
    with engine.begin() as connection:
        connection.execute(
            User.__table__.insert(),
            {"id": "user-1", "username": "owner", "password_hash": "hash", "role": "user"},
        )
        connection.execute(
            DouyinAccount.__table__.insert(),
            {
                "id": "account-1",
                "owner_user_id": "user-1",
                "display_name": "main",
                "encrypted_cookies": sealed.ciphertext,
                "cookie_nonce": sealed.nonce,
                "cookie_version": 1,
                "validation_state": "valid",
            },
        )
    engine.dispose()

    source = readonly_sqlite_engine(database)
    with source.connect() as connection:
        _source_state(connection, cookie_key=cookie_key.read_bytes())
    with source.connect() as connection:
        with pytest.raises(ValueError, match="Cookie"):
            _source_state(connection, cookie_key=b"x" * 32)
    source.dispose()
