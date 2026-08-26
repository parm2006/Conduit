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
from app.server import ConduitServer


def _group(machine_id, name):
    return MachineDisplayGroup(
        machine_id,
        name,
        (
            Display(
                f"{machine_id}-primary",
                NativeRect(0, 0, 1920, 1080),
                100,
                0,
                True,
            ),
        ),
    )


def _topology(version=7):
    server = _group("server", "ParthPC")
    first = _group("client-1", "ParthSurface")
    second = _group("client-2", "OfficeLaptop")
    return DraftTopology(
        "server",
        (
            PlacedMachine(first, -1, 0),
            PlacedMachine(server, 0, 0),
            PlacedMachine(second, 1, 0),
        ),
    ).validate().validated.activate(version)


class _Registry:
    def __init__(self, sessions):
        self.sessions = tuple(sessions)

    def ready_sessions(self):
        return self.sessions


class _Input:
    screen_width = 1920
    screen_height = 1080

    def __init__(self, events):
        self.events = events

    def release_all_injected_input(self):
        self.events.append("release")

    def stop(self):
        self.events.append("input-stop")

    def inject_position(self, x, y):
        self.events.append(("center", x, y))

    def configure_topology_edges(self, topology, machine_id):
        self.events.append(("install", topology.version, machine_id))

    def start_edge_detection(self):
        self.events.append("input-start")

    def clear_topology_edges(self):
        self.events.append("input-clear")


class _Barrier:
    def __init__(self, events, name):
        self.events = events
        self.name = name

    def pause(self):
        self.events.append(f"{self.name}-pause")

    def resume(self):
        self.events.append(f"{self.name}-resume")

    def pause_delivery(self):
        self.pause()

    def resume_delivery(self):
        self.resume()


class _Network:
    def __init__(self, server, behavior=None):
        self.server = server
        self.behavior = behavior or {}
        self.messages = []
        self.disconnected = []

    def send_message(self, message, session_id=None):
        self.messages.append((session_id, dict(message)))
        action = self.behavior.get((session_id, message["type"]), "ack")
        if action == "fail-send":
            return False
        if action == "disconnect":
            self.server._abort_topology_transaction(session_id=session_id)
            return True
        acknowledgement = {
            "topology_apply": self.server.on_topology_ack,
            "topology_commit": self.server.on_topology_commit_ack,
            "topology_rollback": self.server.on_topology_rollback_ack,
        }.get(message["type"])
        if acknowledgement is not None and action == "ack":
            acknowledgement(
                {"version": message["version"], "session_id": session_id}
            )
        return True

    def disconnect(self, session_id=None):
        self.disconnected.append(session_id)
        return True


class AtomicTopologyApplyTests(unittest.TestCase):
    def _server(self, events, behavior=None):
        server = ConduitServer.__new__(ConduitServer)
        server.input_handler = _Input(events)
        server.input_router = None
        server.pressed_keys = set()
        server.forwarded_keys = {}
        server.on_capture_stop = None
        server.control_connected = True
        server._topology_ack_lock = threading.Lock()
        server._topology_transaction = None
        server.session_registry = _Registry(
            (
                SimpleNamespace(
                    session_id="session-1",
                    peer_identity="client-1",
                    ready=True,
                ),
                SimpleNamespace(
                    session_id="session-2",
                    peer_identity="client-2",
                    ready=True,
                ),
            )
        )
        server.clipboard_hub = _Barrier(events, "clipboard")
        server.cluster_file_router = _Barrier(events, "file")
        server.control_network = _Network(server, behavior)
        return server

    def test_success_waits_for_both_clients_then_persists_and_installs(self):
        events = []
        completed = threading.Event()
        server = self._server(events)
        candidate = _topology()

        accepted = server.apply_topology_candidate(
            candidate,
            on_persist=lambda topology: events.append(
                ("persist", topology.version)
            ) or True,
            on_complete=lambda success: events.append(
                ("complete", success)
            ) or completed.set(),
            timeout=0.05,
        )

        self.assertTrue(accepted)
        self.assertTrue(completed.wait(1))
        self.assertEqual(server.active_topology, candidate)
        self.assertEqual(
            {
                (session_id, message["type"])
                for session_id, message in server.control_network.messages
            },
            {
                ("session-1", "topology_apply"),
                ("session-2", "topology_apply"),
                ("session-1", "topology_commit"),
                ("session-2", "topology_commit"),
                ("session-1", "topology_finalize"),
                ("session-2", "topology_finalize"),
            },
        )
        self.assertLess(events.index("release"), events.index("clipboard-pause"))
        self.assertLess(
            events.index(("center", 960, 540)),
            events.index("file-pause"),
        )
        self.assertLess(
            events.index(("persist", 7)),
            events.index(("install", 7, "server")),
        )
        self.assertLess(
            events.index(("install", 7, "server")),
            events.index("clipboard-resume"),
        )
        self.assertEqual(events[-1], ("complete", True))

    def test_timeout_rolls_back_all_recipients_and_disconnects_only_inconsistent(self):
        events = []
        completed = threading.Event()
        behavior = {
            ("session-2", "topology_apply"): "ignore",
            ("session-2", "topology_rollback"): "ignore",
        }
        server = self._server(events, behavior)
        previous = _topology(6)
        server.active_topology = previous
        persisted = []

        server.apply_topology_candidate(
            _topology(7),
            on_persist=lambda topology: persisted.append(topology) or True,
            on_complete=lambda success: completed.set(),
            timeout=0.01,
        )

        self.assertTrue(completed.wait(1))
        self.assertEqual(persisted, [])
        self.assertEqual(server.active_topology, previous)
        self.assertEqual(server.control_network.disconnected, ["session-2"])
        rollback_targets = {
            session_id
            for session_id, message in server.control_network.messages
            if message["type"] == "topology_rollback"
        }
        self.assertEqual(rollback_targets, {"session-1", "session-2"})
        self.assertIn("file-resume", events)
        self.assertIn("clipboard-resume", events)

    def test_install_failure_restores_previous_persisted_topology(self):
        events = []
        completed = threading.Event()
        server = self._server(events)
        previous = _topology(6)
        server.active_topology = previous
        persisted_versions = []

        def install(topology):
            if topology.version == 7:
                raise RuntimeError("install failed")
            server.active_topology = topology

        server._install_topology = install

        server.apply_topology_candidate(
            _topology(7),
            on_persist=lambda topology: persisted_versions.append(
                None if topology is None else topology.version
            ) or True,
            on_complete=lambda success: completed.set(),
            timeout=0.02,
        )

        self.assertTrue(completed.wait(1))
        self.assertEqual(persisted_versions, [7, 6])
        self.assertEqual(server.active_topology, previous)

    def test_persistence_failure_rolls_back_without_installing_candidate(self):
        events = []
        completed = threading.Event()
        outcomes = []
        server = self._server(events)
        previous = _topology(6)
        server.active_topology = previous

        server.apply_topology_candidate(
            _topology(7),
            on_persist=lambda topology: False,
            on_complete=lambda success: outcomes.append(success) or completed.set(),
            timeout=0.02,
        )

        self.assertTrue(completed.wait(1))
        self.assertEqual(outcomes, [False])
        self.assertEqual(server.active_topology, previous)

    def test_failed_reset_keeps_disconnect_suspension_latched(self):
        events = []
        completed = threading.Event()
        server = self._server(events)
        server.routing_suspended = True
        previous = _topology(6)
        server.active_topology = previous
        server._install_topology = lambda topology: events.append(
            ("install", topology.version, server.routing_suspended)
        )

        server.apply_topology_candidate(
            _topology(7),
            on_persist=lambda topology: False,
            on_complete=lambda success: completed.set(),
            timeout=0.02,
        )

        self.assertTrue(completed.wait(1))
        self.assertTrue(server.routing_suspended)
        self.assertIn(("install", 6, True), events)

    def test_successful_reset_clears_disconnect_suspension_before_install(self):
        events = []
        completed = threading.Event()
        server = self._server(events)
        server.routing_suspended = True
        server._install_topology = lambda topology: events.append(
            ("install", topology.version, server.routing_suspended)
        )

        server.apply_topology_candidate(
            _topology(7),
            on_persist=lambda topology: True,
            on_complete=lambda success: completed.set(),
            timeout=0.02,
        )

        self.assertTrue(completed.wait(1))
        self.assertFalse(server.routing_suspended)
        self.assertIn(("install", 7, False), events)

    def test_reentrant_apply_is_rejected_while_first_transaction_waits(self):
        events = []
        completed = threading.Event()
        server = self._server(
            events,
            {("session-2", "topology_apply"): "ignore"},
        )
        candidate = _topology()

        self.assertTrue(
            server.apply_topology_candidate(
                candidate,
                on_persist=lambda topology: True,
                on_complete=lambda success: completed.set(),
                timeout=0.5,
            )
        )
        self.assertFalse(
            server.apply_topology_candidate(
                candidate,
                on_persist=lambda topology: True,
                on_complete=lambda success: None,
                timeout=0.5,
            )
        )
        self.assertTrue(
            server._abort_topology_transaction(session_id="session-2")
        )
        self.assertTrue(completed.wait(1))
        self.assertIsNone(server.active_topology)

    def test_participant_disconnect_aborts_apply(self):
        events = []
        completed = threading.Event()
        outcomes = []
        server = self._server(
            events,
            {("session-2", "topology_apply"): "disconnect"},
        )
        server.active_topology = _topology(6)
        server._active_topology_session_ids = {"session-1", "session-2"}

        server.apply_topology_candidate(
            _topology(),
            on_persist=lambda topology: outcomes.append("persist") or True,
            on_complete=lambda success: outcomes.append(success) or completed.set(),
            timeout=0.02,
        )

        self.assertTrue(completed.wait(1))
        self.assertEqual(outcomes, [False])
        self.assertEqual(
            server._active_topology_session_ids,
            {"session-1"},
        )

    def test_ready_participant_set_is_rechecked_before_install(self):
        events = []
        completed = threading.Event()
        outcomes = []
        server = self._server(events)
        previous = _topology(6)
        server.active_topology = previous

        def persist(_topology):
            server.session_registry.sessions = server.session_registry.sessions[:1]
            return True

        server.apply_topology_candidate(
            _topology(7),
            on_persist=persist,
            on_complete=lambda success: outcomes.append(success) or completed.set(),
            timeout=0.02,
        )

        self.assertTrue(completed.wait(1))
        self.assertEqual(outcomes, [False])
        self.assertEqual(server.active_topology, previous)

    def test_stale_and_unknown_acknowledgements_are_ignored(self):
        events = []
        completed = threading.Event()
        server = self._server(
            events,
            {("session-2", "topology_apply"): "ignore"},
        )

        server.apply_topology_candidate(
            _topology(),
            on_persist=lambda topology: True,
            on_complete=lambda success: completed.set(),
            timeout=0.02,
        )

        self.assertFalse(
            server.on_topology_ack(
                {"version": 6, "session_id": "session-2"}
            )
        )
        self.assertFalse(
            server.on_topology_ack(
                {"version": 7, "session_id": "unknown-session"}
            )
        )
        self.assertTrue(completed.wait(1))

    def test_shutdown_aborts_without_resuming_barrier_services(self):
        events = []
        completed = threading.Event()
        server = self._server(
            events,
            {("session-2", "topology_apply"): "ignore"},
        )

        server.apply_topology_candidate(
            _topology(),
            on_persist=lambda topology: True,
            on_complete=lambda success: completed.set(),
            timeout=0.5,
        )
        self.assertTrue(server._abort_topology_transaction(shutdown=True))

        self.assertTrue(completed.wait(1))
        self.assertNotIn("file-resume", events)
        self.assertNotIn("clipboard-resume", events)
        self.assertFalse(
            any(
                message["type"] == "topology_rollback"
                for _session_id, message in server.control_network.messages
            )
        )


if __name__ == "__main__":
    unittest.main()
