import re
import unittest
from pathlib import Path


class DeploymentContractTests(unittest.TestCase):
    def setUp(self):
        self.compose = Path("compose.console.yml").read_text(encoding="utf-8")
        self.dockerfile = Path("Dockerfile.console").read_text(encoding="utf-8")
        self.dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
        self.backup_script = Path("deploy/ops/backup.sh").read_text(encoding="utf-8")
        self.health_script = Path("deploy/ops/healthcheck.sh").read_text(encoding="utf-8")
        self.fast_update_script = Path("deploy/fast-update.sh").read_text(
            encoding="utf-8"
        )

    def test_loopback_only_and_no_bps_or_docker_socket(self):
        self.assertIn('${SPARK_WEB_PUBLISH_IP:-127.0.0.1}:8899:8899', self.compose)
        self.assertNotIn("bps", self.compose.lower())
        self.assertNotIn("/var/run/docker.sock", self.compose)

    def test_host_metrics_are_read_only_for_web_and_monitor(self):
        web = self.compose.split("  spark-web:", 1)[1].split("  spark-auth:", 1)[0]
        self.assertIn("SPARK_HOST_METRICS_DIR: /run/host-metrics", web)
        for counter in ("stat", "meminfo", "loadavg", "uptime"):
            self.assertIn(f"/proc/{counter}:/run/host-metrics/{counter}:ro", web)
        self.assertIn("/etc/hostname:/run/host-metrics/rootfs:ro", web)
        self.assertNotIn("privileged: true", self.compose)
        self.assertNotIn("/proc:/", self.compose)
        self.assertNotIn("SPARK_HOST_METRICS_DIR", self.compose.split("  spark-auth:", 1)[1])
        monitor = self.compose.split("  spark-monitor:", 1)[1].split("  spark-auth:", 1)[0]
        self.assertIn("volumes: *host-metrics-volumes", monitor)
        self.assertIn("mem_limit: 128m", monitor)
        self.assertIn("cpus: 0.20", monitor)
        self.assertIn("spark_console.ops_history, --health", monitor)
        self.assertNotIn("ports:", monitor)
        self.assertIn("monitor|spark-monitor)", self.fast_update_script)

    def test_worker_is_not_published_and_is_resource_limited(self):
        worker = self.compose.split("  spark-worker:", 1)[1].split("\nvolumes:", 1)[0]
        self.assertNotIn("ports:", worker)
        self.assertIn('SPARK_WORKER_CONCURRENCY: "1"', worker)
        self.assertIn('SPARK_HEALTH_RETRY_WINDOW_MINUTES: "20"', worker)
        self.assertIn("mem_limit: 768m", worker)
        self.assertIn("healthcheck:", worker)
        self.assertIn("spark_console.runtime_health, worker", worker)

    def test_auth_service_is_unpublished_singleton_and_resource_limited(self):
        auth = self.compose.split("  spark-auth:", 1)[1].split("  spark-worker:", 1)[0]
        self.assertIn("<<: *common", auth)
        self.assertIn("command: [python, -m, spark_console.auth_worker]", auth)
        self.assertIn('SPARK_AUTH_POLL_SECONDS: "1"', auth)
        self.assertNotIn("ports:", auth)
        self.assertNotIn("/var/run/docker.sock", auth)
        self.assertIn("mem_limit: 768m", auth)
        self.assertIn("cpus: 1.0", auth)
        self.assertIn('tmpfs: ["/tmp:rw,noexec,nosuid,size=256m"]', auth)
        self.assertIn("healthcheck:", auth)
        self.assertIn("spark_console.runtime_health, auth", auth)

        common = self.compose.split("x-common: &common", 1)[1].split("\nservices:", 1)[0]
        self.assertIn("init: true", common)
        self.assertIn("networks: [spark-private]", common)
        self.assertIn("read_only: true", common)
        self.assertIn("security_opt: [no-new-privileges:true]", common)
        self.assertNotIn("network_mode:", auth)

    def test_host_network_is_limited_to_image_build(self):
        self.assertIn("dockerfile: Dockerfile.console\n    network: host", self.compose)
        service_body = self.compose.split("services:", 1)[1]
        self.assertNotRegex(service_body, r"(?m)^\s{4}network_mode:\s*host")

    def test_example_environment_has_names_but_no_credential_values(self):
        example = Path(".env.console.example").read_text(encoding="utf-8")
        self.assertNotRegex(example, r"(?im)^(?:.*password|.*token|.*secret)=.+$")
        self.assertIn("SPARK_AUTH_POLL_SECONDS=1", example)
        self.assertIn("SPARK_WORKER_POLL_SECONDS=10", example)
        self.assertIn("SPARK_HEALTH_RETRY_WINDOW_MINUTES=20", example)
        self.assertIn("SPARK_OPS_MIN_FREE_GIB=5", example)
        self.assertIn("SPARK_OPS_WORKER_HEARTBEAT_MAX_AGE_SECONDS=90", example)

    def test_systemd_backup_runs_daily_with_retention_and_validation(self):
        service = Path(
            "deploy/systemd/douyin-spark-console-backup.service"
        ).read_text(encoding="utf-8")
        timer = Path(
            "deploy/systemd/douyin-spark-console-backup.timer"
        ).read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", service)
        self.assertIn("StandardOutput=journal", service)
        self.assertIn("OnCalendar=*-*-*", timer)
        self.assertIn("Persistent=false", timer)
        self.assertIn("backup-db --keep 7", self.backup_script)
        self.assertIn("PRAGMA integrity_check", self.backup_script)
        self.assertIn("backups[-1] != expected", self.backup_script)

    def test_systemd_healthcheck_covers_runtime_contract(self):
        service = Path(
            "deploy/systemd/douyin-spark-console-healthcheck.service"
        ).read_text(encoding="utf-8")
        timer = Path(
            "deploy/systemd/douyin-spark-console-healthcheck.timer"
        ).read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", service)
        self.assertIn("StandardError=journal", service)
        self.assertIn("OnBootSec=2min", timer)
        self.assertIn("OnUnitActiveSec=5min", timer)
        for service_name in ("spark-web", "spark-auth", "spark-worker"):
            self.assertIn(service_name, self.health_script)
        self.assertIn(".State.Health", self.health_script)
        self.assertIn("for attempt in {1..12}", self.health_script)
        self.assertIn("http://127.0.0.1:8899/health/ready", self.health_script)
        self.assertIn("shutil.disk_usage", self.health_script)
        self.assertIn("PRAGMA integrity_check", self.health_script)
        self.assertIn("FROM worker_lock WHERE id = 1", self.health_script)
        self.assertIn("?mode=ro", self.health_script)

    def test_ops_installer_supports_install_and_uninstall(self):
        installer = Path("deploy/install-console-ops.sh").read_text(encoding="utf-8")
        self.assertIn("systemctl enable --now", installer)
        self.assertIn("install)", installer)
        self.assertIn("uninstall)", installer)
        self.assertIn("does not remove application data", installer)
        self.assertIn("backups, secrets, or containers", installer)

    def test_fast_update_protects_active_work_and_waits_for_every_healthcheck(self):
        self.assertIn("--force-active", self.fast_update_script)
        self.assertIn(
            "SELECT count(*) FROM task_runs WHERE status = 'running'",
            self.fast_update_script,
        )
        self.assertIn(
            "WHERE status IN ('queued', 'loading_qr', 'awaiting_scan', 'confirming')",
            self.fast_update_script,
        )
        self.assertIn("for _ in {1..60}", self.fast_update_script)
        self.assertIn("running|healthy", self.fast_update_script)
        self.assertIn("failed health check", self.fast_update_script)

    def test_runtime_image_uses_a_source_whitelist(self):
        self.assertNotIn("COPY --chown=spark:spark . /app", self.dockerfile)
        for runtime_path in ("core", "spark_console", "utils"):
            self.assertIn(
                f"COPY --chown=spark:spark {runtime_path} /app/{runtime_path}",
                self.dockerfile,
            )

    def test_runtime_image_installs_only_the_required_browser(self):
        self.assertIn(
            "python -m playwright install --with-deps --only-shell chromium",
            self.dockerfile,
        )
        self.assertNotIn("mcr.microsoft.com/playwright/python", self.dockerfile)

    def test_legacy_credentials_and_local_state_are_excluded(self):
        for excluded in (
            "wz.json",
            "config.json",
            "usersData*.json",
            ".venv*",
            "data",
            "secrets",
            "*.db-wal",
            "*.db-shm",
        ):
            self.assertIn(excluded, self.dockerignore)


if __name__ == "__main__":
    unittest.main()
