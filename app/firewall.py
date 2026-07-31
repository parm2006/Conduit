"""Platform-independent Windows Firewall rule contract for DeskFlow."""

from dataclasses import dataclass
from enum import Enum
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
    MANAGED = "managed"
    UNAVAILABLE = "unavailable"


def _normalized_executable(value):
    return ntpath.normcase(ntpath.normpath(str(value)))


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
            _normalized_executable(observed.application_name)
            == _normalized_executable(spec.executable_path),
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
