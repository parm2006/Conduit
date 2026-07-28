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
        self.client_edge = "left"

    def stop_keyboard_capture(self):
        self.events.append("capture-stopped")

    def start_keyboard_capture(self):
        self.events.append("capture-started")

    def stop(self):
        self.events.append("input-stopped")

    def release_all_injected_keys(self):
        self.events.append("keys-released")


class PasteServiceState:
    def __init__(self, active):
        self.destination_paste_active = active


class BlockingPasteService(PasteServiceState):
    def __init__(self):
        super().__init__(active=False)
        self.request_started = threading.Event()
        self.finish_request = threading.Event()

    def request_paste(self):
        self.request_started.set()
        self.finish_request.wait(1)
        self.destination_paste_active = True
        return object()


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

    def test_server_ignores_edge_crossing_while_local_paste_is_pending(self):
        events = []
        server = DeskFlowServer.__new__(DeskFlowServer)
        server.input_handler = RecordingInputHandler(events)
        server.file_paste_service = PasteServiceState(active=True)
        server.switching_to_client = False
        server.local_files_available = True
        server.layout_position = "right"
        server.paste_coordinator = RecordingCoordinator()
        server.control_network = RecordingNetwork()
        server.on_capture_start = None

        server.on_edge_hit("right", 0.5)

        self.assertFalse(server.switching_to_client)
        self.assertEqual(server.control_network.messages, [])
        self.assertEqual(events, [])

    def test_client_ignores_return_edge_while_local_paste_is_pending(self):
        events = []
        client = DeskFlowClient.__new__(DeskFlowClient)
        client.input_handler = RecordingInputHandler(events)
        client.file_paste_service = PasteServiceState(active=True)
        client.control_network = RecordingNetwork()
        client.is_active = True

        client.on_client_edge_hit("left", 0.5)

        self.assertTrue(client.is_active)
        self.assertEqual(client.control_network.messages, [])
        self.assertEqual(events, [])

    def test_server_edge_cannot_race_paste_destination_latching(self):
        events = []
        service = BlockingPasteService()
        server = DeskFlowServer.__new__(DeskFlowServer)
        server.file_paste_service = service
        server.input_handler = RecordingInputHandler(events)
        server.switching_to_client = False
        server.local_files_available = True
        server.layout_position = "right"
        server.paste_coordinator = RecordingCoordinator()
        server.control_network = RecordingNetwork()
        server.on_capture_start = None

        paste = threading.Thread(target=server._request_remote_file_paste)
        crossing = threading.Thread(
            target=server.on_edge_hit,
            args=("right", 0.5),
        )
        paste.start()
        self.assertTrue(service.request_started.wait(1))
        crossing.start()
        crossing.join(0.05)

        self.assertTrue(crossing.is_alive())

        service.finish_request.set()
        paste.join(1)
        crossing.join(1)
        self.assertFalse(server.switching_to_client)
        self.assertEqual(server.control_network.messages, [])

    def test_client_edge_cannot_race_paste_destination_latching(self):
        events = []
        service = BlockingPasteService()
        client = DeskFlowClient.__new__(DeskFlowClient)
        client.file_paste_service = service
        client.input_handler = RecordingInputHandler(events)
        client.control_network = RecordingNetwork()
        client.is_active = True

        paste = threading.Thread(target=client._request_remote_file_paste)
        crossing = threading.Thread(
            target=client.on_client_edge_hit,
            args=("left", 0.5),
        )
        paste.start()
        self.assertTrue(service.request_started.wait(1))
        crossing.start()
        crossing.join(0.05)

        self.assertTrue(crossing.is_alive())

        service.finish_request.set()
        paste.join(1)
        crossing.join(1)
        self.assertTrue(client.is_active)
        self.assertEqual(client.control_network.messages, [])


if __name__ == "__main__":
    unittest.main()
