import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "spark_console/static/mineradio.css"


class SharedThemeMarkupTests(unittest.TestCase):
    def test_both_shells_use_restored_styles_and_boot_theme_before_them(self):
        for name in ("base.html", "admin_base.html"):
            with self.subTest(name=name):
                shell = (ROOT / "spark_console/templates" / name).read_text(encoding="utf-8")
                self.assertIn("root.dataset.themeMode = mode", shell)
                self.assertNotIn("saved === 'system'", shell)
                self.assertNotIn("theme-icon-system", shell)
                self.assertNotIn("prefers-color-scheme", shell)
                styles = ("app.css", "mineradio.css") if name == "base.html" else ("admin.css", "mineradio_admin.css")
                for stylesheet in styles:
                    self.assertLess(shell.index("root.dataset.themeMode = mode"), shell.index(f"/static/{stylesheet}"))
                for retired in ("henng.css", "henng_ui.js", "workspace_ui.js"):
                    self.assertNotIn(f'/static/{retired}?', shell)

    def test_remote_frame_geometry_and_loading_shell_are_preserved(self):
        stylesheet = CSS.read_text(encoding="utf-8") + (ROOT / "spark_console/static/app.css").read_text(encoding="utf-8")
        self.assertIn("transform: none !important", stylesheet)
        self.assertIn("object-fit: fill", stylesheet)
        self.assertIn("aspect-ratio: 16 / 9", stylesheet)
        self.assertIn('.scan-dialog[data-frame-ready="false"]', stylesheet)

    def test_layout_and_controls_share_responsive_accessible_contracts(self):
        stylesheet = CSS.read_text(encoding="utf-8")
        for value in ("prefers-reduced-motion", ":focus-visible", "overflow-wrap: anywhere",
                      "mobile-nav", "data-theme"):
            self.assertIn(value, stylesheet)

    def test_task_creation_keeps_native_sticker_and_multi_target_contract(self):
        template = (ROOT / "spark_console/templates/tasks.html").read_text(encoding="utf-8")
        script = (ROOT / "spark_console/static/tasks.js").read_text(encoding="utf-8")
        for value in ('value="douyin_sticker" selected', "data-target-multiselect", "data-target-select-all",
                      "data-target-clear", 'id="target-picker-feedback"', "task-overview-row"):
            self.assertIn(value, template)
        for value in ('["target_name", item.name]', '["target_sec_uid", item.sec_uid]',
                      'dialog.addEventListener("cancel"', 'search.addEventListener("input", renderOptions)'):
            self.assertIn(value, script)

    def test_original_theme_initializers_are_restored(self):
        script = (ROOT / "spark_console/static/mineradio_ui.js").read_text(encoding="utf-8")
        for value in ("  initSpotlights();", "  initRailFeedback();", "  initReveals();"):
            self.assertIn(value, script)


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class ThemeJavaScriptTests(unittest.TestCase):
    def test_theme_controls(self):
        result = subprocess.run([shutil.which("node"), str(ROOT / "tests/console/mineradio_theme_js_harness.js")],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, result.returncode, result.stderr)
