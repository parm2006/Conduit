import unittest

from app.firewall import (
    CONDUIT_FIREWALL_RULE_NAME,
    FirewallRuleSpec,
    FirewallState,
)
from app.windows_firewall import WindowsFirewallBackend


class FakeRule:
    def __init__(self, **values):
        self.Name = values.get("Name", "")
        self.Description = values.get("Description", "")
        self._enabled = values.get("Enabled", False)
        self.enabled_set_errors = []
        self.Direction = values.get("Direction", 0)
        self.Action = values.get("Action", 0)
        self.Protocol = values.get("Protocol", 0)
        self.LocalPorts = values.get("LocalPorts", "")
        self.ApplicationName = values.get("ApplicationName", "")
        self.Profiles = values.get("Profiles", 0)
        self.RemoteAddresses = values.get("RemoteAddresses", "")
        self.EdgeTraversal = values.get("EdgeTraversal", True)
        self.Grouping = values.get("Grouping", "")

    @property
    def Enabled(self):
        return self._enabled

    @Enabled.setter
    def Enabled(self, value):
        if self.enabled_set_errors:
            error = self.enabled_set_errors.pop(0)
            if error is not None:
                raise error
        self._enabled = value


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

    def __iter__(self):
        return iter(self.items.values())

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
    def __init__(self, rules=None, current_profiles=2):
        self.Rules = rules or FakeRules()
        self.CurrentProfileTypes = current_profiles


class WindowsFirewallBackendTests(unittest.TestCase):
    def setUp(self):
        self.spec = FirewallRuleSpec(
            r"C:\Program Files\Conduit\Conduit.exe",
            5000,
        )
        self.rules = FakeRules()
        self.policy = FakePolicy(self.rules)
        self.backend = WindowsFirewallBackend(
            policy_factory=lambda: self.policy,
            rule_factory=FakeRule,
        )

    def add_matching_allow(self):
        self.rules.items[CONDUIT_FIREWALL_RULE_NAME] = FakeRule(
            Name=CONDUIT_FIREWALL_RULE_NAME,
            Enabled=True,
            Direction=1,
            Action=1,
            Protocol=6,
            LocalPorts="5000-5002",
            ApplicationName=r"C:\Program Files\Conduit\Conduit.exe",
            Profiles=2,
            RemoteAddresses="LocalSubnet",
            EdgeTraversal=False,
        )

    def add_matching_block(self, name="Python TCP block"):
        rule = FakeRule(
            Name=name,
            Enabled=True,
            Direction=1,
            Action=0,
            Protocol=6,
            LocalPorts="Any",
            ApplicationName=r"C:\Program Files\Conduit\Conduit.exe",
            Profiles=2,
            RemoteAddresses="Any",
            EdgeTraversal=False,
        )
        self.rules.items[name] = rule
        return rule

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
        self.add_matching_allow()

        result = self.backend.inspect(self.spec)

        self.assertEqual(result.state, FirewallState.READY)

    def test_matching_block_overrides_ready_allow_without_mutation(self):
        self.add_matching_allow()
        self.rules.items["Python TCP block"] = FakeRule(
            Name="Python TCP block",
            Enabled=True,
            Direction=1,
            Action=0,
            Protocol=6,
            LocalPorts="Any",
            ApplicationName=r"C:\Program Files\Conduit\Conduit.exe",
            Profiles=2,
            RemoteAddresses="Any",
            EdgeTraversal=False,
        )

        result = self.backend.inspect(self.spec)

        self.assertEqual(result.state, FirewallState.CONFLICT)
        self.assertEqual(result.reason_code, "block_conflict")
        self.assertEqual(result.conflict_count, 1)
        self.assertEqual(self.rules.added, [])
        self.assertEqual(self.rules.removed, [])

    def test_any_protocol_block_can_override_ready_allow(self):
        self.add_matching_allow()
        self.rules.items["Any protocol block"] = FakeRule(
            Name="Any protocol block",
            Enabled=True,
            Direction=1,
            Action=0,
            Protocol=256,
            LocalPorts="5001",
            ApplicationName=r"C:\Program Files\Conduit\Conduit.exe",
            Profiles=2,
            RemoteAddresses="LocalSubnet",
            EdgeTraversal=False,
        )

        result = self.backend.inspect(self.spec)

        self.assertEqual(result.state, FirewallState.CONFLICT)

    def test_unrelated_block_does_not_override_ready_allow(self):
        self.add_matching_allow()
        self.rules.items["Other app block"] = FakeRule(
            Name="Other app block",
            Enabled=True,
            Direction=1,
            Action=0,
            Protocol=6,
            LocalPorts="Any",
            ApplicationName=r"C:\Other\Other.exe",
            Profiles=2,
            RemoteAddresses="Any",
            EdgeTraversal=False,
        )

        result = self.backend.inspect(self.spec)

        self.assertEqual(result.state, FirewallState.READY)

    def test_loopback_only_block_is_not_disabled_as_a_lan_conflict(self):
        self.add_matching_allow()
        block = self.add_matching_block("Loopback-only block")
        block.RemoteAddresses = "127.0.0.1,::1"

        inspection = self.backend.inspect(self.spec)
        repair = self.backend.repair(self.spec)

        self.assertEqual(inspection.state, FirewallState.READY)
        self.assertEqual(repair.state, FirewallState.READY)
        self.assertTrue(block.Enabled)

    def test_unreadable_property_on_irrelevant_rule_is_ignored(self):
        class IrrelevantRule(FakeRule):
            def __getattribute__(self, name):
                if name == "LocalPorts":
                    raise RuntimeError("not valid for this protocol")
                return super().__getattribute__(name)

        self.add_matching_allow()
        self.rules.items["Irrelevant UDP rule"] = IrrelevantRule(
            Name="Irrelevant UDP rule",
            Enabled=True,
            Direction=1,
            Action=0,
            Protocol=17,
            ApplicationName=r"C:\Other\Other.exe",
            Profiles=2,
        )

        result = self.backend.inspect(self.spec)

        self.assertEqual(result.state, FirewallState.READY)

    def test_public_only_active_profile_never_reports_ready(self):
        self.add_matching_allow()
        self.policy.CurrentProfileTypes = 4

        result = self.backend.inspect(self.spec)

        self.assertEqual(result.state, FirewallState.PUBLIC_ONLY)
        self.assertEqual(result.reason_code, "private_profile_inactive")

    def test_malformed_matching_block_never_reports_ready(self):
        self.add_matching_allow()
        self.rules.items["Malformed block"] = FakeRule(
            Name="Malformed block",
            Enabled=True,
            Direction=1,
            Action=0,
            Protocol=6,
            LocalPorts="broken",
            ApplicationName=r"C:\Program Files\Conduit\Conduit.exe",
            Profiles=2,
            RemoteAddresses="Any",
            EdgeTraversal=False,
        )

        result = self.backend.inspect(self.spec)

        self.assertEqual(result.state, FirewallState.UNAVAILABLE)
        self.assertEqual(result.reason_code, "block_rule_unreadable")

    def test_rule_enumeration_failure_is_safely_unavailable(self):
        class BrokenIterationRules(FakeRules):
            def __iter__(self):
                raise RuntimeError("private policy detail")

        rules = BrokenIterationRules()
        policy = FakePolicy(rules)
        backend = WindowsFirewallBackend(
            policy_factory=lambda: policy,
            rule_factory=FakeRule,
        )
        rules.items[CONDUIT_FIREWALL_RULE_NAME] = FakeRule(
            Name=CONDUIT_FIREWALL_RULE_NAME,
            Enabled=True,
            Direction=1,
            Action=1,
            Protocol=6,
            LocalPorts="5000-5002",
            ApplicationName=r"C:\Program Files\Conduit\Conduit.exe",
            Profiles=2,
            RemoteAddresses="LocalSubnet",
            EdgeTraversal=False,
        )

        result = backend.inspect(self.spec)

        self.assertEqual(result.state, FirewallState.UNAVAILABLE)
        self.assertEqual(result.reason_code, "inspection_failed")

    def test_install_builds_the_complete_private_local_subnet_rule(self):
        result = self.backend.install_or_replace(self.spec)

        self.assertEqual(result.state, FirewallState.READY)
        self.assertEqual(len(self.rules.added), 1)
        rule = self.rules.added[0]
        self.assertEqual(rule.Name, CONDUIT_FIREWALL_RULE_NAME)
        self.assertTrue(rule.Enabled)
        self.assertEqual(rule.Direction, 1)
        self.assertEqual(rule.Action, 1)
        self.assertEqual(rule.Protocol, 6)
        self.assertEqual(rule.LocalPorts, "5000-5002")
        self.assertEqual(
            rule.ApplicationName,
            r"C:\Program Files\Conduit\Conduit.exe",
        )
        self.assertEqual(rule.Profiles, 2)
        self.assertEqual(rule.RemoteAddresses, "LocalSubnet")
        self.assertFalse(rule.EdgeTraversal)
        self.assertEqual(rule.Grouping, "Conduit")

    def test_install_replaces_only_the_stable_conduit_rule(self):
        old = FakeRule(Name=CONDUIT_FIREWALL_RULE_NAME)
        unrelated = FakeRule(Name="Unrelated application")
        self.rules.items[old.Name] = old
        self.rules.items[unrelated.Name] = unrelated

        result = self.backend.install_or_replace(self.spec)

        self.assertEqual(result.state, FirewallState.READY)
        self.assertEqual(
            self.rules.removed,
            [CONDUIT_FIREWALL_RULE_NAME],
        )
        self.assertIs(
            self.rules.items["Unrelated application"],
            unrelated,
        )

    def test_failed_add_cleans_up_only_the_conduit_rule(self):
        self.rules.add_error = RuntimeError("private COM detail")

        result = self.backend.install_or_replace(self.spec)

        self.assertEqual(result.state, FirewallState.UNAVAILABLE)
        self.assertNotIn(CONDUIT_FIREWALL_RULE_NAME, self.rules.items)
        self.assertIn(CONDUIT_FIREWALL_RULE_NAME, self.rules.removed)

    def test_failed_verification_removes_the_partial_rule(self):
        self.rules.mutate_on_add = lambda rule: setattr(
            rule,
            "RemoteAddresses",
            "*",
        )

        result = self.backend.install_or_replace(self.spec)

        self.assertEqual(result.state, FirewallState.UNAVAILABLE)
        self.assertEqual(result.reason_code, "verification_failed")
        self.assertNotIn(CONDUIT_FIREWALL_RULE_NAME, self.rules.items)

    def test_install_preserves_exact_allow_when_effective_block_remains(self):
        conflict = self.add_matching_block()

        result = self.backend.install_or_replace(self.spec)

        self.assertEqual(result.state, FirewallState.CONFLICT)
        self.assertTrue(conflict.Enabled)
        self.assertIn(CONDUIT_FIREWALL_RULE_NAME, self.rules.items)

    def test_install_preserves_private_allow_but_never_starts_on_public(self):
        self.policy.CurrentProfileTypes = 4

        result = self.backend.install_or_replace(self.spec)

        self.assertEqual(result.state, FirewallState.PUBLIC_ONLY)
        self.assertIn(CONDUIT_FIREWALL_RULE_NAME, self.rules.items)

    def test_remove_is_idempotent(self):
        first = self.backend.remove()
        self.rules.items[CONDUIT_FIREWALL_RULE_NAME] = FakeRule(
            Name=CONDUIT_FIREWALL_RULE_NAME
        )
        second = self.backend.remove()

        self.assertEqual(first.state, FirewallState.MISSING)
        self.assertEqual(second.state, FirewallState.MISSING)
        self.assertNotIn(CONDUIT_FIREWALL_RULE_NAME, self.rules.items)

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

    def test_repair_disables_exact_conflict_object_and_preserves_allow(self):
        self.add_matching_allow()
        allow_rule = self.rules.items[CONDUIT_FIREWALL_RULE_NAME]
        conflict = self.add_matching_block()
        unrelated = FakeRule(Name="Unrelated", Enabled=True)
        self.rules.items[unrelated.Name] = unrelated

        result = self.backend.repair(self.spec)

        self.assertEqual(result.state, FirewallState.READY)
        self.assertFalse(conflict.Enabled)
        self.assertTrue(unrelated.Enabled)
        self.assertIs(
            self.rules.items[CONDUIT_FIREWALL_RULE_NAME],
            allow_rule,
        )
        self.assertNotIn(conflict.Name, self.rules.removed)

    def test_repair_can_create_missing_allow_rule(self):
        conflict = self.add_matching_block()

        result = self.backend.repair(self.spec)

        self.assertEqual(result.state, FirewallState.READY)
        self.assertFalse(conflict.Enabled)
        self.assertIn(CONDUIT_FIREWALL_RULE_NAME, self.rules.items)
        self.assertEqual(len(self.rules.added), 1)

    def test_repair_updates_stale_conduit_rule_for_packaged_executable(self):
        self.add_matching_allow()
        stale = self.rules.items[CONDUIT_FIREWALL_RULE_NAME]
        stale.ApplicationName = r"C:\Python314\python.exe"

        result = self.backend.repair(self.spec)

        self.assertEqual(result.state, FirewallState.READY)
        self.assertIs(
            self.rules.items[CONDUIT_FIREWALL_RULE_NAME],
            stale,
        )
        self.assertEqual(stale.ApplicationName, self.spec.executable_path)
        self.assertEqual(stale.LocalPorts, self.spec.local_ports)
        self.assertEqual(self.rules.removed, [])

    def test_stale_rule_is_restored_when_repair_verification_fails(self):
        self.add_matching_allow()
        stale = self.rules.items[CONDUIT_FIREWALL_RULE_NAME]
        stale.ApplicationName = r"C:\Python314\python.exe"
        stale.LocalPorts = "5000"
        calls = 0

        def policy_factory():
            nonlocal calls
            calls += 1
            if calls == 1:
                return self.policy
            return FakePolicy(self.rules, current_profiles=4)

        backend = WindowsFirewallBackend(
            policy_factory=policy_factory,
            rule_factory=FakeRule,
        )

        result = backend.repair(self.spec)

        self.assertEqual(result.state, FirewallState.UNAVAILABLE)
        self.assertEqual(result.reason_code, "verification_failed")
        self.assertEqual(stale.ApplicationName, r"C:\Python314\python.exe")
        self.assertEqual(stale.LocalPorts, "5000")

    def test_stale_rule_restore_failure_is_reported(self):
        self.add_matching_allow()
        stale = self.rules.items[CONDUIT_FIREWALL_RULE_NAME]
        stale.ApplicationName = r"C:\Python314\python.exe"
        stale._enabled = False
        stale.enabled_set_errors = [None, RuntimeError("private")]
        calls = 0

        def policy_factory():
            nonlocal calls
            calls += 1
            if calls == 1:
                return self.policy
            return FakePolicy(self.rules, current_profiles=4)

        backend = WindowsFirewallBackend(
            policy_factory=policy_factory,
            rule_factory=FakeRule,
        )

        result = backend.repair(self.spec)

        self.assertEqual(result.state, FirewallState.UNAVAILABLE)
        self.assertEqual(result.reason_code, "rollback_failed")

    def test_repair_on_public_creates_private_allow_without_disabling_block(self):
        self.policy.CurrentProfileTypes = 4
        conflict = self.add_matching_block()

        result = self.backend.repair(self.spec)

        self.assertEqual(result.state, FirewallState.PUBLIC_ONLY)
        self.assertTrue(conflict.Enabled)
        self.assertEqual(len(self.rules.added), 1)
        self.assertIn(CONDUIT_FIREWALL_RULE_NAME, self.rules.items)

    def test_second_disable_failure_rolls_back_first_exact_object(self):
        self.add_matching_allow()
        first = self.add_matching_block("First block")
        second = self.add_matching_block("Second block")
        second.enabled_set_errors = [PermissionError(5, "private")]

        result = self.backend.repair(self.spec)

        self.assertEqual(result.state, FirewallState.MANAGED)
        self.assertTrue(first.Enabled)
        self.assertTrue(second.Enabled)
        self.assertEqual(self.rules.added, [])

    def test_allow_creation_failure_reenables_disabled_conflict(self):
        conflict = self.add_matching_block()
        self.rules.add_error = RuntimeError("private")

        result = self.backend.repair(self.spec)

        self.assertEqual(result.state, FirewallState.UNAVAILABLE)
        self.assertEqual(result.reason_code, "configuration_failed")
        self.assertTrue(conflict.Enabled)

    def test_verification_failure_removes_new_allow_and_restores_block(self):
        conflict = self.add_matching_block()
        calls = 0

        def policy_factory():
            nonlocal calls
            calls += 1
            if calls == 1:
                return self.policy
            return FakePolicy(self.rules, current_profiles=4)

        backend = WindowsFirewallBackend(
            policy_factory=policy_factory,
            rule_factory=FakeRule,
        )

        result = backend.repair(self.spec)

        self.assertEqual(result.state, FirewallState.UNAVAILABLE)
        self.assertEqual(result.reason_code, "verification_failed")
        self.assertTrue(conflict.Enabled)
        self.assertNotIn(CONDUIT_FIREWALL_RULE_NAME, self.rules.items)

    def test_incomplete_reenable_reports_distinct_rollback_failure(self):
        self.add_matching_allow()
        conflict = self.add_matching_block()
        conflict.enabled_set_errors = [None, RuntimeError("private")]
        calls = 0

        def policy_factory():
            nonlocal calls
            calls += 1
            if calls == 1:
                return self.policy
            return FakePolicy(self.rules, current_profiles=4)

        backend = WindowsFirewallBackend(
            policy_factory=policy_factory,
            rule_factory=FakeRule,
        )

        result = backend.repair(self.spec)

        self.assertEqual(result.state, FirewallState.UNAVAILABLE)
        self.assertEqual(result.reason_code, "rollback_failed")

    def test_malformed_relevant_block_prevents_repair_without_mutation(self):
        self.add_matching_allow()
        conflict = self.add_matching_block()
        conflict.LocalPorts = "broken"

        result = self.backend.repair(self.spec)

        self.assertEqual(result.state, FirewallState.UNAVAILABLE)
        self.assertEqual(result.reason_code, "block_rule_unreadable")
        self.assertTrue(conflict.Enabled)


if __name__ == "__main__":
    unittest.main()
