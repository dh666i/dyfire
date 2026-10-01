"""Static deployment contracts for the optional PostgreSQL Compose profile.

These checks intentionally use only the standard library.  They run in the
application validation job even when Docker is unavailable, while the release
job still exercises the actual image and service definition in its deployment
environment.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _service_block(name: str) -> str:
    compose = (ROOT / "compose.console.yml").read_text(encoding="utf-8")
    match = re.search(
        rf"^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|^volumes:\n)",
        compose,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, f"Compose service {name!r} is missing"
    return match.group("body")


def test_postgres_is_an_explicitly_enabled_isolation_profile():
    service = _service_block("postgres")
    assert "image: postgres:16-alpine" in service
    assert "profiles: [postgres]" in service
    assert "depends_on:" not in service
    assert "postgres-data:/var/lib/postgresql/data" in service
    assert "mem_limit: 512m" in service
    assert "cpus: 0.50" in service


def test_postgres_healthcheck_uses_configured_database_identity():
    service = _service_block("postgres")
    assert (
        'test: [CMD-SHELL, "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]'
        in service
    )
    assert "interval: 10s" in service
    assert "timeout: 5s" in service
    assert "retries: 6" in service
    assert "start_period: 15s" in service


def test_sqlite_services_have_no_implicit_postgres_dependency():
    compose = (ROOT / "compose.console.yml").read_text(encoding="utf-8")
    assert "postgres-data:" in compose
    assert "SPARK_DATABASE_URL" not in compose
    for service_name in ("spark-web", "spark-monitor", "spark-auth", "spark-worker"):
        service = _service_block(service_name)
        assert "depends_on:" not in service
        assert "postgres" not in service.lower()


def test_postgres_credentials_are_explicitly_isolated_in_examples_and_docs():
    env_example = (ROOT / ".env.console.example").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "SPARK_POSTGRES_PASSWORD=change-me-in-isolation" in env_example
    assert "--profile postgres" in readme
    assert "--apply" in readme
    assert "不要把这个 profile 直接接到线上发送服务" in readme

