from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

from spark_console.config import Settings
from spark_console.redis_backend import (
    RedisBackend,
    RedisBackendConfig,
    RedisDisabled,
)


def _settings(root: str, **overrides: str) -> Settings:
    root_path = Path(root)
    (root_path / "cookie.key").write_bytes(b"c" * 32)
    (root_path / "session.key").write_bytes(b"s" * 32)
    values = {
        "SPARK_DATA_DIR": root,
        "SPARK_COOKIE_KEY_FILE": str(root_path / "cookie.key"),
        "SPARK_SESSION_KEY_FILE": str(root_path / "session.key"),
    }
    values.update(overrides)
    return Settings.from_env(values)


def test_redis_is_disabled_by_default_without_constructing_a_client():
    with tempfile.TemporaryDirectory() as root:
        settings = _settings(root)
        backend = RedisBackend.from_settings(settings)
        assert not backend.enabled
        assert not backend.connected
        with pytest.raises(RedisDisabled):
            backend.ping()


def test_enabled_redis_requires_a_url():
    with tempfile.TemporaryDirectory() as root:
        with pytest.raises(ValueError, match="redis URL"):
            _settings(root, SPARK_REDIS_ENABLED="true")


def test_redis_key_prefix_is_validated_before_connecting():
    with tempfile.TemporaryDirectory() as root:
        with pytest.raises(ValueError, match="safe ASCII"):
            _settings(root, SPARK_REDIS_KEY_PREFIX="bad prefix")


def test_settings_load_optional_redis_configuration():
    with tempfile.TemporaryDirectory() as root:
        settings = _settings(
            root,
            SPARK_REDIS_ENABLED="true",
            SPARK_REDIS_URL="redis://localhost:6379/9",
            SPARK_REDIS_KEY_PREFIX="test-dyfire",
            SPARK_REDIS_MAX_CONNECTIONS="7",
        )
        backend = RedisBackend.from_settings(settings)
        assert backend.enabled
        assert not backend.connected
        assert backend._config == RedisBackendConfig(
            url="redis://localhost:6379/9",
            key_prefix="test-dyfire",
            max_connections=7,
        )


def test_redis_primitives_against_service():
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not configured")
    backend = RedisBackend(RedisBackendConfig(url=url, key_prefix=f"test-{uuid.uuid4().hex}"))
    assert backend.ping()
    key = uuid.uuid4().hex
    assert backend.allow_rate_limit(key, limit=2, window_seconds=30)
    assert backend.allow_rate_limit(key, limit=2, window_seconds=30)
    assert not backend.allow_rate_limit(key, limit=2, window_seconds=30)
    assert backend.claim_idempotency(key, "result", ttl_seconds=30)
    assert not backend.claim_idempotency(key, "other", ttl_seconds=30)
    assert backend.get_idempotency(key) == "result"
    queue = uuid.uuid4().hex
    assert backend.enqueue(queue, {"id": 1}, max_items=2) == 1
    assert backend.enqueue(queue, {"id": 2}, max_items=2) == 2
    assert backend.enqueue(queue, {"id": 3}, max_items=2) == 2
    assert backend.dequeue(queue) == {"id": 2}
    assert backend.dequeue(queue) == {"id": 3}
    assert backend.dequeue(queue) is None
