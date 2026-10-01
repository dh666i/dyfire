import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "console" / "account_scan_js_harness.js"


@unittest.skipUnless(shutil.which("node"), "Node.js is required for account scan UI tests")
class AccountScanJavaScriptTests(unittest.TestCase):
    def run_scenario(self, scenario: str):
        script = (
            f"process.chdir({json.dumps(str(HARNESS.parent))});"
            f"process.argv=['node',{json.dumps(str(HARNESS))},"
            f"{json.dumps(scenario)}];"
            f"eval(require('fs').readFileSync({json.dumps(str(HARNESS))},'utf8'));"
        )
        completed = subprocess.run(
            [shutil.which("node"), "--input-type=commonjs", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn(f'"scenario":"{scenario}"', completed.stdout)

    def test_close_during_pending_start_cancels_returned_scan_before_hiding(self):
        self.run_scenario("pending-close")

    def test_cancel_during_pending_start_cancels_returned_scan_before_hiding(self):
        self.run_scenario("pending-cancel")

    def test_request_timeout_surfaces_without_leaving_scan_busy(self):
        self.run_scenario("request-timeout")

    def test_cancel_failure_stays_visible_allows_retry_and_resumes_polling(self):
        self.run_scenario("active-cancel-failure")

    def test_failed_pending_start_restores_close_button(self):
        self.run_scenario("pending-start-failure")

    def test_status_polling_skips_an_unchanged_live_frame_revision(self):
        self.run_scenario("live-view-refresh")

    def test_status_polling_refreshes_when_live_frame_revision_changes(self):
        self.run_scenario("live-view-revision-change")

    def test_pending_frame_refresh_keeps_the_latest_revision(self):
        self.run_scenario("live-view-queued-revision")

    def test_pending_live_view_refreshes_are_coalesced(self):
        self.run_scenario("live-view-coalesces")

    def test_failed_live_view_refresh_keeps_the_last_loaded_frame(self):
        self.run_scenario("live-view-failure-keeps-old")

    def test_confirming_and_verification_keep_the_cloud_page_visible(self):
        self.run_scenario("confirming-keeps-live-page")

    def test_cloud_page_click_is_forwarded_and_activates_direct_keyboard_input(self):
        self.run_scenario("browser-click")

    def test_numeric_code_can_be_forwarded_by_button_or_enter(self):
        self.run_scenario("browser-text")

    def test_long_unicode_password_can_be_forwarded_without_trimming(self):
        self.run_scenario("browser-password")

    def test_failed_code_forwarding_keeps_the_entered_value(self):
        self.run_scenario("browser-text-failure")

    def test_button_and_enter_share_one_lock_until_the_interaction_is_final(self):
        self.run_scenario("browser-text-shared-lock")

    def test_missing_legacy_receipt_requires_explicit_confirmation_before_retry(self):
        self.run_scenario("legacy-receipt-uncertain")

    def test_third_text_attempt_shows_a_stronger_cooldown_warning(self):
        self.run_scenario("third-text-attempt-warning")

    def test_wrong_first_password_can_be_followed_by_a_second_candidate(self):
        self.run_scenario("wrong-password-second-candidate")

    def test_in_progress_conflict_resumes_the_original_receipt_without_resending(self):
        self.run_scenario("interaction-in-progress")

    def test_account_scan_script_has_a_global_single_initialization_guard(self):
        self.run_scenario("single-initialization")

    def test_invalid_browser_text_is_rejected_locally_without_clearing_it(self):
        self.run_scenario("invalid-text")

    def test_temporary_poll_failure_retries_without_terminal_state(self):
        self.run_scenario("poll-network-retry")

    def test_terminal_state_clears_live_view_and_verification_code(self):
        self.run_scenario("terminal-clears")

    def test_new_session_clears_the_previous_verification_code(self):
        self.run_scenario("new-session-clears-code")

    def test_leaving_page_sends_background_cancellation(self):
        self.run_scenario("pagehide-cancel")

    def test_success_message_closes_dialog_before_refresh(self):
        self.run_scenario("success-close")

    def test_scan_still_works_without_optional_code_controls(self):
        self.run_scenario("no-code-controls")

    def test_existing_account_relogin_posts_account_id_without_new_account_control(self):
        self.run_scenario("relogin-account")

    def test_mismatched_response_target_is_rejected_before_polling(self):
        self.run_scenario("response-target-mismatch")

    def test_accounts_template_has_no_legacy_scan_placeholder(self):
        self.run_scenario("template-no-placeholder")


if __name__ == "__main__":
    unittest.main()
