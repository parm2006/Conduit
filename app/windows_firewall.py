"""Windows Firewall COM boundary for the Conduit-owned inbound rule."""

from app.firewall import (
    CONDUIT_FIREWALL_RULE_NAME,
    CONDUIT_FIREWALL_RULE_NAMES,
    CONDUIT_FIREWALL_TCP_RULE_NAME,
    CONDUIT_FIREWALL_UDP_RULE_NAME,
    FirewallInspection,
    FirewallState,
    ObservedFirewallRule,
    block_rule_conflicts,
    compare_firewall_rule,
    evaluate_effective_firewall,
    executable_paths_match,
)


_NET_FW_RULE_DIR_IN = 1
_NET_FW_ACTION_BLOCK = 0
_NET_FW_ACTION_ALLOW = 1
_NET_FW_IP_PROTOCOL_TCP = 6
_NET_FW_IP_PROTOCOL_UDP = 17
_NET_FW_IP_PROTOCOL_ANY = 256
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
    excepinfo = getattr(error, "excepinfo", None)
    nested_hresult = (
        excepinfo[5]
        if isinstance(excepinfo, tuple) and len(excepinfo) > 5
        else None
    )
    return code in {2, 1168} or hresult in {
        -2147024894,  # 0x80070002
        -2147023728,  # 0x80070490
    } or nested_hresult in {
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


def _protocol_name(value):
    if value == _NET_FW_IP_PROTOCOL_TCP:
        return "tcp"
    if value == _NET_FW_IP_PROTOCOL_UDP:
        return "udp"
    if value == _NET_FW_IP_PROTOCOL_ANY:
        return "any"
    return str(value)


def _observed_rule(rule):
    return ObservedFirewallRule(
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
        protocol=_protocol_name(rule.Protocol),
        local_ports=str(rule.LocalPorts),
        application_name=str(rule.ApplicationName),
        profiles=_profiles_from_mask(int(rule.Profiles)),
        remote_addresses=_address_set(rule.RemoteAddresses),
        edge_traversal=bool(rule.EdgeTraversal),
    )


def _observed_block_rule(rule, spec):
    if not bool(rule.Enabled):
        return None
    if rule.Direction != _NET_FW_RULE_DIR_IN:
        return None
    if rule.Action != _NET_FW_ACTION_BLOCK:
        return None
    protocol = _protocol_name(rule.Protocol)
    if protocol not in {"tcp", "udp", "any"}:
        return None
    if not executable_paths_match(
        rule.ApplicationName,
        spec.executable_path,
    ):
        return None
    profiles = _profiles_from_mask(int(rule.Profiles))
    if "private" not in profiles:
        return None
    return ObservedFirewallRule(
        name=str(rule.Name),
        enabled=True,
        direction="inbound",
        action="block",
        protocol=protocol,
        local_ports=str(rule.LocalPorts),
        application_name=str(rule.ApplicationName),
        profiles=profiles,
        remote_addresses=_address_set(rule.RemoteAddresses),
        edge_traversal=False,
    )


def _observed_block_rules(rules, spec):
    for rule in rules:
        observed = _observed_block_rule(rule, spec)
        if observed is not None:
            yield observed


def _matching_block_rule_objects(rules, spec):
    for rule in rules:
        observed = _observed_block_rule(rule, spec)
        if observed is not None and block_rule_conflicts(spec, observed):
            yield rule


class WindowsFirewallBackend:
    def __init__(self, policy_factory=None, rule_factory=None):
        self.policy_factory = policy_factory or _default_policy_factory
        self.rule_factory = rule_factory or _default_rule_factory

    def _inspect_allow_rules(self, rules, spec):
        try:
            tcp_rule = rules.Item(CONDUIT_FIREWALL_TCP_RULE_NAME)
        except Exception as error:
            if _is_missing(error):
                tcp_inspection = FirewallInspection(
                    FirewallState.MISSING,
                    "rule_missing",
                )
            else:
                return None, None, _failure_inspection(error, "inspection_failed")
        else:
            tcp_inspection = compare_firewall_rule(
                spec,
                _observed_rule(tcp_rule),
                protocol="tcp",
            )

        try:
            udp_rule = rules.Item(CONDUIT_FIREWALL_UDP_RULE_NAME)
        except Exception as error:
            if _is_missing(error):
                udp_inspection = FirewallInspection(
                    FirewallState.MISSING,
                    "rule_missing",
                )
            else:
                return None, None, _failure_inspection(error, "inspection_failed")
        else:
            udp_inspection = compare_firewall_rule(
                spec,
                _observed_rule(udp_rule),
                protocol="udp",
            )

        if (
            tcp_inspection.state is FirewallState.MISSING
            or udp_inspection.state is FirewallState.MISSING
        ):
            combined = FirewallInspection(FirewallState.MISSING, "rule_missing")
        elif (
            tcp_inspection.state is FirewallState.STALE
            or udp_inspection.state is FirewallState.STALE
        ):
            reason = (
                tcp_inspection.reason_code
                if tcp_inspection.state is FirewallState.STALE
                else udp_inspection.reason_code
            )
            combined = FirewallInspection(FirewallState.STALE, reason)
        elif (
            tcp_inspection.state is FirewallState.DEVELOPMENT
            or udp_inspection.state is FirewallState.DEVELOPMENT
        ):
            combined = FirewallInspection(FirewallState.DEVELOPMENT, "python_scope")
        else:
            combined = FirewallInspection(FirewallState.READY, "rule_ready")

        return tcp_inspection, udp_inspection, combined

    def inspect(self, spec):
        try:
            policy = self.policy_factory()
            rules = policy.Rules
            _, _, allow_inspection = self._inspect_allow_rules(rules, spec)
            if allow_inspection is None or allow_inspection.state not in {
                FirewallState.READY,
                FirewallState.DEVELOPMENT,
            }:
                return allow_inspection
            return evaluate_effective_firewall(
                spec,
                allow_inspection,
                _observed_block_rules(rules, spec),
                _profiles_from_mask(int(policy.CurrentProfileTypes)),
            )
        except Exception as error:
            return _failure_inspection(error, "inspection_failed")

    def install_or_replace(self, spec):
        try:
            rules = self.policy_factory().Rules
            removal = self._remove_from(rules)
            if removal is not None:
                return removal

            self._add_allow_rules(rules, spec)
        except Exception as error:
            self._cleanup_after_failure()
            return _failure_inspection(error, "configuration_failed")

        result = self.inspect(spec)
        if result.state in {
            FirewallState.READY,
            FirewallState.DEVELOPMENT,
            FirewallState.CONFLICT,
            FirewallState.PUBLIC_ONLY,
        }:
            return result
        else:
            self._cleanup_after_failure()
            return FirewallInspection(
                FirewallState.UNAVAILABLE,
                "verification_failed",
            )

    def repair(self, spec):
        """Disable exact conflicting objects and verify the effective policy."""
        disabled_rules = []
        created_rules = []
        tcp_snapshot = None
        udp_snapshot = None
        try:
            policy = self.policy_factory()
            rules = policy.Rules
            active_profiles = _profiles_from_mask(
                int(policy.CurrentProfileTypes)
            )
            private_active = "private" in active_profiles

            tcp_inspection, udp_inspection, combined = self._inspect_allow_rules(
                rules, spec
            )
            if combined is None or combined.state not in {
                FirewallState.MISSING,
                FirewallState.READY,
                FirewallState.DEVELOPMENT,
                FirewallState.STALE,
            }:
                return combined

            conflicts = (
                list(_matching_block_rule_objects(rules, spec))
                if private_active
                else []
            )

            needs_allow_fix = combined.state in {
                FirewallState.MISSING,
                FirewallState.STALE,
            }
            if not needs_allow_fix and not conflicts:
                return self.inspect(spec)
        except (AttributeError, TypeError, ValueError):
            return FirewallInspection(
                FirewallState.UNAVAILABLE,
                "block_rule_unreadable",
            )
        except Exception as error:
            return _failure_inspection(error, "inspection_failed")

        snapshots = []
        try:
            for rule in conflicts:
                rule.Enabled = False
                disabled_rules.append(rule)

            if tcp_inspection.state is FirewallState.MISSING:
                rule = self.rule_factory()
                self._configure_tcp_rule(rule, spec)
                rules.Add(rule)
                created_rules.append(CONDUIT_FIREWALL_TCP_RULE_NAME)
            elif tcp_inspection.state is FirewallState.STALE:
                tcp_rule = rules.Item(CONDUIT_FIREWALL_TCP_RULE_NAME)
                tcp_snapshot = self._snapshot_allow_rule(tcp_rule)
                snapshots.append((CONDUIT_FIREWALL_TCP_RULE_NAME, tcp_snapshot))
                self._configure_tcp_rule(tcp_rule, spec)

            if udp_inspection.state is FirewallState.MISSING:
                rule = self.rule_factory()
                self._configure_udp_rule(rule, spec)
                rules.Add(rule)
                created_rules.append(CONDUIT_FIREWALL_UDP_RULE_NAME)
            elif udp_inspection.state is FirewallState.STALE:
                udp_rule = rules.Item(CONDUIT_FIREWALL_UDP_RULE_NAME)
                udp_snapshot = self._snapshot_allow_rule(udp_rule)
                snapshots.append((CONDUIT_FIREWALL_UDP_RULE_NAME, udp_snapshot))
                self._configure_udp_rule(udp_rule, spec)
        except Exception as error:
            if not self._rollback_repair(
                rules,
                disabled_rules,
                created_rules,
                snapshots=snapshots,
            ):
                return FirewallInspection(
                    FirewallState.UNAVAILABLE,
                    "rollback_failed",
                )
            return _failure_inspection(error, "configuration_failed")

        try:
            result = self.inspect(spec)
        except Exception as error:
            self._rollback_repair(
                rules,
                disabled_rules,
                created_rules,
                snapshots=snapshots,
            )
            return _failure_inspection(error, "configuration_failed")

        if result.state in {
            FirewallState.READY,
            FirewallState.DEVELOPMENT,
        } or (
            not private_active
            and result.state is FirewallState.PUBLIC_ONLY
        ):
            return result

        if not self._rollback_repair(
            rules,
            disabled_rules,
            created_rules,
            snapshots=snapshots,
        ):
            return FirewallInspection(
                FirewallState.UNAVAILABLE,
                "rollback_failed",
            )
        return (
            result
            if result.state is FirewallState.CONFLICT
            else FirewallInspection(
                FirewallState.UNAVAILABLE,
                "verification_failed",
            )
        )

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
        for name in CONDUIT_FIREWALL_RULE_NAMES:
            try:
                rules.Remove(name)
            except Exception as error:
                if _is_missing(error):
                    continue
                return _failure_inspection(error, "removal_failed")
        return None

    def _add_allow_rules(self, rules, spec):
        tcp_rule = self.rule_factory()
        self._configure_tcp_rule(tcp_rule, spec)
        rules.Add(tcp_rule)

        udp_rule = self.rule_factory()
        self._configure_udp_rule(udp_rule, spec)
        rules.Add(udp_rule)

    _add_allow_rule = _add_allow_rules

    @staticmethod
    def _snapshot_allow_rule(rule):
        fields = (
            "Description",
            "Grouping",
            "Protocol",
            "LocalPorts",
            "ApplicationName",
            "Profiles",
            "RemoteAddresses",
            "Direction",
            "Action",
            "EdgeTraversal",
            "Enabled",
        )
        return tuple((field, getattr(rule, field)) for field in fields)

    @staticmethod
    def _configure_tcp_rule(rule, spec):
        rule.Name = CONDUIT_FIREWALL_TCP_RULE_NAME
        rule.Description = (
            f"Allow Conduit Server on private local networks "
            f"(TCP {spec.tcp_ports})."
        )
        rule.Grouping = "Conduit"
        rule.Protocol = _NET_FW_IP_PROTOCOL_TCP
        rule.LocalPorts = spec.tcp_ports
        rule.ApplicationName = spec.executable_path
        rule.Profiles = _NET_FW_PROFILE2_PRIVATE
        rule.RemoteAddresses = "LocalSubnet"
        rule.Direction = _NET_FW_RULE_DIR_IN
        rule.Action = _NET_FW_ACTION_ALLOW
        rule.EdgeTraversal = False
        rule.Enabled = True

    _configure_allow_rule = _configure_tcp_rule

    @staticmethod
    def _configure_udp_rule(rule, spec):
        rule.Name = CONDUIT_FIREWALL_UDP_RULE_NAME
        rule.Description = (
            f"Allow Conduit Streamer on private local networks "
            f"(UDP {spec.udp_ports})."
        )
        rule.Grouping = "Conduit"
        rule.Protocol = _NET_FW_IP_PROTOCOL_UDP
        rule.LocalPorts = spec.udp_ports
        rule.ApplicationName = spec.executable_path
        rule.Profiles = _NET_FW_PROFILE2_PRIVATE
        rule.RemoteAddresses = "LocalSubnet"
        rule.Direction = _NET_FW_RULE_DIR_IN
        rule.Action = _NET_FW_ACTION_ALLOW
        rule.EdgeTraversal = False
        rule.Enabled = True

    def _rollback_repair(
        self,
        rules,
        disabled_rules,
        allow_created,
        allow_rule=None,
        allow_snapshot=None,
        snapshots=None,
    ):
        complete = True
        if isinstance(allow_created, (list, tuple, set)):
            for name in allow_created:
                try:
                    rules.Remove(name)
                except Exception as error:
                    if not _is_missing(error):
                        complete = False
        elif allow_created:
            removal = self._remove_from(rules)
            if removal is not None:
                complete = False
        if snapshots is not None:
            for name, snapshot in snapshots:
                if snapshot is not None:
                    try:
                        rule = rules.Item(name)
                        for field, value in snapshot:
                            setattr(rule, field, value)
                    except Exception:
                        complete = False
        elif allow_snapshot is not None and allow_rule is not None:
            for field, value in allow_snapshot:
                try:
                    setattr(allow_rule, field, value)
                except Exception:
                    complete = False
        for rule in reversed(disabled_rules):
            try:
                rule.Enabled = True
            except Exception:
                complete = False
        return complete

    def _cleanup_after_failure(self):
        try:
            rules = self.policy_factory().Rules
            self._remove_from(rules)
        except Exception:
            pass
