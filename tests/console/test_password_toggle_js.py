import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "console" / "password_toggle_js_harness.js"


@unittest.skipUnless(shutil.which("node"), "Node.js is required for password UI tests")
class PasswordToggleJavaScriptTests(unittest.TestCase):
    def test_each_password_visibility_button_toggles_only_its_own_field(self):
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
