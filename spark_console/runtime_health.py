from __future__ import annotations

import argparse
import os
import socket
import time
from pathlib import Path

from sqlalchemy import select

from spark_console.config import Settings
from spark_console.db import create_engine_for
from spark_console.models import WorkerLock


AUTH_HEARTBEAT = Path("/data/auth-worker.heartbeat")
AUTH_HEARTBEAT_MAX_AGE_SECONDS = 60


def _module_process_running(module: str) -> bool:
    return bool(_module_process_ids(module))


def _module_process_ids(module: str) -> tuple[int, ...]:
    """Return PIDs whose argv contains the exact ``python -m`` module."""
    expected = module.encode()
    own_pid = os.getpid()
    found: list[int] = []
    for cmdline_path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            pid = int(cmdline_path.parent.name)
            if pid == own_pid:
                continue
            arguments = cmdline_path.read_bytes().split(b"\0")
        except (OSError, ValueError):
            continue
        if b"-m" in arguments and expected in arguments:
            found.append(pid)
    return tuple(found)


def _worker_lease_running(
    database_url: str,
    shard: int,
    *,
    now: float | None = None,
    process_ids: tuple[int, ...] = (),
) -> bool:
    """Require a live lease owned by this host's worker process.

    A process can remain visible briefly after losing its database lease.  The
    lease owner contains the worker PID, so health checks reject a stale or
    foreign shard lease while keeping the check read-only.
    """
    from datetime import datetime, timezone

    engine = None
    try:
        engine = create_engine_for(
            Settings(
                data_dir=Path("/tmp"),
                database_url=database_url,
                cookie_key_file=Path("/dev/null"),
                session_key_file=Path("/dev/null"),
                worker_shard=shard,
                worker_shards=max(1, shard + 1),
            )
        )
        current = datetime.now(timezone.utc) if now is None else datetime.fromtimestamp(now, timezone.utc)
        with engine.connect() as connection:
            row = connection.execute(
                select(WorkerLock.worker_id, WorkerLock.lease_until).where(
                    WorkerLock.id == shard + 1
                )
            ).first()
        if row is None or not row.worker_id or row.lease_until is None:
            return False
        lease_until = row.lease_until
        if lease_until.tzinfo is None:
            lease_until = lease_until.replace(tzinfo=timezone.utc)
        else:
            lease_until = lease_until.astimezone(timezone.utc)
        if lease_until <= current:
            return False
        owner = str(row.worker_id)
        prefix = socket.gethostname() + "-"
        if not owner.startswith(prefix):
            return False
        try:
            owner_pid = int(owner[len(prefix):].split("-", 1)[0])
        except (TypeError, ValueError):
            return False
        return owner_pid in process_ids
    except Exception:
        return False
    finally:
        if engine is not None:
            engine.dispose()


def _fresh(path: Path, maximum_age_seconds: int) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return -30 <= age <= maximum_age_seconds


def main() -> int:
    parser = argparse.ArgumentParser(prog="spark-runtime-health")
    parser.add_argument("service", choices=("auth", "worker"))
    args = parser.parse_args()
    if args.service == "auth":
        healthy = _module_process_running(
            "spark_console.auth_worker"
        ) and _fresh(AUTH_HEARTBEAT, AUTH_HEARTBEAT_MAX_AGE_SECONDS)
    else:
        process_ids = _module_process_ids("spark_console.worker")
        healthy = bool(process_ids)
        if healthy:
            try:
                settings = Settings.from_env(os.environ)
                healthy = _worker_lease_running(
                    settings.database_url,
                    settings.worker_shard,
                    process_ids=process_ids,
                )
            except (KeyError, OSError, ValueError):
                healthy = False
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
