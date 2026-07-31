import unittest

from app.firewall import (
    DESKFLOW_FIREWALL_RULE_NAME,
    FirewallRuleSpec,
    FirewallState,
)
from app.windows_firewall import WindowsFirewallBackend


class FakeRule:
    def __init__(self, **values):
        self.Name = values.get("Name", "")
        self.Description = values.get("Description", "")
        self.Enabled = values.get("Enabled", False)
        self.Direction = values.get("Direction", 0)
        self.Action = values.get("Action", 0)
        self.Protocol = values.get("Protocol", 0)
        self.LocalPorts = values.get("LocalPorts", "")
        self.ApplicationName = values.get("ApplicationName", "")
        self.Profiles = values.get("Profiles", 0)
        self.RemoteAddresses = values.get("RemoteAddresses", "")
        self.EdgeTraversal = values.get("EdgeTraversal", True)
        self.Grouping = values.get("Grouping", "")


class FakeRules:
    def __init__(self):
        self.items = {}
        self.added = []
        self.removed = []
        self.add_error = None
        self.mutate_on_add = None

    def Item(self, name):
        if name not in self.items:
            raise KeyError(name)
        return self.items[name]

    def Add(self, rule):
        if self.add_error:
            raise self.add_error
        if self.mutate_on_add:
            self.mutate_on_add(rule)
        self.items[rule.Name] = rule
        self.added.append(rule)

    def Remove(self, name):
        self.removed.append(name)
        if name not in self.items:
            raise KeyError(name)
        del self.items[name]


class FakePolicy:
    def __init__(self, rules=None):
        self.Rules = rules or FakeRules()


class WindowsFirewallBackendTests(unittest.TestCase):
    def setUp(self):
        self.spec = FirewallRuleSpec(
            r"C:\Program Files\DeskFlow\DeskFlow.exe",
            5000,
        )
        self.rules = FakeRules()
        self.policy = FakePolicy(self.rules)
        self.backend = WindowsFirewallBackend(
            policy_factory=lambda: self.policy,
            rule_factory=FakeRule,
        )

    def test_missing_rule_is_reported_without_mutation(self):
        result = self.backend.inspect(self.spec)

        self.assertEqual(result.state, FirewallState.MISSING)
        self.assertEqual(self.rules.added, [])
        self.assertEqual(self.rules.removed, [])

    def test_windows_com_missing_rule_is_reported_as_missing(self):
        class MissingComRuleError(Exception):
            hresult = -2147352567
            excepinfo = (0, None, None, None, 0, -2147024894)

        class MissingComRules(FakeRules):
            def Item(self, name):
                raise MissingComRuleError("Exception occurred.")

        backend = WindowsFirewallBackend(
            policy_factory=lambda: FakePolicy(MissingComRules()),
            rule_factory=FakeRule,
        )

        result = backend.inspect(self.spec)

        self.assertEqual(result.state, FirewallState.MISSING)
        self.assertEqual(result.reason_code, "rule_missing")

    def test_matching_numeric_com_rule_is_ready(self):
        self.rules.items[DESKFLOW_FIREWALL_RULE_NAME] = FakeRule(
            Name=DESKFLOW_FIREWALL_RULE_NAME,
            Enabled=True,
            Direction=1,
            Action=1,
            Protocol=6,
            LocalPorts="5000-5002",
            ApplicationName=r"C:\Program Files\DeskFlow\DeskFlow.exe",
            Profiles=2,
            RemoteAddresses="LocalSubnet",
            EdgeTraversal=False,
        )

        result = self.backend.inspect(self.spec)

        self.assertEqual(result.state, FirewallState.READY)

    def test_install_builds_the_complete_private_local_subnet_rule(self):
        result = self.backend.install_or_replace(self.spec)

        self.assertEqual(result.state, FirewallState.READY)
        self.assertEqual(len(self.rules.added), 1)
        rule = self.rules.added[0]
        self.assertEqual(rule.Name, DESKFLOW_FIREWALL_RULE_NAME)
        self.assertTrue(rule.Enabled)
        self.assertEqual(rule.Direction, 1)
        self.assertEqual(rule.Action, 1)
        self.assertEqual(rule.Protocol, 6)
        self.assertEqual(rule.LocalPorts, "5000-5002")
        self.assertEqual(
            rule.ApplicationName,
            r"C:\Program Files\DeskFlow\DeskFlow.exe",
        )
        self.assertEqual(rule.Profiles, 2)
        self.assertEqual(rule.RemoteAddresses, "LocalSubnet")
        self.assertFalse(rule.EdgeTraversal)
        self.assertEqual(rule.Grouping, "DeskFlow")

    def test_install_replaces_only_the_stable_deskflow_rule(self):
        old = FakeRule(Name=DESKFLOW_FIREWALL_RULE_NAME)
        unrelated = FakeRule(Name="Unrelated application")
        self.rules.items[old.Name] = old
        self.rules.items[unrelated.Name] = unrelated

        result = self.backend.install_or_replace(self.spec)

        self.assertEqual(result.state, FirewallState.READY)
        self.assertEqual(
            self.rules.removed,
            [DESKFLOW_FIREWALL_RULE_NAME],
        )
        self.assertIs(
            self.rules.items["Unrelated application"],
            unrelated,
        )

    def test_failed_add_cleans_up_only_the_deskflow_rule(self):
        self.rules.add_error = RuntimeError("private COM detail")

        result = self.backend.install_or_replace(self.spec)

        self.assertEqual(result.state, FirewallState.UNAVAILABLE)
        self.assertNotIn(DESKFLOW_FIREWALL_RULE_NAME, self.rules.items)
        self.assertIn(DESKFLOW_FIREWALL_RULE_NAME, self.rules.removed)

    def test_failed_verification_removes_the_partial_rule(self):
        self.rules.mutate_on_add = lambda rule: setattr(
            rule,
            "RemoteAddresses",
            "*",
        )

        result = self.backend.install_or_replace(self.spec)

        self.assertEqual(result.state, FirewallState.UNAVAILABLE)
        self.assertEqual(result.reason_code, "verification_failed")
        self.assertNotIn(DESKFLOW_FIREWALL_RULE_NAME, self.rules.items)

    def test_remove_is_idempotent(self):
        first = self.backend.remove()
        self.rules.items[DESKFLOW_FIREWALL_RULE_NAME] = FakeRule(
            Name=DESKFLOW_FIREWALL_RULE_NAME
        )
        second = self.backend.remove()

        self.assertEqual(first.state, FirewallState.MISSING)
        self.assertEqual(second.state, FirewallState.MISSING)
        self.assertNotIn(DESKFLOW_FIREWALL_RULE_NAME, self.rules.items)

    def test_access_denied_is_mapped_to_managed_without_private_text(self):
        class DeniedRules(FakeRules):
            def Item(self, name):
                raise PermissionError(5, "private policy path")

        backend = WindowsFirewallBackend(
            policy_factory=lambda: FakePolicy(DeniedRules()),
            rule_factory=FakeRule,
        )

        result = backend.inspect(self.spec)

        self.assertEqual(result.state, FirewallState.MANAGED)
        self.assertEqual(result.reason_code, "policy_denied")
        self.assertNotIn("private", result.reason_code)

    def test_other_com_error_is_mapped_to_unavailable(self):
        class BrokenRules(FakeRules):
            def Item(self, name):
                raise RuntimeError("private policy dump")

        backend = WindowsFirewallBackend(
            policy_factory=lambda: FakePolicy(BrokenRules()),
            rule_factory=FakeRule,
        )

        result = backend.inspect(self.spec)

        self.assertEqual(result.state, FirewallState.UNAVAILABLE)
        self.assertEqual(result.reason_code, "inspection_failed")


if __name__ == "__main__":
    unittest.main()
