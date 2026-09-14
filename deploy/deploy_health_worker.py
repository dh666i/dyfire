"""Graceful worker-only rollout; never kill an active send."""
import subprocess
import time
import re
from pathlib import Path
ROOT = Path("/opt/henng-spark-console")
SOURCE = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.console.yml"
NAME = "douyin-spark-console-spark-worker-1"
def run(*args):
    return subprocess.check_output(args, text=True).strip()
def main():
    version = (SOURCE / "VERSION").read_text().strip()
    image = "dyfire:worker-" + version
    run("docker", "image", "inspect", image)
    original = COMPOSE.read_text()
    previous = run("docker", "inspect", NAME, "--format", "{{.Image}}")
    updated, count = re.subn(r"(  spark-worker:\n    image: )[^\n]+", lambda m: m[1] + image, original)
    if count != 1:
        raise RuntimeError("Unexpected worker compose configuration")
    cmd = ["docker", "compose", "--env-file", str(ROOT / ".env.console"), "-f", str(COMPOSE)]
    replaced = False
    run("docker", "update", "--restart=no", NAME)
    try:
        run("docker", "kill", "--signal=SIGTERM", NAME)
        for _ in range(150):
            if run("docker", "inspect", NAME, "--format", "{{.State.Running}}") == "false":
                break
            time.sleep(2)
        else:
            raise RuntimeError("Worker still draining; refused forced termination")
        COMPOSE.write_text(updated)
        subprocess.run(cmd + ["config", "--quiet"], check=True)
        replaced = True
        subprocess.run(cmd + ["up", "-d", "--no-build", "--no-deps", "spark-worker"], check=True)
        for _ in range(45):
            result = subprocess.run(["docker", "exec", NAME, "python", "-m", "spark_console.runtime_health", "worker"], capture_output=True)
            if result.returncode == 0:
                break
            time.sleep(2)
        else:
            raise RuntimeError("New worker heartbeat not ready")
        import shutil
        for name in ("models.py", "scheduler.py", "worker.py", "health_retry.py"):
            shutil.copy2(SOURCE / "spark_console" / name, ROOT / "spark_console" / name)
        print("Worker ready: " + image)
    except Exception:
        # Pin rollback to the exact previous image, not a mutable tag.
        rollback = re.sub(r"(  spark-worker:\n    image: )[^\n]+", lambda m: m[1] + previous, original)
        COMPOSE.write_text(rollback)
        if replaced and run("docker", "inspect", NAME, "--format", "{{.State.Running}}") == "true":
            run("docker", "update", "--restart=no", NAME)
            run("docker", "kill", "--signal=SIGTERM", NAME)
            for _ in range(150):
                if run("docker", "inspect", NAME, "--format", "{{.State.Running}}") == "false":
                    break
                time.sleep(2)
            else:
                raise RuntimeError("Rollback waiting for active worker; no forced termination")
        run("docker", "update", "--restart=unless-stopped", NAME)
        if run("docker", "inspect", NAME, "--format", "{{.State.Running}}") == "false":
            subprocess.run(cmd + ["up", "-d", "--no-build", "--no-deps", "spark-worker"], check=True)
        raise
if __name__ == "__main__":
    main()
