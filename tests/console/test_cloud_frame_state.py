import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / 'tests' / 'console' / 'cloud_frame_state_harness.cjs'


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for CloudFrame tests')
class CloudFrameStateTests(unittest.TestCase):
    def run_scenario(self, scenario):
        result = subprocess.run(
            [shutil.which('node'), str(HARNESS), scenario],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding='utf-8',
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(json.dumps({'scenario': scenario}, ensure_ascii=False, separators=(',', ':')), result.stdout)

    def test_transient_frame_errors_keep_loading_state(self):
        self.run_scenario('transient-errors-stay-loading')

    def test_only_elapsed_timeout_shows_timeout_state(self):
        self.run_scenario('timeout-is-explicit')

    def test_expired_scan_shows_expired_state(self):
        self.run_scenario('expiry-is-explicit')

    def test_manual_retry_keeps_original_scan_id(self):
        self.run_scenario('retry-keeps-scan-id')


if __name__ == '__main__':
    unittest.main()
