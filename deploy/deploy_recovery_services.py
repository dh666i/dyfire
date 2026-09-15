"""Drain auth/worker and deploy verified patch images without forced kills."""
import subprocess
import time
import re
import shutil
from pathlib import Path
ROOT = Path("/opt/henng-spark-console")
SOURCE = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.console.yml"
VERSION = (SOURCE / "VERSION").read_text().strip()
CMD = ["docker", "compose", "--env-file", str(ROOT / ".env.console"), "-f", str(COMPOSE)]
def run(*args):
    return subprocess.check_output(args, text=True).strip()
def running(name):
    return run("docker", "inspect", name, "--format", "{{.State.Running}}") == "true"
def drain(name):
    run("docker", "update", "--restart=no", name)
    if running(name):
        run("docker", "kill", "--signal=SIGTERM", name)
    for _ in range(150):
        if not running(name):
            return
        time.sleep(2)
    run("docker", "update", "--restart=unless-stopped", name)
    raise RuntimeError("Still draining; refused to kill " + name)
def rollout(service, image):
    name = "douyin-spark-console-" + service + "-1"
    run("docker", "image", "inspect", image)
    tag = run("docker", "inspect", name, "--format", "{{.Config.Image}}")
    previous = run("docker", "image", "inspect", tag, "--format", "{{.Id}}")
    original = COMPOSE.read_text()
    pattern = r"(  " + service + r":\n    image: )[^\n]+"
    updated, count = re.subn(pattern, lambda m: m[1] + image, original)
    assert count == 1
    if service == "spark-auth":
        # Never intentionally interrupt an active login interaction.
        code = "import sqlite3; d=sqlite3.connect('file:/data/spark.db?mode=ro',uri=True); print(d.execute(\"select count(*) from douyin_login_sessions where slot is not null and status in ('queued','loading_qr','awaiting_scan','confirming') and expires_at>datetime('now')\").fetchone()[0])"
        for _ in range(150):
            if run("docker", "exec", name, "python", "-c", code) == "0":
                break
            time.sleep(2)
        else:
            raise RuntimeError("Active login; auth deployment deferred")
    drain(name)
    try:
        COMPOSE.write_text(updated)
        subprocess.run(CMD + ["config", "--quiet"], check=True)
        subprocess.run(CMD + ["up", "-d", "--no-build", "--no-deps", service], check=True)
        for _ in range(60):
            if run("docker", "inspect", name, "--format", "{{.State.Health.Status}}") == "healthy":
                print(service + " healthy: " + image, flush=True)
                return
            time.sleep(2)
        raise RuntimeError("Service failed healthcheck")
    except Exception:
        drain(name)
        COMPOSE.write_text(re.sub(pattern, lambda m: m[1] + previous, original))
        subprocess.run(CMD + ["up", "-d", "--no-build", "--no-deps", service], check=True)
        raise
def main():
    rollout("spark-auth", "dyfire:auth-" + VERSION)
    rollout("spark-worker", "dyfire:worker-" + VERSION)
    for name in ("models.py", "scheduler.py", "worker.py", "health_retry.py", "run_attempts.py",
                 "executor.py", "health_worker.py", "ops_monitor.py"):
        shutil.copy2(SOURCE / "spark_console" / name, ROOT / "spark_console" / name)
    shutil.copy2(SOURCE / "spark_console/services/account_health.py", ROOT / "spark_console/services/account_health.py")
    shutil.copy2(SOURCE / "core/session_health.py", ROOT / "core/session_health.py")
if __name__ == "__main__":
    main()
