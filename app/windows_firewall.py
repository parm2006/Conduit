"""Windows Firewall COM boundary for the DeskFlow-owned inbound rule."""

from app.firewall import (
    DESKFLOW_FIREWALL_RULE_NAME,
    FirewallInspection,
    FirewallState,
    ObservedFirewallRule,
    compare_firewall_rule,
)


_NET_FW_RULE_DIR_IN = 1
_NET_FW_ACTION_ALLOW = 1
_NET_FW_IP_PROTOCOL_TCP = 6
_NET_FW_PROFILE2_DOMAIN = 1
_NET_FW_PROFILE2_PRIVATE = 2
_NET_FW_PROFILE2_PUBLIC = 4


def _default_policy_factory():
    import win32com.client

    return win32com.client.Dispatch("HNetCfg.FwPolicy2")


def _default_rule_factory():
    import win32com.client

    return win32com.client.Dispatch("HNetCfg.FWRule")


def _is_missing(error):
    if isinstance(error, KeyError):
        return True
    code = getattr(error, "winerror", None)
    hresult = getattr(error, "hresult", None)
    return code in {2, 1168} or hresult in {
        -2147024894,  # 0x80070002
        -2147023728,  # 0x80070490
    }


def _is_access_denied(error):
    code = getattr(error, "winerror", None)
    hresult = getattr(error, "hresult", None)
    if isinstance(error, PermissionError):
        return True
    return code == 5 or hresult == -2147024891  # 0x80070005


def _failure_inspection(error, fallback):
    if _is_access_denied(error):
        return FirewallInspection(FirewallState.MANAGED, "policy_denied")
    return FirewallInspection(FirewallState.UNAVAILABLE, fallback)


def _profiles_from_mask(mask):
    profiles = set()
    if mask & _NET_FW_PROFILE2_DOMAIN:
        profiles.add("domain")
    if mask & _NET_FW_PROFILE2_PRIVATE:
        profiles.add("private")
    if mask & _NET_FW_PROFILE2_PUBLIC:
        profiles.add("public")
    return frozenset(profiles)


def _address_set(value):
    return frozenset(
        part.strip().casefold()
        for part in str(value).split(",")
        if part.strip()
    )


class WindowsFirewallBackend:
    def __init__(self, policy_factory=None, rule_factory=None):
        self.policy_factory = policy_factory or _default_policy_factory
        self.rule_factory = rule_factory or _default_rule_factory

    def inspect(self, spec):
        try:
            rules = self.policy_factory().Rules
            try:
                rule = rules.Item(DESKFLOW_FIREWALL_RULE_NAME)
            except Exception as error:
                if _is_missing(error):
                    return FirewallInspection(
                        FirewallState.MISSING,
                        "rule_missing",
                    )
                return _failure_inspection(error, "inspection_failed")
            observed = ObservedFirewallRule(
                name=str(rule.Name),
                enabled=bool(rule.Enabled),
                direction=(
                    "inbound"
                    if rule.Direction == _NET_FW_RULE_DIR_IN
                    else "outbound"
                ),
                action=(
                    "allow"
                    if rule.Action == _NET_FW_ACTION_ALLOW
                    else "block"
                ),
                protocol=(
                    "tcp"
                    if rule.Protocol == _NET_FW_IP_PROTOCOL_TCP
                    else str(rule.Protocol)
                ),
                local_ports=str(rule.LocalPorts),
                application_name=str(rule.ApplicationName),
                profiles=_profiles_from_mask(int(rule.Profiles)),
                remote_addresses=_address_set(rule.RemoteAddresses),
                edge_traversal=bool(rule.EdgeTraversal),
            )
            return compare_firewall_rule(spec, observed)
        except Exception as error:
            return _failure_inspection(error, "inspection_failed")

    def install_or_replace(self, spec):
        try:
            rules = self.policy_factory().Rules
            removal = self._remove_from(rules)
            if removal is not None:
                return removal

            rule = self.rule_factory()
            rule.Name = DESKFLOW_FIREWALL_RULE_NAME
            rule.Description = (
                f"Allow DeskFlow Server on private local networks "
                f"(TCP {spec.local_ports})."
            )
            rule.Grouping = "DeskFlow"
            rule.Protocol = _NET_FW_IP_PROTOCOL_TCP
            rule.LocalPorts = spec.local_ports
            rule.ApplicationName = spec.executable_path
            rule.Profiles = _NET_FW_PROFILE2_PRIVATE
            rule.RemoteAddresses = "LocalSubnet"
            rule.Direction = _NET_FW_RULE_DIR_IN
            rule.Action = _NET_FW_ACTION_ALLOW
            rule.EdgeTraversal = False
            rule.Enabled = True
            rules.Add(rule)
        except Exception as error:
            self._cleanup_after_failure()
            return _failure_inspection(error, "configuration_failed")

        result = self.inspect(spec)
        if result.state not in {
            FirewallState.READY,
            FirewallState.DEVELOPMENT,
        }:
            self._cleanup_after_failure()
            return FirewallInspection(
                FirewallState.UNAVAILABLE,
                "verification_failed",
            )
        return result

    def remove(self):
        try:
            rules = self.policy_factory().Rules
            result = self._remove_from(rules)
            if result is not None:
                return result
            return FirewallInspection(FirewallState.MISSING, "rule_missing")
        except Exception as error:
            return _failure_inspection(error, "removal_failed")

    @staticmethod
    def _remove_from(rules):
        try:
            rules.Remove(DESKFLOW_FIREWALL_RULE_NAME)
        except Exception as error:
            if _is_missing(error):
                return None
            return _failure_inspection(error, "removal_failed")
        return None

    def _cleanup_after_failure(self):
        try:
            rules = self.policy_factory().Rules
            self._remove_from(rules)
        except Exception:
            pass
