from __future__ import annotations

import os
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
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


def test_stream_validation_happens_before_a_disabled_backend_can_connect():
    backend = RedisBackend.disabled()
    with pytest.raises(ValueError, match="payload"):
        backend.stream_enqueue("jobs", ["not", "an", "object"])
    with pytest.raises(ValueError, match="count"):
        backend.stream_read("jobs", "workers", "worker-a", count=0)
    with pytest.raises(ValueError, match="group and consumer"):
        backend.stream_read("jobs", "bad group", "worker-a")
    with pytest.raises(ValueError, match="idle"):
        backend.stream_reclaim(
            "jobs", "workers", "worker-a", min_idle_seconds=0
        )


def test_stream_member_rejects_malformed_payloads():
    with pytest.raises(ValueError, match="no payload"):
        RedisBackend._stream_member(("1-0", {}))
    with pytest.raises(ValueError, match="JSON object"):
        RedisBackend._stream_member(("1-0", {"payload": "[]"}))


def test_redis_primitives_against_service():
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not configured")
    backend = RedisBackend(RedisBackendConfig(url=url, key_prefix=f"test-{uuid.uuid4().hex}"))
    assert backend.ping()
    probe = backend.stream_probe()
    assert probe.acknowledged
    key = uuid.uuid4().hex
    assert backend.allow_rate_limit(key, limit=2, window_seconds=30)
    assert backend.allow_rate_limit(key, limit=2, window_seconds=30)
    assert not backend.allow_rate_limit(key, limit=2, window_seconds=30)
    failure_key = uuid.uuid4().hex
    assert backend.rate_limit_count(failure_key) == 0
    assert backend.record_rate_limit_failure(failure_key, window_seconds=30) == 1
    assert backend.rate_limit_count(failure_key) == 1
    assert backend.clear_rate_limit(failure_key)
    assert backend.rate_limit_count(failure_key) == 0

    # Admission and reservation share one Lua transaction, so many workers
    # racing for a small failed-attempt budget can only enter the password
    # verification path up to that budget. Reservations do not become failed
    # attempts until committed.
    concurrent_key = uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=24) as executor:
        reservations = list(
            executor.map(
                lambda _index: backend.reserve_failed_attempt(
                    concurrent_key, limit=4, window_seconds=30
                ),
                range(24),
            )
        )
    accepted = [token for token in reservations if token is not None]
    assert len(accepted) == 4
    assert backend.rate_limit_count(concurrent_key) == 0
    for token in accepted:
        assert backend.commit_failed_attempt(
            concurrent_key, token, window_seconds=30
        ) >= 1
    assert backend.rate_limit_count(concurrent_key) == 4
    assert backend.reserve_failed_attempt(
        concurrent_key, limit=4, window_seconds=30
    ) is None
    assert backend.clear_rate_limit(concurrent_key)
    assert backend.rate_limit_count(concurrent_key) == 0

    # A successful credential check releases its own reservation and resets
    # the prior failure window without leaving a pending slot behind.
    success_key = uuid.uuid4().hex
    success_reservation = backend.reserve_failed_attempt(
        success_key, limit=2, window_seconds=30
    )
    assert success_reservation
    assert backend.release_failed_attempt(
        success_key, success_reservation, clear_failures=True
    )
    assert backend.rate_limit_count(success_key) == 0
    assert backend.reserve_failed_attempt(
        success_key, limit=2, window_seconds=30
    )
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

    # Streams keep entries pending until the consumer acknowledges them.  A
    # different consumer can reclaim an abandoned entry after its lease has
    # expired, which is the recovery primitive required by a durable queue.
    stream = uuid.uuid4().hex
    message_id = backend.stream_enqueue(stream, {"run_id": "run-1", "attempt": 1})
    first = backend.stream_read(stream, "workers", "worker-a", count=1)
    assert len(first) == 1
    assert first[0].id == message_id
    assert first[0].payload == {"run_id": "run-1", "attempt": 1}
    assert backend.stream_read(stream, "workers", "worker-b", count=1) == []
    # The one-second lease is intentionally short so this CI-only integration
    # check remains deterministic without relying on Redis internals.
    import time

    time.sleep(1.05)
    recovered = backend.stream_reclaim(
        stream,
        "workers",
        "worker-b",
        min_idle_seconds=1,
        count=1,
    )
    assert [item.id for item in recovered] == [message_id]
    assert recovered[0].payload == {"run_id": "run-1", "attempt": 1}
    assert backend.stream_ack(stream, "workers", message_id)
    assert backend.stream_reclaim(
        stream, "workers", "worker-c", min_idle_seconds=1, count=1
    ) == []
