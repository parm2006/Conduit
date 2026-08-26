"""Topology-backed ownership for Conduit's single roaming Server cursor."""

from dataclasses import dataclass
import logging
import threading

from app.display_topology import edge_ratio


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalServer:
    display_id: str
    position: tuple[int, int]


@dataclass(frozen=True)
class RemoteClient:
    session_id: str
    machine_id: str
    display_id: str
    position: tuple[int, int]


@dataclass(frozen=True)
class Transitioning:
    source: object
    destination_machine_id: str
    released_state: bool


@dataclass(frozen=True)
class Paused:
    reason: str


class InputRouter:
    """Serializes graph transitions and targets Server-originated input."""

    def __init__(self, topology, *, session_for_machine, input_effects):
        self.topology = topology
        self._session_for_machine = session_for_machine
        self._input_effects = input_effects
        display_id, center = topology.server_primary_center()
        self.state = LocalServer(display_id, center)
        self._held_keys = {}
        self._held_buttons = set()
        self._lock = threading.RLock()

    @property
    def held_keys(self):
        with self._lock:
            return tuple(self._held_keys.values())

    @property
    def held_buttons(self):
        with self._lock:
            return tuple(sorted(self._held_buttons))

    @property
    def active_session_id(self):
        with self._lock:
            if isinstance(self.state, RemoteClient):
                return self.state.session_id
            return None

    @property
    def active_machine_id(self):
        with self._lock:
            if isinstance(self.state, RemoteClient):
                return self.state.machine_id
            if isinstance(self.state, LocalServer):
                return self.topology.server_id
            return None

    def handle_edge(
        self,
        source_machine_id,
        source_display_id,
        side,
        ratio,
        *,
        session_id=None,
        topology_version=None,
    ):
        with self._lock:
            if isinstance(self.state, (Paused, Transitioning)):
                logger.warning(
                    "[cursor] Rejected edge while router state=%s",
                    type(self.state).__name__,
                )
                return False
            if topology_version != self.topology.version:
                logger.warning(
                    "[cursor] Rejected edge with topology version %r; active=%r",
                    topology_version,
                    self.topology.version,
                )
                return False
            if isinstance(self.state, LocalServer):
                if source_machine_id != self.topology.server_id or session_id is not None:
                    logger.warning(
                        "[cursor] Rejected Server edge from unexpected owner "
                        "machine=%r session=%r",
                        source_machine_id,
                        session_id,
                    )
                    return False
            elif (
                source_machine_id != self.state.machine_id
                or session_id != self.state.session_id
            ):
                logger.warning(
                    "[cursor] Rejected Client edge from unexpected owner "
                    "machine=%r session=%r active_machine=%r active_session=%r",
                    source_machine_id,
                    str(session_id)[:8] if session_id else None,
                    self.state.machine_id,
                    str(self.state.session_id)[:8],
                )
                return False
            try:
                edge = self.topology.resolve_edge(
                    source_machine_id,
                    source_display_id,
                    side,
                    ratio,
                )
            except (KeyError, ValueError):
                logger.warning(
                    "[cursor] No graph edge for machine=%r display=%r side=%r",
                    source_machine_id,
                    source_display_id,
                    side,
                )
                return False
            logger.info(
                "[cursor] Graph transition %s/%s:%s -> %s/%s:%s "
                "ratio=%.4f entry=%s topology=%s",
                edge.mapping.source_machine_id,
                edge.mapping.source_display_id,
                edge.mapping.source_side,
                edge.mapping.destination_machine_id,
                edge.mapping.destination_display_id,
                edge.mapping.destination_side,
                ratio,
                edge.destination_position,
                self.topology.version,
            )
            return self._transition(edge)

    def forward_mouse_move(self, dx, dy):
        return self._send_active({"type": "mouse_move", "dx": dx, "dy": dy})

    def forward_scroll(self, dx, dy):
        return self._send_active({"type": "mouse_scroll", "dx": dx, "dy": dy})

    def forward_button(self, button, pressed):
        with self._lock:
            if not isinstance(self.state, RemoteClient):
                return False
            sent = self._send_to_state(
                self.state,
                {"type": "mouse_click", "button": button, "pressed": bool(pressed)},
            )
            if sent:
                if pressed:
                    self._held_buttons.add(button)
                else:
                    self._held_buttons.discard(button)
            return sent

    def forward_key_press(self, key_data):
        with self._lock:
            if not isinstance(self.state, RemoteClient):
                return False
            message = {"type": "key_press", "key": dict(key_data)}
            sent = self._send_to_state(self.state, message)
            if sent:
                self._held_keys[self._key_identity(key_data)] = dict(key_data)
            return sent

    def forward_key_release(self, key_data):
        with self._lock:
            if not isinstance(self.state, RemoteClient):
                return False
            sent = self._send_to_state(
                self.state,
                {"type": "key_release", "key": dict(key_data)},
            )
            if sent:
                self._held_keys.pop(self._key_identity(key_data), None)
            return sent

    def destination_lost(self, session_id):
        with self._lock:
            if not (
                isinstance(self.state, RemoteClient)
                and self.state.session_id == session_id
            ):
                return False
            self._held_keys.clear()
            self._held_buttons.clear()
            self._return_to_server_center()
            return True

    def pause(self, reason):
        with self._lock:
            if isinstance(self.state, Paused):
                return False
            previous = self.state
            if isinstance(previous, RemoteClient):
                self._release_remote(previous)
            self._input_effects.release_local_input()
            _display_id, center = self.topology.server_primary_center()
            self._input_effects.restore_local(center)
            self.state = Paused(str(reason))
            return True

    def resume(self):
        with self._lock:
            if not isinstance(self.state, Paused):
                return False
            display_id, center = self.topology.server_primary_center()
            self.state = LocalServer(display_id, center)
            return True

    def _transition(self, edge):
        previous = self.state
        mapping = edge.mapping
        self.state = Transitioning(previous, mapping.destination_machine_id, False)
        if isinstance(previous, RemoteClient):
            released = self._release_remote(previous)
        else:
            self._input_effects.release_local_input()
            self._held_keys.clear()
            self._held_buttons.clear()
            released = True
        self.state = Transitioning(previous, mapping.destination_machine_id, released)
        if not released:
            self._return_to_server_center()
            return False

        if mapping.destination_machine_id == self.topology.server_id:
            self._input_effects.restore_local(edge.destination_position)
            self.state = LocalServer(
                mapping.destination_display_id,
                edge.destination_position,
            )
            return True

        session = self._session_for_machine(mapping.destination_machine_id)
        if (
            session is None
            or not getattr(session, "ready", False)
            or getattr(session, "control_lane", None) is None
        ):
            self._return_to_server_center()
            return False
        message = {
            "type": "switch",
            "topology_version": self.topology.version,
            "direction": mapping.source_side,
            "source_machine_id": mapping.source_machine_id,
            "source_display_id": mapping.source_display_id,
            "source_side": mapping.source_side,
            "source_rect": self._rect_values(edge.source_rect),
            "destination_machine_id": mapping.destination_machine_id,
            "destination_display_id": mapping.destination_display_id,
            "destination_side": mapping.destination_side,
            "destination_rect": self._rect_values(edge.destination_rect),
            "position": list(edge.destination_position),
            "ratio": edge_ratio(
                edge.destination_rect,
                mapping.destination_side,
                *edge.destination_position,
            ),
            "scale_x": edge.scale_x,
            "scale_y": edge.scale_y,
            "destination_dpi_percent": edge.destination_dpi_percent,
            "destination_edges": self._machine_edge_commands(
                mapping.destination_machine_id
            ),
        }
        if not session.control_lane.send_message(message):
            logger.warning(
                "[cursor] Switch command failed for machine=%r session=%s",
                mapping.destination_machine_id,
                str(session.session_id)[:8],
            )
            self._return_to_server_center()
            return False
        if isinstance(previous, LocalServer):
            self._input_effects.begin_remote_capture(session.session_id)
        logger.info(
            "[cursor] Remote ownership active on machine=%r session=%s entry=%s",
            mapping.destination_machine_id,
            str(session.session_id)[:8],
            edge.destination_position,
        )
        self.state = RemoteClient(
            session.session_id,
            mapping.destination_machine_id,
            mapping.destination_display_id,
            edge.destination_position,
        )
        return True

    def _release_remote(self, state):
        session = self._session_for_machine(state.machine_id)
        if session is None or session.session_id != state.session_id:
            self._held_keys.clear()
            self._held_buttons.clear()
            return True
        released = True
        for key_data in reversed(tuple(self._held_keys.values())):
            released = bool(session.control_lane.send_message({
                "type": "key_release",
                "key": dict(key_data),
            })) and released
        for button in sorted(self._held_buttons):
            released = bool(session.control_lane.send_message({
                "type": "mouse_click",
                "button": button,
                "pressed": False,
            })) and released
        self._held_keys.clear()
        self._held_buttons.clear()
        return released

    def _return_to_server_center(self):
        self._held_keys.clear()
        self._held_buttons.clear()
        self._input_effects.release_local_input()
        display_id, center = self.topology.server_primary_center()
        self._input_effects.restore_local(center)
        self.state = LocalServer(display_id, center)

    def _send_active(self, message):
        with self._lock:
            if not isinstance(self.state, RemoteClient):
                return False
            return self._send_to_state(self.state, message)

    def _send_to_state(self, state, message):
        session = self._session_for_machine(state.machine_id)
        if (
            session is None
            or not getattr(session, "ready", False)
            or session.session_id != state.session_id
        ):
            self._return_to_server_center()
            return False
        sent = bool(session.control_lane.send_message(message))
        if not sent:
            self._return_to_server_center()
        return sent

    @staticmethod
    def _key_identity(key_data):
        return (
            key_data.get("type"),
            key_data.get("value"),
            key_data.get("vk"),
            key_data.get("scan"),
            key_data.get("extended"),
        )

    @staticmethod
    def _rect_values(rect):
        return [rect.left, rect.top, rect.right, rect.bottom]

    def _machine_edge_commands(self, machine_id):
        commands = []
        for mapping in self.topology.edge_mappings:
            if mapping.source_machine_id != machine_id:
                continue
            source = self.topology.display(
                mapping.source_machine_id,
                mapping.source_display_id,
            )
            destination = self.topology.display(
                mapping.destination_machine_id,
                mapping.destination_display_id,
            )
            commands.append({
                "source_machine_id": mapping.source_machine_id,
                "source_display_id": mapping.source_display_id,
                "source_side": mapping.source_side,
                "source_rect": self._rect_values(source.rect),
                "destination_machine_id": mapping.destination_machine_id,
                "destination_display_id": mapping.destination_display_id,
                "destination_side": mapping.destination_side,
                "destination_rect": self._rect_values(destination.rect),
            })
        return commands
