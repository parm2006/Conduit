import unittest
import threading

from app.client import ConduitClient
from app.display_topology import (
    Display,
    DraftTopology,
    MachineDisplayGroup,
    NativeRect,
    PlacedMachine,
)
from app.server import ConduitServer
from app.input_handler import TopologyEdgeRegion
from app.input_router import LocalServer


def group():
    return MachineDisplayGroup(
        "client",
        "ParthSurface",
        (
            Display(
                "display",
                NativeRect(0, 0, 1920, 1080),
                100,
                0,
                True,
            ),
        ),
    )


class RecordingNetwork:
    def __init__(self):
        self.messages = []

    def send_message(self, message):
        self.messages.append(message)
        return True


class Lifecycle:
    def __init__(self):
        self.starts = 0
        self.screen_width = 1920
        self.screen_height = 1080

    def start(self, *args):
        self.starts += 1

    def start_edge_detection(self, *args):
        self.starts += 1


class TopologyProtocolTests(unittest.TestCase):
    def test_client_topology_suspend_releases_input_and_disables_edges(self):
        events = []
        client = ConduitClient.__new__(ConduitClient)
        client.is_active = True
        client.pending_topology = {"version": 5}
        client.committed_topology = (5, {"version": 4})
        client.input_handler = type(
            "Input",
            (),
            {
                "release_all_injected_input": lambda self: events.append("release"),
                "set_client_topology_edges": lambda self, edges: events.append(
                    ("edges", tuple(edges))
                ),
            },
        )()

        client.on_topology_suspend({"reason": "client_disconnected"})

        self.assertEqual(events, ["release", ("edges", ())])
        self.assertFalse(client.is_active)
        self.assertEqual(client.pending_topology, {"version": 5})
        self.assertEqual(client.committed_topology, (5, {"version": 4}))

    def test_ready_client_loss_pauses_entire_cluster_input_graph(self):
        events = []
        server = ConduitServer.__new__(ConduitServer)
        server._topology_ack_lock = threading.Lock()
        server._topology_transaction = None
        server._active_topology_session_ids = {"lost", "survivor"}
        server._clipboard_endpoint_ids = {}
        server._clipboard_sessions_by_endpoint = {}
        server.input_router = type(
            "Router",
            (),
            {
                "topology": type(
                    "Topology",
                    (),
                    {
                        "server_primary_center": lambda self: (
                            "primary",
                            (960, 540),
                        )
                    },
                )(),
                "request_pause": lambda self, reason: events.append(
                    ("request-pause", reason)
                )
                or True,
                "pause": lambda self, reason: events.append(("pause", reason))
                or True,
            },
        )()
        server.input_handler = type(
            "Input",
            (),
            {
                "stop": lambda self: events.append("input-stop"),
                "stop_keyboard_capture": lambda self: events.append(
                    "keyboard-stop"
                ),
                "inject_position": lambda self, x, y: events.append(
                    ("position", x, y)
                ),
            },
        )()
        server.pressed_keys = {"ctrl"}
        server.on_capture_stop = lambda: events.append("capture-stop")
        survivor = type(
            "Session",
            (),
            {
                "session_id": "survivor",
                "peer_identity": "survivor-machine",
                "ready": True,
            },
        )()
        server.session_registry = type(
            "Registry",
            (),
            {"ready_sessions": lambda self: (survivor,)},
        )()
        server.control_network = type(
            "Network",
            (),
            {
                "send_message": lambda self, message, session_id=None: events.append(
                    ("send", session_id, dict(message))
                )
                or True,
            },
        )()
        server.cluster_file_router = None
        server.clipboard_hub = None

        server.on_client_disconnected("lost")

        self.assertTrue(server.routing_suspended)
        self.assertIn(("pause", "client disconnected"), events)
        self.assertIn("input-stop", events)
        self.assertIn("capture-stop", events)
        self.assertIn(
            (
                "send",
                "survivor",
                {"type": "topology_suspend", "reason": "client_disconnected"},
            ),
            events,
        )
        self.assertEqual(server.pressed_keys, set())

    def test_disconnect_reclaims_local_input_before_waiting_for_router_lock(self):
        events = []
        pause_entered = threading.Event()
        release_pause = threading.Event()

        class Router:
            topology = type(
                "Topology",
                (),
                {"server_primary_center": lambda self: ("primary", (960, 540))},
            )()

            def request_pause(self, reason):
                events.append(("request-pause", reason))

            def pause(self, reason, blocking=True):
                events.append(("pause", reason, blocking))
                pause_entered.set()
                release_pause.wait(1)
                return True

        class Input:
            def stop(self):
                events.append("input-stop")

            def stop_keyboard_capture(self):
                events.append("keyboard-stop")

            def inject_position(self, x, y):
                events.append(("position", x, y))

        survivor = type(
            "Session",
            (),
            {"session_id": "survivor", "peer_identity": "survivor", "ready": True},
        )()
        server = ConduitServer.__new__(ConduitServer)
        server.routing_suspended = False
        server.input_router = Router()
        server.input_handler = Input()
        server.on_capture_stop = None
        server.pressed_keys = set()
        server.session_registry = type(
            "Registry",
            (),
            {"ready_sessions": lambda self: (survivor,)},
        )()
        server.control_network = type(
            "Network",
            (),
            {
                "send_message": lambda self, message, session_id=None: events.append(
                    ("send", session_id, dict(message))
                )
                or True,
            },
        )()

        worker = threading.Thread(
            target=lambda: server.suspend_input_routing("client disconnected")
        )
        worker.start()
        try:
            worker.join(0.2)
            self.assertFalse(
                worker.is_alive(),
                "disconnect callback waited for router cleanup",
            )
            self.assertTrue(
                pause_entered.wait(1),
                "background router cleanup did not begin",
            )
            self.assertIn(("request-pause", "client disconnected"), events)
            self.assertIn("input-stop", events)
            self.assertIn(("position", 960, 540), events)
            self.assertIn(
                (
                    "send",
                    "survivor",
                    {"type": "topology_suspend", "reason": "client_disconnected"},
                ),
                events,
            )
            self.assertTrue(server.routing_suspended)
        finally:
            release_pause.set()

    def test_suspension_latch_rejects_every_server_input_entry_point(self):
        routed = []

        class Router:
            topology = type("Topology", (), {"version": 7})()

            def handle_edge(self, *args, **kwargs):
                routed.append(("edge", args, kwargs))
                return True

            def forward_mouse_move(self, *args):
                routed.append(("move", args))
                return True

            def forward_button(self, *args):
                routed.append(("button", args))
                return True

            def forward_scroll(self, *args):
                routed.append(("scroll", args))
                return True

            def forward_key_press(self, *args):
                routed.append(("key-press", args))
                return True

            def forward_key_release(self, *args):
                routed.append(("key-release", args))
                return True

        server = ConduitServer.__new__(ConduitServer)
        server.routing_suspended = True
        server.input_router = Router()
        server._paste_route_lock = threading.RLock()
        server.file_paste_service = None
        server.on_topology_edit_cancel = None
        server._apply_clipboard_offer_route = lambda: None
        server.pressed_keys = set()
        server.paste_coordinator = type(
            "Paste",
            (),
            {
                "on_key_press": lambda self, value: False,
                "on_key_release": lambda self, value: False,
            },
        )()

        region = TopologyEdgeRegion(
            "server",
            "primary",
            "right",
            "client",
            "display",
            "left",
            NativeRect(0, 0, 1920, 1080),
            NativeRect(0, 0, 1920, 1080),
        )
        switch_back = {
            "peer_identity": "client",
            "session_id": "session",
            "source_display_id": "display",
            "source_side": "left",
            "ratio": 0.5,
            "topology_version": 7,
        }

        self.assertFalse(server.on_edge_hit("right", 0.5, region))
        self.assertFalse(server.on_switch_back(switch_back))
        self.assertFalse(server.on_mouse_move(1, 2))
        self.assertFalse(server.on_mouse_click("left", True))
        self.assertFalse(server.on_mouse_scroll(0, 1))
        self.assertFalse(server.on_key_press({"type": "char", "value": "a"}))
        self.assertFalse(server.on_key_release({"type": "char", "value": "a"}))
        self.assertEqual(routed, [])

    def test_ready_client_sends_its_real_display_inventory(self):
        client = ConduitClient.__new__(ConduitClient)
        client.control_network = RecordingNetwork()
        client.display_discovery = type(
            "Discovery",
            (),
            {"discover": lambda self, machine_id, windows_name: group()},
        )()
        client.machine_id = "client"
        client.windows_name = "ParthSurface"

        sent = client.send_display_inventory()

        self.assertTrue(sent)
        self.assertEqual(client.control_network.messages[0]["type"], "display_inventory")
        self.assertEqual(
            client.control_network.messages[0]["inventory"]["windows_name"],
            "ParthSurface",
        )

    def test_client_display_change_reuses_discovered_group_and_marks_reason(self):
        client = ConduitClient.__new__(ConduitClient)
        client.control_network = RecordingNetwork()
        client.display_group = None
        client.display_monitor = type(
            "Monitor",
            (),
            {"update_baseline": lambda self, group: None},
        )()
        changed = group()

        sent = client._on_display_group_changed(changed)

        self.assertTrue(sent)
        self.assertIs(client.display_group, changed)
        self.assertEqual(
            client.control_network.messages,
            [{
                "type": "display_inventory",
                "inventory": client.control_network.messages[0]["inventory"],
                "reason": "display_changed",
            }],
        )

    def test_client_connection_stays_unroutable_and_scalar_layout_is_rejected(self):
        server = ConduitServer.__new__(ConduitServer)
        server.control_network = RecordingNetwork()
        server.input_handler = Lifecycle()
        server.clipboard = Lifecycle()
        server.hotkey_monitor = Lifecycle()
        server.pressed_keys = {"old"}
        server._offer_file_lane = lambda: None

        server.on_client_connected()

        self.assertEqual(server.input_handler.starts, 0)
        self.assertEqual(server.clipboard.starts, 1)
        self.assertEqual(server.hotkey_monitor.starts, 1)
        self.assertEqual(
            server.control_network.messages,
            [{"type": "display_inventory_request"}],
        )

        with self.assertRaises(TypeError):
            server.activate_client_topology("left")
        self.assertEqual(server.input_handler.starts, 0)

    def test_topology_activation_distributes_real_source_and_destination_displays(self):
        server_group = MachineDisplayGroup(
            "server",
            "ParthPC",
            (
                Display(
                    "server-left",
                    NativeRect(-2560, 0, 0, 1440),
                    150,
                    0,
                    False,
                ),
                Display(
                    "server-primary",
                    NativeRect(0, 0, 1920, 1080),
                    100,
                    0,
                    True,
                ),
            ),
        )
        client_group = group()
        active = DraftTopology(
            "server",
            (
                PlacedMachine(server_group, 0, 0),
                PlacedMachine(client_group, -2, 0),
            ),
        ).validate().validated.activate(2)

        class Input(Lifecycle):
            def configure_topology_edges(self, topology, machine_id):
                self.configured = (topology, machine_id)

        server = ConduitServer.__new__(ConduitServer)
        server.control_network = RecordingNetwork()
        server.input_handler = Input()

        server.activate_client_topology(active)

        message = server.control_network.messages[0]
        self.assertEqual(server.input_handler.configured, (active, "server"))
        self.assertEqual(message["position"], "left")
        self.assertEqual(message["server_display_id"], "server-left")
        self.assertEqual(message["server_rect"], [-2560, 0, 0, 1440])
        self.assertEqual(message["client_display_id"], "display")
        self.assertEqual(message["client_rect"], [0, 0, 1920, 1080])
        self.assertEqual(message["client_edge"], "right")

    def test_server_switch_message_uses_the_display_edge_that_was_actually_crossed(self):
        region = TopologyEdgeRegion(
            "server",
            "server-left",
            "left",
            "client",
            "client-primary",
            "right",
            NativeRect(-2560, 0, 0, 1440),
            NativeRect(0, 0, 1920, 1080),
        )
        server = ConduitServer.__new__(ConduitServer)
        server._paste_route_lock = None
        calls = []
        server.input_router = type(
            "Router",
            (),
            {
                "topology": type("Topology", (), {"version": 2})(),
                "handle_edge": lambda self, *args, **kwargs: calls.append(
                    (args, kwargs)
                ) or True,
            },
        )()
        server.file_paste_service = None
        cancelled = []
        server.on_topology_edit_cancel = lambda: cancelled.append(True)
        server._apply_clipboard_offer_route = lambda: None

        server.on_edge_hit("left", 0.5, region)

        self.assertEqual(
            calls,
            [
                (
                    ("server", "server-left", "left", 0.5),
                    {"topology_version": 2},
                )
            ],
        )
        self.assertEqual(cancelled, [True])

    def test_client_enters_the_requested_physical_display_at_the_scaled_ratio(self):
        positions = []
        configured = []
        client = ConduitClient.__new__(ConduitClient)
        client.input_handler = type(
            "Input",
            (),
            {
                "screen_width": 1920,
                "screen_height": 1080,
                "inject_position": lambda self, x, y: positions.append((x, y)),
                "set_client_topology_edge": lambda self, region: configured.append(region),
            },
        )()
        client.is_active = False
        client.active_topology_config = {"version": 2}
        client.control_network = RecordingNetwork()
        client._apply_clipboard_offer_route = lambda: None

        client.on_switch(
            {
                "handoff_id": "handoff-1",
                "topology_version": 2,
                "direction": "right",
                "ratio": 0.5,
                "destination_display_id": "client-hdmi",
                "destination_side": "left",
                "destination_rect": [1920, 0, 4480, 1440],
                "source_display_id": "server-left",
                "source_side": "right",
                "source_rect": [-2560, 0, 0, 1440],
            }
        )

        self.assertEqual(positions, [(1923, 720)])
        self.assertEqual(configured[0].source_display_id, "client-hdmi")
        self.assertEqual(configured[0].destination_display_id, "server-left")

    def test_client_layout_uses_physical_edge_rectangles_for_return_and_speed_scaling(self):
        configured = []
        client = ConduitClient.__new__(ConduitClient)
        client.input_handler = type(
            "Input",
            (),
            {
                "screen_width": 1920,
                "screen_height": 1080,
                "set_layout": lambda self, **values: None,
                "set_client_topology_edge": lambda self, region: configured.append(region),
            },
        )()

        client.on_layout_config(
            {
                "position": "right",
                "server_width": 1920,
                "server_height": 1080,
                "server_display_id": "server-primary",
                "server_rect": [0, 0, 1920, 1080],
                "client_display_id": "client-hdmi",
                "client_rect": [1920, 0, 4480, 1440],
                "client_edge": "left",
            }
        )

        self.assertEqual(client.speed_scale_x, 2560 / 1920)
        self.assertEqual(client.speed_scale_y, 1440 / 1080)
        self.assertEqual(configured[0].source_display_id, "client-hdmi")
        self.assertEqual(configured[0].source_side, "left")
        self.assertEqual(configured[0].destination_display_id, "server-primary")

    def test_client_return_message_preserves_the_physical_server_destination(self):
        region = TopologyEdgeRegion(
            "client",
            "client-hdmi",
            "left",
            "server",
            "server-primary",
            "right",
            NativeRect(1920, 0, 4480, 1440),
            NativeRect(0, 0, 1920, 1080),
        )
        client = ConduitClient.__new__(ConduitClient)
        client.is_active = True
        client.input_handler = type(
            "Input",
            (),
            {
                "client_edge": "right",
                "release_all_injected_keys": lambda self: None,
            },
        )()
        client.control_network = RecordingNetwork()

        client.on_client_edge_hit("left", 0.5, region)

        message = client.control_network.messages[0]
        self.assertEqual(message["destination_display_id"], "server-primary")
        self.assertEqual(message["destination_side"], "right")
        self.assertEqual(message["destination_rect"], [0, 0, 1920, 1080])

    def test_server_return_warps_into_the_requested_attached_display(self):
        calls = []
        server = ConduitServer.__new__(ConduitServer)
        server._paste_route_lock = None
        server.input_router = type(
            "Router",
            (),
            {
                "state": LocalServer("server-primary", (960, 540)),
                "handle_edge": lambda self, *args, **kwargs: calls.append(
                    (args, kwargs)
                ) or True,
            },
        )()
        server._apply_clipboard_offer_route = lambda: None

        server.on_switch_back(
            {
                "session_id": "session-client",
                "peer_identity": "client",
                "source_display_id": "client-primary",
                "source_side": "right",
                "ratio": 0.5,
                "topology_version": 2,
            }
        )

        self.assertEqual(
            calls,
            [
                (
                    ("client", "client-primary", "right", 0.5),
                    {
                        "session_id": "session-client",
                        "topology_version": 2,
                    },
                )
            ],
        )

    def test_client_apply_releases_input_before_acknowledging_candidate(self):
        events = []
        client = ConduitClient.__new__(ConduitClient)
        client.is_active = True
        client.input_handler = type(
            "Input",
            (),
            {"release_all_injected_keys": lambda self: events.append("release")},
        )()
        client.on_layout_config = lambda data: events.append("layout")
        client.control_network = type(
            "Network",
            (),
            {
                "send_message": lambda self, message: events.append(
                    ("send", message)
                ) or True,
            },
        )()

        client.on_topology_apply({"version": 9})

        self.assertEqual(events[0], "release")
        self.assertEqual(events[1][1], {"type": "topology_ack", "version": 9})
        self.assertNotIn("layout", events)
        self.assertEqual(client.pending_topology["version"], 9)

        client.on_topology_commit({"version": 9})

        self.assertIn("layout", events)
        self.assertIsNone(client.pending_topology)
        self.assertIn(
            ("send", {"type": "topology_commit_ack", "version": 9}),
            events,
        )

        client.on_topology_finalize({"version": 9})

        self.assertIsNone(client.committed_topology)

    def test_client_rollback_discards_pending_candidate_without_changing_layout(self):
        events = []
        client = ConduitClient.__new__(ConduitClient)
        client.is_active = False
        client.input_handler = type(
            "Input",
            (),
            {"release_all_injected_keys": lambda self: events.append("release")},
        )()
        client.on_layout_config = lambda data: events.append("layout")
        client.control_network = type(
            "Network",
            (),
            {"send_message": lambda self, message: True},
        )()
        client.on_topology_apply({"version": 10})

        client.on_topology_rollback({"version": 10})

        self.assertIsNone(client.pending_topology)
        self.assertNotIn("layout", events)

    def test_client_rollback_restores_layout_after_commit_ack(self):
        layouts = []
        client = ConduitClient.__new__(ConduitClient)
        client.is_active = False
        client.active_topology_config = {"position": "left"}
        client.committed_topology = None
        client.input_handler = type(
            "Input",
            (),
            {"release_all_injected_keys": lambda self: None},
        )()
        client.on_layout_config = lambda data: layouts.append(dict(data))
        client.control_network = type(
            "Network",
            (),
            {"send_message": lambda self, message: True},
        )()
        client.on_topology_apply({"version": 11, "position": "right"})
        client.on_topology_commit({"version": 11})

        client.on_topology_rollback({"version": 11})

        self.assertEqual(
            layouts,
            [
                {"version": 11, "position": "right"},
                {"position": "left"},
            ],
        )
        self.assertIsNone(client.committed_topology)

    def test_server_activates_only_after_ack_and_persistence(self):
        server_group = MachineDisplayGroup(
            "server",
            "ParthPC",
            (
                Display(
                    "server-primary",
                    NativeRect(0, 0, 1920, 1080),
                    100,
                    0,
                    True,
                ),
            ),
        )
        client_group = group()
        candidate = DraftTopology(
            "server",
            (
                PlacedMachine(server_group, 0, 0),
                PlacedMachine(client_group, 1, 0),
            ),
        ).validate().validated.activate(3)
        events = []
        completed = threading.Event()

        class Input:
            screen_width = 1920
            screen_height = 1080

            def release_all_injected_keys(self):
                events.append("release")

            def stop(self):
                events.append("stop")

            def inject_position(self, x, y):
                events.append(("center", x, y))

            def configure_topology_edges(self, topology, machine_id):
                events.append(("configure", topology.version, machine_id))

            def start_edge_detection(self, *args):
                events.append("start")

        server = ConduitServer.__new__(ConduitServer)
        server.input_handler = Input()
        server.pressed_keys = set()
        server.forwarded_keys = {}
        server.on_capture_stop = None
        server._topology_ack_lock = threading.Lock()
        server._topology_ack_event = None
        server._topology_ack_version = None
        server._topology_commit_ack_event = None
        server._topology_commit_ack_version = None

        class ClipboardHub:
            def pause_delivery(self):
                events.append("clipboard-pause")

            def resume_delivery(self):
                events.append("clipboard-resume")

        server.clipboard_hub = ClipboardHub()

        class Network(RecordingNetwork):
            def send_message(self, message):
                super().send_message(message)
                if message["type"] == "topology_apply":
                    server.on_topology_ack({"version": message["version"]})
                elif message["type"] == "topology_commit":
                    server.on_topology_commit_ack(
                        {"version": message["version"]}
                    )
                return True

        server.control_network = Network()

        server.apply_topology_candidate(
            candidate,
            on_persist=lambda topology: events.append(
                ("persist", topology.version)
            ) or True,
            on_complete=lambda success: events.append(
                ("complete", success)
            ) or completed.set(),
            timeout=0.2,
        )

        self.assertTrue(completed.wait(1))
        self.assertLess(events.index(("persist", 3)), events.index(("configure", 3, "server")))
        self.assertEqual(server.active_topology, candidate)
        self.assertIn(("complete", True), events)
        self.assertLess(
            events.index("clipboard-pause"),
            events.index("clipboard-resume"),
        )
        self.assertLess(
            events.index("clipboard-resume"),
            events.index(("complete", True)),
        )
        self.assertIn(
            {"type": "topology_commit", "version": 3},
            server.control_network.messages,
        )
        self.assertIn(
            {"type": "topology_finalize", "version": 3},
            server.control_network.messages,
        )

    def test_server_rolls_back_when_client_does_not_ack_commit(self):
        server_group = MachineDisplayGroup(
            "server",
            "ParthPC",
            (
                Display(
                    "server-primary",
                    NativeRect(0, 0, 1920, 1080),
                    100,
                    0,
                    True,
                ),
            ),
        )
        validated = DraftTopology(
            "server",
            (
                PlacedMachine(server_group, 0, 0),
                PlacedMachine(group(), 1, 0),
            ),
        ).validate().validated
        previous = validated.activate(2)
        candidate = validated.activate(3)
        completed = threading.Event()
        outcomes = []
        installs = []

        class Input:
            screen_width = 1920
            screen_height = 1080

            def release_all_injected_keys(self):
                pass

            def stop(self):
                pass

            def inject_position(self, x, y):
                pass

        server = ConduitServer.__new__(ConduitServer)
        server.input_handler = Input()
        server.pressed_keys = set()
        server.forwarded_keys = {}
        server.on_capture_stop = None
        server.active_topology = previous
        server._topology_ack_lock = threading.Lock()
        server._topology_ack_event = None
        server._topology_ack_version = None
        server._topology_commit_ack_event = None
        server._topology_commit_ack_version = None
        server._install_topology = lambda topology: installs.append(
            topology.version
        )

        class ClipboardHub:
            def pause_delivery(self):
                outcomes.append("clipboard-pause")

            def resume_delivery(self):
                outcomes.append("clipboard-resume")

        server.clipboard_hub = ClipboardHub()

        class Network(RecordingNetwork):
            def send_message(self, message):
                super().send_message(message)
                if message["type"] == "topology_apply":
                    server.on_topology_ack({"version": message["version"]})
                return True

        server.control_network = Network()

        server.apply_topology_candidate(
            candidate,
            on_persist=lambda topology: outcomes.append("persist") or True,
            on_complete=lambda success: outcomes.append(success) or completed.set(),
            timeout=0.01,
        )

        self.assertTrue(completed.wait(1))
        self.assertEqual(installs, [2])
        self.assertEqual(
            outcomes,
            ["clipboard-pause", "clipboard-resume", False],
        )
        self.assertIn(
            {"type": "topology_rollback", "version": 3},
            server.control_network.messages,
        )


if __name__ == "__main__":
    unittest.main()
