import unittest
from pathlib import Path


class DeploymentPathTests(unittest.TestCase):
    def test_updater_unit_uses_canonical_deployment_directory(self):
        unit = (Path(__file__).resolve().parents[1] / "deploy" / "dyfire-updater.service").read_text(
            encoding="utf-8"
        )

        self.assertIn("WorkingDirectory=/opt/dyfire", unit)
        self.assertIn("Environment=DYFIRE_DEPLOY_DIR=/opt/dyfire", unit)
        self.assertIn("Environment=DYFIRE_DATA_DIR=/opt/dyfire/data", unit)
        self.assertIn(
            "Environment=DYFIRE_GITHUB_TOKEN_FILE=/opt/dyfire/secrets/github.read.token",
            unit,
        )
        self.assertIn("ExecStart=/usr/bin/python3 /opt/dyfire/deploy/updater.py", unit)
        self.assertNotIn("/opt/henng-spark-console", unit)


if __name__ == "__main__":
    unittest.main()
