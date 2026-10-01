from __future__ import annotations

import tempfile
from pathlib import Path

from spark_console.config import Settings
from spark_console.db import create_engine_for, create_schema
from spark_console.queue_preflight import run_queue_preflight


def _settings(root: str, **overrides: str) -> Settings:
    path = Path(root)
    (path / "cookie.key").write_bytes(b"c" * 32)
    (path / "session.key").write_bytes(b"s" * 32)
    values = {
        "SPARK_DATA_DIR": root,
        "SPARK_COOKIE_KEY_FILE": str(path / "cookie.key"),
        "SPARK_SESSION_KEY_FILE": str(path / "session.key"),
    }
    values.update(overrides)
    return Settings.from_env(values)


class _ProbeBackend:
    def __init__(self):
        self.pings = 0
        self.probes = 0

    def ping(self):
        self.pings += 1
        return True

    def stream_probe(self):
        self.probes += 1
        return type("Probe", (), {"message_id": "1-0"})()


def test_preflight_is_read_only_and_disabled_redis_is_explicit():
    with tempfile.TemporaryDirectory() as root:
        settings = _settings(root)
        engine = create_engine_for(settings)
        create_schema(engine)
        report = run_queue_preflight(settings, engine=engine)
        assert report.passed
        assert report.as_dict()["status"] == "ok"
        checks = {check.name: check for check in report.checks}
        assert checks["database_claim"].status == "ok"
        assert checks["redis_queue"].status == "disabled"
        engine.dispose()


def test_preflight_probe_is_opt_in_and_uses_ephemeral_backend_contract():
    with tempfile.TemporaryDirectory() as root:
        settings = _settings(
            root,
            SPARK_REDIS_ENABLED="true",
            SPARK_REDIS_URL="redis://example.invalid/0",
        )
        engine = create_engine_for(settings)
        create_schema(engine)
        backend = _ProbeBackend()
        report = run_queue_preflight(
            settings,
            engine=engine,
            backend=backend,
            probe_stream=True,
        )
        assert report.passed
        assert backend.pings == 1
        assert backend.probes == 1
        assert report.stream_probe is True
        engine.dispose()


def test_preflight_reports_incomplete_database_without_mutating_it():
    with tempfile.TemporaryDirectory() as root:
        settings = _settings(root)
        engine = create_engine_for(settings)
        report = run_queue_preflight(settings, engine=engine)
        assert not report.passed
        assert {check.name for check in report.checks} == {
            "database_claim",
            "worker_sharding",
            "redis_queue",
        }
        assert next(check for check in report.checks if check.name == "database_claim").status == "failed"
        engine.dispose()
