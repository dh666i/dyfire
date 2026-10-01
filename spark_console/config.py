from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


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
    auth_poll_seconds: int = 1
    clock_offset_limit_seconds: int = 5
    secure_cookies: bool = True
    max_request_bytes: int = 2 * 1024 * 1024
    trusted_proxy_cidrs: tuple[str, ...] = ()
    hsts_max_age_seconds: int = 31536000
    hsts_include_subdomains: bool = False

    def __post_init__(self) -> None:
        for cidr in self.trusted_proxy_cidrs:
            try:
                ipaddress.ip_network(cidr, strict=False)
            except (TypeError, ValueError) as error:
                raise ValueError(f"invalid trusted proxy CIDR: {cidr!r}") from error
        if self.hsts_max_age_seconds < 0:
            raise ValueError("HSTS max age must be non-negative")

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "Settings":
        try:
            data_dir = Path(environ["SPARK_DATA_DIR"]).resolve()
            cookie_key_file = Path(environ["SPARK_COOKIE_KEY_FILE"]).resolve()
            session_key_file = Path(environ["SPARK_SESSION_KEY_FILE"]).resolve()
        except KeyError as error:
            raise ValueError(f"missing required setting: {error.args[0]}") from error

        data_dir.mkdir(parents=True, exist_ok=True)
        if len(cookie_key_file.read_bytes()) != 32:
            raise ValueError("cookie key must be exactly 32 bytes")
        if len(session_key_file.read_bytes()) < 32:
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
            hsts_max_age_seconds=hsts_max_age_seconds,
            hsts_include_subdomains=_env_bool(
                environ.get("SPARK_HSTS_INCLUDE_SUBDOMAINS", "false"),
                name="SPARK_HSTS_INCLUDE_SUBDOMAINS",
            ),
        )
