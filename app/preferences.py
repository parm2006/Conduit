"""Non-secret local Conduit UI preferences."""

import json
import ipaddress
import logging
import os
from pathlib import Path
import uuid

from app.safe_errors import error_name
from app.ports import DEFAULT_BASE_PORT
from app.display_topology import (
    Display,
    DraftTopology,
    MachineDisplayGroup,
    NativeRect,
    PlacedMachine,
)


logger = logging.getLogger(__name__)
VALID_ROLES = frozenset(("server", "client"))
VALID_CLIENT_POSITIONS = frozenset(("top", "left", "right", "bottom"))
MAX_SUCCESSFUL_HOSTS = 10
TOPOLOGY_SCHEMA_VERSION = 1


def _validated_successful_host(ip, port):
    try:
        address = ipaddress.IPv4Address(str(ip).strip()).compressed
        parsed_port = int(port)
    except (ipaddress.AddressValueError, TypeError, ValueError) as error:
        raise ValueError("host must contain a valid IPv4 address and port") from error
    if isinstance(port, bool) or not (1 <= parsed_port <= 65533):
        raise ValueError("host port must be between 1 and 65533")
    return {"ip": address, "port": parsed_port}


class UserPreferences:
    def __init__(self, root=None):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Conduit"
        self.root = Path(root or base).resolve()
        self.path = self.root / "preferences.json"

    def load_role(self):
        role = self._load_values().get("last_successful_role")
        return role if role in VALID_ROLES else None

    def load_client_position(self):
        position = self._load_values().get("client_position")
        return position if position in VALID_CLIENT_POSITIONS else "right"

    def load_server_port(self):
        try:
            port = int(
                self._load_values().get("server_port", DEFAULT_BASE_PORT)
            )
            return port if 1 <= port <= 65533 else DEFAULT_BASE_PORT
        except (TypeError, ValueError):
            return DEFAULT_BASE_PORT

    def save_role(self, role):
        if role not in VALID_ROLES:
            raise ValueError("role must be server or client")
        self._save_value("last_successful_role", role)

    def save_client_position(self, position):
        if position not in VALID_CLIENT_POSITIONS:
            raise ValueError("client position must be top, left, right, or bottom")
        self._save_value("client_position", position)

    def save_server_port(self, port):
        try:
            val = int(port)
            if not (1 <= val <= 65533):
                raise ValueError("port must be between 1 and 65533")
            self._save_value("server_port", val)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "port must be an integer between 1 and 65533"
            ) from error

    def save_active_topology(self, topology):
        machines = []
        for placed in topology.machines:
            displays = []
            for display in placed.group.displays:
                displays.append(
                    {
                        "display_id": display.display_id,
                        "rect": _rect_values(display.rect),
                        "work_rect": (
                            _rect_values(display.work_rect)
                            if display.work_rect is not None
                            else None
                        ),
                        "dpi_percent": display.dpi_percent,
                        "orientation": display.orientation,
                        "primary": display.primary,
                        "enabled": display.enabled,
                    }
                )
            machines.append(
                {
                    "machine_id": placed.group.machine_id,
                    "windows_name": placed.group.windows_name,
                    "x": placed.x,
                    "y": placed.y,
                    "displays": displays,
                }
            )
        self._save_value(
            "active_topology",
            {
                "schema_version": TOPOLOGY_SCHEMA_VERSION,
                "activation_version": topology.version,
                "server_id": topology.server_id,
                "machines": machines,
            },
        )

    def load_active_topology(self):
        stored = self._load_values().get("active_topology")
        if stored is None:
            return None
        try:
            if stored["schema_version"] != TOPOLOGY_SCHEMA_VERSION:
                return None
            activation_version = stored["activation_version"]
            if type(activation_version) is not int or activation_version < 0:
                return None
            machines = []
            for machine in stored["machines"]:
                displays = tuple(
                    Display(
                        display_id=display["display_id"],
                        rect=NativeRect(*display["rect"]),
                        work_rect=(
                            NativeRect(*display["work_rect"])
                            if display.get("work_rect") is not None
                            else None
                        ),
                        dpi_percent=display["dpi_percent"],
                        orientation=display["orientation"],
                        primary=display["primary"],
                        enabled=display["enabled"],
                    )
                    for display in machine["displays"]
                )
                group = MachineDisplayGroup(
                    machine_id=machine["machine_id"],
                    windows_name=machine["windows_name"],
                    displays=displays,
                )
                machines.append(
                    PlacedMachine(group, x=machine["x"], y=machine["y"])
                )
            result = DraftTopology(
                server_id=stored["server_id"],
                machines=tuple(machines),
            ).validate()
            if not result.is_valid:
                return None
            return result.validated.activate(activation_version)
        except Exception as error:
            logger.error("Could not load Conduit topology (%s)", error_name(error))
            return None

    def load_or_seed_draft(self, server_group, client_group=None):
        active = self.load_active_topology()
        if active is not None:
            return DraftTopology(
                server_id=active.server_id,
                machines=active.machines,
            )
        machines = [PlacedMachine(server_group, x=0, y=0)]
        if client_group is not None:
            positions = {
                "right": (1, 0),
                "left": (-1, 0),
                "top": (0, -1),
                "bottom": (0, 1),
            }
            x, y = positions[self.load_client_position()]
            machines.append(PlacedMachine(client_group, x=x, y=y))
        return DraftTopology(
            server_id=server_group.machine_id,
            machines=tuple(machines),
        )

    def load_successful_hosts(self):
        stored = self._load_values().get("successful_hosts", [])
        if not isinstance(stored, list):
            return []
        hosts = []
        seen = set()
        for value in stored:
            if not isinstance(value, dict):
                continue
            try:
                host = _validated_successful_host(
                    value.get("ip"),
                    value.get("port"),
                )
            except ValueError:
                continue
            key = host["ip"]
            if key in seen:
                continue
            seen.add(key)
            hosts.append(host)
            if len(hosts) == MAX_SUCCESSFUL_HOSTS:
                break
        return hosts

    def save_successful_host(self, ip, port):
        host = _validated_successful_host(ip, port)
        hosts = self.load_successful_hosts()
        hosts = [
            saved
            for saved in hosts
            if saved["ip"] != host["ip"]
        ]
        hosts.insert(0, host)
        self._save_value("successful_hosts", hosts[:MAX_SUCCESSFUL_HOSTS])

    def _load_values(self):
        if not self.path.exists():
            return {}
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
            return values if isinstance(values, dict) else {}
        except Exception as error:
            logger.error("Could not load Conduit preferences (%s)", error_name(error))
            return {}

    def _save_value(self, key, value):
        self.root.mkdir(parents=True, exist_ok=True)
        values = self._load_values()
        values[key] = value
        payload = json.dumps(values, separators=(",", ":"))
        temporary = self.path.with_name(
            f".{self.path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


def _rect_values(rect):
    return [rect.left, rect.top, rect.right, rect.bottom]
