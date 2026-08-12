import threading
import unittest

from app.clipboard_formats import ClipboardEntry, ClipboardSnapshot
from app.client import DeskFlowClient
from app.file_transfer.paste_coordinator import PasteCoordinator
from app.server import DeskFlowServer


class RecordingNetwork:
    def __init__(self):
        self.messages = []

    def send_message(self, message):
        self.messages.append(message)
        return True


class SessionNetwork(RecordingNetwork):
    def __init__(self, session_id="session-one"):
        super().__init__()
        self.session_id = session_id
        self.session_info = {"session_id": session_id}


class DeliveringNetwork(RecordingNetwork):
    def __init__(self, receiver):
        super().__init__()
        self.receiver = receiver

    def send_message(self, message):
        result = super().send_message(message)
        self.receiver(message)
        return result


class DeliveringSessionNetwork(SessionNetwork):
    def __init__(self, receiver, session_id="session-one"):
        super().__init__(session_id)
        self.receiver = receiver

    def send_message(self, message):
        result = super().send_message(message)
        self.receiver(message)
        return result


class RecordingCoordinator:
    def __init__(self):
        self.values = []

    def set_route(self, offer, destination):
        self.values.append((offer, destination))


class RecordingClipboardSender:
    def submit(self, payload):
        self.payload = payload
        return True


class RecordingInputHandler:
    def __init__(self, events):
        self.events = events
        self.client_edge = "left"
        self.screen_width = 100
        self.screen_height = 80

    def stop_keyboard_capture(self):
        self.events.append("capture-stopped")

    def start_keyboard_capture(self):
        self.events.append("capture-started")

    def start_edge_detection(self, edge):
        self.events.append(("edge-started", edge))

    def stop(self):
        self.events.append("input-stopped")

    def release_all_injected_keys(self):
        self.events.append("keys-released")

    def inject_position(self, x, y):
        self.events.append(("position", x, y))

    def inject_key_press(self, key_data):
        self.events.append(("key-press", key_data))

    def inject_key_release(self, key_data):
        self.events.append(("key-release", key_data))


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


class RecordingPasteService(PasteServiceState):
    def __init__(self):
        super().__init__(active=False)
        self.requests = 0

    def request_paste(self):
        self.requests += 1
        return True


class RefreshingClipboard:
    def __init__(self, refresh):
        self.refresh = refresh
        self.refreshes = 0

    def refresh_offer(self):
        self.refreshes += 1
        return self.refresh()


class FileAvailabilityRoutingTests(unittest.TestCase):
    def test_client_ordinary_payload_queue_preserves_explicit_local_offer(self):
        client = DeskFlowClient.__new__(DeskFlowClient)
        client.is_active = True
        client.control_network = SessionNetwork()
        client.paste_coordinator = PasteCoordinator(lambda: None)
        client.clipboard_sender = RecordingClipboardSender()
        client.on_local_clipboard_offer("ordinary", 43)
        snapshot = ClipboardSnapshot([ClipboardEntry("html", b"new copy")])

        self.assertTrue(client.on_local_copy(snapshot))

        self.assertIsNotNone(client.paste_coordinator.current_offer)
        self.assertEqual(client.paste_coordinator.current_offer.source, "client")
        self.assertEqual(client.paste_coordinator.current_offer.kind, "ordinary")

    def test_server_ordinary_payload_queue_preserves_explicit_local_offer(self):
        server = DeskFlowServer.__new__(DeskFlowServer)
        server.switching_to_client = False
        server.control_network = SessionNetwork()
        server.paste_coordinator = PasteCoordinator(lambda: None)
        server.clipboard_sender = RecordingClipboardSender()
        server.on_local_clipboard_offer("ordinary", 43)
        snapshot = ClipboardSnapshot([ClipboardEntry("rtf", b"new copy")])

        self.assertTrue(server.on_local_copy(snapshot))

        self.assertIsNotNone(server.paste_coordinator.current_offer)
        self.assertEqual(server.paste_coordinator.current_offer.source, "server")
        self.assertEqual(server.paste_coordinator.current_offer.kind, "ordinary")

    def test_client_publishes_explicit_local_offer(self):
        client = DeskFlowClient.__new__(DeskFlowClient)
        client.is_active = True
        client.control_network = SessionNetwork()
        client.paste_coordinator = PasteCoordinator(lambda: None)

        handler = getattr(client, "on_local_clipboard_offer", None)
        self.assertIsNotNone(handler)
        self.assertTrue(handler("files", 42))

        self.assertEqual(
            client.control_network.messages,
            [
                {
                    "type": "clipboard_offer",
                    "session_id": "session-one",
                    "revision": 1,
                    "source": "client",
                    "kind": "files",
                    "sequence": 42,
                }
            ],
        )
        self.assertFalse(client.paste_coordinator.transfer_required)

    def test_server_routes_received_client_offer_against_client_destination(self):
        server = DeskFlowServer.__new__(DeskFlowServer)
        server.switching_to_client = True
        server.control_network = SessionNetwork()
        server.paste_coordinator = PasteCoordinator(lambda: None)

        handler = getattr(server, "on_remote_clipboard_offer", None)
        self.assertIsNotNone(handler)
        self.assertTrue(
            handler(
                {
                    "type": "clipboard_offer",
                    "session_id": "session-one",
                    "revision": 1,
                    "source": "client",
                    "kind": "files",
                    "sequence": 42,
                }
            )
        )

        self.assertEqual(server.paste_coordinator.destination, "client")
        self.assertEqual(server.paste_coordinator.current_offer.source, "client")
        self.assertFalse(server.paste_coordinator.transfer_required)

    def test_server_screen_transition_recomputes_route_from_same_offer(self):
        events = []
        server = DeskFlowServer.__new__(DeskFlowServer)
        server.switching_to_client = False
        server.control_network = SessionNetwork()
        server.input_handler = RecordingInputHandler(events)
        server.layout_position = "right"
        server.on_capture_start = None
        server.paste_coordinator = PasteCoordinator(lambda: None)
        state = server._get_clipboard_offer_state()
        offer = state.observe_local("files", 20)
        server.paste_coordinator.set_route(offer, "server")

        server.on_edge_hit("right", 0.5)

        self.assertEqual(server.paste_coordinator.current_offer, offer)
        self.assertEqual(server.paste_coordinator.destination, "client")
        self.assertTrue(server.paste_coordinator.transfer_required)

    def test_client_activation_recomputes_route_from_same_offer(self):
        events = []
        client = DeskFlowClient.__new__(DeskFlowClient)
        client.is_active = False
        client.control_network = SessionNetwork()
        client.input_handler = RecordingInputHandler(events)
        client.paste_coordinator = PasteCoordinator(lambda: None)
        client.on_remote_clipboard_offer(
            {
                "type": "clipboard_offer",
                "session_id": "session-one",
                "revision": 1,
                "source": "server",
                "kind": "files",
                "sequence": 20,
            }
        )

        client.on_switch({"direction": "right", "ratio": 0.5})

        self.assertIsNotNone(client.paste_coordinator.current_offer)
        self.assertEqual(client.paste_coordinator.current_offer.source, "server")
        self.assertEqual(client.paste_coordinator.destination, "client")
        self.assertTrue(client.paste_coordinator.transfer_required)

    def test_server_return_recomputes_route_from_same_client_offer(self):
        events = []
        server = DeskFlowServer.__new__(DeskFlowServer)
        server.switching_to_client = True
        server.control_network = SessionNetwork()
        server.input_handler = RecordingInputHandler(events)
        server.layout_position = "right"
        server.on_capture_stop = None
        server.pressed_keys = set()
        server.forwarded_keys = {}
        server.paste_coordinator = PasteCoordinator(lambda: None)
        server.on_remote_clipboard_offer(
            {
                "type": "clipboard_offer",
                "session_id": "session-one",
                "revision": 1,
                "source": "client",
                "kind": "files",
                "sequence": 20,
            }
        )

        server.on_switch_back({"ratio": 0.5})

        self.assertIsNotNone(server.paste_coordinator.current_offer)
        self.assertEqual(server.paste_coordinator.current_offer.source, "client")
        self.assertEqual(server.paste_coordinator.destination, "server")
        self.assertTrue(server.paste_coordinator.transfer_required)

    def test_client_file_copy_supersedes_server_file_on_physical_server_hotkey_path(self):
        server = DeskFlowServer.__new__(DeskFlowServer)
        server.switching_to_client = False
        server.control_network = SessionNetwork()
        server.pressed_keys = set()
        server.forwarded_keys = {}
        server._paste_route_lock = threading.RLock()
        server.paste_coordinator = PasteCoordinator(
            server._request_remote_file_paste
        )
        self.assertTrue(server.on_local_clipboard_offer("files", 10))
        server.switching_to_client = True
        server._apply_clipboard_offer_route()
        self.assertTrue(server.paste_coordinator.transfer_required)

        client = DeskFlowClient.__new__(DeskFlowClient)
        client.is_active = True
        client.paste_coordinator = PasteCoordinator(lambda: None)
        client.control_network = DeliveringSessionNetwork(
            server.on_remote_clipboard_offer
        )

        self.assertTrue(client.on_local_clipboard_offer("files", 20))
        server.on_key_press({"type": "special", "value": "ctrl_l"})
        server.on_key_press({"type": "char", "value": "v"})

        message_types = [
            message["type"] for message in server.control_network.messages
        ]
        self.assertNotIn("file_paste_trigger", message_types)
        self.assertNotIn("file_paste_intent", message_types)
        self.assertEqual(message_types[-1], "key_press")
        self.assertEqual(
            server.control_network.messages[-1]["key"]["value"], "v"
        )

    def test_client_paste_intent_uses_native_v_after_newer_client_file_copy(self):
        events = []
        client = DeskFlowClient.__new__(DeskFlowClient)
        client.input_handler = RecordingInputHandler(events)

        handler = getattr(client, "on_file_paste_intent", None)
        self.assertIsNotNone(handler)
        handler({})

        self.assertEqual(
            events,
            [
                ("key-press", {"type": "char", "value": "v"}),
                ("key-release", {"type": "char", "value": "v"}),
            ],
        )

    def test_client_native_paste_releases_v_when_press_fails(self):
        events = []

        class FailingInput:
            def inject_key_press(self, key_data):
                events.append(("key-press", key_data))
                raise RuntimeError("injection failed")

            def inject_key_release(self, key_data):
                events.append(("key-release", key_data))

        client = DeskFlowClient.__new__(DeskFlowClient)
        client.input_handler = FailingInput()

        with self.assertRaisesRegex(RuntimeError, "injection failed"):
            client._inject_native_paste()

        self.assertEqual(events[-1][0], "key-release")

    def test_client_paste_intent_refreshes_unpolled_local_copy_before_routing(self):
        events = []
        client = DeskFlowClient.__new__(DeskFlowClient)
        client.is_active = True
        client.control_network = SessionNetwork()
        client.input_handler = RecordingInputHandler(events)
        client.paste_coordinator = PasteCoordinator(lambda: None)
        client.file_paste_service = RecordingPasteService()
        client._paste_route_lock = threading.RLock()
        client.on_remote_clipboard_offer(
            {
                "type": "clipboard_offer",
                "session_id": "session-one",
                "revision": 1,
                "source": "server",
                "kind": "files",
                "sequence": 20,
            }
        )
        client.clipboard = RefreshingClipboard(
            lambda: (
                client.on_local_clipboard_offer("files", 42)
                and "files"
            )
        )

        client.on_file_paste_intent({})

        self.assertEqual(client.clipboard.refreshes, 1)
        self.assertEqual(client.file_paste_service.requests, 0)
        self.assertEqual(client.clipboard_offer_state.current_offer.source, "client")
        self.assertEqual(
            events,
            [
                ("key-press", {"type": "char", "value": "v"}),
                ("key-release", {"type": "char", "value": "v"}),
            ],
        )

    def test_client_paste_intent_aborts_when_clipboard_refresh_is_unknown(self):
        events = []
        client = DeskFlowClient.__new__(DeskFlowClient)
        client.is_active = True
        client.control_network = SessionNetwork()
        client.input_handler = RecordingInputHandler(events)
        client.paste_coordinator = PasteCoordinator(lambda: None)
        client.file_paste_service = RecordingPasteService()
        client._paste_route_lock = threading.RLock()
        client.on_remote_clipboard_offer(
            {
                "type": "clipboard_offer",
                "session_id": "session-one",
                "revision": 1,
                "source": "server",
                "kind": "files",
                "sequence": 20,
            }
        )
        client.clipboard = RefreshingClipboard(lambda: None)

        self.assertFalse(client.on_file_paste_intent({}))

        self.assertEqual(client.clipboard.refreshes, 1)
        self.assertEqual(client.file_paste_service.requests, 0)
        self.assertEqual(events, [])

    def test_server_hotkey_refreshes_unpolled_local_copy_before_routing(self):
        server = DeskFlowServer.__new__(DeskFlowServer)
        server.switching_to_client = False
        server.control_network = SessionNetwork()
        server.paste_coordinator = PasteCoordinator(lambda: None)
        server.on_remote_clipboard_offer(
            {
                "type": "clipboard_offer",
                "session_id": "session-one",
                "revision": 1,
                "source": "client",
                "kind": "files",
                "sequence": 20,
            }
        )
        server.clipboard = RefreshingClipboard(
            lambda: (
                server.on_local_clipboard_offer("files", 42)
                and "files"
            )
        )
        refresh = getattr(server, "_refresh_active_destination_offer", None)
        self.assertIsNotNone(refresh)
        server.paste_coordinator.before_paste = refresh
        server.paste_coordinator.on_key_press("ctrl")

        self.assertFalse(server.paste_coordinator.on_key_press("v"))
        self.assertEqual(server.clipboard.refreshes, 1)
        self.assertEqual(server.clipboard_offer_state.current_offer.source, "server")

    def test_server_hotkey_suppresses_paste_when_clipboard_refresh_is_unknown(self):
        requested = []
        server = DeskFlowServer.__new__(DeskFlowServer)
        server.switching_to_client = False
        server.control_network = SessionNetwork()
        server.paste_coordinator = PasteCoordinator(
            lambda: requested.append("paste")
        )
        server.on_remote_clipboard_offer(
            {
                "type": "clipboard_offer",
                "session_id": "session-one",
                "revision": 1,
                "source": "client",
                "kind": "files",
                "sequence": 20,
            }
        )
        server.clipboard = RefreshingClipboard(lambda: None)
        server.paste_coordinator.before_paste = (
            server._refresh_active_destination_offer
        )
        server.paste_coordinator.on_key_press("ctrl")

        self.assertTrue(server.paste_coordinator.on_key_press("v"))

        self.assertEqual(server.clipboard.refreshes, 1)
        self.assertEqual(requested, [])
        self.assertTrue(server.paste_coordinator.transfer_required)

    def test_client_keeps_remote_file_offer_inactive_until_server_switches_control(self):
        events = []
        client = DeskFlowClient.__new__(DeskFlowClient)
        client.is_active = False
        client.control_network = SessionNetwork()
        client.paste_coordinator = PasteCoordinator(lambda: None)
        client.input_handler = RecordingInputHandler(events)

        client.on_remote_clipboard_offer(
            {
                "type": "clipboard_offer",
                "session_id": "session-one",
                "revision": 1,
                "source": "server",
                "kind": "files",
                "sequence": 20,
            }
        )

        self.assertFalse(client.paste_coordinator.transfer_required)
        self.assertIsNone(client.paste_coordinator.current_offer)

        client.on_switch({"direction": "right", "ratio": 0.5})

        self.assertTrue(client.is_active)
        self.assertEqual(client.paste_coordinator.current_offer.source, "server")
        self.assertTrue(client.paste_coordinator.transfer_required)

    def test_server_ignores_edge_crossing_while_local_paste_is_pending(self):
        events = []
        server = DeskFlowServer.__new__(DeskFlowServer)
        server.input_handler = RecordingInputHandler(events)
        server.file_paste_service = PasteServiceState(active=True)
        server.switching_to_client = False
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
