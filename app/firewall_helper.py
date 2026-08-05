"""Restricted command surface for DeskFlow firewall operations."""

import sys

from app.firewall import (
    FirewallInspection,
    FirewallRuleSpec,
    FirewallState,
    current_process_executable,
)
from app.windows_firewall import WindowsFirewallBackend


EXIT_SUCCESS = 0
EXIT_INVALID_REQUEST = 2
EXIT_CONFIGURATION_FAILED = 3
EXIT_POLICY_MANAGED = 4


class _DiscardOutput:
    @staticmethod
    def write(value):
        return len(value)


def _write_result(output, inspection):
    output.write(f"firewall_state={inspection.state.value}\n")
    output.write(f"reason={inspection.reason_code}\n")


def _parse_request(arguments):
    if arguments == ["remove"]:
        return "remove", None
    if (
        len(arguments) == 3
        and arguments[0] in {"install", "inspect", "repair"}
        and arguments[1] == "--base-port"
    ):
        try:
            base_port = int(arguments[2], 10)
        except (TypeError, ValueError):
            return None
        return arguments[0], base_port
    return None


def _exit_code(operation, inspection):
    if inspection.state in {FirewallState.READY, FirewallState.DEVELOPMENT}:
        return EXIT_SUCCESS
    if (
        operation in {"install", "repair"}
        and inspection.state is FirewallState.PUBLIC_ONLY
    ):
        return EXIT_SUCCESS
    if operation == "remove" and inspection.state is FirewallState.MISSING:
        return EXIT_SUCCESS
    if inspection.state is FirewallState.MANAGED:
        return EXIT_POLICY_MANAGED
    return EXIT_CONFIGURATION_FAILED


def run_firewall_helper(
    arguments,
    *,
    backend_factory=WindowsFirewallBackend,
    executable_path=None,
    output=None,
):
    """Run one allowlisted firewall operation and return a stable exit code."""
    if output is None:
        output = sys.stdout or _DiscardOutput()
    request = _parse_request(list(arguments))
    if request is None:
        output.write("firewall_state=invalid\nreason=invalid_request\n")
        return EXIT_INVALID_REQUEST

    operation, base_port = request
    if operation == "remove":
        inspection = backend_factory().remove()
    else:
        try:
            spec = FirewallRuleSpec(
                executable_path or current_process_executable(),
                base_port,
            )
        except ValueError:
            output.write("firewall_state=invalid\nreason=invalid_request\n")
            return EXIT_INVALID_REQUEST
        backend = backend_factory()
        if operation == "install":
            inspection = backend.install_or_replace(spec)
        elif operation == "repair":
            inspection = backend.repair(spec)
        else:
            inspection = backend.inspect(spec)

    _write_result(output, inspection)
    return _exit_code(operation, inspection)
