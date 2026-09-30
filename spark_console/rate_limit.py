from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta

from spark_console.models import utc_now


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
