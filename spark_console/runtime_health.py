from __future__ import annotations

import argparse
import os
import time
from pathlib import Path


AUTH_HEARTBEAT = Path("/data/auth-worker.heartbeat")
AUTH_HEARTBEAT_MAX_AGE_SECONDS = 60


def _module_process_running(module: str) -> bool:
    expected = module.encode()
    own_pid = os.getpid()
    for cmdline_path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            if int(cmdline_path.parent.name) == own_pid:
                continue
            arguments = cmdline_path.read_bytes().split(b"\0")
        except (OSError, ValueError):
            continue
        if b"-m" in arguments and expected in arguments:
            return True
    return False


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
        healthy = _module_process_running("spark_console.worker")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
