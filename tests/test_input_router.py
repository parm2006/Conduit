import threading
import unittest
from types import SimpleNamespace

from app.display_topology import (
    Display,
    DraftTopology,
    MachineDisplayGroup,
    NativeRect,
    PlacedMachine,
)
from app.input_router import (
    InputRouter,
    LocalServer,
    Paused,
    RemoteClient,
    Transitioning,
)
from app.input_handler import TopologyEdgeRegion
from app.client import ConduitClient
from app.server import ConduitServer, _ServerInputEffects


class RecordingLane:
    def __init__(self, session_id, log, send_result=True):
        self.session_id = session_id
        self.log = log
        self.send_result = send_result
        self.sent = threading.Event()

    def send_message(self, message):
        self.log.append((self.session_id, dict(message)))
        self.sent.set()
        return self.send_result


class RecordingInputEffects:
    def __init__(self, log):
        self.log = log

    def release_local_input(self):
        self.log.append(("server", "release"))

    def begin_remote_capture(self, session_id):
        self.log.append(("server", "remote", session_id))

    def restore_local(self, position):
        self.log.append(("server", "local", position))


class ManualDeadline:
    class Handle:
        def __init__(self, callback):
            self.callback = callback
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    def __init__(self):
        self.calls = []

    def schedule(self, delay, callback):
        handle = self.Handle(callback)
        self.calls.append((delay, handle))
        return handle

    def fire_next(self):
        _delay, handle = self.calls.pop(0)
        if not handle.cancelled:
            handle.callback()


class ClientSwitchInput:
    screen_width = 1920
    screen_height = 1080

    def __init__(self):
        self.positions = []
        self.edge_sets = []
        self.releases = 0

    def inject_position(self, x, y):
        self.positions.append((x, y))
        return x, y

    def set_client_topology_edges(self, edges):
        self.edge_sets.append(tuple(edges))

    def release_all_injected_input(self):
        self.releases += 1
        return True


def display(display_id, rect, *, primary=True, dpi=100):
    return Display(display_id, rect, dpi, 0, primary)


def group(machine_id, *displays):
    return MachineDisplayGroup(machine_id, machine_id, tuple(displays))


def active_chain():
    server = group(
        "server",
        display("server-primary", NativeRect(0, 0, 1920, 1080)),
    )
    first = group(
        "client-1",
        display("client-1-primary", NativeRect(0, 0, 2560, 1440), dpi=150),
    )
    second = group(
        "client-2",
        display("client-2-primary", NativeRect(0, 0, 1280, 1024), dpi=125),
    )
    return DraftTopology(
        "server",
        (
            PlacedMachine(server, 0, 0),
            PlacedMachine(first, 1, 0),
            PlacedMachine(second, 2, 0),
        ),
    ).validate().validated.activate(7)


def acknowledge_latest(router, session, log, *, topology_version=None):
    if not session.control_lane.sent.wait(0.2):
        raise AssertionError("switch command was not dispatched")
    message = next(
        item[1]
        for item in reversed(log)
        if len(item) == 2
        and item[0] == session.session_id
        and item[1].get("type") == "switch"
    )
    return router.acknowledge_handoff(
        handoff_id=message["handoff_id"],
        session_id=session.session_id,
        machine_id=session.peer_identity,
        topology_version=(
            message["topology_version"]
            if topology_version is None
            else topology_version
        ),
    )


class InputRouterTests(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.sessions = {
            "client-1": SimpleNamespace(
                session_id="session-1",
                peer_identity="client-1",
                ready=True,
                control_lane=RecordingLane("session-1", self.log),
            ),
            "client-2": SimpleNamespace(
                session_id="session-2",
                peer_identity="client-2",
                ready=True,
                control_lane=RecordingLane("session-2", self.log),
            ),
        }
        self.effects = RecordingInputEffects(self.log)
        self.router = InputRouter(
            active_chain(),
            session_for_machine=self.sessions.get,
            input_effects=self.effects,
        )

    def test_capture_ui_callbacks_cannot_block_input_ownership_changes(self):
        callback_entered = threading.Event()
        release_callback = threading.Event()
        restore_finished = threading.Event()

        class Input:
            def stop_keyboard_capture(self):
                pass

            def inject_position(self, x, y):
                pass

            def start_edge_detection(self):
                pass

        def blocking_capture_stop():
            callback_entered.set()
            release_callback.wait(1)

        server = SimpleNamespace(
            input_handler=Input(),
            on_capture_stop=blocking_capture_stop,
        )
        effects = _ServerInputEffects(server)

        worker = threading.Thread(
            target=lambda: (
                effects.restore_local((960, 540)),
                restore_finished.set(),
            )
        )
        worker.start()
        self.assertTrue(callback_entered.wait(0.2))
        try:
            self.assertTrue(
                restore_finished.wait(0.2),
                "GUI overlay callback blocked cursor ownership restoration",
            )
        finally:
            release_callback.set()
            worker.join(1)

    def test_pause_request_prevents_a_blocked_handoff_from_starting_capture(self):
        send_entered = threading.Event()
        release_send = threading.Event()
        result = []

        class BlockingLane(RecordingLane):
            def send_message(inner_self, message):
                inner_self.log.append((inner_self.session_id, dict(message)))
                if message.get("type") == "switch":
                    send_entered.set()
                    release_send.wait(1)
                return True

        self.sessions["client-1"].control_lane = BlockingLane(
            "session-1",
            self.log,
        )
        transition = threading.Thread(
            target=lambda: result.append(self.router.handle_edge(
                "server",
                "server-primary",
                "right",
                0.5,
                topology_version=7,
            ))
        )
        transition.start()
        self.assertTrue(send_entered.wait(0.2))
        try:
            self.assertTrue(
                hasattr(self.router, "request_pause"),
                "disconnect cannot invalidate a handoff without waiting for its lock",
            )
            self.router.request_pause("client disconnected")
        finally:
            release_send.set()
            transition.join(1)

        self.assertEqual(result, [True])
        self.assertFalse(
            any(item[:2] == ("server", "remote") for item in self.log),
        )
        self.assertTrue(self.router.pause("client disconnected"))
        self.assertIsInstance(self.router.state, Paused)

    def test_server_client_client_server_transitions_follow_graph_without_server_hop(self):
        self.assertIsInstance(self.router.state, LocalServer)

        self.assertTrue(
            self.router.handle_edge(
                "server",
                "server-primary",
                "right",
                0.25,
                topology_version=7,
            )
        )
        self.assertTrue(acknowledge_latest(
            self.router,
            self.sessions["client-1"],
            self.log,
        ))
        self.assertEqual(self.router.state.session_id, "session-1")

        self.log.clear()
        self.assertTrue(self.router.forward_key_press({"type": "special", "value": "ctrl"}))
        self.assertTrue(self.router.forward_button("left", True))
        self.assertTrue(
            self.router.handle_edge(
                "client-1",
                "client-1-primary",
                "right",
                0.75,
                session_id="session-1",
                topology_version=7,
            )
        )
        self.assertTrue(acknowledge_latest(
            self.router,
            self.sessions["client-2"],
            self.log,
        ))

        self.assertIsInstance(self.router.state, RemoteClient)
        self.assertEqual(self.router.state.session_id, "session-2")
        self.assertNotIn(
            "local",
            tuple(item[1] for item in self.log if item[0] == "server"),
        )
        first_switch = next(
            index
            for index, item in enumerate(self.log)
            if item[0] == "session-2" and item[1]["type"] == "switch"
        )
        release_indexes = tuple(
            index
            for index, item in enumerate(self.log)
            if item[0] == "session-1"
            and (
                item[1]["type"] == "key_release"
                or (
                    item[1]["type"] == "mouse_click"
                    and item[1]["pressed"] is False
                )
            )
        )
        self.assertEqual(len(release_indexes), 2)
        self.assertLess(max(release_indexes), first_switch)

        self.assertTrue(
            self.router.handle_edge(
                "client-2",
                "client-2-primary",
                "left",
                0.5,
                session_id="session-2",
                topology_version=7,
            )
        )
        self.assertTrue(acknowledge_latest(
            self.router,
            self.sessions["client-1"],
            self.log,
        ))
        self.assertIsInstance(self.router.state, RemoteClient)
        self.assertEqual(self.router.state.session_id, "session-1")

        self.assertTrue(
            self.router.handle_edge(
                "client-1",
                "client-1-primary",
                "left",
                0.5,
                session_id="session-1",
                topology_version=7,
            )
        )
        self.assertEqual(
            self.router.state,
            LocalServer("server-primary", (1916, 540)),
        )

    def test_same_machine_monitor_edge_stays_native_and_outer_edge_routes(self):
        left = display(
            "server-left",
            NativeRect(-2560, 0, 0, 1440),
            primary=False,
            dpi=150,
        )
        primary = display(
            "server-primary",
            NativeRect(0, 0, 1920, 1080),
        )
        server = group("server", left, primary)
        client = group(
            "client-1",
            display("client-primary", NativeRect(0, 0, 1920, 1080)),
        )
        topology = DraftTopology(
            "server",
            (
                PlacedMachine(server, 0, 0),
                PlacedMachine(client, -2, 0),
            ),
        ).validate().validated.activate(3)
        router = InputRouter(
            topology,
            session_for_machine=self.sessions.get,
            input_effects=self.effects,
        )

        self.assertFalse(
            router.handle_edge(
                "server",
                "server-primary",
                "left",
                0.5,
                topology_version=3,
            )
        )
        self.assertIsInstance(router.state, LocalServer)
        self.assertTrue(
            router.handle_edge(
                "server",
                "server-left",
                "left",
                0.5,
                topology_version=3,
            )
        )
        self.assertTrue(acknowledge_latest(
            router,
            self.sessions["client-1"],
            self.log,
            topology_version=3,
        ))
        self.assertTrue(
            router.handle_edge(
                "client-1",
                "client-primary",
                "right",
                0.5,
                session_id="session-1",
                topology_version=3,
            )
        )
        self.assertEqual(
            router.state,
            LocalServer("server-left", (-2557, 720)),
        )

    def test_entry_mapping_clamps_corners_and_reports_native_resolution_scale(self):
        self.assertTrue(
            self.router.handle_edge(
                "server",
                "server-primary",
                "right",
                -4.0,
                topology_version=7,
            )
        )
        self.assertTrue(self.sessions["client-1"].control_lane.sent.wait(0.2))
        message = next(
            item[1]
            for item in reversed(self.log)
            if item[0] == "session-1"
        )

        self.assertEqual(message["position"], [3, 0])
        self.assertAlmostEqual(message["scale_x"], 2560 / 1920)
        self.assertAlmostEqual(message["scale_y"], 1440 / 1080)
        self.assertEqual(message["destination_dpi_percent"], 150)
        self.assertEqual(
            {
                (edge["source_side"], edge["destination_machine_id"])
                for edge in message["destination_edges"]
            },
            {("left", "server"), ("right", "client-2")},
        )

    def test_disconnected_destination_releases_and_returns_to_server_primary_center(self):
        self.sessions["client-1"].ready = False

        self.assertFalse(
            self.router.handle_edge(
                "server",
                "server-primary",
                "right",
                0.5,
                topology_version=7,
            )
        )

        self.assertEqual(
            self.router.state,
            LocalServer("server-primary", (960, 540)),
        )
        self.assertIn(("server", "release"), self.log)
        self.assertIn(("server", "local", (960, 540)), self.log)


class AcknowledgedHandoffTests(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.deadlines = ManualDeadline()
        self.failures = []
        self.failure_seen = threading.Event()
        self.ids = iter(("handoff-1", "handoff-2", "handoff-3"))
        self.sessions = {
            "client-1": SimpleNamespace(
                session_id="session-1",
                peer_identity="client-1",
                ready=True,
                control_lane=RecordingLane("session-1", self.log),
            ),
            "client-2": SimpleNamespace(
                session_id="session-2",
                peer_identity="client-2",
                ready=True,
                control_lane=RecordingLane("session-2", self.log),
            ),
        }
        self.effects = RecordingInputEffects(self.log)
        self.router = InputRouter(
            active_chain(),
            session_for_machine=self.sessions.get,
            input_effects=self.effects,
        )
        self.router._handoff_id_factory = lambda: next(self.ids)
        self.router._schedule_deadline = self.deadlines.schedule
        self.router._handoff_timeout = 0.75
        self.router._handoff_failed = self._record_failure

    def _record_failure(self, session_id, reason):
        self.failures.append((session_id, reason))
        self.failure_seen.set()

    def _begin_server_to_first(self):
        self.assertTrue(self.router.handle_edge(
            "server",
            "server-primary",
            "right",
            0.5,
            topology_version=7,
        ))
        self.assertTrue(self.sessions["client-1"].control_lane.sent.wait(0.2))
        return next(
            item[1]
            for item in reversed(self.log)
            if len(item) == 2
            and item[0] == "session-1"
            and item[1]["type"] == "switch"
        )

    def _ack(self, message, **overrides):
        values = {
            "handoff_id": message["handoff_id"],
            "session_id": "session-1",
            "machine_id": "client-1",
            "topology_version": 7,
        }
        values.update(overrides)
        return self.router.acknowledge_handoff(**values)

    def test_blocked_switch_write_does_not_block_edge_callback_or_start_capture(self):
        send_entered = threading.Event()
        release_send = threading.Event()
        returned = threading.Event()

        class BlockingLane(RecordingLane):
            def send_message(inner_self, message):
                inner_self.log.append((inner_self.session_id, dict(message)))
                inner_self.sent.set()
                send_entered.set()
                release_send.wait(1)
                return True

        self.sessions["client-1"].control_lane = BlockingLane(
            "session-1",
            self.log,
        )
        worker = threading.Thread(target=lambda: (
            self.router.handle_edge(
                "server",
                "server-primary",
                "right",
                0.5,
                topology_version=7,
            ),
            returned.set(),
        ))
        worker.start()
        self.assertTrue(send_entered.wait(0.2))
        returned_promptly = returned.wait(0.1)
        state_before_release = self.router.state
        capture_before_release = any(
            item[:2] == ("server", "remote") for item in self.log
        )
        release_send.set()
        worker.join(1)

        self.assertTrue(returned_promptly, "edge callback waited for socket write")
        self.assertIsInstance(state_before_release, Transitioning)
        self.assertFalse(capture_before_release)

    def test_exact_acknowledgement_commits_once_and_mismatches_do_nothing(self):
        message = self._begin_server_to_first()

        self.assertIn("handoff_id", message)
        self.assertEqual(message["handoff_id"], "handoff-1")
        self.assertIsInstance(self.router.state, Transitioning)
        self.assertFalse(any(item[:2] == ("server", "remote") for item in self.log))
        for overrides in (
            {"handoff_id": "wrong"},
            {"session_id": "session-2"},
            {"machine_id": "client-2"},
            {"topology_version": 6},
        ):
            self.assertFalse(self._ack(message, **overrides))
            self.assertIsInstance(self.router.state, Transitioning)

        self.assertTrue(self._ack(message))
        self.assertEqual(self.router.active_session_id, "session-1")
        self.assertEqual(
            [item for item in self.log if item[:2] == ("server", "remote")],
            [("server", "remote", "session-1")],
        )
        self.assertFalse(self._ack(message))

    def test_deadline_restores_server_reports_session_and_rejects_late_ack(self):
        message = self._begin_server_to_first()

        self.assertTrue(self.deadlines.calls, "handoff deadline was not scheduled")
        self.assertEqual(self.deadlines.calls[0][0], 0.75)
        self.deadlines.fire_next()

        self.assertEqual(
            self.router.state,
            LocalServer("server-primary", (960, 540)),
        )
        self.assertEqual(self.failures, [("session-1", "handoff timeout")])
        self.assertFalse(self._ack(message))

    def test_send_failure_restores_server_and_reports_destination_once(self):
        self.sessions["client-1"].control_lane.send_result = False

        self.assertTrue(self.router.handle_edge(
            "server",
            "server-primary",
            "right",
            0.5,
            topology_version=7,
        ))
        self.assertTrue(self.failure_seen.wait(0.2))

        self.assertEqual(
            self.router.state,
            LocalServer("server-primary", (960, 540)),
        )
        self.assertEqual(self.failures, [("session-1", "switch send failed")])
        self.deadlines.fire_next()
        self.assertEqual(len(self.failures), 1)

    def test_pending_server_and_client_handoffs_drop_movement(self):
        first_message = self._begin_server_to_first()
        self.assertFalse(self.router.forward_mouse_move(4, 5))
        self.assertTrue(self._ack(first_message))

        self.sessions["client-2"].control_lane.sent.clear()
        self.assertTrue(self.router.handle_edge(
            "client-1",
            "client-1-primary",
            "right",
            0.5,
            session_id="session-1",
            topology_version=7,
        ))
        self.assertTrue(self.sessions["client-2"].control_lane.sent.wait(0.2))
        self.assertIsInstance(self.router.state, Transitioning)
        self.assertFalse(self.router.forward_mouse_move(6, 7))

    def test_pause_and_destination_loss_cancel_pending_acknowledgement(self):
        message = self._begin_server_to_first()
        self.assertIn("handoff_id", message)
        self.assertTrue(self.router.pause("apply"))
        self.assertIsInstance(self.router.state, Paused)
        self.assertFalse(self._ack(message))

        self.assertTrue(self.router.resume())
        self.sessions["client-1"].control_lane.sent.clear()
        message = self._begin_server_to_first()
        self.assertTrue(self.router.destination_lost("session-1"))
        self.assertEqual(
            self.router.state,
            LocalServer("server-primary", (960, 540)),
        )
        self.assertFalse(self._ack(message))


class ServerInputRouterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.sessions = {
            "client-1": SimpleNamespace(
                session_id="session-1",
                peer_identity="client-1",
                ready=True,
                control_lane=RecordingLane("session-1", self.log),
            ),
            "client-2": SimpleNamespace(
                session_id="session-2",
                peer_identity="client-2",
                ready=True,
                control_lane=RecordingLane("session-2", self.log),
            ),
        }
        self.effects = RecordingInputEffects(self.log)
        self.router = InputRouter(
            active_chain(),
            session_for_machine=self.sessions.get,
            input_effects=self.effects,
        )

    def test_server_edge_callbacks_target_the_graph_session_and_accept_its_return(self):
        topology = active_chain()
        log = []
        route_updates = []
        sessions = (
            SimpleNamespace(
                session_id="session-1",
                peer_identity="client-1",
                ready=True,
                control_lane=RecordingLane("session-1", log),
            ),
            SimpleNamespace(
                session_id="session-2",
                peer_identity="client-2",
                ready=True,
                control_lane=RecordingLane("session-2", log),
            ),
        )

        class Input:
            screen_width = 1920
            screen_height = 1080

            def configure_topology_edges(self, value, machine_id):
                log.append(("configure", value.version, machine_id))

            def start_edge_detection(self):
                log.append(("server", "edge"))

            def stop(self):
                log.append(("server", "stop"))

            def start_keyboard_capture(self):
                log.append(("server", "capture"))

            def stop_keyboard_capture(self):
                log.append(("server", "uncapture"))

            def inject_position(self, x, y):
                log.append(("server", "position", (x, y)))

        server = ConduitServer.__new__(ConduitServer)
        server.session_registry = SimpleNamespace(active_sessions=lambda: sessions)
        server.input_handler = Input()
        server.control_connected = True
        server.on_capture_start = None
        server.on_capture_stop = None
        server.on_topology_edit_cancel = None
        server.pressed_keys = set()
        server.forwarded_keys = {}
        server._paste_route_lock = __import__("threading").RLock()
        server.file_paste_service = None
        server.paste_coordinator = SimpleNamespace(
            set_route=lambda *args: route_updates.append(args)
        )
        server.clipboard_offer_state = SimpleNamespace(current_offer=None)

        server._install_topology(topology)
        server.on_edge_hit(
            "right",
            0.5,
            TopologyEdgeRegion(
                "server",
                "server-primary",
                "right",
                "client-1",
                "client-1-primary",
                "left",
                NativeRect(0, 0, 1920, 1080),
                NativeRect(0, 0, 2560, 1440),
            ),
        )
        self.assertEqual(route_updates, [])
        self.assertTrue(sessions[0].control_lane.sent.wait(0.2))
        switch = next(
            item[1]
            for item in reversed(log)
            if len(item) == 2
            and item[0] == "session-1"
            and item[1].get("type") == "switch"
        )
        self.assertTrue(server.on_switch_ack({
            "session_id": "session-1",
            "peer_identity": "client-1",
            "handoff_id": switch["handoff_id"],
            "topology_version": 7,
        }))
        self.assertEqual(len(route_updates), 1)

        self.assertEqual(server.input_router.active_session_id, "session-1")
        self.assertTrue(
            any(
                item[0] == "session-1" and item[1]["type"] == "switch"
                for item in log
            )
        )
        self.assertFalse(any(item[0] == "session-2" for item in log))

        server.on_switch_back(
            {
                "session_id": "session-1",
                "peer_identity": "client-1",
                "source_display_id": "client-1-primary",
                "source_side": "left",
                "ratio": 0.5,
                "topology_version": 7,
            }
        )

        self.assertIsInstance(server.input_router.state, LocalServer)

    def test_handoff_failure_suspends_then_closes_only_failed_session(self):
        events = []
        disconnected = threading.Event()
        server = ConduitServer.__new__(ConduitServer)
        server.suspend_input_routing = lambda reason: events.append(
            ("suspend", reason)
        )
        server.control_network = SimpleNamespace(
            disconnect=lambda session_id=None: (
                events.append(("disconnect", session_id)),
                disconnected.set(),
                True,
            )[-1]
        )

        server._on_handoff_failed("session-1", "handoff timeout")

        self.assertTrue(disconnected.wait(0.2))
        self.assertEqual(events, [
            ("suspend", "cursor handoff failed"),
            ("disconnect", "session-1"),
        ])

    def test_active_destination_loss_returns_center_with_empty_held_state(self):
        self.router.handle_edge(
            "server",
            "server-primary",
            "right",
            0.5,
            topology_version=7,
        )
        self.assertTrue(acknowledge_latest(
            self.router,
            self.sessions["client-1"],
            self.log,
        ))
        self.router.forward_key_press({"type": "special", "value": "shift"})
        self.router.forward_button("left", True)

        self.assertTrue(self.router.destination_lost("session-1"))

        self.assertEqual(
            self.router.state,
            LocalServer("server-primary", (960, 540)),
        )


class ClientEdgeReportingTests(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.sessions = {
            "client-1": SimpleNamespace(
                session_id="session-1",
                peer_identity="client-1",
                ready=True,
                control_lane=RecordingLane("session-1", self.log),
            ),
            "client-2": SimpleNamespace(
                session_id="session-2",
                peer_identity="client-2",
                ready=True,
                control_lane=RecordingLane("session-2", self.log),
            ),
        }
        self.effects = RecordingInputEffects(self.log)
        self.router = InputRouter(
            active_chain(),
            session_for_machine=self.sessions.get,
            input_effects=self.effects,
        )

    def test_client_releases_all_injected_input_before_reporting_graph_edge(self):
        events = []

        class Input:
            client_edge = "right"

            def release_all_injected_input(self):
                events.append("release")

        class Network:
            def send_message(self, message):
                events.append(dict(message))
                return True

        client = ConduitClient.__new__(ConduitClient)
        client.is_active = True
        client.input_handler = Input()
        client.control_network = Network()
        client.file_paste_service = None
        client.paste_coordinator = SimpleNamespace(set_route=lambda *args: None)
        client._paste_route_lock = __import__("threading").RLock()
        client.active_topology_config = {"topology_version": 7}
        region = TopologyEdgeRegion(
            "client-1",
            "client-1-primary",
            "right",
            "client-2",
            "client-2-primary",
            "left",
            NativeRect(0, 0, 2560, 1440),
            NativeRect(0, 0, 1280, 1024),
        )

        client.on_client_edge_hit("right", 0.25, region)

        self.assertEqual(events[0], "release")
        self.assertEqual(events[1]["type"], "switch_back")
        self.assertEqual(events[1]["source_machine_id"], "client-1")
        self.assertEqual(events[1]["source_display_id"], "client-1-primary")
        self.assertEqual(events[1]["source_side"], "right")
        self.assertEqual(events[1]["topology_version"], 7)

    def test_client_ignores_switch_from_a_stale_topology_version(self):
        positions = []
        messages = []
        client = ConduitClient.__new__(ConduitClient)
        client.is_active = False
        client.active_topology_config = {"version": 8}
        client.control_network = SimpleNamespace(
            send_message=lambda message: messages.append(dict(message)) or True
        )
        client.input_handler = SimpleNamespace(
            inject_position=lambda x, y: positions.append((x, y)),
            screen_width=1920,
            screen_height=1080,
        )
        client._apply_clipboard_offer_route = lambda: None

        self.assertFalse(
            client.on_switch({
                "topology_version": 7,
                "handoff_id": "handoff-1",
                "direction": "right",
                "ratio": 0.5,
                "destination_display_id": "client-primary",
                "destination_side": "left",
            })
        )

        self.assertFalse(client.is_active)
        self.assertEqual(positions, [])
        self.assertEqual(
            messages,
            [
                {
                    "type": "switch_back",
                    "source_display_id": "client-primary",
                    "source_side": "left",
                    "ratio": 0.5,
                    "topology_version": 7,
                }
            ],
        )
        self.assertEqual(self.router.held_keys, ())
        self.assertEqual(self.router.held_buttons, ())

    def test_client_rejects_switch_without_a_nonempty_handoff_id(self):
        for handoff_id in (None, ""):
            with self.subTest(handoff_id=handoff_id):
                input_handler = ClientSwitchInput()
                messages = []
                client = ConduitClient.__new__(ConduitClient)
                client.is_active = False
                client.active_topology_config = {"version": 7}
                client.control_network = SimpleNamespace(
                    send_message=lambda message: messages.append(dict(message)) or True
                )
                client.input_handler = input_handler
                client._apply_clipboard_offer_route = lambda: None
                data = {
                    "topology_version": 7,
                    "handoff_id": handoff_id,
                    "direction": "right",
                    "position": [3, 720],
                }

                self.assertFalse(client.on_switch(data))
                self.assertFalse(client.is_active)
                self.assertEqual(input_handler.positions, [])
                self.assertEqual(messages, [])

    def test_client_ack_failure_deactivates_releases_and_clears_edges(self):
        messages = []
        input_handler = ClientSwitchInput()

        def send(message):
            messages.append(dict(message))
            return message.get("type") != "switch_ack"

        client = ConduitClient.__new__(ConduitClient)
        client.is_active = False
        client.active_topology_config = {"version": 7}
        client.control_network = SimpleNamespace(send_message=send)
        client.input_handler = input_handler
        client._apply_clipboard_offer_route = lambda: None

        self.assertFalse(client.on_switch({
            "topology_version": 7,
            "handoff_id": "handoff-1",
            "direction": "right",
            "position": [3, 720],
        }))

        self.assertFalse(client.is_active)
        self.assertEqual(input_handler.positions, [(3, 720)])
        self.assertEqual(input_handler.releases, 1)
        self.assertEqual(input_handler.edge_sets[-1], ())
        self.assertEqual(messages, [{
            "type": "switch_ack",
            "handoff_id": "handoff-1",
            "topology_version": 7,
        }])

    def test_client_activates_only_after_exact_switch_ack_is_sent(self):
        messages = []
        input_handler = ClientSwitchInput()
        client = ConduitClient.__new__(ConduitClient)
        client.is_active = False
        client.active_topology_config = {"version": 7}
        client.control_network = SimpleNamespace(
            send_message=lambda message: messages.append(dict(message)) or True
        )
        client.input_handler = input_handler
        client._apply_clipboard_offer_route = lambda: messages.append(
            {"type": "clipboard_route_applied"}
        )

        self.assertTrue(client.on_switch({
            "topology_version": 7,
            "handoff_id": "handoff-1",
            "direction": "right",
            "position": [3, 720],
        }))

        self.assertTrue(client.is_active)
        self.assertEqual(input_handler.positions, [(3, 720)])
        self.assertEqual(messages, [
            {
                "type": "switch_ack",
                "handoff_id": "handoff-1",
                "topology_version": 7,
            },
            {"type": "clipboard_route_applied"},
        ])

    def test_inactive_client_ignores_remote_input_packets(self):
        calls = []
        client = ConduitClient.__new__(ConduitClient)
        client.is_active = False
        client.speed_scale_x = 1
        client.speed_scale_y = 1
        client.input_handler = SimpleNamespace(
            inject_move=lambda *args: calls.append(("move", args)),
            inject_click=lambda *args: calls.append(("click", args)),
            inject_scroll=lambda *args: calls.append(("scroll", args)),
            inject_key_press=lambda *args: calls.append(("press", args)),
            inject_key_release=lambda *args: calls.append(("release", args)),
        )

        client.on_mouse_move({"dx": 4, "dy": 5})
        client.on_mouse_click({"button": "left", "pressed": True})
        client.on_mouse_scroll({"dx": 0, "dy": 1})
        client.on_key_press({"key": {"type": "char", "value": "x"}})
        client.on_key_release({"key": {"type": "char", "value": "x"}})

        self.assertEqual(calls, [])

    def test_failed_forward_send_is_destination_loss_and_returns_center(self):
        self.router.handle_edge(
            "server",
            "server-primary",
            "right",
            0.5,
            topology_version=7,
        )
        self.assertTrue(acknowledge_latest(
            self.router,
            self.sessions["client-1"],
            self.log,
        ))
        self.sessions["client-1"].control_lane.send_result = False

        self.assertFalse(self.router.forward_mouse_move(3, 4))

        self.assertEqual(
            self.router.state,
            LocalServer("server-primary", (960, 540)),
        )

    def test_stale_repeated_and_client_local_events_cannot_change_destination(self):
        initial = self.router.state
        self.assertFalse(
            self.router.handle_edge(
                "client-1",
                "client-1-primary",
                "right",
                0.5,
                session_id="session-1",
                topology_version=7,
            )
        )
        self.assertFalse(
            self.router.handle_edge(
                "server",
                "server-primary",
                "right",
                0.5,
                topology_version=6,
            )
        )
        self.assertEqual(self.router.state, initial)

        self.router.handle_edge(
            "server",
            "server-primary",
            "right",
            0.5,
            topology_version=7,
        )
        self.assertTrue(acknowledge_latest(
            self.router,
            self.sessions["client-1"],
            self.log,
        ))
        destination = self.router.state
        self.assertFalse(
            self.router.handle_edge(
                "server",
                "server-primary",
                "right",
                0.5,
                topology_version=7,
            )
        )
        self.assertEqual(self.router.state, destination)

    def test_pause_releases_to_server_and_rejects_transitions_until_resumed(self):
        self.router.handle_edge(
            "server",
            "server-primary",
            "right",
            0.5,
            topology_version=7,
        )
        self.assertTrue(acknowledge_latest(
            self.router,
            self.sessions["client-1"],
            self.log,
        ))

        self.router.pause("apply")

        self.assertIsInstance(self.router.state, Paused)
        self.assertFalse(
            self.router.handle_edge(
                "server",
                "server-primary",
                "right",
                0.5,
                topology_version=7,
            )
        )
        self.assertTrue(self.router.resume())
        self.assertEqual(
            self.router.state,
            LocalServer("server-primary", (960, 540)),
        )


if __name__ == "__main__":
    unittest.main()
