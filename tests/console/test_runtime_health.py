import os
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine

from spark_console.db import create_schema, session_scope
from spark_console.models import WorkerLock
from spark_console.runtime_health import (
    _fresh,
    _module_process_ids,
    _module_process_running,
    _worker_lease_running,
)


class _CommandLine:
    def __init__(self, pid: int, arguments: bytes):
        self.parent = type("Parent", (), {"name": str(pid)})()
        self._arguments = arguments

    def read_bytes(self):
        return self._arguments


class RuntimeHealthTests(unittest.TestCase):
    def test_module_process_detection_uses_exact_argument(self):
        entries = [
            _CommandLine(999_001, b"python\0-m\0spark_console.auth_worker\0"),
            _CommandLine(999_002, b"python\0-m\0spark_console.worker_helper\0"),
        ]
        with patch("spark_console.runtime_health.Path.glob", return_value=entries):
            self.assertTrue(_module_process_running("spark_console.auth_worker"))
            self.assertFalse(_module_process_running("spark_console.worker"))

    def test_module_process_ids_return_every_exact_worker_pid(self):
        entries = [
            _CommandLine(999_001, b"python\0-m\0spark_console.worker\0"),
            _CommandLine(999_002, b"python\0-m\0spark_console.worker\0"),
            _CommandLine(999_003, b"python\0-m\0spark_console.worker_helper\0"),
        ]
        with patch("spark_console.runtime_health.Path.glob", return_value=entries):
            self.assertEqual((999_001, 999_002), _module_process_ids("spark_console.worker"))

    def test_heartbeat_must_be_recent(self):
        with tempfile.TemporaryDirectory() as directory:
            heartbeat = Path(directory) / "heartbeat"
            heartbeat.touch()
            self.assertTrue(_fresh(heartbeat, 60))
            old = heartbeat.stat().st_mtime - 120
            os.utime(heartbeat, (old, old))
            self.assertFalse(_fresh(heartbeat, 60))

    def test_worker_health_requires_fresh_lease_owned_by_visible_local_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'health.db'}")
            create_schema(engine)
            now = datetime.now(timezone.utc)
            with session_scope(engine) as db:
                db.add(WorkerLock(
                    id=3,
                    worker_id=f"{socket.gethostname()}-321-worker",
                    lease_until=now + timedelta(seconds=30),
                ))
            database_url = str(engine.url)
            engine.dispose()

            self.assertTrue(_worker_lease_running(database_url, 2, process_ids=(321,)))
            self.assertFalse(_worker_lease_running(database_url, 2, process_ids=(999,)))
            self.assertFalse(_worker_lease_running(
                database_url, 2, now=(now + timedelta(seconds=31)).timestamp(), process_ids=(321,)
            ))

    def test_worker_health_fails_closed_if_database_cannot_be_checked(self):
        self.assertFalse(_worker_lease_running("postgresql://127.0.0.1:1/missing", 0, process_ids=(321,)))
