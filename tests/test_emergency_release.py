import unittest

from app.client import ConduitClient
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
        server.switching_to_client = True
        server.paste_coordinator = Coordinator()
        server.control_network = RecordingNetwork()
        server.data_network = RecordingNetwork()

        server.on_key_press({"type": "special", "value": "r"})

        self.assertFalse(server.switching_to_client)
        self.assertEqual(server.pressed_keys, set())
        self.assertTrue(server.control_network.disconnected)
        self.assertTrue(server.data_network.disconnected)

    def test_switch_back_releases_forwarded_control_before_capture_stops(self):
        events = []

        class Network(RecordingNetwork):
            def send_message(self, message):
                events.append(("message", message))
                return super().send_message(message)

        class Input:
            screen_width = 1920
            screen_height = 1080

            def stop_keyboard_capture(self):
                events.append(("capture", "stopped"))

            def inject_position(self, x, y):
                events.append(("position", (x, y)))

            def start_edge_detection(self, edge):
                events.append(("edge", edge))

        server = ConduitServer.__new__(ConduitServer)
        server.switching_to_client = True
        server.pressed_keys = {"ctrl"}
        server.forwarded_keys = {
            ("special", "ctrl"): {"type": "special", "value": "ctrl"}
        }
        server.paste_coordinator = type(
            "Coordinator",
            (),
            {"set_route": lambda self, offer, destination: None},
        )()
        server.control_network = Network()
        server.input_handler = Input()
        server.on_capture_stop = None
        server._active_edge_side = "right"

        server.on_switch_back({"ratio": 0.5})

        self.assertEqual(events[0][0:2], ("message", {
            "type": "key_release",
            "key": {"type": "special", "value": "ctrl"},
        }))
        self.assertEqual(events[1], ("capture", "stopped"))
        self.assertEqual(server.pressed_keys, set())
        self.assertEqual(server.forwarded_keys, {})


if __name__ == "__main__":
    unittest.main()
