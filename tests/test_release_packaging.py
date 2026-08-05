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
        self.assertIn("disable only that exact executable", self.script)
        self.assertLess(
            self.script.index("Page custom FirewallConsentPage"),
            self.script.index('File "..\\dist\\DeskFlow.exe"'),
        )
        self.assertIn("Function FirewallConsentNo", self.script)
        self.assertIn("!define MUI_CUSTOMFUNCTION_ABORT OnUserAbort", self.script)
        self.assertIn("Function OnUserAbort", self.script)

    def test_verified_repair_is_the_final_fallible_step_before_completion(self):
        repair = (
            '"$INSTDIR\\DeskFlow.exe" --deskflow-firewall-helper '
            "repair --base-port 28903"
        )
        self.assertIn(repair, self.script)
        self.assertNotIn(
            '"$INSTDIR\\DeskFlow.exe" --deskflow-firewall-helper '
            "inspect --base-port 28903",
            self.script,
        )
        self.assertIn("Call RollbackInstall", self.script)
        self.assertGreater(
            self.script.index(repair),
            self.script.index("WriteRegStr HKLM \"Software\\DeskFlow\""),
        )
        after_repair = self.script[self.script.index(repair):]
        self.assertLess(
            after_repair.index('StrCpy $InstallComplete "1"'),
            after_repair.index("install_complete:"),
        )

    def test_uninstall_removes_rule_before_executable(self):
        remove = (
            '"$INSTDIR\\DeskFlow.exe" --deskflow-firewall-helper remove'
        )
        self.assertEqual(self.script.count(remove), 1)
        uninstall = self.script.index('Section "Uninstall"')
        remove_at = self.script.index(remove, uninstall)
        delete_at = self.script.index('Delete "$INSTDIR\\DeskFlow.exe"', uninstall)
        self.assertLess(remove_at, delete_at)

    def test_uninstall_continues_when_firewall_cleanup_is_denied(self):
        uninstall = self.script[
            self.script.index('Section "Uninstall"'):
            self.script.index("SectionEnd", self.script.index('Section "Uninstall"'))
        ]

        self.assertIn("could not remove its firewall rule", uninstall)
        self.assertIn("IfSilent uninstall_firewall_warning_done 0", uninstall)
        self.assertLess(
            uninstall.index("IfSilent uninstall_firewall_warning_done 0"),
            uninstall.index("could not remove its firewall rule"),
        )
        self.assertGreater(
            uninstall.index("uninstall_firewall_warning_done:"),
            uninstall.index("could not remove its firewall rule"),
        )
        self.assertIn('Delete "$INSTDIR\\DeskFlow.exe"', uninstall)
        self.assertNotIn("Uninstall was cancelled", uninstall)
        self.assertNotIn("SetErrorLevel 4", uninstall)
        self.assertNotIn("Quit", uninstall)

    def test_installer_classifies_exact_packaged_upgrade_and_partial_remnants(self):
        self.assertNotIn("MUI_PAGE_DIRECTORY", self.script)
        self.assertIn('StrCpy $INSTDIR "$PROGRAMFILES64\\DeskFlow"', self.script)
        self.assertIn("Var ExistingInstallState", self.script)
        self.assertIn("Function ClassifyExistingInstall", self.script)
        self.assertIn('ReadRegStr $0 HKLM "Software\\DeskFlow" "InstallDir"', self.script)
        self.assertIn(
            'ReadRegStr $1 HKLM "${UNINSTALL_KEY}" "UninstallString"',
            self.script,
        )
        self.assertIn('StrCmp $0 "$INSTDIR"', self.script)
        self.assertIn("FindFirst", self.script)
        for filename in (
            "DeskFlow.exe",
            "Uninstall.exe",
            "DeskFlow Source.url",
            "DeskFlow.installing",
            "THIRD_PARTY_NOTICES.txt",
            "LICENSE",
        ):
            with self.subTest(filename=filename):
                self.assertIn(filename, self.script)
        self.assertIn('StrCpy $ExistingInstallState "upgrade"', self.script)
        self.assertIn('StrCpy $ExistingInstallState "partial"', self.script)
        self.assertIn("unknown_install_contents", self.script)
        self.assertNotIn('$"', self.script)
        self.assertIn("classify_done:\n  ClearErrors\n  Return", self.script)

    def test_existing_install_is_not_mutated_before_firewall_consent(self):
        consent = self.script.index("Function FirewallConsentLeave")
        preparation = self.script.index("Function PrepareExistingInstall")
        section = self.script.index('Section "DeskFlow"')
        prepare_call = self.script.index("Call PrepareExistingInstall", section)

        self.assertLess(consent, section)
        self.assertLess(preparation, section)
        self.assertGreater(prepare_call, section)
        self.assertIn("Var TransactionFilesWritten", self.script)
        self.assertIn('StrCpy $TransactionFilesWritten "1"', self.script)
        abort = self.script[
            self.script.index("Function OnUserAbort"):
            self.script.index("FunctionEnd", self.script.index("Function OnUserAbort"))
        ]
        self.assertIn('${If} $TransactionFilesWritten == "1"', abort)
        self.assertNotIn('IfFileExists "$INSTDIR\\DeskFlow.exe"', abort)

    def test_install_cannot_be_cancelled_after_existing_install_mutation_starts(self):
        section = self.script[
            self.script.index('Section "DeskFlow"'):
            self.script.index("SectionEnd", self.script.index('Section "DeskFlow"'))
        ]
        disable_cancel = section.index("GetDlgItem $2 $HWNDPARENT 2")
        prepare = section.index("Call PrepareExistingInstall")

        self.assertLess(disable_cancel, prepare)
        self.assertIn("EnableWindow $2 0", section[disable_cancel:prepare])

    def test_upgrade_uses_reversible_exact_executable_lock_preflight(self):
        preflight = self.script[
            self.script.index("Function PreflightUpgrade"):
            self.script.index(
                "FunctionEnd",
                self.script.index("Function PreflightUpgrade"),
            )
        ]
        self.assertIn("DeskFlow.upgrade-lock-test", preflight)
        self.assertIn(
            'Rename "$INSTDIR\\DeskFlow.exe" '
            '"$INSTDIR\\DeskFlow.upgrade-lock-test"',
            preflight,
        )
        self.assertIn(
            'Rename "$INSTDIR\\DeskFlow.upgrade-lock-test" '
            '"$INSTDIR\\DeskFlow.exe"',
            preflight,
        )
        self.assertIn("IfErrors", preflight)

    def test_upgrade_waits_for_exact_uninstaller_before_new_file_copy(self):
        section = self.script[self.script.index('Section "DeskFlow"'):]
        prepare = section.index("Call PrepareExistingInstall")
        old_uninstall = section.index(
            "ExecWait '\"$INSTDIR\\Uninstall.exe\" /S _?=$INSTDIR'"
        )
        delete_old_uninstaller = section.index(
            'Delete "$INSTDIR\\Uninstall.exe"', old_uninstall
        )
        enumerate_remaining = section.index(
            'FindFirst $2 $3 "$INSTDIR\\*"', old_uninstall
        )
        verify_empty = section.index("upgrade_directory_not_empty")
        copy_new = section.index('File "..\\dist\\DeskFlow.exe"')

        self.assertLess(prepare, old_uninstall)
        self.assertLess(old_uninstall, delete_old_uninstaller)
        self.assertLess(delete_old_uninstaller, enumerate_remaining)
        self.assertLess(old_uninstall, verify_empty)
        self.assertLess(verify_empty, copy_new)
        self.assertIn("upgrade_ready:\n    ClearErrors", section)
        self.assertNotIn(
            'IfFileExists "$INSTDIR\\*.*" upgrade_directory_not_empty',
            section,
        )

    def test_partial_recovery_deletes_allowlist_without_executing_disk_content(self):
        partial = self.script[
            self.script.index("Function CleanupPartialInstall"):
            self.script.index(
                "FunctionEnd",
                self.script.index("Function CleanupPartialInstall"),
            )
        ]
        for filename in (
            "DeskFlow.exe",
            "Uninstall.exe",
            "DeskFlow Source.url",
            "DeskFlow.installing",
            "THIRD_PARTY_NOTICES.txt",
            "LICENSE",
        ):
            with self.subTest(filename=filename):
                self.assertIn(f'Delete "$INSTDIR\\{filename}"', partial)
        self.assertIn("IfErrors", partial)
        self.assertNotIn("Exec", partial)
        self.assertNotIn("ExecWait", partial)
        self.assertNotIn("RMDir /r", partial)
        self.assertNotIn("$LOCALAPPDATA", self.script)

    def test_install_rollback_does_not_mutate_firewall_outside_repair(self):
        rollback = self.script[
            self.script.index("Function RollbackInstall"):
            self.script.index("FunctionEnd", self.script.index("Function RollbackInstall"))
        ]
        self.assertNotIn("--deskflow-firewall-helper", rollback)
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

    def test_release_build_removes_exact_stale_outputs_before_packaging(self):
        executable_cleanup = 'Remove-Item -LiteralPath $DeskFlowExecutable'
        installer_cleanup = 'Remove-Item -LiteralPath $InstallerExecutable'
        pyinstaller = '# pyinstaller.exe'

        self.assertIn(executable_cleanup, self.script)
        self.assertIn(installer_cleanup, self.script)
        self.assertLess(
            self.script.index(executable_cleanup),
            self.script.index("# compileall"),
        )
        self.assertLess(
            self.script.index(installer_cleanup),
            self.script.index("# compileall"),
        )
        self.assertLess(
            self.script.index("# compileall"),
            self.script.index(pyinstaller),
        )

    def test_supported_build_is_the_only_nsis_entry_point(self):
        root = Path(__file__).resolve().parents[1]
        installer = (root / "installer" / "DeskFlow.nsi").read_text(
            encoding="utf-8"
        )

        self.assertIn("!ifndef DESKFLOW_RELEASE_BUILD", installer)
        self.assertIn("!error", installer)
        self.assertIn("DESKFLOW_RELEASE_BUILD", self.script)
        self.assertIn('/DDESKFLOW_RELEASE_BUILD=1', self.script)

    def test_fresh_helper_smoke_precedes_nsis_assembly(self):
        executable_check = 'Test-Path -LiteralPath $DeskFlowExecutable'
        helper_smoke = '# --deskflow-firewall-helper'
        nsis = '# makensis'
        installer_check = 'Test-Path -LiteralPath $InstallerExecutable'

        self.assertLess(
            self.script.index(executable_check),
            self.script.index(helper_smoke),
        )
        self.assertLess(self.script.index(helper_smoke), self.script.index(nsis))
        self.assertGreater(
            self.script.rindex(installer_check),
            self.script.index(nsis),
        )

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
            "removes old release outputs before compilation",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, readme)


if __name__ == "__main__":
    unittest.main()
