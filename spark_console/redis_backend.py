"""Optional Redis primitives for a future distributed deployment.

The application currently uses SQLite/PostgreSQL for its source of truth.  A
``RedisBackend`` is therefore never constructed by the existing web, auth, or
worker paths.  Callers that opt in explicitly can use these small atomic
primitives while migrating one concern at a time:

* ``allow_rate_limit`` uses one Lua script for an atomic fixed-window counter;
* ``claim_idempotency`` stores a value only once with a TTL;
* ``enqueue``/``dequeue`` provide a bounded, inspectable queue hand-off;
* ``stream_enqueue``/``stream_read``/``stream_ack``/``stream_reclaim`` provide
  a durable Redis Streams hand-off with consumer-group recovery.  The latter
  is the primitive intended for a future persistent task queue: a worker must
  explicitly acknowledge a message, and another worker can reclaim messages
  whose lease has expired.

The import of redis-py is lazy so the default SQLite installation can start
without a Redis server and without importing an optional network client.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any


class RedisBackendError(RuntimeError):
    """Base error for explicitly enabled Redis operations."""


class RedisDisabled(RedisBackendError):
    """Raised when an operation requires Redis but the adapter is disabled."""


class RedisOperationError(RedisBackendError):
    """Raised when an enabled Redis operation cannot be completed."""


_KEY_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_KEY_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,63}$")


@dataclass(frozen=True)
class RedisBackendConfig:
    url: str
    key_prefix: str = "dyfire"
    socket_timeout_seconds: float = 1.0
    connect_timeout_seconds: float = 1.0
    max_connections: int = 20

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("Redis URL must be non-empty")
        if not _KEY_PREFIX.fullmatch(self.key_prefix):
            raise ValueError("Redis key prefix contains unsupported characters")
        if self.socket_timeout_seconds <= 0 or self.connect_timeout_seconds <= 0:
            raise ValueError("Redis timeouts must be positive")
        if self.max_connections < 1:
            raise ValueError("Redis max connections must be positive")


@dataclass(frozen=True)
class RedisStreamMessage:
    """One decoded Redis Stream entry held by a consumer until it is acked."""

    id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class RedisStreamProbe:
    """Result of an ephemeral producer/consumer-group round trip."""

    stream: str
    message_id: str
    consumer: str
    acknowledged: bool


_RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
return current
"""


class RedisBackend:
    """Lazy Redis client with explicit, atomic building blocks.

    ``RedisBackend.disabled()`` is the only object returned for the default
    configuration.  It has no client and no network side effects; this makes
    it safe to add the preparation layer before a production Redis rollout.
    """

    def __init__(self, config: RedisBackendConfig | None = None):
        self._config = config
        self._client: Any | None = None

    @classmethod
    def disabled(cls) -> "RedisBackend":
        return cls(None)

    @classmethod
    def from_settings(cls, settings: Any) -> "RedisBackend":
        if not getattr(settings, "redis_enabled", False):
            return cls.disabled()
        url = getattr(settings, "redis_url", None)
        if not url:
            raise ValueError("Redis is enabled but SPARK_REDIS_URL is empty")
        return cls(
            RedisBackendConfig(
                url=url,
                key_prefix=getattr(settings, "redis_key_prefix", "dyfire"),
                socket_timeout_seconds=float(
                    getattr(settings, "redis_socket_timeout_seconds", 1.0)
                ),
                connect_timeout_seconds=float(
                    getattr(settings, "redis_connect_timeout_seconds", 1.0)
                ),
                max_connections=int(getattr(settings, "redis_max_connections", 20)),
            )
        )

    @property
    def enabled(self) -> bool:
        return self._config is not None

    @property
    def connected(self) -> bool:
        return self._client is not None

    def _require_client(self) -> Any:
        if self._config is None:
            raise RedisDisabled("Redis backend is disabled")
        if self._client is None:
            try:
                import redis

                pool = redis.ConnectionPool.from_url(
                    self._config.url,
                    max_connections=self._config.max_connections,
                    socket_timeout=self._config.socket_timeout_seconds,
                    socket_connect_timeout=self._config.connect_timeout_seconds,
                    health_check_interval=30,
                    decode_responses=True,
                )
                self._client = redis.Redis(connection_pool=pool)
            except Exception as error:  # pragma: no cover - depends on optional package
                raise RedisOperationError("Redis client could not be initialized") from error
        return self._client

    def _key(self, namespace: str, value: str) -> str:
        if not _KEY_COMPONENT.fullmatch(namespace) or not _KEY_COMPONENT.fullmatch(value):
            raise ValueError("Redis namespace and key contain unsupported characters")
        return f"{self._config.key_prefix}:{namespace}:{value}"

    def ping(self) -> bool:
        """Check connectivity; only explicit Redis configurations connect."""
        try:
            return bool(self._require_client().ping())
        except RedisDisabled:
            raise
        except Exception as error:
            raise RedisOperationError("Redis ping failed") from error

    def allow_rate_limit(self, key: str, *, limit: int, window_seconds: int) -> bool:
        """Atomically consume a fixed-window token.

        This method is intentionally not wired into HTTP routes yet.  A
        future rollout can select it for one endpoint while retaining the
        existing process-local limiter as the default behavior.
        """
        if limit < 1 or window_seconds < 1:
            raise ValueError("rate limit and window must be positive")
        client = self._require_client()
        try:
            current = int(
                client.eval(
                    _RATE_LIMIT_SCRIPT,
                    1,
                    self._key("rate", key),
                    int(window_seconds * 1000),
                )
            )
        except Exception as error:
            raise RedisOperationError("Redis rate-limit operation failed") from error
        return current <= limit

    def claim_idempotency(self, key: str, value: str, *, ttl_seconds: int) -> bool:
        """Store an idempotency result only if ``key`` is currently absent."""
        if ttl_seconds < 1:
            raise ValueError("idempotency TTL must be positive")
        try:
            result = self._require_client().set(
                self._key("idempotency", key), value, nx=True, ex=ttl_seconds
            )
        except Exception as error:
            raise RedisOperationError("Redis idempotency operation failed") from error
        return bool(result)

    def get_idempotency(self, key: str) -> str | None:
        try:
            result = self._require_client().get(self._key("idempotency", key))
        except Exception as error:
            raise RedisOperationError("Redis idempotency read failed") from error
        return None if result is None else str(result)

    def enqueue(self, queue: str, payload: dict[str, Any], *, max_items: int = 0) -> int:
        """Append one JSON payload, optionally trimming the queue to a bound."""
        if max_items < 0:
            raise ValueError("max_items cannot be negative")
        client = self._require_client()
        try:
            key = self._key("queue", queue)
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if max_items:
                # Keep append + trim in one transaction so concurrent
                # producers cannot observe an unbounded queue between calls.
                with client.pipeline(transaction=True) as pipeline:
                    pipeline.rpush(key, encoded)
                    pipeline.ltrim(key, -max_items, -1)
                    pipeline.llen(key)
                    _, _, length = pipeline.execute()
                return int(length)
            return int(client.rpush(key, encoded))
        except Exception as error:
            raise RedisOperationError("Redis enqueue operation failed") from error

    def dequeue(self, queue: str, *, timeout_seconds: int = 0) -> dict[str, Any] | None:
        """Pop one JSON payload; zero is non-blocking, positive values bound wait."""
        if timeout_seconds < 0:
            raise ValueError("queue timeout cannot be negative")
        try:
            client = self._require_client()
            if timeout_seconds == 0:
                raw = client.lpop(self._key("queue", queue))
                if raw is None:
                    return None
            else:
                item = client.blpop(
                    self._key("queue", queue), timeout=timeout_seconds
                )
                if item is None:
                    return None
                _, raw = item
            if raw is None:
                return None
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("queue payload must be a JSON object")
            return value
        except RedisBackendError:
            raise
        except Exception as error:
            raise RedisOperationError("Redis dequeue operation failed") from error

    def _stream_key(self, stream: str) -> str:
        return self._key("stream", stream)

    @staticmethod
    def _stream_member(value: Any) -> RedisStreamMessage:
        """Decode one redis-py ``(id, fields)`` entry defensively."""
        try:
            message_id, fields = value
            raw_payload = fields.get("payload")
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("invalid Redis Stream entry") from error
        if raw_payload is None:
            raise ValueError("Redis Stream entry has no payload")
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            raise ValueError("Redis Stream payload must be a JSON object")
        return RedisStreamMessage(id=str(message_id), payload=payload)

    def _ensure_stream_group(
        self, client: Any, stream: str, group: str, *, start_id: str = "0-0"
    ) -> None:
        key = self._stream_key(stream)
        try:
            client.xgroup_create(key, group, id=start_id, mkstream=True)
        except Exception as error:
            # Creating the same group is an idempotent operation.  Do not hide
            # connection or ACL failures behind the BUSYGROUP compatibility
            # path.
            if "BUSYGROUP" not in str(error).upper():
                raise

    def stream_enqueue(
        self,
        stream: str,
        payload: dict[str, Any],
        *,
        max_items: int = 0,
    ) -> str:
        """Append a JSON object to a durable consumer-group stream.

        ``max_items`` is an exact trim bound (``0`` means no trim).  Trimming
        must be chosen with care: entries still pending in a consumer group
        should not be removed before their processing lease is resolved.
        """
        if max_items < 0:
            raise ValueError("max_items cannot be negative")
        if not isinstance(payload, dict):
            raise ValueError("stream payload must be a JSON object")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            kwargs = {"maxlen": max_items, "approximate": False} if max_items else {}
            return str(self._require_client().xadd(self._stream_key(stream), {"payload": encoded}, **kwargs))
        except RedisBackendError:
            raise
        except Exception as error:
            raise RedisOperationError("Redis Stream enqueue operation failed") from error

    def stream_read(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int = 1,
        block_seconds: int = 0,
        start_id: str = ">",
    ) -> list[RedisStreamMessage]:
        """Read new or pending entries for a consumer group.

        ``start_id='>'`` reads entries never delivered to any consumer.  A
        caller recovering its own pending entries can pass ``'0'``; entries
        owned by another dead consumer should use :meth:`stream_reclaim`.
        Group creation starts at ``start_id`` (default ``0-0``), so creating a
        group before producers publish cannot lose messages.
        """
        if count < 1:
            raise ValueError("stream read count must be positive")
        if block_seconds < 0:
            raise ValueError("stream block timeout cannot be negative")
        if not _KEY_COMPONENT.fullmatch(group) or not _KEY_COMPONENT.fullmatch(consumer):
            raise ValueError("stream group and consumer contain unsupported characters")
        try:
            client = self._require_client()
            self._ensure_stream_group(client, stream, group)
            block_ms = int(block_seconds * 1000) if block_seconds else None
            entries = client.xreadgroup(
                group,
                consumer,
                {self._stream_key(stream): start_id},
                count=count,
                block=block_ms,
            )
            messages: list[RedisStreamMessage] = []
            for _stream_name, values in entries or []:
                messages.extend(self._stream_member(value) for value in values)
            return messages
        except RedisBackendError:
            raise
        except Exception as error:
            raise RedisOperationError("Redis Stream read operation failed") from error

    def stream_ack(self, stream: str, group: str, message_id: str) -> bool:
        """Acknowledge one successfully processed Stream entry."""
        if not _KEY_COMPONENT.fullmatch(group) or not _KEY_COMPONENT.fullmatch(message_id):
            raise ValueError("stream group and message id contain unsupported characters")
        try:
            return bool(self._require_client().xack(self._stream_key(stream), group, message_id))
        except RedisBackendError:
            raise
        except Exception as error:
            raise RedisOperationError("Redis Stream ack operation failed") from error

    def stream_probe(self) -> RedisStreamProbe:
        """Exercise an ephemeral Stream producer, consumer, and ack path.

        The probe is deliberately opt-in and uses a random key.  It is useful
        during a staged Redis rollout because it validates the ACL, stream,
        consumer-group, and acknowledgement permissions without publishing a
        real task.  The temporary stream is deleted in a ``finally`` block so
        repeated health checks cannot accumulate queue data.
        """
        token = uuid.uuid4().hex
        stream = f"preflight-{token}"
        group = f"preflight-{token}"
        consumer = f"probe-{token}"
        payload = {"kind": "dyfire-queue-preflight", "token": token}
        client = self._require_client()
        try:
            message_id = self.stream_enqueue(stream, payload)
            messages = self.stream_read(stream, group, consumer, count=1)
            if len(messages) != 1 or messages[0].id != message_id:
                raise RedisOperationError(
                    "Redis Stream probe did not receive its published message"
                )
            if messages[0].payload != payload:
                raise RedisOperationError(
                    "Redis Stream probe payload was changed in transit"
                )
            acknowledged = self.stream_ack(stream, group, message_id)
            if not acknowledged:
                raise RedisOperationError("Redis Stream probe acknowledgement failed")
            return RedisStreamProbe(
                stream=stream,
                message_id=message_id,
                consumer=consumer,
                acknowledged=True,
            )
        except RedisBackendError:
            raise
        except Exception as error:
            raise RedisOperationError("Redis Stream probe failed") from error
        finally:
            try:
                client.delete(self._stream_key(stream))
            except Exception as error:
                raise RedisOperationError(
                    "Redis Stream probe cleanup failed"
                ) from error

    def stream_reclaim(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        min_idle_seconds: int,
        count: int = 10,
    ) -> list[RedisStreamMessage]:
        """Claim another consumer's idle pending entries for recovery.

        Redis keeps an entry pending until ``stream_ack``.  The caller should
        make processing idempotent before acknowledging a reclaimed entry;
        reclaiming is deliberately at-least-once and can produce duplicates.
        """
        if min_idle_seconds < 1:
            raise ValueError("minimum stream idle time must be positive")
        if count < 1:
            raise ValueError("stream reclaim count must be positive")
        if not _KEY_COMPONENT.fullmatch(group) or not _KEY_COMPONENT.fullmatch(consumer):
            raise ValueError("stream group and consumer contain unsupported characters")
        try:
            client = self._require_client()
            self._ensure_stream_group(client, stream, group)
            pending = client.xpending_range(
                self._stream_key(stream), group, min="-", max="+", count=count
            )
            ids = [str(item["message_id"]) for item in pending]
            if not ids:
                return []
            claimed = client.xclaim(
                self._stream_key(stream),
                group,
                consumer,
                min_idle_time=int(min_idle_seconds * 1000),
                message_ids=ids,
            )
            return [self._stream_member(value) for value in claimed or []]
        except RedisBackendError:
            raise
        except Exception as error:
            raise RedisOperationError("Redis Stream reclaim operation failed") from error
