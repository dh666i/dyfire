from __future__ import annotations

from datetime import timedelta

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

    def allow_rate_limit(self, key, *, limit, window_seconds):
        self.calls.append(("allow", key, limit, window_seconds))
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key] <= limit

    def rate_limit_count(self, key):
        self.calls.append(("count", key))
        return self.counts.get(key, 0)

    def record_rate_limit_failure(self, key, *, window_seconds):
        self.calls.append(("failure", key, window_seconds))
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def clear_rate_limit(self, key):
        self.calls.append(("clear", key))
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
    assert backend.calls[0] == ("count", "login:client")
    assert ("clear", "login:client") in backend.calls


def test_scan_style_limiter_keeps_endpoint_specific_limits():
    backend = FakeRedisBackend()
    limiter = RedisRequestLimiter(backend, namespace="scan", window_seconds=60)
    assert limiter.allow("user:start", 2)
    assert limiter.allow("user:start", 2)
    assert not limiter.allow("user:start", 2)
    assert backend.calls[0] == ("allow", "scan:user:start", 2, 60)


def test_enabled_backend_errors_fail_closed_without_local_fallback():
    class Broken(FakeRedisBackend):
        def rate_limit_count(self, *args, **kwargs):
            raise RuntimeError("redis unavailable")

    limiter = build_rate_limiter(Broken(), namespace="login")
    assert isinstance(limiter, RedisRateLimiter)
    assert not limiter.allow("client")
