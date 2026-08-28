"""Topology-backed ownership for Conduit's single roaming Server cursor."""

from dataclasses import dataclass, replace
import logging
import threading
import uuid

from app.display_topology import edge_ratio
from app.input_dispatcher import InputDispatcher


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
    destination_session_id: str
    destination_machine_id: str
    destination_display_id: str
    destination_position: tuple[int, int]
    topology_version: int
    handoff_id: str
    released_state: bool
    capture_on_ack: bool
    acknowledged: bool = False


@dataclass(frozen=True)
class Paused:
    reason: str


class InputRouter:
    """Serializes graph transitions and targets Server-originated input."""

    def __init__(
        self,
        topology,
        *,
        session_for_machine,
        input_effects,
        handoff_id_factory=None,
        schedule_deadline=None,
        handoff_timeout=0.75,
        handoff_failed=None,
        ownership_changed=None,
    ):
        self.topology = topology
        self._session_for_machine = session_for_machine
        self._input_effects = input_effects
        display_id, center = topology.server_primary_center()
        self.state = LocalServer(display_id, center)
        self._held_keys = {}
        self._held_buttons = set()
        self._lock = threading.RLock()
        self._pause_requested = threading.Event()
        self._handoff_id_factory = handoff_id_factory or (
            lambda: uuid.uuid4().hex
        )
        self._schedule_deadline = (
            schedule_deadline or self._start_deadline_timer
        )
        self._handoff_timeout = float(handoff_timeout)
        self._handoff_failed = handoff_failed
        self._ownership_changed = ownership_changed
        self._pending_deadline = None
        self._dispatch_machines = {}
        self._dispatcher = InputDispatcher(
            lane_for_session=self._lane_for_dispatch,
            on_failure=self._on_dispatch_failure,
        )

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
        if self._pause_requested.is_set():
            logger.warning("[cursor] Rejected edge while pause is pending")
            return False
        with self._lock:
            if self._pause_requested.is_set():
                logger.warning("[cursor] Rejected edge while pause is pending")
                return False
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
        state = self._active_remote_snapshot()
        if state is None:
            return False
        return self._dispatcher.enqueue_move(state.session_id, dx, dy)

    def forward_scroll(self, dx, dy):
        return self._enqueue_active_discrete({
            "type": "mouse_scroll",
            "dx": dx,
            "dy": dy,
        })

    def forward_button(self, button, pressed):
        if self._pause_requested.is_set():
            return False
        state = self._active_remote_snapshot()
        if state is None:
            return False
        sent = self._dispatcher.enqueue_discrete(
            state.session_id,
            {"type": "mouse_click", "button": button, "pressed": bool(pressed)},
        )
        if sent:
            with self._lock:
                if self.state != state or self._pause_requested.is_set():
                    return sent
                if pressed:
                    self._held_buttons.add(button)
                else:
                    self._held_buttons.discard(button)
        return sent

    def forward_key_press(self, key_data):
        if self._pause_requested.is_set():
            return False
        state = self._active_remote_snapshot()
        if state is None:
            return False
        sent = self._dispatcher.enqueue_discrete(
            state.session_id,
            {"type": "key_press", "key": dict(key_data)},
        )
        if sent:
            with self._lock:
                if self.state != state or self._pause_requested.is_set():
                    return sent
                self._held_keys[self._key_identity(key_data)] = dict(key_data)
        return sent

    def forward_key_release(self, key_data):
        if self._pause_requested.is_set():
            return False
        state = self._active_remote_snapshot()
        if state is None:
            return False
        sent = self._dispatcher.enqueue_discrete(
            state.session_id,
            {"type": "key_release", "key": dict(key_data)},
        )
        if sent:
            with self._lock:
                if self.state != state or self._pause_requested.is_set():
                    return sent
                self._held_keys.pop(self._key_identity(key_data), None)
        return sent

    def destination_lost(self, session_id):
        restore_center = None
        with self._lock:
            if isinstance(self.state, RemoteClient):
                matches = self.state.session_id == session_id
            elif isinstance(self.state, Transitioning):
                matches = self.state.destination_session_id == session_id
            else:
                matches = False
            if not matches:
                return False
            self._stop_dispatch_locked(session_id)
            self._cancel_pending_deadline_locked()
            self._held_keys.clear()
            self._held_buttons.clear()
            display_id, restore_center = self.topology.server_primary_center()
            self.state = LocalServer(display_id, restore_center)
        self._input_effects.release_local_input()
        self._input_effects.restore_local(restore_center)
        return True

    def request_pause(self, reason):
        """Reject new input immediately, without waiting for the router lock."""
        self._pause_requested.set()
        return True

    def pause(self, reason):
        self.request_pause(reason)
        with self._lock:
            if isinstance(self.state, Paused):
                return False
            self._cancel_pending_deadline_locked()
            previous = self.state
            if isinstance(previous, RemoteClient):
                self._stop_dispatch_locked(previous.session_id)
                self._release_remote(previous)
            elif isinstance(previous, Transitioning):
                self._stop_dispatch_locked(previous.destination_session_id)
            self._input_effects.release_local_input()
            _display_id, center = self.topology.server_primary_center()
            restore = getattr(
                self._input_effects,
                "restore_paused",
                self._input_effects.restore_local,
            )
            restore(center)
            self.state = Paused(str(reason))
            return True

    def resume(self):
        with self._lock:
            if not isinstance(self.state, Paused):
                return False
            display_id, center = self.topology.server_primary_center()
            self.state = LocalServer(display_id, center)
            self._pause_requested.clear()
            return True

    def return_to_server_primary(self, reason="shortcut"):
        ownership_callback = None
        next_state = None
        with self._lock:
            previous = self.state
            paused = isinstance(previous, Paused)
            if isinstance(previous, RemoteClient):
                self._stop_dispatch_locked(previous.session_id)
                self._release_remote(previous)
            elif isinstance(previous, Transitioning):
                self._stop_dispatch_locked(previous.destination_session_id)
                self._held_keys.clear()
                self._held_buttons.clear()
            else:
                self._held_keys.clear()
                self._held_buttons.clear()
            self._cancel_pending_deadline_locked()
            display_id, center = self.topology.server_primary_center()
            if not paused:
                next_state = LocalServer(display_id, center)
                self.state = next_state
                if not isinstance(previous, LocalServer):
                    ownership_callback = self._ownership_changed
            self._input_effects.release_local_input()
            restore = (
                getattr(
                    self._input_effects,
                    "restore_paused",
                    self._input_effects.restore_local,
                )
                if paused
                else self._input_effects.restore_local
            )
            restore(center)
        logger.info("[cursor] Returned to Server primary (%s)", reason)
        if ownership_callback is not None:
            ownership_callback(next_state)
        return True

    def _transition(self, edge):
        previous = self.state
        mapping = edge.mapping
        placeholder = Transitioning(
            previous,
            "",
            mapping.destination_machine_id,
            mapping.destination_display_id,
            edge.destination_position,
            self.topology.version,
            "",
            False,
            isinstance(previous, LocalServer),
        )
        self.state = placeholder
        if isinstance(previous, RemoteClient):
            self._stop_dispatch_locked(previous.session_id)
            released = self._release_remote(previous)
        else:
            self._input_effects.release_local_input()
            self._held_keys.clear()
            self._held_buttons.clear()
            released = True
        self.state = Transitioning(
            previous,
            "",
            mapping.destination_machine_id,
            mapping.destination_display_id,
            edge.destination_position,
            self.topology.version,
            "",
            released,
            isinstance(previous, LocalServer),
        )
        if not released:
            self._return_to_server_center()
            return False
        if self._pause_requested.is_set():
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
        handoff_id = str(self._handoff_id_factory())
        if not handoff_id:
            self._return_to_server_center()
            return False
        pending = Transitioning(
            previous,
            session.session_id,
            mapping.destination_machine_id,
            mapping.destination_display_id,
            edge.destination_position,
            self.topology.version,
            handoff_id,
            released,
            isinstance(previous, LocalServer),
        )
        self.state = pending
        message = {
            "type": "switch",
            "handoff_id": handoff_id,
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
        deadline = self._schedule_deadline(
            self._handoff_timeout,
            lambda: self._fail_handoff(handoff_id, "handoff timeout"),
        )
        self._pending_deadline = deadline
        threading.Thread(
            target=self._send_switch,
            args=(pending, session.control_lane, message),
            name=f"cursor-handoff-{str(session.session_id)[:8]}",
            daemon=True,
        ).start()
        return True

    def acknowledge_handoff(
        self,
        *,
        handoff_id,
        session_id,
        machine_id,
        topology_version,
    ):
        capture_session_id = None
        ownership_callback = None
        next_state = None
        with self._lock:
            pending = self.state
            if not isinstance(pending, Transitioning):
                return False
            if (
                self._pause_requested.is_set()
                or pending.acknowledged
                or handoff_id != pending.handoff_id
                or session_id != pending.destination_session_id
                or machine_id != pending.destination_machine_id
                or topology_version != pending.topology_version
                or topology_version != self.topology.version
            ):
                return False
            self._cancel_pending_deadline_locked()
            committing = replace(pending, acknowledged=True)
            self.state = committing
            self._dispatch_machines[pending.destination_session_id] = (
                pending.destination_machine_id
            )
            if not self._dispatcher.start_session(
                pending.destination_session_id
            ):
                self._dispatch_machines.pop(
                    pending.destination_session_id,
                    None,
                )
                return False
            if pending.capture_on_ack:
                capture_session_id = pending.destination_session_id
            next_state = RemoteClient(
                pending.destination_session_id,
                pending.destination_machine_id,
                pending.destination_display_id,
                pending.destination_position,
            )
            if capture_session_id is None:
                self.state = next_state
            ownership_callback = self._ownership_changed

        if capture_session_id is not None:
            self._input_effects.begin_remote_capture(capture_session_id)
            with self._lock:
                if self.state != committing or self._pause_requested.is_set():
                    return False
                self.state = next_state

        logger.info(
            "[cursor] Remote ownership acknowledged machine=%r session=%s "
            "entry=%s handoff=%s",
            next_state.machine_id,
            str(next_state.session_id)[:8],
            next_state.position,
            str(handoff_id)[:8],
        )
        if ownership_callback is not None:
            ownership_callback(next_state)
        return True

    def _send_switch(self, pending, lane, message):
        try:
            sent = bool(lane.send_message(message))
        except Exception as exc:
            logger.warning(
                "[cursor] Switch command raised for machine=%r session=%s (%s)",
                pending.destination_machine_id,
                str(pending.destination_session_id)[:8],
                type(exc).__name__,
            )
            sent = False
        if sent:
            return
        logger.warning(
            "[cursor] Switch command failed for machine=%r session=%s",
            pending.destination_machine_id,
            str(pending.destination_session_id)[:8],
        )
        self._fail_handoff(pending.handoff_id, "switch send failed")

    def _fail_handoff(self, handoff_id, reason):
        callback = None
        session_id = None
        center = None
        with self._lock:
            pending = self.state
            if not (
                isinstance(pending, Transitioning)
                and pending.handoff_id == handoff_id
                and not pending.acknowledged
            ):
                return False
            session_id = pending.destination_session_id
            self._stop_dispatch_locked(session_id)
            self._cancel_pending_deadline_locked()
            self._held_keys.clear()
            self._held_buttons.clear()
            display_id, center = self.topology.server_primary_center()
            self.state = LocalServer(display_id, center)
            callback = self._handoff_failed

        self._input_effects.release_local_input()
        self._input_effects.restore_local(center)
        if callback is not None:
            callback(session_id, reason)
        return True

    def _cancel_pending_deadline_locked(self):
        deadline = self._pending_deadline
        self._pending_deadline = None
        if deadline is not None:
            deadline.cancel()

    @staticmethod
    def _start_deadline_timer(delay, callback):
        timer = threading.Timer(delay, callback)
        timer.daemon = True
        timer.start()
        return timer

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
        current = self.state
        if isinstance(current, RemoteClient):
            self._stop_dispatch_locked(current.session_id)
        elif isinstance(current, Transitioning):
            self._stop_dispatch_locked(current.destination_session_id)
        self._cancel_pending_deadline_locked()
        self._held_keys.clear()
        self._held_buttons.clear()
        self._input_effects.release_local_input()
        display_id, center = self.topology.server_primary_center()
        self._input_effects.restore_local(center)
        self.state = LocalServer(display_id, center)

    def _active_remote_snapshot(self):
        if self._pause_requested.is_set():
            return None
        with self._lock:
            if self._pause_requested.is_set() or not isinstance(
                self.state, RemoteClient
            ):
                return None
            return self.state

    def _enqueue_active_discrete(self, message):
        state = self._active_remote_snapshot()
        if state is None:
            return False
        return self._dispatcher.enqueue_discrete(state.session_id, message)

    def _lane_for_dispatch(self, session_id):
        with self._lock:
            machine_id = self._dispatch_machines.get(session_id)
        if machine_id is None:
            return None
        session = self._session_for_machine(machine_id)
        if (
            session is None
            or not getattr(session, "ready", False)
            or session.session_id != session_id
        ):
            return None
        return getattr(session, "control_lane", None)

    def _stop_dispatch_locked(self, session_id):
        if not session_id:
            return False
        self._dispatch_machines.pop(session_id, None)
        return self._dispatcher.stop_session(session_id)

    def _on_dispatch_failure(self, session_id, reason):
        callback = None
        center = None
        with self._lock:
            if not (
                isinstance(self.state, RemoteClient)
                and self.state.session_id == session_id
            ):
                return False
            self._stop_dispatch_locked(session_id)
            self._held_keys.clear()
            self._held_buttons.clear()
            display_id, center = self.topology.server_primary_center()
            self.state = LocalServer(display_id, center)
            callback = self._handoff_failed

        self._input_effects.release_local_input()
        self._input_effects.restore_local(center)
        if callback is not None:
            callback(session_id, f"input dispatch failed: {reason}")
        return True

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
