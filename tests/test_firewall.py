import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app.firewall as firewall

from app.firewall import (
    DESKFLOW_FIREWALL_RULE_NAME,
    FirewallInspection,
    FirewallRuleSpec,
    FirewallState,
    ObservedFirewallRule,
    compare_firewall_rule,
    current_process_executable,
)


def matching_rule(spec, **changes):
    values = {
        "name": DESKFLOW_FIREWALL_RULE_NAME,
        "enabled": True,
        "direction": "inbound",
        "action": "allow",
        "protocol": "tcp",
        "local_ports": spec.local_ports,
        "application_name": spec.executable_path,
        "profiles": frozenset({"private"}),
        "remote_addresses": frozenset({"localsubnet"}),
        "edge_traversal": False,
    }
    values.update(changes)
    return ObservedFirewallRule(**values)


class FirewallRuleSpecTests(unittest.TestCase):
    def test_current_process_executable_uses_the_windows_process_image(self):
        actual_image = (
            r"C:\Program Files\WindowsApps\PythonSoftwareFoundation."
            r"Python.3.12\python3.12.exe"
        )

        with patch(
            "win32api.GetModuleFileName",
            return_value=actual_image,
        ):
            result = current_process_executable()

        self.assertEqual(result, actual_image)

    def test_accepts_boundary_base_ports_and_derives_three_port_range(self):
        low = FirewallRuleSpec(r"C:\Program Files\DeskFlow\DeskFlow.exe", 1)
        high = FirewallRuleSpec(r"C:\Program Files\DeskFlow\DeskFlow.exe", 65533)

        self.assertEqual(low.local_ports, "1-3")
        self.assertEqual(high.local_ports, "65533-65535")

    def test_rejects_invalid_base_ports(self):
        invalid = (True, False, "5000", 1.5, 0, -1, 65534, 65535)

        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    FirewallRuleSpec(r"C:\DeskFlow.exe", value)

    def test_rejects_empty_or_relative_executable_paths(self):
        for value in ("", "DeskFlow.exe", r".\DeskFlow.exe"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    FirewallRuleSpec(value, 5000)

    def test_identifies_source_python_as_development_scope(self):
        packaged = FirewallRuleSpec(
            r"C:\Program Files\DeskFlow\DeskFlow.exe",
            5000,
        )
        source = FirewallRuleSpec(r"C:\Python314\python.exe", 5000)
        store_source = FirewallRuleSpec(
            r"C:\Program Files\WindowsApps\PythonSoftwareFoundation."
            r"Python.3.12\python3.12.exe",
            5000,
        )

        self.assertFalse(packaged.development_scope)
        self.assertTrue(source.development_scope)
        self.assertTrue(store_source.development_scope)


class FirewallRuleComparisonTests(unittest.TestCase):
    def setUp(self):
        self.spec = FirewallRuleSpec(
            r"C:\Program Files\DeskFlow\DeskFlow.exe",
            5000,
        )

    def test_missing_rule_reports_setup_required(self):
        result = compare_firewall_rule(self.spec, None)

        self.assertEqual(
            result,
            FirewallInspection(FirewallState.MISSING, "rule_missing"),
        )

    def test_exact_packaged_rule_is_ready(self):
        result = compare_firewall_rule(self.spec, matching_rule(self.spec))

        self.assertEqual(result.state, FirewallState.READY)
        self.assertEqual(result.reason_code, "rule_ready")

    def test_exact_python_rule_reports_development_scope(self):
        spec = FirewallRuleSpec(r"C:\Python314\python.exe", 5000)

        result = compare_firewall_rule(spec, matching_rule(spec))

        self.assertEqual(result.state, FirewallState.DEVELOPMENT)
        self.assertEqual(result.reason_code, "python_scope")

    def test_executable_comparison_is_normalized_and_case_insensitive(self):
        observed = matching_rule(
            self.spec,
            application_name=r"c:\program files\deskflow\DESKFLOW.EXE",
        )

        self.assertEqual(
            compare_firewall_rule(self.spec, observed).state,
            FirewallState.READY,
        )

    def test_every_security_property_can_make_the_rule_stale(self):
        stale_values = {
            "name": "Another rule",
            "enabled": False,
            "direction": "outbound",
            "action": "block",
            "protocol": "udp",
            "local_ports": "5000-5003",
            "application_name": r"C:\Other\DeskFlow.exe",
            "profiles": frozenset({"private", "public"}),
            "remote_addresses": frozenset({"any"}),
            "edge_traversal": True,
        }

        for field, value in stale_values.items():
            with self.subTest(field=field):
                observed = matching_rule(self.spec, **{field: value})
                result = compare_firewall_rule(self.spec, observed)
                self.assertEqual(result.state, FirewallState.STALE)
                self.assertEqual(result.reason_code, f"stale_{field}")


class EffectiveFirewallContractTests(unittest.TestCase):
    def setUp(self):
        self.spec = FirewallRuleSpec(
            r"C:\Program Files\DeskFlow\DeskFlow.exe",
            5000,
        )

    def block_rule(self, **changes):
        values = {
            "enabled": True,
            "direction": "inbound",
            "action": "block",
            "protocol": "tcp",
            "local_ports": "any",
            "application_name": self.spec.executable_path,
            "profiles": frozenset({"private"}),
            "remote_addresses": frozenset({"any"}),
        }
        values.update(changes)
        return SimpleNamespace(**values)

    def test_effective_policy_contract_exposes_safe_states_and_matcher(self):
        self.assertTrue(hasattr(FirewallState, "CONFLICT"))
        self.assertTrue(hasattr(FirewallState, "PUBLIC_ONLY"))
        self.assertTrue(hasattr(firewall, "block_rule_conflicts"))
        self.assertTrue(hasattr(firewall, "evaluate_effective_firewall"))

    def test_tcp_and_any_protocol_port_expressions_overlap(self):
        overlapping = (
            ("tcp", ""),
            ("tcp", "*"),
            ("tcp", "Any"),
            ("tcp", "5000"),
            ("tcp", "4999-5001"),
            ("tcp", "80, 443, 5002"),
            ("any", "5000-5002"),
        )

        for protocol, ports in overlapping:
            with self.subTest(protocol=protocol, ports=ports):
                self.assertTrue(
                    firewall.block_rule_conflicts(
                        self.spec,
                        self.block_rule(protocol=protocol, local_ports=ports),
                    )
                )

    def test_irrelevant_rules_do_not_conflict(self):
        irrelevant = (
            {"enabled": False},
            {"direction": "outbound"},
            {"action": "allow"},
            {"protocol": "udp"},
            {"local_ports": "4990-4999"},
            {"application_name": r"C:\Other\DeskFlow.exe"},
            {"profiles": frozenset({"public"})},
        )

        for changes in irrelevant:
            with self.subTest(changes=changes):
                self.assertFalse(
                    firewall.block_rule_conflicts(
                        self.spec,
                        self.block_rule(**changes),
                    )
                )

    def test_matching_rule_with_malformed_ports_is_indeterminate(self):
        for ports in ("5000-", "later", "5002-5000", "0", "65536"):
            with self.subTest(ports=ports):
                with self.assertRaises(ValueError):
                    firewall.block_rule_conflicts(
                        self.spec,
                        self.block_rule(local_ports=ports),
                    )

    def test_effective_policy_reports_conflict_with_safe_count(self):
        result = firewall.evaluate_effective_firewall(
            self.spec,
            FirewallInspection(FirewallState.READY, "rule_ready"),
            [self.block_rule()],
            frozenset({"private"}),
        )

        self.assertEqual(result.state, FirewallState.CONFLICT)
        self.assertEqual(result.reason_code, "block_conflict")
        self.assertEqual(getattr(result, "conflict_count", None), 1)
        self.assertFalse(hasattr(result, "repairable"))

    def test_effective_policy_does_not_guess_rule_origin(self):
        result = firewall.evaluate_effective_firewall(
            self.spec,
            FirewallInspection(FirewallState.DEVELOPMENT, "python_scope"),
            [self.block_rule()],
            frozenset({"private"}),
        )

        self.assertEqual(result.state, FirewallState.CONFLICT)
        self.assertEqual(result.reason_code, "block_conflict")

    def test_effective_policy_reports_public_only_without_private_profile(self):
        result = firewall.evaluate_effective_firewall(
            self.spec,
            FirewallInspection(FirewallState.READY, "rule_ready"),
            [],
            frozenset({"public"}),
        )

        self.assertEqual(result.state, FirewallState.PUBLIC_ONLY)
        self.assertEqual(result.reason_code, "private_profile_inactive")

    def test_nonready_allow_state_is_preserved(self):
        missing = FirewallInspection(FirewallState.MISSING, "rule_missing")

        result = firewall.evaluate_effective_firewall(
            self.spec,
            missing,
            [self.block_rule()],
            frozenset({"public"}),
        )

        self.assertIs(result, missing)

    def test_malformed_relevant_block_never_reports_ready(self):
        result = firewall.evaluate_effective_firewall(
            self.spec,
            FirewallInspection(FirewallState.READY, "rule_ready"),
            [self.block_rule(local_ports="broken")],
            frozenset({"private"}),
        )

        self.assertEqual(result.state, FirewallState.UNAVAILABLE)
        self.assertEqual(result.reason_code, "block_rule_unreadable")


if __name__ == "__main__":
    unittest.main()
