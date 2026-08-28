import unittest

from app.client import ConduitClient
from app.input_handler import InputHandler
from app.server import ConduitServer


class RecordingNetwork:
    def __init__(self):
        self.messages = []
        self.disconnected = False

    def send_message(self, message):
        self.messages.append(message)
        return True

    def disconnect(self):
        self.disconnected = True


class Coordinator:
    def on_key_press(self, value):
        return False


class EmergencyReleaseTests(unittest.TestCase):
    def test_input_handler_releases_injected_buttons_with_keys(self):
        events = []

        class Mouse:
            def press(self, button):
                events.append(("press", button))

            def release(self, button):
                events.append(("release", button))

        handler = InputHandler.__new__(InputHandler)
        handler.mouse = Mouse()
        handler.keyboard = type(
            "Keyboard",
            (),
            {"press": lambda self, key: None, "release": lambda self, key: None},
        )()
        handler.special_key_injector = None

        handler.inject_click("left", True)
        handler.release_all_injected_input()

        self.assertEqual([event[0] for event in events], ["press", "release"])

    def test_server_captured_emergency_hotkey_delegates_to_app_shutdown(self):
        events = []
        server = ConduitServer.__new__(ConduitServer)
        server.pressed_keys = {"ctrl", "alt", "shift"}
        server.forwarded_keys = {}
        server.paste_coordinator = Coordinator()
        server.control_network = RecordingNetwork()
        server.on_app_shutdown = lambda: events.append("shutdown")

        server.on_key_press({"type": "special", "value": "esc"})

        self.assertEqual(events, ["shutdown"])
        self.assertFalse(server.control_network.disconnected)

    def test_client_releases_injected_keys_before_requesting_switch_back(self):
        events = []

        class Input:
            client_edge = "left"

            def release_all_injected_keys(self):
                events.append("release")

        class Network:
            def send_message(self, message):
                events.append(message["type"])
                return True

        client = ConduitClient.__new__(ConduitClient)
        client.is_active = True
        client.input_handler = Input()
        client.control_network = Network()

        client.on_client_edge_hit("left", 0.5)

        self.assertEqual(events, ["release", "switch_back"])

    def test_emergency_exit_releases_forwarded_modifiers_before_disconnect(self):
        server = ConduitServer.__new__(ConduitServer)
        server.pressed_keys = {"ctrl", "alt", "shift"}
        server.paste_coordinator = Coordinator()
        server.control_network = RecordingNetwork()
        server.data_network = RecordingNetwork()

        server.on_key_press({"type": "special", "value": "esc"})

        released = [
            message["key"]["value"]
            for message in server.control_network.messages
            if message["type"] == "key_release"
        ]
        self.assertEqual(released, ["alt", "ctrl", "shift"])
        self.assertTrue(server.control_network.disconnected)

    def test_reload_connection_releases_keys_and_resets_lanes(self):
        server = ConduitServer.__new__(ConduitServer)
        server.pressed_keys = {"ctrl", "alt", "shift"}
        server.forwarded_keys = {("special", "ctrl"): {"type": "special", "value": "ctrl"}}
        server.paste_coordinator = Coordinator()
        server.control_network = RecordingNetwork()
        server.data_network = RecordingNetwork()

        server.on_key_press({"type": "special", "value": "r"})

        self.assertFalse(server._remote_destination_active())
        self.assertEqual(server.pressed_keys, set())
        self.assertTrue(server.control_network.disconnected)
        self.assertTrue(server.data_network.disconnected)

    def test_switch_back_delegates_stable_session_and_display_identity_to_router(self):
        calls = []

        class Router:
            def handle_edge(self, *args, **kwargs):
                calls.append((args, kwargs))
                return True

        server = ConduitServer.__new__(ConduitServer)
        server.input_router = Router()
        server._paste_route_lock = None
        server._apply_clipboard_offer_route = lambda: None

        server.on_switch_back({
            "peer_identity": "client-a",
            "source_display_id": "display-a",
            "source_side": "left",
            "ratio": 0.5,
            "session_id": "session-a",
            "topology_version": 4,
        })

        self.assertEqual(
            calls,
            [
                (
                    ("client-a", "display-a", "left", 0.5),
                    {"session_id": "session-a", "topology_version": 4},
                )
            ],
        )

    def test_server_return_shortcut_delegates_without_disconnecting_sessions(self):
        calls = []

        class Router:
            def return_to_server_primary(self, reason="shortcut"):
                calls.append(reason)
                return True

        server = ConduitServer.__new__(ConduitServer)
        server.input_router = Router()
        server.control_network = RecordingNetwork()
        server.data_network = RecordingNetwork()

        self.assertTrue(server._return_cursor_to_server())

        self.assertEqual(calls, ["shortcut"])
        self.assertFalse(server.control_network.disconnected)
        self.assertFalse(server.data_network.disconnected)

    def test_server_return_shortcut_without_router_releases_then_centers(self):
        events = []

        class Input:
            screen_width = 1600
            screen_height = 900

            def release_all_injected_input(self):
                events.append("release-injected")

            def stop_keyboard_capture(self):
                events.append("stop-capture")

            def inject_position(self, x, y):
                events.append(("center", x, y))

            def start_edge_detection(self):
                events.append("start-edges")

        server = ConduitServer.__new__(ConduitServer)
        server.input_router = None
        server.input_handler = Input()
        server.pressed_keys = {"ctrl_l", "space"}
        server.on_capture_stop = None
        server.routing_suspended = False

        self.assertTrue(server._return_cursor_to_server())

        self.assertEqual(server.pressed_keys, set())
        self.assertEqual(events, [
            "release-injected",
            "stop-capture",
            ("center", 800, 450),
            "start-edges",
        ])


if __name__ == "__main__":
    unittest.main()
