import tempfile
import unittest
from pathlib import Path
import platform

from app.version import (
    FILE_VERSION,
    FILE_VERSION_STRING,
    NSIS_LICENSE_URL,
    NSIS_SOURCE_URL,
    PRODUCT_VERSION,
    PYINSTALLER_LICENSE_URL,
    PYINSTALLER_SOURCE_URL,
    SOURCE_URL,
)
from scripts.generate_third_party_notices import (
    RELEASE_DISTRIBUTIONS,
    generate_notices,
)


class FakeMetadata:
    def __init__(self, values):
        self.values = values

    def get(self, name, default=None):
        return self.values.get(name, default)

    def get_all(self, name):
        value = self.values.get(name)
        return value if isinstance(value, list) else None


class FakeDistribution:
    def __init__(self, root, name, *, license_text="MIT", source=True):
        self.root = Path(root)
        self.version = "1.2.3"
        values = {
            "Name": name,
            "License": license_text,
            "Home-page": "https://example.invalid/source" if source else None,
        }
        self.metadata = FakeMetadata(values)
        self.files = [Path(f"{name}-1.2.3.dist-info/LICENSE")]
        path = self.locate_file(self.files[0])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{name} license body", encoding="utf-8")

    def locate_file(self, relative):
        return self.root / relative


class ReleaseMetadataTests(unittest.TestCase):
    def test_product_and_tool_metadata_is_canonical(self):
        self.assertEqual(PRODUCT_VERSION, "4.3s")
        self.assertEqual(FILE_VERSION, (4, 3, 0, 0))
        self.assertEqual(FILE_VERSION_STRING, "4.3.0.0")
        self.assertEqual(SOURCE_URL, "https://github.com/parm2006/DeskFlow")
        for value in (
            PYINSTALLER_LICENSE_URL,
            PYINSTALLER_SOURCE_URL,
            NSIS_LICENSE_URL,
            NSIS_SOURCE_URL,
        ):
            self.assertTrue(value.startswith("https://"))

    def test_release_inventory_names_exact_installed_distributions(self):
        self.assertEqual(
            RELEASE_DISTRIBUTIONS,
            tuple(sorted(RELEASE_DISTRIBUTIONS, key=str.casefold)),
        )
        self.assertIn("PyInstaller", RELEASE_DISTRIBUTIONS)
        self.assertIn("pywin32", RELEASE_DISTRIBUTIONS)
        self.assertIn("customtkinter", RELEASE_DISTRIBUTIONS)

    def test_notice_generation_is_deterministic_and_omits_private_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            distributions = {
                name: FakeDistribution(root, name)
                for name in ("Zulu", "alpha")
            }
            factory = lambda name: distributions[name]

            first = generate_notices(
                ("Zulu", "alpha"),
                distribution_factory=factory,
            )
            second = generate_notices(
                ("alpha", "Zulu"),
                distribution_factory=factory,
            )

        self.assertEqual(first, second)
        self.assertLess(first.index("alpha 1.2.3"), first.index("Zulu 1.2.3"))
        self.assertNotIn(str(root), first)
        self.assertIn("alpha license body", first)
        self.assertIn(NSIS_SOURCE_URL, first)
        self.assertIn(f"Python {platform.python_version()} runtime", first)
        self.assertIn("Tcl/Tk runtime", first)

    def test_notice_generation_fails_closed_without_license_or_source(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_license = FakeDistribution(directory, "missing")
            missing_license.files = []
            with self.assertRaises(ValueError):
                generate_notices(
                    ("missing",),
                    distribution_factory=lambda name: missing_license,
                )

        with tempfile.TemporaryDirectory() as directory:
            missing_source = FakeDistribution(
                directory,
                "missing",
                source=False,
            )
            with self.assertRaises(ValueError):
                generate_notices(
                    ("missing",),
                    distribution_factory=lambda name: missing_source,
                )


class PyInstallerSpecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]

    def test_canonical_spec_is_unignored(self):
        ignore = (self.root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("*.spec", ignore)
        self.assertIn("!DeskFlow.spec", ignore)

    def test_spec_builds_one_windowed_executable_with_release_inputs(self):
        spec = (self.root / "DeskFlow.spec").read_text(encoding="utf-8")
        required = (
            "run.py",
            "name=\"DeskFlow\"",
            "console=False",
            "app_icon.ico",
            "app/assets",
            "LICENSE",
            "THIRD_PARTY_NOTICES.txt",
            "version=",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, spec)
        self.assertNotIn("COLLECT(", spec)

    def test_spec_includes_firewall_and_pywin32_hidden_imports(self):
        spec = (self.root / "DeskFlow.spec").read_text(encoding="utf-8")
        for module in (
            "app.firewall_helper",
            "app.windows_firewall",
            "win32com.client",
            "win32com.shell.shell",
            "pythoncom",
            "pywintypes",
            "win32api",
            "win32event",
            "win32process",
        ):
            with self.subTest(module=module):
                self.assertIn(module, spec)


class NsisInstallerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.script = (cls.root / "installer" / "DeskFlow.nsi").read_text(
            encoding="utf-8"
        )
        cls.lower = cls.script.casefold()

    def test_installer_requires_admin_and_rejects_silent_install(self):
        self.assertIn("RequestExecutionLevel admin", self.script)
        self.assertIn("IfSilent", self.script)
        self.assertIn("Silent installation is not supported", self.script)
        self.assertIn("SetErrorLevel", self.script)
        self.assertIn("Quit", self.script)

    def test_firewall_consent_is_explicit_and_before_file_copy(self):
        self.assertIn("Page custom FirewallConsentPage", self.script)
        self.assertIn("Yes - Continue", self.script)
        self.assertIn("No - Cancel installation", self.script)
        self.assertIn("Public networks remain blocked", self.script)
        self.assertLess(
            self.script.index("Page custom FirewallConsentPage"),
            self.script.index('File "..\\dist\\DeskFlow.exe"'),
        )
        self.assertIn("Function FirewallConsentNo", self.script)
        self.assertIn("!define MUI_CUSTOMFUNCTION_ABORT OnUserAbort", self.script)
        self.assertIn("Function OnUserAbort", self.script)

    def test_helper_install_and_inspect_are_mandatory_before_completion(self):
        install = (
            '"$INSTDIR\\DeskFlow.exe" --deskflow-firewall-helper '
            "install --base-port 28903"
        )
        inspect = (
            '"$INSTDIR\\DeskFlow.exe" --deskflow-firewall-helper '
            "inspect --base-port 28903"
        )
        self.assertIn(install, self.script)
        self.assertIn(inspect, self.script)
        self.assertIn("Call RollbackInstall", self.script)
        self.assertLess(
            self.script.index(install),
            self.script.index("WriteRegStr HKLM \"Software\\DeskFlow\""),
        )
        self.assertLess(
            self.script.index(inspect),
            self.script.index("WriteRegStr HKLM \"Software\\DeskFlow\""),
        )

    def test_rollback_and_uninstall_remove_rule_before_executable(self):
        remove = (
            '"$INSTDIR\\DeskFlow.exe" --deskflow-firewall-helper remove'
        )
        self.assertGreaterEqual(self.script.count(remove), 2)
        uninstall = self.script.index('Section "Uninstall"')
        remove_at = self.script.index(remove, uninstall)
        delete_at = self.script.index('Delete "$INSTDIR\\DeskFlow.exe"', uninstall)
        self.assertLess(remove_at, delete_at)

    def test_installer_never_runs_or_deletes_a_preexisting_target(self):
        self.assertNotIn("MUI_PAGE_DIRECTORY", self.script)
        self.assertIn('StrCpy $INSTDIR "$PROGRAMFILES64\\DeskFlow"', self.script)
        self.assertIn('IfFileExists "$INSTDIR\\*.*" existing_install', self.script)
        self.assertIn("Var TransactionFilesWritten", self.script)
        self.assertIn('StrCpy $TransactionFilesWritten "1"', self.script)
        abort = self.script[
            self.script.index("Function OnUserAbort"):
            self.script.index("FunctionEnd", self.script.index("Function OnUserAbort"))
        ]
        self.assertIn('${If} $TransactionFilesWritten == "1"', abort)
        self.assertNotIn('IfFileExists "$INSTDIR\\DeskFlow.exe"', abort)

    def test_rollback_preserves_recovery_binary_when_rule_removal_fails(self):
        rollback = self.script[
            self.script.index("Function RollbackInstall"):
            self.script.index("FunctionEnd", self.script.index("Function RollbackInstall"))
        ]
        self.assertIn("Var FirewallRemovalFailed", self.script)
        self.assertIn('${If} $0 != 0', rollback)
        self.assertIn('StrCpy $FirewallRemovalFailed "1"', rollback)
        self.assertIn(
            'StrCpy $FirewallRemovalFailed "1"\n    Return',
            rollback,
        )
        self.assertIn("Call CleanupTransactionFiles", rollback)
        cleanup = self.script[
            self.script.index("Function CleanupTransactionFiles"):
            self.script.index(
                "FunctionEnd",
                self.script.index("Function CleanupTransactionFiles"),
            )
        ]
        self.assertIn(
            'Delete "$INSTDIR\\DeskFlow.exe"',
            cleanup,
        )

    def test_partial_install_recovery_never_auto_executes_disk_content(self):
        self.assertNotIn("recover_partial_install", self.script)
        self.assertIn("ClearErrors\n  FileOpen", self.script)
        self.assertIn("IfErrors marker_write_failed", self.script)
        install = self.script.index(
            '"$INSTDIR\\DeskFlow.exe" --deskflow-firewall-helper '
            "install --base-port 28903"
        )
        self.assertLess(
            self.script.index('WriteUninstaller "$INSTDIR\\Uninstall.exe"'),
            install,
        )

    def test_installer_carries_license_source_and_notices(self):
        for marker in (
            "LICENSE",
            "THIRD_PARTY_NOTICES.txt",
            SOURCE_URL,
            "DeskFlow Source.url",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.script)

    def test_installer_contains_no_broad_or_shell_firewall_behavior(self):
        forbidden = (
            "netsh.exe",
            " netsh ",
            "powershell",
            "disable-netfirewall",
            "publicprofile",
            "currentprofile",
            "advfirewall",
        )
        for marker in forbidden:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, self.lower)


class ReleaseBuildScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.script = (root / "scripts" / "build_release.ps1").read_text(
            encoding="utf-8"
        )
        cls.lower = cls.script.casefold()

    def test_build_gate_runs_in_security_first_order(self):
        ordered = (
            "# compileall",
            "# unittest",
            "# git diff --check",
            "# generate_third_party_notices.py",
            "# pyinstaller.exe",
            "# --deskflow-firewall-helper",
            "# makensis",
        )
        positions = [self.lower.index(marker.casefold()) for marker in ordered]
        self.assertEqual(positions, sorted(positions))

    def test_native_failures_and_artifacts_are_checked(self):
        self.assertIn("$LASTEXITCODE", self.script)
        self.assertIn("Test-Path", self.script)
        self.assertIn("throw", self.script)
        self.assertIn("-Wait", self.script)
        self.assertIn("-PassThru", self.script)
        self.assertIn("helper exit code", self.lower)

    def test_nsis_is_found_locally_and_never_downloaded(self):
        self.assertIn("MakensisPath", self.script)
        self.assertIn("Get-Command", self.script)
        self.assertIn("NSIS", self.script)
        for forbidden in (
            "invoke-webrequest",
            "start-bitstransfer",
            "winget install",
            "choco install",
        ):
            self.assertNotIn(forbidden, self.lower)

    def test_optional_signing_invokes_an_explicit_tool_without_shell_eval(self):
        self.assertIn("SigningToolPath", self.script)
        self.assertIn("SigningArguments", self.script)
        self.assertNotIn("Invoke-Expression", self.script)


class ReleaseDocumentationTests(unittest.TestCase):
    def test_readme_documents_supported_release_and_firewall_behavior(self):
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        required = (
            "scripts\\build_release.ps1",
            "DeskFlow.exe",
            "TCP ports 28903-28905",
            "Private networks",
            "local subnet",
            "No cancels installation",
            "unsigned development build",
            "GPL-3.0",
            SOURCE_URL,
            "Uninstall",
            "firewall rule",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, readme)


if __name__ == "__main__":
    unittest.main()
