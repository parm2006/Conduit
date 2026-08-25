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

    def test_client_connection_stays_unroutable_until_topology_is_applied(self):
        server = ConduitServer.__new__(ConduitServer)
        server._active_edge_side = "right"
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

        server.activate_client_topology("left")

        self.assertEqual(server.input_handler.starts, 1)
        self.assertEqual(
            server.control_network.messages[-1]["type"],
            "layout_config",
        )
        self.assertEqual(
            server.control_network.messages[-1]["position"],
            "left",
        )

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
        server._active_edge_side = "right"
        server.switching_to_client = False
        server.control_network = RecordingNetwork()
        server.input_handler = type(
            "Input",
            (),
            {
                "stop": lambda self: None,
                "start_keyboard_capture": lambda self: None,
            },
        )()
        server.on_capture_start = None
        cancelled = []
        server.on_topology_edit_cancel = lambda: cancelled.append(True)
        server._apply_clipboard_offer_route = lambda: None

        server.on_edge_hit("left", 0.5, region)

        message = server.control_network.messages[0]
        self.assertEqual(message["destination_display_id"], "client-primary")
        self.assertEqual(message["destination_side"], "right")
        self.assertEqual(message["destination_rect"], [0, 0, 1920, 1080])
        self.assertEqual(message["source_display_id"], "server-left")
        self.assertEqual(message["source_rect"], [-2560, 0, 0, 1440])
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
        client._apply_clipboard_offer_route = lambda: None

        client.on_switch(
            {
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

        self.assertEqual(positions, [(1921, 720)])
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
        positions = []
        server = ConduitServer.__new__(ConduitServer)
        server._paste_route_lock = None
        server.switching_to_client = True
        server.pressed_keys = set()
        server.forwarded_keys = {}
        server.control_network = RecordingNetwork()
        server.input_handler = type(
            "Input",
            (),
            {
                "screen_width": 1920,
                "screen_height": 1080,
                "stop_keyboard_capture": lambda self: None,
                "inject_position": lambda self, x, y: positions.append((x, y)),
                "start_edge_detection": lambda self, *args: None,
            },
        )()
        server.on_capture_stop = None
        server._active_edge_side = "left"
        server._apply_clipboard_offer_route = lambda: None

        server.on_switch_back(
            {
                "ratio": 0.5,
                "destination_display_id": "server-left",
                "destination_side": "left",
                "destination_rect": [-2560, 0, 0, 1440],
            }
        )

        self.assertEqual(positions, [(-2559, 720)])

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
        server.switching_to_client = False
        server.on_capture_stop = None
        server._topology_ack_lock = threading.Lock()
        server._topology_ack_event = None
        server._topology_ack_version = None
        server._topology_commit_ack_event = None
        server._topology_commit_ack_version = None

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
        self.assertLess(events.index(("configure", 3, "server")), events.index(("persist", 3)))
        self.assertEqual(server.active_topology, candidate)
        self.assertIn(("complete", True), events)
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
        server.switching_to_client = False
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
        self.assertEqual(outcomes, [False])
        self.assertIn(
            {"type": "topology_rollback", "version": 3},
            server.control_network.messages,
        )


if __name__ == "__main__":
    unittest.main()
