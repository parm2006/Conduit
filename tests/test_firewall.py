import unittest

from app.firewall import (
    DESKFLOW_FIREWALL_RULE_NAME,
    FirewallInspection,
    FirewallRuleSpec,
    FirewallState,
    ObservedFirewallRule,
    compare_firewall_rule,
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

        self.assertFalse(packaged.development_scope)
        self.assertTrue(source.development_scope)


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


if __name__ == "__main__":
    unittest.main()
