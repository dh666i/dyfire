from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Lock, Barrier
import uuid

from spark_console.rate_limit import (
    FailedAttemptLimiter,
    RedisRateLimiter,
    RedisRequestLimiter,
    build_rate_limiter,
)


class FakeRedisBackend:
    """Small deterministic backend double; no redis-py or network required."""

    enabled = True

    def __init__(self):
        self.calls = []
        self.counts = {}
        self.pending = {}
        self.reservations = {}
        self.lock = Lock()

    def allow_rate_limit(self, key, *, limit, window_seconds):
        with self.lock:
            self.calls.append(("allow", key, limit, window_seconds))
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key] <= limit

    def rate_limit_count(self, key):
        with self.lock:
            self.calls.append(("count", key))
            return self.counts.get(key, 0)

    def record_rate_limit_failure(self, key, *, window_seconds):
        with self.lock:
            self.calls.append(("failure", key, window_seconds))
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key]

    def clear_rate_limit(self, key):
        with self.lock:
            self.calls.append(("clear", key))
            self.counts.pop(key, None)
            return True

    def reserve_failed_attempt(self, key, *, limit, window_seconds, reservation_seconds=None):
        with self.lock:
            self.calls.append(("reserve", key, limit, window_seconds))
            if self.counts.get(key, 0) + self.pending.get(key, 0) >= limit:
                return None
            token = uuid.uuid4().hex
            self.pending[key] = self.pending.get(key, 0) + 1
            self.reservations[token] = key
            return token

    def commit_failed_attempt(self, key, token, *, window_seconds):
        with self.lock:
            self.calls.append(("commit", key, token, window_seconds))
            if self.reservations.pop(token, None) != key:
                return 0
            self.pending[key] = max(0, self.pending.get(key, 0) - 1)
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key]

    def release_failed_attempt(self, key, token, *, clear_failures=False):
        with self.lock:
            self.calls.append(("release", key, token, clear_failures))
            if self.reservations.pop(token, None) == key:
                self.pending[key] = max(0, self.pending.get(key, 0) - 1)
            if clear_failures:
                self.counts.pop(key, None)
            return True


def test_disabled_backend_keeps_process_limiter_without_network_side_effects():
    class Disabled:
        enabled = False

        def allow_rate_limit(self, *args, **kwargs):
            raise AssertionError("disabled limiter must not call Redis")

    backend = Disabled()
    limiter = build_rate_limiter(
        backend, limit=2, window=timedelta(seconds=30), namespace="login"
    )
    assert isinstance(limiter, FailedAttemptLimiter)
    limiter.record_failure("client")
    limiter.record_failure("client")
    assert not limiter.allow("client")


def test_enabled_backend_uses_shared_counter_and_clears_after_success():
    backend = FakeRedisBackend()
    limiter = build_rate_limiter(
        backend, limit=2, window=timedelta(seconds=30), namespace="login"
    )
    assert isinstance(limiter, RedisRateLimiter)
    assert limiter.allow("client")
    limiter.record_failure("client")
    assert limiter.allow("client")
    limiter.record_failure("client")
    assert not limiter.allow("client")
    limiter.clear("client")
    assert limiter.allow("client")
    assert backend.calls[0] == ("reserve", "login:client", 2, 30)
    assert any(
        call[0] in {"clear", "release"} and call[1] == "login:client"
        for call in backend.calls
    )


def test_scan_style_limiter_keeps_endpoint_specific_limits():
    backend = FakeRedisBackend()
    limiter = RedisRequestLimiter(backend, namespace="scan", window_seconds=60)
    assert limiter.allow("user:start", 2)
    assert limiter.allow("user:start", 2)
    assert not limiter.allow("user:start", 2)
    assert backend.calls[0] == ("allow", "scan:user:start", 2, 60)


def test_enabled_backend_errors_fail_closed_without_local_fallback():
    class Broken(FakeRedisBackend):
        def reserve_failed_attempt(self, *args, **kwargs):
            raise RuntimeError("redis unavailable")

        def rate_limit_count(self, *args, **kwargs):
            raise RuntimeError("redis unavailable")

    limiter = build_rate_limiter(Broken(), namespace="login")
    assert isinstance(limiter, RedisRateLimiter)
    assert not limiter.allow("client")


def test_failed_attempt_reservations_bound_concurrent_admissions():
    backend = FakeRedisBackend()
    limiter = build_rate_limiter(
        backend, limit=3, window=timedelta(seconds=30), namespace="login"
    )
    barrier = Barrier(20)

    def attempt(_index):
        admitted = limiter.allow("client")
        barrier.wait()
        if admitted:
            limiter.record_failure("client")
        return admitted

    with ThreadPoolExecutor(max_workers=20) as executor:
        admitted = list(executor.map(attempt, range(20)))

    assert sum(admitted) == 3
    assert backend.counts["login:client"] == 3
