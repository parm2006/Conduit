"""Platform-independent Windows Firewall rule contract for DeskFlow."""

from dataclasses import dataclass
from enum import Enum
import ipaddress
import ntpath
from pathlib import PureWindowsPath
import re
import sys


DESKFLOW_FIREWALL_RULE_NAME = "DeskFlow Server - Private LAN"


def current_process_executable():
    """Return the executable image Windows uses for firewall matching."""
    try:
        import win32api

        return win32api.GetModuleFileName(None)
    except Exception:
        return sys.executable


class FirewallState(str, Enum):
    READY = "ready"
    MISSING = "missing"
    STALE = "stale"
    DEVELOPMENT = "development"
    CONFLICT = "conflict"
    PUBLIC_ONLY = "public_only"
    MANAGED = "managed"
    UNAVAILABLE = "unavailable"


def _normalized_executable(value):
    return ntpath.normcase(ntpath.normpath(str(value)))


def executable_paths_match(first, second):
    return _normalized_executable(first) == _normalized_executable(second)


@dataclass(frozen=True)
class FirewallRuleSpec:
    executable_path: str
    base_port: int

    def __post_init__(self):
        path = str(self.executable_path).strip()
        if not path or not PureWindowsPath(path).is_absolute():
            raise ValueError("executable path must be an absolute Windows path")
        if (
            isinstance(self.base_port, bool)
            or not isinstance(self.base_port, int)
            or not 1 <= self.base_port <= 65533
        ):
            raise ValueError("base port must be an integer from 1 through 65533")
        object.__setattr__(self, "executable_path", ntpath.normpath(path))

    @property
    def local_ports(self):
        return f"{self.base_port}-{self.base_port + 2}"

    @property
    def development_scope(self):
        executable_name = ntpath.basename(self.executable_path).casefold()
        return re.fullmatch(
            r"python(?:\d+(?:\.\d+)*)?w?\.exe",
            executable_name,
        ) is not None


@dataclass(frozen=True)
class ObservedFirewallRule:
    name: str
    enabled: bool
    direction: str
    action: str
    protocol: str
    local_ports: str
    application_name: str
    profiles: frozenset[str]
    remote_addresses: frozenset[str]
    edge_traversal: bool


@dataclass(frozen=True)
class FirewallInspection:
    state: FirewallState
    reason_code: str
    conflict_count: int = 0


def _port_intervals(expression):
    value = str(expression).strip().casefold()
    if value in {"", "*", "any"}:
        return ((1, 65535),)

    intervals = []
    for item in value.split(","):
        token = item.strip()
        if not token:
            raise ValueError("empty firewall port token")
        parts = token.split("-")
        if len(parts) == 1:
            if not parts[0].isdigit():
                raise ValueError("invalid firewall port")
            start = end = int(parts[0], 10)
        elif len(parts) == 2 and all(part.isdigit() for part in parts):
            start, end = (int(part, 10) for part in parts)
        else:
            raise ValueError("invalid firewall port range")
        if not 1 <= start <= end <= 65535:
            raise ValueError("firewall port is out of range")
        intervals.append((start, end))
    return tuple(intervals)


def _scope_is_loopback_only(scope):
    token = str(scope).strip()
    try:
        if "-" in token:
            first, last = token.split("-", 1)
            first_address = ipaddress.ip_address(first.strip())
            last_address = ipaddress.ip_address(last.strip())
            return (
                first_address.version == last_address.version
                and int(first_address) <= int(last_address)
                and first_address.is_loopback
                and last_address.is_loopback
            )
        return ipaddress.ip_network(token, strict=False).is_loopback
    except ValueError:
        return False


def remote_scope_may_overlap_local_subnet(addresses):
    scopes = tuple(addresses)
    if not scopes:
        return True
    return any(not _scope_is_loopback_only(scope) for scope in scopes)


def block_rule_conflicts(spec, observed):
    if not observed.enabled:
        return False
    if str(observed.direction).casefold() != "inbound":
        return False
    if str(observed.action).casefold() != "block":
        return False
    if str(observed.protocol).casefold() not in {"tcp", "any", "6", "256"}:
        return False
    if not executable_paths_match(
        observed.application_name,
        spec.executable_path,
    ):
        return False
    profiles = {str(profile).casefold() for profile in observed.profiles}
    if "private" not in profiles and "all" not in profiles:
        return False
    if not remote_scope_may_overlap_local_subnet(
        observed.remote_addresses
    ):
        return False

    deskflow_start = spec.base_port
    deskflow_end = spec.base_port + 2
    return any(
        start <= deskflow_end and end >= deskflow_start
        for start, end in _port_intervals(observed.local_ports)
    )


def evaluate_effective_firewall(
    spec,
    allow_inspection,
    block_rules,
    active_profiles,
):
    if allow_inspection.state not in {
        FirewallState.READY,
        FirewallState.DEVELOPMENT,
    }:
        return allow_inspection

    normalized_profiles = {
        str(profile).casefold() for profile in active_profiles
    }
    if "private" not in normalized_profiles:
        return FirewallInspection(
            FirewallState.PUBLIC_ONLY,
            "private_profile_inactive",
        )

    conflicts = []
    try:
        for rule in block_rules:
            if block_rule_conflicts(spec, rule):
                conflicts.append(rule)
    except (AttributeError, TypeError, ValueError):
        return FirewallInspection(
            FirewallState.UNAVAILABLE,
            "block_rule_unreadable",
        )

    if not conflicts:
        return allow_inspection

    return FirewallInspection(
        FirewallState.CONFLICT,
        "block_conflict",
        conflict_count=len(conflicts),
    )


def compare_firewall_rule(spec, observed):
    if observed is None:
        return FirewallInspection(FirewallState.MISSING, "rule_missing")

    comparisons = (
        ("name", observed.name == DESKFLOW_FIREWALL_RULE_NAME),
        ("enabled", observed.enabled is True),
        ("direction", observed.direction.casefold() == "inbound"),
        ("action", observed.action.casefold() == "allow"),
        ("protocol", observed.protocol.casefold() == "tcp"),
        ("local_ports", observed.local_ports == spec.local_ports),
        (
            "application_name",
            executable_paths_match(
                observed.application_name,
                spec.executable_path,
            ),
        ),
        ("profiles", observed.profiles == frozenset({"private"})),
        (
            "remote_addresses",
            observed.remote_addresses == frozenset({"localsubnet"}),
        ),
        ("edge_traversal", observed.edge_traversal is False),
    )
    for field, matches in comparisons:
        if not matches:
            return FirewallInspection(
                FirewallState.STALE,
                f"stale_{field}",
            )

    if spec.development_scope:
        return FirewallInspection(FirewallState.DEVELOPMENT, "python_scope")
    return FirewallInspection(FirewallState.READY, "rule_ready")
