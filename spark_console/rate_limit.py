from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta
from math import ceil

from spark_console.models import utc_now
from spark_console.redis_backend import RedisBackend


class FailedAttemptLimiter:
    def __init__(
        self,
        limit: int = 10,
        window: timedelta = timedelta(minutes=10),
        now=utc_now,
        max_keys: int = 10_000,
    ):
        if max_keys <= 0:
            raise ValueError("max_keys must be positive")
        self.limit = limit
        self.window = window
        self.now = now
        self.max_keys = max_keys
        self.attempts: dict[str, deque[datetime]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[datetime]:
        # This limiter is intentionally process-local, but it must still have
        # a hard memory bound.  Public endpoints can receive unbounded unique
        # client keys (for example, from a rotating botnet).
        if key not in self.attempts and len(self.attempts) >= self.max_keys:
            self.attempts.pop(next(iter(self.attempts)), None)
        values = self.attempts[key]
        cutoff = self.now() - self.window
        while values and values[0] <= cutoff:
            values.popleft()
        return values

    def allow(self, key: str) -> bool:
        return len(self._prune(key)) < self.limit

    def record_failure(self, key: str) -> None:
        self._prune(key).append(self.now())

    def clear(self, key: str) -> None:
        self.attempts.pop(key, None)


class RedisRateLimiter:
    """Redis-backed fixed-window limiter with the local limiter interface.

    ``FailedAttemptLimiter`` records only failed attempts. Redis mirrors that
    behavior by reading the shared counter during ``allow`` and incrementing
    it only from ``record_failure``. Successful authentication clears it.

    Redis errors fail closed. Falling back to an in-process counter after an
    explicitly enabled Redis backend would make a multi-worker deployment
    silently lose its protection, so fallback is only selected by
    :func:`build_rate_limiter` when Redis is disabled.
    """

    def __init__(
        self,
        backend: RedisBackend,
        *,
        limit: int = 10,
        window: timedelta = timedelta(minutes=10),
        namespace: str = "auth",
    ):
        if limit < 1:
            raise ValueError("limit must be positive")
        window_seconds = int(ceil(window.total_seconds()))
        if window_seconds < 1:
            raise ValueError("window must be positive")
        if not namespace or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:@/-"
            for character in namespace
        ):
            raise ValueError("namespace contains unsupported characters")
        self.backend = backend
        self.limit = limit
        self.window = window
        self.window_seconds = window_seconds
        self.namespace = namespace

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def allow(self, key: str) -> bool:
        try:
            return self.backend.rate_limit_count(self._key(key)) < self.limit
        except Exception:
            return False

    def record_failure(self, key: str) -> None:
        try:
            self.backend.record_rate_limit_failure(
                self._key(key), window_seconds=self.window_seconds
            )
        except Exception:
            # A failed write must not disable a limit that is already being
            # enforced; the next request remains fail-closed in ``allow``.
            return None

    def clear(self, key: str) -> None:
        try:
            self.backend.clear_rate_limit(self._key(key))
        except Exception:
            # A reset failure must not disable the limiter. The next attempt
            # remains protected by the existing Redis counter.
            return None


class RedisRequestLimiter:
    """Distributed per-key request limiter for endpoints with varying limits."""

    def __init__(self, backend: RedisBackend, *, namespace: str = "request", window_seconds: int = 60):
        if window_seconds < 1:
            raise ValueError("window_seconds must be positive")
        self.backend = backend
        self.namespace = namespace
        self.window_seconds = window_seconds

    def allow(self, key: str, limit: int) -> bool:
        if limit < 1:
            raise ValueError("limit must be positive")
        try:
            return bool(
                self.backend.allow_rate_limit(
                    f"{self.namespace}:{key}",
                    limit=limit,
                    window_seconds=self.window_seconds,
                )
            )
        except Exception:
            return False


def build_rate_limiter(
    backend: RedisBackend | None,
    *,
    limit: int = 10,
    window: timedelta = timedelta(minutes=10),
    namespace: str = "auth",
    now=utc_now,
    max_keys: int = 10_000,
):
    """Choose the distributed limiter only for an explicitly enabled backend."""
    if backend is not None and backend.enabled:
        return RedisRateLimiter(
            backend,
            limit=limit,
            window=window,
            namespace=namespace,
        )
    return FailedAttemptLimiter(
        limit=limit,
        window=window,
        now=now,
        max_keys=max_keys,
    )
