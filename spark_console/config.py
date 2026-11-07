from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from typing import Mapping

from spark_console.worker_sharding import validate_shard


_REDIS_KEY_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,63}$")


def _env_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _trusted_proxy_cidrs(value: str) -> tuple[str, ...]:
    """Return explicit proxy networks; an empty value means no trust."""
    networks = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(str(ipaddress.ip_network(item, strict=False)))
        except ValueError as error:
            raise ValueError(f"invalid trusted proxy CIDR: {item!r}") from error
    return tuple(networks)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    cookie_key_file: Path
    session_key_file: Path
    timezone: str = "Asia/Shanghai"
    web_bind: str = "127.0.0.1"
    web_port: int = 8899
    worker_poll_seconds: int = 10
    # A worker owns accounts whose stable dispatch key maps to this shard.
    # Defaults deliberately preserve the historical single-worker behavior.
    worker_shard: int = 0
    worker_shards: int = 1
    auth_poll_seconds: int = 1
    clock_offset_limit_seconds: int = 5
    secure_cookies: bool = True
    max_request_bytes: int = 2 * 1024 * 1024
    trusted_proxy_cidrs: tuple[str, ...] = ()
    public_origin: str | None = None
    hsts_max_age_seconds: int = 31536000
    hsts_include_subdomains: bool = False
    # PostgreSQL pool settings. SQLite keeps its existing file/WAL behavior.
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout_seconds: int = 30
    database_pool_recycle_seconds: int = 1800
    # Redis is an optional preparation layer for distributed primitives.  It
    # is deliberately disabled by default so SQLite deployments never open a
    # Redis connection or change their existing request/queue behavior.
    redis_enabled: bool = False
    redis_url: str | None = None
    redis_key_prefix: str = "dyfire"
    redis_socket_timeout_seconds: float = 1.0
    redis_connect_timeout_seconds: float = 1.0
    redis_max_connections: int = 20

    def __post_init__(self) -> None:
        if self.public_origin is not None:
            try:
                origin = urlsplit(self.public_origin)
                port = origin.port
                valid = (origin.scheme in ('http', 'https') and bool(origin.hostname)
                         and origin.username is None and origin.password is None
                         and origin.path in ('', '/') and not origin.query and not origin.fragment
                         and not any(ord(c) <= 32 for c in self.public_origin))
            except (TypeError, ValueError):
                valid = False
            if not valid:
                raise ValueError('public origin must be a complete http(s) origin without path or credentials')
        for cidr in self.trusted_proxy_cidrs:
            try:
                ipaddress.ip_network(cidr, strict=False)
            except (TypeError, ValueError) as error:
                raise ValueError(f"invalid trusted proxy CIDR: {cidr!r}") from error
        if self.hsts_max_age_seconds < 0:
            raise ValueError("HSTS max age must be non-negative")
        if self.database_pool_size < 1:
            raise ValueError("database pool size must be positive")
        if self.database_max_overflow < 0:
            raise ValueError("database max overflow must be non-negative")
        if self.database_pool_timeout_seconds < 1:
            raise ValueError("database pool timeout must be positive")
        if self.database_pool_recycle_seconds < 0:
            raise ValueError("database pool recycle must be non-negative")
        if self.redis_enabled and not self.redis_url:
            raise ValueError("redis URL is required when Redis is enabled")
        if not _REDIS_KEY_PREFIX.fullmatch(self.redis_key_prefix):
            raise ValueError(
                "redis key prefix must contain 1-64 safe ASCII characters"
            )
        if self.redis_socket_timeout_seconds <= 0:
            raise ValueError("redis socket timeout must be positive")
        if self.redis_connect_timeout_seconds <= 0:
            raise ValueError("redis connect timeout must be positive")
        if self.redis_max_connections < 1:
            raise ValueError("redis max connections must be positive")
        validate_shard(self.worker_shard, self.worker_shards)

    @classmethod
    def from_env(cls, environ: Mapping[str, str], *, service: str = 'web') -> "Settings":
        if service not in ('web', 'auth', 'worker', 'monitor'):
            raise ValueError('unknown service')
        try:
            data_dir = Path(environ["SPARK_DATA_DIR"]).resolve()
            cookie_key_file = Path(environ["SPARK_COOKIE_KEY_FILE"]).resolve()
            session_key_file = Path(environ["SPARK_SESSION_KEY_FILE"]).resolve()
        except KeyError as error:
            raise ValueError(f"missing required setting: {error.args[0]}") from error

        data_dir.mkdir(parents=True, exist_ok=True)
        if service != 'monitor' and len(cookie_key_file.read_bytes()) != 32:
            raise ValueError("cookie key must be exactly 32 bytes")
        if service == 'web' and len(session_key_file.read_bytes()) < 32:
            raise ValueError("session key must contain at least 32 bytes")

        hsts_max_age_seconds = int(environ.get("SPARK_HSTS_MAX_AGE_SECONDS", "31536000"))

        return cls(
            data_dir=data_dir,
            database_url=environ.get(
                "SPARK_DATABASE_URL", f"sqlite:///{data_dir / 'spark.db'}"
            ),
            cookie_key_file=cookie_key_file,
            session_key_file=session_key_file,
            timezone=environ.get("SPARK_TIMEZONE", "Asia/Shanghai"),
            web_bind=environ.get("SPARK_WEB_BIND", "127.0.0.1"),
            web_port=int(environ.get("SPARK_WEB_PORT", "8899")),
            worker_poll_seconds=int(environ.get("SPARK_WORKER_POLL_SECONDS", "10")),
            worker_shard=int(environ.get("SPARK_WORKER_SHARD", "0")),
            worker_shards=int(environ.get("SPARK_WORKER_SHARDS", "1")),
            auth_poll_seconds=int(environ.get("SPARK_AUTH_POLL_SECONDS", "1")),
            clock_offset_limit_seconds=int(
                environ.get("SPARK_CLOCK_OFFSET_LIMIT_SECONDS", "5")
            ),
            secure_cookies=_env_bool(
                environ.get("SPARK_SECURE_COOKIES", "true"),
                name="SPARK_SECURE_COOKIES",
            ),
            max_request_bytes=int(
                environ.get("SPARK_MAX_REQUEST_BYTES", str(2 * 1024 * 1024))
            ),
            trusted_proxy_cidrs=_trusted_proxy_cidrs(
                environ.get("SPARK_TRUSTED_PROXY_CIDRS", "")
            ),
            public_origin=environ.get('SPARK_PUBLIC_ORIGIN', '').strip() or None,
            hsts_max_age_seconds=hsts_max_age_seconds,
            hsts_include_subdomains=_env_bool(
                environ.get("SPARK_HSTS_INCLUDE_SUBDOMAINS", "false"),
                name="SPARK_HSTS_INCLUDE_SUBDOMAINS",
            ),
            database_pool_size=int(environ.get("SPARK_DATABASE_POOL_SIZE", "10")),
            database_max_overflow=int(environ.get("SPARK_DATABASE_MAX_OVERFLOW", "20")),
            database_pool_timeout_seconds=int(
                environ.get("SPARK_DATABASE_POOL_TIMEOUT_SECONDS", "30")
            ),
            database_pool_recycle_seconds=int(
                environ.get("SPARK_DATABASE_POOL_RECYCLE_SECONDS", "1800")
            ),
            redis_enabled=_env_bool(
                environ.get("SPARK_REDIS_ENABLED", "false"),
                name="SPARK_REDIS_ENABLED",
            ),
            redis_url=(environ.get("SPARK_REDIS_URL") or None),
            redis_key_prefix=environ.get("SPARK_REDIS_KEY_PREFIX", "dyfire"),
            redis_socket_timeout_seconds=float(
                environ.get("SPARK_REDIS_SOCKET_TIMEOUT_SECONDS", "1")
            ),
            redis_connect_timeout_seconds=float(
                environ.get("SPARK_REDIS_CONNECT_TIMEOUT_SECONDS", "1")
            ),
            redis_max_connections=int(
                environ.get("SPARK_REDIS_MAX_CONNECTIONS", "20")
            ),
        )
