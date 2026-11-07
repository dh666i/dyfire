import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from spark_console.config import Settings
from spark_console.db import create_schema, session_scope
from spark_console.models import (
    DouyinAccount,
    DouyinLoginSession,
    SparkTask,
    TaskRun,
    User,
    WebSession,
    WorkerLock,
)
from spark_console.ops_monitor import (
    _cpu_usage_metrics,
    _cpu_usage_percent,
    _database_metrics,
    _disk_metrics,
    _memory_metrics,
    _read_cpu_ticks,
    operations_snapshot,
)
from spark_console.ops_history import (
    RETENTION_SECONDS, activity_counts, history_path, read_history, recent_activity, write_sample,
)
from spark_console.security import PasswordService, SessionService
from spark_console.web.app import create_app


ROOT = Path(__file__).resolve().parents[2]


class OperationsMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = Settings(
            data_dir=self.root,
            database_url=f"sqlite:///{self.root / 'ops.db'}",
            cookie_key_file=self.root / "cookie.key",
            session_key_file=self.root / "session.key",
            secure_cookies=True,
        )
        self.settings.cookie_key_file.write_bytes(b"c" * 32)
        self.settings.session_key_file.write_bytes(b"s" * 32)
        self.engine = create_engine(
            self.settings.database_url,
            connect_args={"check_same_thread": False},
        )
        create_schema(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def test_cpu_delta_and_memory_parser_are_deterministic(self):
        empty_cgroup = self.root / "empty-cgroup"
        with patch(
            "spark_console.ops_monitor._read_cpu_ticks",
            side_effect=[(1_000, 400), (1_100, 420)],
        ):
            self.assertEqual(
                80.0,
                _cpu_usage_percent(
                    sample_seconds=0, cgroup_root=empty_cgroup
                ),
            )

        proc_root = self.root / "proc"
        proc_root.mkdir()
        (proc_root / "meminfo").write_text(
            "MemTotal:       4096 kB\nMemAvailable:   1024 kB\n",
            encoding="utf-8",
        )
        memory = _memory_metrics(proc_root, empty_cgroup)
        self.assertTrue(memory["available"])
        self.assertEqual(4_194_304, memory["total_bytes"])
        self.assertEqual(3_145_728, memory["used_bytes"])
        self.assertEqual(75.0, memory["percent"])

        (proc_root / "stat").write_text(
            "cpu  100 20 30 400 50 6 7 8 900 700\n", encoding="utf-8"
        )
        # guest fields are excluded because Linux already includes them in
        # user/nice time.
        self.assertEqual((621, 450), _read_cpu_ticks(proc_root))

    def test_cgroup_v2_limits_take_precedence_and_report_container_scope(self):
        cgroup = self.root / "cgroup"
        cgroup.mkdir()
        (cgroup / "memory.current").write_text("67108864\n", encoding="utf-8")
        (cgroup / "memory.max").write_text("268435456\n", encoding="utf-8")
        proc_root = self.root / "proc-cgroup"
        proc_root.mkdir()
        (proc_root / "meminfo").write_text(
            "MemTotal:       4194304 kB\nMemAvailable:   2097152 kB\n",
            encoding="utf-8",
        )
        memory = _memory_metrics(proc_root, cgroup)
        self.assertEqual("cgroup_v2", memory["source"])
        self.assertEqual("web_container", memory["scope"])
        self.assertEqual(25.0, memory["percent"])

        with patch(
            "spark_console.ops_monitor._read_cgroup_cpu_sample",
            side_effect=[(1_000_000, 0.5), (1_025_000, 0.5)],
        ), patch(
            "spark_console.ops_monitor.time.monotonic", side_effect=[10.0, 10.1]
        ):
            cpu = _cpu_usage_metrics(
                proc_root, cgroup, sample_seconds=0
            )
        self.assertEqual("cgroup_v2", cpu["source"])
        self.assertEqual("web_container", cpu["scope"])
        self.assertEqual(50.0, cpu["percent"])
        self.assertEqual(0.5, cpu["capacity_cores"])

    def test_snapshot_is_redacted_and_contains_service_and_queue_health(self):
        now = datetime.now(timezone.utc)
        heartbeat = self.root / "auth-worker.heartbeat"
        heartbeat.touch()
        os.utime(heartbeat, (now.timestamp(), now.timestamp()))
        with session_scope(self.engine) as db:
            owner = User(
                username="owner", password_hash="secret-hash", role="admin"
            )
            db.add(owner)
            db.flush()
            account = DouyinAccount(
                owner_user_id=owner.id,
                display_name="private-account-name",
                encrypted_cookies=b"private-cookie",
                cookie_nonce=b"private-nonce",
                validation_state="invalid",
            )
            db.add(account)
            db.flush()
            task = SparkTask(
                owner_user_id=owner.id,
                douyin_account_id=account.id,
                target_name="private-friend-name",
                send_time="08:00",
                message_template="private-message-body",
                next_run_at=now - timedelta(seconds=1),
                enabled=True,
            )
            db.add(task)
            db.flush()
            db.add_all(
                [
                    TaskRun(
                        task_id=task.id,
                        scheduled_for=now,
                        status="running",
                        lease_until=now + timedelta(minutes=5),
                    ),
                    TaskRun(
                        task_id=task.id,
                        scheduled_for=now - timedelta(seconds=30),
                        status="running",
                        lease_until=now - timedelta(seconds=1),
                    ),
                    TaskRun(
                        task_id=task.id,
                        scheduled_for=now - timedelta(minutes=1),
                        status="failed",
                    ),
                    TaskRun(
                        task_id=task.id,
                        scheduled_for=now - timedelta(minutes=2),
                        status="retrying",
                        retry_after=task.next_run_at,
                    ),
                    TaskRun(
                        task_id=task.id,
                        scheduled_for=now - timedelta(minutes=3),
                        status="retrying",
                        retry_after=now - timedelta(minutes=3),
                    ),
                    DouyinLoginSession(
                        owner_user_id=owner.id,
                        status="awaiting_scan",
                        expires_at=now + timedelta(minutes=5),
                    ),
                ]
            )
            inactive_owner = User(
                username="inactive-owner",
                password_hash="inactive-secret-hash",
                status="disabled",
            )
            db.add(inactive_owner)
            db.flush()
            inactive_account = DouyinAccount(
                owner_user_id=inactive_owner.id,
                display_name="inactive-private-account",
                encrypted_cookies=b"inactive-cookie",
                cookie_nonce=b"inactive-nonce",
            )
            db.add(inactive_account)
            db.flush()
            db.add(
                SparkTask(
                    owner_user_id=inactive_owner.id,
                    douyin_account_id=inactive_account.id,
                    target_name="inactive-private-friend",
                    send_time="09:00",
                    message_template="inactive-private-message",
                    next_run_at=now - timedelta(minutes=1),
                    enabled=True,
                )
            )
            lock = db.get(WorkerLock, 1)
            lock.worker_id = "private-hostname-1234"
            lock.lease_until = now + timedelta(seconds=20)

        fake_cpu = {
            "available": True,
            "percent": 37.5,
            "logical_cores": 2,
            "capacity_cores": 0.5,
            "source": "cgroup_v2",
            "scope": "web_container",
        }
        with session_scope(self.engine) as db, patch(
            "spark_console.ops_monitor._cpu_usage_metrics", return_value=fake_cpu
        ):
            snapshot = operations_snapshot(db, self.settings, now=now)

        self.assertEqual("bytes", snapshot["units"]["storage"])
        self.assertEqual("percent", snapshot["units"]["utilization"])
        self.assertEqual(37.5, snapshot["system"]["cpu"]["percent"])
        self.assertEqual("web_container", snapshot["system"]["scope"])
        self.assertIn("Web 容器", snapshot["system"]["scope_label"])
        self.assertEqual("healthy", snapshot["services"]["web"]["status"])
        self.assertEqual("healthy", snapshot["services"]["auth"]["status"])
        self.assertEqual("healthy", snapshot["services"]["worker"]["status"])
        self.assertEqual(2, snapshot["queue"]["enabled_tasks"])
        self.assertEqual(2, snapshot["queue"]["total_accounts"])
        # The disabled owner's overdue task is not scheduler-eligible.
        self.assertEqual(1, snapshot["queue"]["due_tasks"])
        self.assertEqual(1, snapshot["queue"]["running_runs"])
        self.assertEqual(1, snapshot["queue"]["retrying_runs"])
        self.assertEqual(1, snapshot["queue"]["active_logins"])
        self.assertEqual(1, snapshot["queue"]["attention_24h"])
        self.assertEqual(1, snapshot["queue"]["invalid_accounts"])
        self.assertGreater(snapshot["database"]["size_bytes"], 0)

        encoded = json.dumps(snapshot, ensure_ascii=False).lower()
        for private_value in (
            "private-cookie",
            "private-message-body",
            "private-friend-name",
            "private-account-name",
            "private-hostname-1234",
            "secret-hash",
            "inactive-private-message",
            "inactive-private-friend",
            "inactive-private-account",
            "inactive-secret-hash",
            "database_url",
            "data_dir",
            "ops.db",
            "cmdline",
        ):
            self.assertNotIn(private_value, encoded)

    def test_host_snapshot_ignores_container_limits_and_uses_host_files(self):
        host = self.root / "host"
        host.mkdir()
        (host / "stat").write_text("cpu 1 2 3 4\ncpu0 1 2 3 4\ncpu1 1 2 3 4\n")
        (host / "meminfo").write_text("MemTotal: 4194304 kB\nMemAvailable: 1048576 kB\n")
        (host / "loadavg").write_text("0.50 0.25 0.10 1/80 123\n")
        (host / "uptime").write_text("86401.99 90000\n")
        (host / "rootfs").touch()
        with session_scope(self.engine) as db, patch.dict(
            os.environ, {"SPARK_HOST_METRICS_DIR": str(host)}
        ), patch(
            "spark_console.ops_monitor._read_cpu_ticks", side_effect=[(1000, 400), (1100, 425)]
        ), patch("spark_console.ops_monitor._read_cgroup_cpu_sample") as cgroup:
            snapshot = operations_snapshot(db, self.settings, cpu_sample_seconds=0)
        system = snapshot["system"]
        cgroup.assert_not_called()
        self.assertEqual("host", system["scope"])
        self.assertEqual(75.0, system["cpu"]["percent"])
        self.assertEqual(2, system["cpu"]["logical_cores"])
        self.assertEqual(4 * 1024 ** 3, system["memory"]["total_bytes"])
        self.assertEqual(75.0, system["memory"]["percent"])
        self.assertEqual("host", system["memory"]["scope"])
        self.assertEqual("host_root", system["disk"]["scope"])
        self.assertTrue(system["disk"]["available"])
        self.assertEqual(0.5, system["load"]["one"])
        self.assertEqual(86401, system["uptime_seconds"])

    def test_missing_host_files_do_not_fall_back_to_container_metrics(self):
        with session_scope(self.engine) as db, patch(
            "spark_console.ops_monitor._read_cgroup_cpu_sample"
        ) as cgroup:
            snapshot = operations_snapshot(
                db, self.settings, host_metrics_root=self.root / "absent", cpu_sample_seconds=0,
            )
        cgroup.assert_not_called()
        system = snapshot["system"]
        self.assertEqual("host", system["scope"])
        self.assertIn("不可读取", system["scope_label"])
        for name in ("cpu", "memory", "disk", "load"):
            self.assertFalse(system[name]["available"], name)
        self.assertIsNone(system["cpu"]["percent"])
        self.assertIsNone(system["memory"]["total_bytes"])
        self.assertIsNone(system["uptime_seconds"])

    def test_history_is_persistent_peak_preserving_and_retention_bounded(self):
        path = history_path(self.settings)
        now = 1_800_000
        self.assertEqual("empty", read_history(path, now=now)["state"])
        self.assertFalse(path.exists())
        write_sample(path, {"at": now - RETENTION_SECONDS - 100, "cpu": 100})
        write_sample(path, {"at": now - 30, "cpu": 90, "memory": 40, "logins": 1, "sends": 0})
        write_sample(path, {"at": now - 25, "cpu": 10, "memory": 45, "logins": 0, "sends": 1})
        history = read_history(path, 6, now=now)
        self.assertEqual("fresh", history["state"])
        self.assertEqual(1, len(history["points"]))
        self.assertEqual(90, history["points"][0]["cpu"])
        self.assertEqual(45, history["points"][0]["memory"])
        self.assertEqual(1, history["points"][0]["logins"])
        self.assertEqual(1, history["points"][0]["sends"])
        self.assertEqual(2, history["points"][0]["samples"])
        self.assertEqual("stale", read_history(path, now=now + 60)["state"])
        self.assertEqual(1, read_history(path, 999, now=now)["hours"])
        connection = sqlite3.connect(path)
        try:
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0])
        finally:
            connection.close()

    def test_history_preserves_unknown_values_and_reports_corruption(self):
        path = history_path(self.settings)
        write_sample(path, {"at": 100, "cpu": None, "logins": None})
        result = read_history(path, now=100)
        self.assertIsNone(result["cpu_peak"])
        self.assertEqual("partial", result["state"])
        self.assertIsNone(result["points"][0]["cpu"])
        self.assertIsNone(result["points"][0]["logins"])
        broken = self.root / "broken.sqlite3"
        broken.write_text("not a database")
        self.assertEqual("unavailable", read_history(broken)["state"])

    def test_history_counts_short_completed_activity_without_reading_secrets(self):
        now = datetime.now(timezone.utc)
        with session_scope(self.engine) as db:
            owner = User(username="history-owner", password_hash="not-collected")
            db.add(owner)
            db.flush()
            task = SparkTask(owner_user_id=owner.id, target_name="not-collected", send_time="09:00", message_template="not-collected")
            db.add(task)
            db.flush()
            db.add(TaskRun(task_id=task.id, scheduled_for=now, started_at=now - timedelta(seconds=4), finished_at=now - timedelta(seconds=2), status="success"))
            db.add(DouyinLoginSession(owner_user_id=owner.id, created_at=now - timedelta(seconds=4),
                                     finished_at=now - timedelta(seconds=1), expires_at=now + timedelta(seconds=300), status="succeeded", qr_png=b"not-collected"))
        with session_scope(self.engine) as db:
            self.assertEqual((1, 1), activity_counts(db, now, now - timedelta(seconds=5)))
            self.assertEqual((0, 0), activity_counts(db, now, now))
            events = recent_activity(db, 1, now=now)
        self.assertEqual(2, len(events))
        self.assertEqual({2, 3}, {item["duration"] for item in events})
        self.assertNotIn("not-collected", json.dumps(events))

    def test_missing_proc_files_degrade_without_failing_snapshot(self):
        empty_proc = self.root / "missing-proc"
        empty_cgroup = self.root / "missing-cgroup"
        with session_scope(self.engine) as db:
            snapshot = operations_snapshot(
                db,
                self.settings,
                proc_root=empty_proc,
                cgroup_root=empty_cgroup,
                cpu_sample_seconds=0,
            )
        self.assertFalse(snapshot["system"]["cpu"]["available"])
        self.assertFalse(snapshot["system"]["memory"]["available"])
        self.assertFalse(snapshot["system"]["uptime_available"])
        self.assertTrue(snapshot["system"]["disk"]["available"])
        self.assertEqual("offline", snapshot["services"]["auth"]["status"])
        self.assertEqual("offline", snapshot["services"]["worker"]["status"])

    def test_missing_sqlite_file_is_not_reported_as_zero_bytes(self):
        missing = Settings(
            data_dir=self.root,
            database_url=f"sqlite:///{self.root / 'not-created.db'}",
            cookie_key_file=self.settings.cookie_key_file,
            session_key_file=self.settings.session_key_file,
        )
        metric = _database_metrics(missing)
        self.assertFalse(metric["size_available"])
        self.assertIsNone(metric["size_bytes"])

    def test_disk_usage_uses_space_available_to_the_service(self):
        fake = SimpleNamespace(
            f_frsize=1,
            f_bsize=1,
            f_blocks=1_000,
            f_bfree=400,
            f_bavail=250,
        )
        with patch(
            "spark_console.ops_monitor.os.statvfs", return_value=fake, create=True
        ):
            metric = _disk_metrics(self.root)
        self.assertEqual(750, metric["used_bytes"])
        self.assertEqual(75.0, metric["percent"])
        with patch("spark_console.ops_monitor.os.statvfs", return_value=fake, create=True):
            host_metric = _disk_metrics(self.root, include_reserved=False)
        self.assertEqual(600, host_metric["used_bytes"])
        self.assertEqual(60.0, host_metric["percent"])


class OperationsWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(
            data_dir=root,
            database_url=f"sqlite:///{root / 'web.db'}",
            cookie_key_file=root / "cookie.key",
            session_key_file=root / "session.key",
            secure_cookies=True,
        )
        self.settings.cookie_key_file.write_bytes(b"c" * 32)
        self.settings.session_key_file.write_bytes(b"s" * 32)
        self.engine = create_engine(
            self.settings.database_url,
            connect_args={"check_same_thread": False},
        )
        create_schema(self.engine)
        password_hash = PasswordService().hash("AdminPass123")
        sessions = SessionService(self.settings.session_key_file.read_bytes())
        with session_scope(self.engine) as db:
            admin = User(
                username="admin",
                password_hash=password_hash,
                role="admin",
                must_change_password=False,
            )
            customer = User(
                username="customer",
                password_hash=password_hash,
                role="user",
                must_change_password=False,
            )
            db.add_all([admin, customer])
            db.flush()
            self.admin_token, admin_session = sessions.create_record(admin.id)
            self.customer_token, customer_session = sessions.create_record(customer.id)
            db.add_all([admin_session, customer_session])
        self.client = TestClient(
            create_app(self.settings, self.engine), base_url="https://testserver"
        )

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.temp.cleanup()

    def test_page_and_snapshot_are_admin_only_no_store_and_auto_refresh(self):
        self.client.cookies.set("spark_session", self.admin_token)
        with patch(
            "spark_console.web.auth.membership_summary",
            side_effect=AssertionError("operations must not load membership"),
        ):
            page = self.client.get("/admin/operations")
        self.assertEqual(200, page.status_code)
        self.assertEqual("no-store", page.headers["cache-control"])
        self.assertIn('<div id="app"></div>', page.text)
        self.assertIn('/assets/index-', page.text)
        self.assertNotIn('admin_operations.css', page.text)
        self.assertNotIn('admin_operations.js', page.text)

        with patch(
            "spark_console.web.auth.membership_summary",
            side_effect=AssertionError("snapshot must not load membership"),
        ):
            snapshot = self.client.get("/admin/operations/snapshot")
        self.assertEqual(200, snapshot.status_code)
        self.assertEqual("no-store", snapshot.headers["cache-control"])
        self.assertEqual(30, snapshot.json()["refresh_interval_seconds"])
        self.assertEqual({"web", "auth", "worker"}, set(snapshot.json()["services"]))

        history = self.client.get("/admin/operations/history?hours=24")
        self.assertEqual(200, history.status_code)
        self.assertEqual("no-store", history.headers["cache-control"])
        self.assertEqual("empty", history.json()["state"])
        self.assertEqual(24, history.json()["hours"])
        self.assertFalse(history_path(self.settings).exists())

        for path in ("/admin/operations", "/admin/operations/snapshot", "/admin/operations/history"):
            with self.subTest(path=path):
                self.client.cookies.clear()
                anonymous = self.client.get(path, follow_redirects=False)
                self.assertEqual(303, anonymous.status_code)
                self.assertEqual("/login", anonymous.headers["location"])
                self.client.cookies.set("spark_session", self.customer_token)
                self.assertEqual(404, self.client.get(path).status_code)


if __name__ == "__main__":
    unittest.main()
