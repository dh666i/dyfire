import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spark_console.runtime_health import _fresh, _module_process_running


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

    def test_heartbeat_must_be_recent(self):
        with tempfile.TemporaryDirectory() as directory:
            heartbeat = Path(directory) / "heartbeat"
            heartbeat.touch()
            self.assertTrue(_fresh(heartbeat, 60))
            old = heartbeat.stat().st_mtime - 120
            os.utime(heartbeat, (old, old))
            self.assertFalse(_fresh(heartbeat, 60))
