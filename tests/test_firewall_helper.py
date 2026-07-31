import io
import unittest
from unittest.mock import patch

from app.firewall import FirewallInspection, FirewallState
from app.firewall_helper import (
    EXIT_CONFIGURATION_FAILED,
    EXIT_INVALID_REQUEST,
    EXIT_POLICY_MANAGED,
    EXIT_SUCCESS,
    run_firewall_helper,
)


class RecordingBackend:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def inspect(self, spec):
        self.calls.append(("inspect", spec))
        return self.result

    def install_or_replace(self, spec):
        self.calls.append(("install", spec))
        return self.result

    def remove(self):
        self.calls.append(("remove",))
        return self.result


class FirewallHelperTests(unittest.TestCase):
    def run_helper(self, arguments, result):
        backend = RecordingBackend(result)
        output = io.StringIO()
        code = run_firewall_helper(
            arguments,
            backend_factory=lambda: backend,
            executable_path=r"C:\Program Files\DeskFlow\DeskFlow.exe",
            output=output,
        )
        return code, backend, output.getvalue()

    def test_install_accepts_only_a_valid_base_port(self):
        code, backend, output = self.run_helper(
            ["install", "--base-port", "5000"],
            FirewallInspection(FirewallState.READY, "rule_ready"),
        )

        self.assertEqual(code, EXIT_SUCCESS)
        self.assertEqual(len(backend.calls), 1)
        operation, spec = backend.calls[0]
        self.assertEqual(operation, "install")
        self.assertEqual(spec.base_port, 5000)
        self.assertEqual(
            spec.executable_path,
            r"C:\Program Files\DeskFlow\DeskFlow.exe",
        )
        self.assertEqual(
            output,
            "firewall_state=ready\nreason=rule_ready\n",
        )

    def test_inspect_reports_nonmatching_state_as_configuration_failure(self):
        code, backend, output = self.run_helper(
            ["inspect", "--base-port", "5000"],
            FirewallInspection(FirewallState.MISSING, "rule_missing"),
        )

        self.assertEqual(code, EXIT_CONFIGURATION_FAILED)
        self.assertEqual(backend.calls[0][0], "inspect")
        self.assertNotIn(r"C:\Program Files", output)

    def test_remove_is_idempotent_success_for_missing_rule(self):
        code, backend, _ = self.run_helper(
            ["remove"],
            FirewallInspection(FirewallState.MISSING, "rule_missing"),
        )

        self.assertEqual(code, EXIT_SUCCESS)
        self.assertEqual(backend.calls, [("remove",)])

    def test_development_rule_is_a_successful_matching_configuration(self):
        code, _, _ = self.run_helper(
            ["install", "--base-port", "5000"],
            FirewallInspection(FirewallState.DEVELOPMENT, "python_scope"),
        )

        self.assertEqual(code, EXIT_SUCCESS)

    def test_policy_denial_has_a_distinct_safe_exit_code(self):
        code, _, output = self.run_helper(
            ["install", "--base-port", "5000"],
            FirewallInspection(FirewallState.MANAGED, "policy_denied"),
        )

        self.assertEqual(code, EXIT_POLICY_MANAGED)
        self.assertEqual(
            output,
            "firewall_state=managed\nreason=policy_denied\n",
        )

    def test_unavailable_operation_is_a_configuration_failure(self):
        code, _, _ = self.run_helper(
            ["install", "--base-port", "5000"],
            FirewallInspection(
                FirewallState.UNAVAILABLE,
                "configuration_failed",
            ),
        )

        self.assertEqual(code, EXIT_CONFIGURATION_FAILED)

    def test_rejects_arbitrary_or_malformed_arguments_without_backend_call(self):
        invalid = (
            [],
            ["install"],
            ["remove", "--base-port", "5000"],
            ["install", "--base-port", "0"],
            ["install", "--base-port", "65534"],
            ["install", "--base-port", "5000-5002"],
            ["install", "--program", r"C:\malware.exe"],
            ["install", "--profile", "public", "--base-port", "5000"],
            ["install", "--command", "Disable-NetFirewallProfile"],
            ["delete-all"],
        )

        for arguments in invalid:
            with self.subTest(arguments=arguments):
                code, backend, output = self.run_helper(
                    arguments,
                    FirewallInspection(FirewallState.READY, "rule_ready"),
                )
                self.assertEqual(code, EXIT_INVALID_REQUEST)
                self.assertEqual(backend.calls, [])
                self.assertEqual(
                    output,
                    "firewall_state=invalid\nreason=invalid_request\n",
                )

    def test_windowed_package_without_stdout_still_returns_an_exit_code(self):
        with patch("app.firewall_helper.sys.stdout", None):
            code = run_firewall_helper(["delete-all"])

        self.assertEqual(code, EXIT_INVALID_REQUEST)


if __name__ == "__main__":
    unittest.main()
