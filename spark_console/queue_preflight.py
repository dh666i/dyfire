"""Read-only queue rollout checks with an explicit ephemeral Redis probe.

The production worker still claims tasks from the database.  This module is
an operator-facing gate for a later PostgreSQL/Redis rollout: it verifies the
database claim tables and shard configuration, then optionally exercises a
random Redis Stream without touching a real task stream.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, inspect, text

from spark_console.config import Settings
from spark_console.db import create_engine_for
from spark_console.redis_backend import RedisBackend, RedisStreamProbe
from spark_console.worker_sharding import validate_shard


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    status: str
    detail: str
    duration_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class QueuePreflightReport:
    checks: tuple[PreflightCheck, ...]
    redis_enabled: bool
    stream_probe: bool

    @property
    def passed(self) -> bool:
        return all(check.status in {"ok", "disabled"} for check in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.passed else "failed",
            "redis_enabled": self.redis_enabled,
            "stream_probe": self.stream_probe,
            "checks": [check.as_dict() for check in self.checks],
        }


def _run_check(name: str, operation) -> PreflightCheck:
    started = time.perf_counter()
    try:
        status, detail = operation()
    except Exception as error:
        # A connection string can contain a password.  Keep command output
        # useful to an operator without leaking exception text or secrets.
        status, detail = "failed", f"{type(error).__name__}: check failed"
    elapsed = int(round((time.perf_counter() - started) * 1000))
    return PreflightCheck(name, status, detail, max(0, elapsed))


_CLAIM_TABLES = {
    "users",
    "douyin_accounts",
    "spark_tasks",
    "task_runs",
    "worker_lock",
}
_CLAIM_COLUMNS = {
    "douyin_accounts": {"dispatch_key"},
    "spark_tasks": {"next_run_at", "enabled", "douyin_account_id"},
    "task_runs": {"task_id", "status", "worker_id", "lease_until"},
    "worker_lock": {"id", "worker_id", "lease_until"},
}


def _database_check(engine: Engine) -> tuple[str, str]:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    missing_tables = sorted(_CLAIM_TABLES - tables)
    if missing_tables:
        raise RuntimeError("missing claim tables")
    missing_columns = []
    for table, expected in _CLAIM_COLUMNS.items():
        actual = {column["name"] for column in inspector.get_columns(table)}
        missing_columns.extend(f"{table}.{column}" for column in expected - actual)
    if missing_columns:
        raise RuntimeError("missing claim columns")
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return "ok", f"{engine.dialect.name}; claim tables and columns present"


def _shard_check(settings: Settings) -> tuple[str, str]:
    shard, shards = validate_shard(settings.worker_shard, settings.worker_shards)
    return "ok", f"worker shard {shard}/{shards}"


def _redis_check(
    settings: Settings,
    backend: RedisBackend | Any,
    probe_stream: bool,
) -> tuple[str, str]:
    if not settings.redis_enabled:
        return "disabled", "Redis disabled; database claim path remains active"
    if not backend.ping():
        raise RuntimeError("Redis ping returned false")
    if not probe_stream:
        return "ok", "Redis ping succeeded; Stream probe not requested"
    result: RedisStreamProbe = backend.stream_probe()
    return "ok", f"ephemeral Stream round trip acknowledged ({result.message_id})"


def run_queue_preflight(
    settings: Settings,
    *,
    engine: Engine | None = None,
    backend: RedisBackend | Any | None = None,
    probe_stream: bool = False,
) -> QueuePreflightReport:
    """Run rollout checks without creating schema or changing task state."""
    owned_engine = engine is None
    engine = engine or create_engine_for(settings)
    try:
        checks = [
            _run_check("database_claim", lambda: _database_check(engine)),
            _run_check("worker_sharding", lambda: _shard_check(settings)),
        ]
        if backend is None:
            backend = RedisBackend.from_settings(settings)
        checks.append(
            _run_check(
                "redis_queue",
                lambda: _redis_check(settings, backend, probe_stream),
            )
        )
        return QueuePreflightReport(
            checks=tuple(checks),
            redis_enabled=settings.redis_enabled,
            stream_probe=probe_stream,
        )
    finally:
        if owned_engine:
            engine.dispose()


def _runtime_settings() -> Settings:
    return Settings.from_env(os.environ)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spark-queue-preflight")
    parser.add_argument(
        "--probe-stream",
        action="store_true",
        help="publish and acknowledge one random ephemeral Redis Stream entry",
    )
    args = parser.parse_args(argv)
    settings = _runtime_settings()
    report = run_queue_preflight(settings, probe_stream=args.probe_stream)
    print(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
