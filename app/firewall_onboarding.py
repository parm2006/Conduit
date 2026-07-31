"""Consent and status coordination for DeskFlow firewall onboarding."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import subprocess
import sys
import threading

from app.firewall import (
    FirewallInspection,
    FirewallRuleSpec,
    FirewallState,
    current_process_executable,
)
from app.firewall_helper import (
    EXIT_CONFIGURATION_FAILED,
    EXIT_POLICY_MANAGED,
    EXIT_SUCCESS,
)
from app.windows_firewall import WindowsFirewallBackend


@dataclass(frozen=True)
class FirewallDisplay:
    label: str
    color: str
    action: str | None
    explanation: str


class FirewallSetupOutcome(str, Enum):
    READY = "ready"
    DECLINED = "declined"
    FAILED = "failed"
    MANAGED = "managed"
    INVALID = "invalid"
    BUSY = "busy"


@dataclass(frozen=True)
class FirewallSetupResult:
    outcome: FirewallSetupOutcome
    inspection: FirewallInspection | None = None


_DISPLAY = {
    FirewallState.READY: FirewallDisplay(
        "Ready",
        "green",
        None,
        "DeskFlow is allowed on private local networks.",
    ),
    FirewallState.MISSING: FirewallDisplay(
        "Setup required",
        "orange",
        "Configure",
        "Windows Firewall has no matching DeskFlow rule.",
    ),
    FirewallState.STALE: FirewallDisplay(
        "Repair required",
        "orange",
        "Repair",
        "The DeskFlow rule does not match this executable and port.",
    ),
    FirewallState.DEVELOPMENT: FirewallDisplay(
        "Development rule",
        "orange",
        "View help",
        "The rule applies to Python because DeskFlow is running from source.",
    ),
    FirewallState.MANAGED: FirewallDisplay(
        "Managed by administrator",
        "orange",
        "View help",
        "Windows policy did not allow DeskFlow to change this rule.",
    ),
    FirewallState.UNAVAILABLE: FirewallDisplay(
        "Unavailable",
        "red",
        "View help",
        "DeskFlow could not safely read Windows Firewall status.",
    ),
}


def firewall_display(inspection):
    return _DISPLAY[inspection.state]


def _default_scheduler(callback):
    callback()


class FirewallOnboarding:
    def __init__(
        self,
        *,
        backend=None,
        elevation_runner=None,
        executable_path=None,
        scheduler=None,
        thread_factory=None,
    ):
        self.backend = backend or WindowsFirewallBackend()
        self.elevation_runner = (
            elevation_runner or run_elevated_firewall_install
        )
        self.executable_path = (
            executable_path or current_process_executable()
        )
        self.scheduler = scheduler or _default_scheduler
        self.thread_factory = thread_factory or threading.Thread
        self.busy = False
        self.inspection = FirewallInspection(
            FirewallState.UNAVAILABLE,
            "not_inspected",
        )

    def _spec(self, base_port):
        return FirewallRuleSpec(self.executable_path, base_port)

    def refresh(self, base_port):
        try:
            self.inspection = self.backend.inspect(self._spec(base_port))
        except (TypeError, ValueError):
            self.inspection = FirewallInspection(
                FirewallState.UNAVAILABLE,
                "invalid_port",
            )
        return firewall_display(self.inspection)

    def _begin_configuration(self, base_port, consent):
        if self.busy:
            return FirewallSetupResult(FirewallSetupOutcome.BUSY)
        try:
            spec = self._spec(base_port)
        except (TypeError, ValueError):
            return FirewallSetupResult(FirewallSetupOutcome.INVALID)

        if not consent(spec):
            self.refresh(base_port)
            return FirewallSetupResult(
                FirewallSetupOutcome.DECLINED,
                self.inspection,
            )

        self.busy = True
        return None

    def _finish_configuration(self, base_port, exit_code, on_ready=None):
        try:
            self.refresh(base_port)
        finally:
            self.busy = False

        if exit_code is None:
            outcome = FirewallSetupOutcome.DECLINED
        elif exit_code == EXIT_POLICY_MANAGED:
            outcome = FirewallSetupOutcome.MANAGED
        elif exit_code != EXIT_SUCCESS:
            outcome = FirewallSetupOutcome.FAILED
        elif self.inspection.state in {
            FirewallState.READY,
            FirewallState.DEVELOPMENT,
        }:
            outcome = FirewallSetupOutcome.READY
        else:
            outcome = FirewallSetupOutcome.FAILED

        if outcome is FirewallSetupOutcome.READY and on_ready is not None:
            self.scheduler(on_ready)
        return FirewallSetupResult(outcome, self.inspection)

    def configure(self, base_port, *, consent, on_ready=None):
        """Configure synchronously for non-GUI callers and unit tests."""
        early_result = self._begin_configuration(base_port, consent)
        if early_result is not None:
            return early_result
        try:
            exit_code = self.elevation_runner(base_port)
        except Exception:
            exit_code = EXIT_CONFIGURATION_FAILED
        return self._finish_configuration(base_port, exit_code, on_ready)

    def configure_async(
        self,
        base_port,
        *,
        consent,
        on_complete=None,
        on_ready=None,
    ):
        """Run the elevated helper off the UI thread and schedule completion."""
        early_result = self._begin_configuration(base_port, consent)
        if early_result is not None:
            return early_result

        def worker():
            try:
                exit_code = self.elevation_runner(base_port)
            except Exception:
                exit_code = EXIT_CONFIGURATION_FAILED

            def complete():
                result = self._finish_configuration(
                    base_port,
                    exit_code,
                    on_ready,
                )
                if on_complete is not None:
                    on_complete(result)

            self.scheduler(complete)

        try:
            thread = self.thread_factory(target=worker, daemon=True)
            thread.start()
        except Exception:
            return self._finish_configuration(
                base_port,
                EXIT_CONFIGURATION_FAILED,
            )
        return None


def run_elevated_firewall_install(base_port):
    """Elevate the fixed DeskFlow helper command; return None on UAC cancel."""
    FirewallRuleSpec(current_process_executable(), base_port)
    project_root = Path(__file__).resolve().parents[1]
    frozen = getattr(sys, "frozen", False)
    arguments = [
        "--deskflow-firewall-helper",
        "install",
        "--base-port",
        str(base_port),
    ]
    if not frozen:
        entry_path = project_root / "run.py"
        arguments.insert(0, str(entry_path))
    parameters = subprocess.list2cmdline(arguments)
    working_directory = (
        Path(sys.executable).resolve().parent if frozen else project_root
    )

    try:
        import win32api
        import win32con
        import win32event
        import win32process
        from win32com.shell import shell, shellcon

        process = shell.ShellExecuteEx(
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS,
            lpVerb="runas",
            lpFile=sys.executable,
            lpParameters=parameters,
            lpDirectory=str(working_directory),
            nShow=win32con.SW_SHOWNORMAL,
        )["hProcess"]
        win32event.WaitForSingleObject(process, win32event.INFINITE)
        exit_code = int(win32process.GetExitCodeProcess(process))
        win32api.CloseHandle(process)
        return exit_code
    except Exception as error:
        code = getattr(error, "winerror", None)
        if code is None and error.args:
            code = error.args[0]
        if code == 1223:
            return None
        return EXIT_CONFIGURATION_FAILED
