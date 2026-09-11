import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "console" / "tasks_js_harness.js"


@unittest.skipUnless(shutil.which("node"), "Node.js is required for task UI tests")
class TaskJavaScriptTests(unittest.TestCase):
    def test_target_identity_deduplication_limits_and_submission_mapping(self):
        completed = subprocess.run(
            [shutil.which("node"), str(HARNESS)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn('"ok":true', completed.stdout)


if __name__ == "__main__":
    unittest.main()
