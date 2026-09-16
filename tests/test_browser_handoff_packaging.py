import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension" / "ConduitBrowserHandoff"


class BrowserHandoffPackagingTests(unittest.TestCase):
    def test_shipping_manifest_and_popup_are_present_without_diagnostic_probe(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(manifest["action"]["default_popup"], "popup.html")
        self.assertTrue((EXTENSION / "popup.html").is_file())
        self.assertTrue((EXTENSION / "popup.js").is_file())
        self.assertFalse(any("probe" in path.name.lower() for path in EXTENSION.glob("*.js")))

    def test_dev_build_script_excludes_tests_and_requires_manifest_at_root(self):
        script = (ROOT / "scripts" / "build_browser_extension.ps1").read_text(encoding="utf-8")

        self.assertIn("ConduitBrowserHandoff-dev.zip", script)
        self.assertIn("manifest.json", script)
        self.assertIn("tests", script)
        self.assertIn("ConduitBrowserHandoff", script)

    def test_dedicated_console_host_spec_and_exact_origin_dev_script_exist(self):
        spec = (ROOT / "ConduitBrowserHost.spec").read_text(encoding="utf-8")
        registration = (ROOT / "scripts" / "register_browser_handoff_dev.ps1").read_text(encoding="utf-8")

        self.assertIn("console=True", spec)
        self.assertIn("conduit_browser_host.py", spec)
        self.assertIn("ValidatePattern", registration)
        self.assertIn("chrome-extension://", registration)

