import threading
import unittest

from app.client import DeskFlowClient
from app.server import DeskFlowServer


class RecordingNetwork:
    def __init__(self):
        self.messages = []

    def send_message(self, message):
        self.messages.append(message)
        return True


class RecordingCoordinator:
    def __init__(self):
        self.values = []

    def set_remote_files_available(self, value):
        self.values.append(value)


class RecordingInputHandler:
    def __init__(self, events):
        self.events = events

    def stop_keyboard_capture(self):
        self.events.append("capture-stopped")

    def start_keyboard_capture(self):
        self.events.append("capture-started")

    def stop(self):
        self.events.append("input-stopped")


class RecordingKeyboard:
    def __init__(self, events):
        self.events = events

    def press(self, key):
        self.events.append(("press", key))

    def release(self, key):
        self.events.append(("release", key))


class FileAvailabilityRoutingTests(unittest.TestCase):
    def test_client_sends_local_boolean_and_applies_remote_boolean(self):
        client = DeskFlowClient.__new__(DeskFlowClient)
        client.control_network = RecordingNetwork()
        client.paste_coordinator = RecordingCoordinator()

        client.on_local_file_availability(True)
        client.on_remote_file_availability({"available": False})

        self.assertEqual(client.control_network.messages, [{"type": "file_clipboard_available", "available": True}])
        self.assertEqual(client.paste_coordinator.values, [False])

    def test_server_sends_local_boolean_and_applies_remote_boolean(self):
        server = DeskFlowServer.__new__(DeskFlowServer)
        server.control_network = RecordingNetwork()
        server.paste_coordinator = RecordingCoordinator()

        server.on_local_file_availability(False)
        server.on_remote_file_availability({"available": True})

        self.assertEqual(server.control_network.messages, [{"type": "file_clipboard_available", "available": False}])
        self.assertEqual(server.paste_coordinator.values, [True])

    def test_server_local_paste_pauses_remote_capture_during_injection(self):
        events = []
        server = DeskFlowServer.__new__(DeskFlowServer)
        server.input_handler = RecordingInputHandler(events)
        server.switching_to_client = True

        server._inject_local_file_paste(RecordingKeyboard(events))

        self.assertEqual(events[0], "capture-stopped")
        self.assertEqual(events[-1], "capture-started")
        self.assertEqual(
            [event[0] for event in events[1:-1]],
            ["press", "press", "release", "release"],
        )

    def test_server_screen_transition_waits_for_local_paste_injection(self):
        injection_started = threading.Event()
        finish_injection = threading.Event()

        class BlockingKeyboard(RecordingKeyboard):
            def press(self, key):
                super().press(key)
                injection_started.set()
                finish_injection.wait(1)

        events = []
        server = DeskFlowServer.__new__(DeskFlowServer)
        server._input_route_lock = threading.RLock()
        server.input_handler = RecordingInputHandler(events)
        server.switching_to_client = False
        server.local_files_available = True
        server.layout_position = "right"
        server.paste_coordinator = RecordingCoordinator()
        server.control_network = RecordingNetwork()
        server.on_capture_start = None

        injection = threading.Thread(
            target=server._inject_local_file_paste,
            args=(BlockingKeyboard(events),),
        )
        transition = threading.Thread(
            target=server.on_edge_hit,
            args=("right", 0.5),
        )
        injection.start()
        self.assertTrue(injection_started.wait(1))
        transition.start()
        transition.join(0.05)

        self.assertTrue(transition.is_alive())

        finish_injection.set()
        injection.join(1)
        transition.join(1)
        self.assertFalse(injection.is_alive())
        self.assertFalse(transition.is_alive())


if __name__ == "__main__":
    unittest.main()
