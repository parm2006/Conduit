import unittest
from pathlib import Path
from unittest.mock import patch

from app.firewall import FirewallInspection, FirewallState
from app.firewall_helper import (
    EXIT_CONFIGURATION_FAILED,
    EXIT_POLICY_MANAGED,
    EXIT_SUCCESS,
)
from app.firewall_onboarding import (
    FirewallOnboarding,
    FirewallSetupOutcome,
    firewall_display,
)


class Backend:
    def __init__(self, *results):
        self.results = list(results)
        self.specs = []

    def inspect(self, spec):
        self.specs.append(spec)
        return self.results.pop(0)


class FirewallDisplayTests(unittest.TestCase):
    def test_each_state_has_safe_compact_copy_and_action(self):
        expected = {
            FirewallState.READY: ("Ready", "green", None),
            FirewallState.MISSING: (
                "Setup required",
                "orange",
                "Configure",
            ),
            FirewallState.STALE: (
                "Repair required",
                "orange",
                "Repair",
            ),
            FirewallState.DEVELOPMENT: (
                "Development rule",
                "orange",
                "View help",
            ),
            FirewallState.CONFLICT: (
                "Connection blocked",
                "red",
                "View help",
            ),
            FirewallState.PUBLIC_ONLY: (
                "Blocked on Public network",
                "orange",
                "View help",
            ),
            FirewallState.MANAGED: (
                "Managed by administrator",
                "orange",
                "View help",
            ),
            FirewallState.UNAVAILABLE: (
                "Unavailable",
                "red",
                "View help",
            ),
        }
        for state, values in expected.items():
            with self.subTest(state=state):
                display = firewall_display(
                    FirewallInspection(state, "safe_reason")
                )
                self.assertEqual(
                    (display.label, display.color, display.action),
                    values,
                )
                self.assertNotIn("safe_reason", display.explanation)


class FirewallOnboardingTests(unittest.TestCase):
    def make_onboarding(self, results, runner=None):
        backend = Backend(*results)
        calls = []
        onboarding = FirewallOnboarding(
            backend=backend,
            elevation_runner=runner or (lambda port: EXIT_SUCCESS),
            executable_path=r"C:\Program Files\DeskFlow\DeskFlow.exe",
            scheduler=lambda callback: calls.append(callback) or callback(),
        )
        return onboarding, backend, calls

    def test_refresh_only_inspects_and_never_mutates(self):
        onboarding, backend, _ = self.make_onboarding(
            [FirewallInspection(FirewallState.MISSING, "rule_missing")]
        )

        display = onboarding.refresh(5000)

        self.assertEqual(display.label, "Setup required")
        self.assertEqual(backend.specs[0].base_port, 5000)

    def test_default_inspection_targets_the_actual_windows_process_image(self):
        actual_image = (
            r"C:\Program Files\WindowsApps\PythonSoftwareFoundation."
            r"Python.3.12\python3.12.exe"
        )
        backend = Backend(
            FirewallInspection(FirewallState.MISSING, "rule_missing")
        )

        with patch(
            "app.firewall_onboarding.current_process_executable",
            return_value=actual_image,
        ):
            onboarding = FirewallOnboarding(
                backend=backend,
                elevation_runner=lambda port: EXIT_SUCCESS,
            )
            onboarding.refresh(28903)

        self.assertEqual(backend.specs[0].executable_path, actual_image)

    def test_configure_requires_explicit_consent(self):
        runner_calls = []
        onboarding, backend, _ = self.make_onboarding(
            [FirewallInspection(FirewallState.MISSING, "rule_missing")],
            runner=lambda port: runner_calls.append(port) or EXIT_SUCCESS,
        )

        result = onboarding.configure(5000, consent=lambda scope: False)

        self.assertEqual(result.outcome, FirewallSetupOutcome.DECLINED)
        self.assertEqual(runner_calls, [])
        self.assertEqual(len(backend.specs), 1)

    def test_configure_passes_only_validated_base_port_and_continues_when_ready(self):
        runner_calls = []
        continued = []
        onboarding, backend, callbacks = self.make_onboarding(
            [FirewallInspection(FirewallState.READY, "rule_ready")],
            runner=lambda port: runner_calls.append(port) or EXIT_SUCCESS,
        )

        result = onboarding.configure(
            5000,
            consent=lambda scope: True,
            on_ready=lambda: continued.append(True),
        )

        self.assertEqual(result.outcome, FirewallSetupOutcome.READY)
        self.assertEqual(runner_calls, [5000])
        self.assertEqual(backend.specs[0].base_port, 5000)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(continued, [True])

    def test_invalid_port_is_rejected_before_consent_or_elevation(self):
        runner_calls = []
        onboarding, backend, _ = self.make_onboarding(
            [],
            runner=lambda port: runner_calls.append(port),
        )

        result = onboarding.configure(65534, consent=lambda scope: True)

        self.assertEqual(result.outcome, FirewallSetupOutcome.INVALID)
        self.assertEqual(runner_calls, [])
        self.assertEqual(backend.specs, [])

    def test_uac_cancel_and_helper_failure_refresh_but_never_continue(self):
        cases = (
            (None, FirewallSetupOutcome.DECLINED),
            (EXIT_CONFIGURATION_FAILED, FirewallSetupOutcome.FAILED),
            (EXIT_POLICY_MANAGED, FirewallSetupOutcome.MANAGED),
        )
        for exit_code, expected in cases:
            with self.subTest(exit_code=exit_code):
                continued = []
                onboarding, backend, _ = self.make_onboarding(
                    [FirewallInspection(FirewallState.MISSING, "rule_missing")],
                    runner=lambda port, value=exit_code: value,
                )

                result = onboarding.configure(
                    5000,
                    consent=lambda scope: True,
                    on_ready=lambda: continued.append(True),
                )

                self.assertEqual(result.outcome, expected)
                self.assertEqual(len(backend.specs), 1)
                self.assertEqual(continued, [])

    def test_success_exit_still_requires_matching_reinspection(self):
        onboarding, _, _ = self.make_onboarding(
            [FirewallInspection(FirewallState.STALE, "stale_local_ports")]
        )

        result = onboarding.configure(5000, consent=lambda scope: True)

        self.assertEqual(result.outcome, FirewallSetupOutcome.FAILED)

    def test_development_scope_is_identified_and_can_continue(self):
        backend = Backend(
            FirewallInspection(FirewallState.DEVELOPMENT, "python_scope")
        )
        continued = []
        onboarding = FirewallOnboarding(
            backend=backend,
            elevation_runner=lambda port: EXIT_SUCCESS,
            executable_path=r"C:\Python314\python.exe",
        )

        result = onboarding.configure(
            5000,
            consent=lambda scope: scope.development_scope,
            on_ready=lambda: continued.append(True),
        )

        self.assertEqual(result.outcome, FirewallSetupOutcome.READY)
        self.assertEqual(continued, [True])

    def test_repeated_configuration_is_rejected_while_busy(self):
        onboarding, _, _ = self.make_onboarding(
            [FirewallInspection(FirewallState.READY, "rule_ready")]
        )
        onboarding.busy = True

        result = onboarding.configure(5000, consent=lambda scope: True)

        self.assertEqual(result.outcome, FirewallSetupOutcome.BUSY)

    def test_async_configuration_does_not_wait_for_elevation_on_caller_thread(self):
        runner_calls = []
        completed = []
        scheduled = []

        class DeferredThread:
            def __init__(self, *, target, daemon):
                self.target = target
                self.daemon = daemon
                self.started = False

            def start(self):
                self.started = True

        threads = []
        onboarding = FirewallOnboarding(
            backend=Backend(
                FirewallInspection(FirewallState.READY, "rule_ready")
            ),
            elevation_runner=lambda port: runner_calls.append(port)
            or EXIT_SUCCESS,
            executable_path=r"C:\Program Files\DeskFlow\DeskFlow.exe",
            scheduler=lambda callback: scheduled.append(callback),
            thread_factory=lambda **kwargs: threads.append(
                DeferredThread(**kwargs)
            )
            or threads[-1],
        )

        result = onboarding.configure_async(
            5000,
            consent=lambda scope: True,
            on_complete=completed.append,
        )

        self.assertIsNone(result)
        self.assertEqual(runner_calls, [])
        self.assertEqual(len(threads), 1)
        self.assertTrue(threads[0].started)
        self.assertTrue(threads[0].daemon)

        threads[0].target()
        self.assertEqual(runner_calls, [5000])
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(completed, [])

        scheduled[0]()
        self.assertEqual(
            completed[0].outcome,
            FirewallSetupOutcome.READY,
        )

    def test_gui_uses_async_configuration_for_firewall_actions(self):
        gui_source = (
            Path(__file__).resolve().parents[1] / "app" / "gui.py"
        ).read_text(encoding="utf-8")
        firewall_action = gui_source[
            gui_source.index("    def _on_firewall_action"):
            gui_source.index("    def _start_server_after_firewall")
        ]

        self.assertNotIn("onboarding.configure(", firewall_action)
        self.assertEqual(firewall_action.count("onboarding.configure_async("), 2)


if __name__ == "__main__":
    unittest.main()
