import unittest
from pathlib import Path


class DeploymentPathTests(unittest.TestCase):
    def test_updater_unit_and_compose_use_canonical_deployment_directory(self):
        root = Path(__file__).resolve().parents[1]
        unit = (root / "deploy" / "dyfire-updater.service").read_text(encoding="utf-8")
        compose = (root / "compose.console.yml").read_text(encoding="utf-8")

        self.assertIn("WorkingDirectory=/opt/dyfire", unit)
        self.assertIn("Environment=DYFIRE_DEPLOY_DIR=/opt/dyfire", unit)
        self.assertIn("Environment=DYFIRE_DATA_DIR=/opt/dyfire/data", unit)
        self.assertIn(
            "Environment=DYFIRE_GITHUB_TOKEN_FILE=/opt/dyfire/secrets/github.read.token",
            unit,
        )
        self.assertIn("ExecStart=/usr/bin/python3 /opt/dyfire/deploy/updater.py", unit)
        self.assertNotIn("/opt/henng-spark-console", unit)
        self.assertIn("Environment=DYFIRE_COMPOSE_PROJECT=douyin-spark-console", unit)
        self.assertIn("name: dyfire", compose)


if __name__ == "__main__":
    unittest.main()
