import unittest
import threading
from types import SimpleNamespace

from app.client import ConduitClient
from app.server import ConduitServer
from app.network import ConnectionPhase, NetworkClient, ServerUnavailable
from app.file_transfer.paste_service import FilePasteService
from app.file_transfer.publisher import VirtualPastePublisher
from app.file_transfer.status import TransferPhase
from app.ports import DEFAULT_FILE_PORT


class RecordingControl:
    def __init__(self):
        self.messages = []
        self.session_offer = SimpleNamespace(
            session_id="logical-session", file_token="file-token"
        )
        self.session_info = {
            "session_id": "logical-session", "file_token": "file-token"
        }

    def send_message(self, message):
        self.messages.append(message)
        return True

    def peer_certificate_fingerprint(self):
        return "a" * 64


class RecordingFileServer:
    port = 5002

    def __init__(self):
        self.offers = []

    def offer_session(self, token, session_id):
        self.offers.append((token, session_id))


class RecordingFileClient:
    def __init__(self):
        self.connections = []

    def connect(self, host, port, fingerprint, token, session_id=None):
        self.connections.append((host, port, fingerprint, token, session_id))


class FileLaneLifecycleTests(unittest.TestCase):
    def test_destination_latch_stays_active_through_cancelled_session_cleanup(self):
        cleanup_started = threading.Event()
        release_cleanup = threading.Event()

        class Session:
            decision_pending = False

            def observe(self):
                return False

            def record_stream_open(self):
                return None

            def record_performed_effect(self, effect):
                return None

            def record_terminal(self, phase):
                return True

            def request_cancel(self):
                return False

            def cleanup_cancelled_empty_directories(self):
                cleanup_started.set()
                release_cleanup.wait(1)
                return {}

        class Receiver:
            def __init__(self):
                self.phase = None

            def accept_manifest(self, manifest):
                return manifest

            def cancel_job(self, job_id, reason_code="UserCancelled"):
                self.phase = TransferPhase.CANCELLED
                return True

            def is_paste_terminal(self, job_id):
                return self.phase is not None

            def terminal_outcome(self, job_id):
                return (
                    None
                    if self.phase is None
                    else SimpleNamespace(phase=self.phase)
                )

            def fail_paste(self, job_id, error_code):
                self.phase = TransferPhase.FAILED
                return True

        receiver = Receiver()
        publisher = VirtualPastePublisher(
            publish=lambda file_set, on_performed_drop=None: object(),
            inject=lambda keyboard: receiver.cancel_job("a" * 32),
            release=lambda owner: True,
            keyboard_factory=object,
            session_factory=lambda manifest: Session(),
        )
        control = RecordingControl()
        service = FilePasteService(
            control=control,
            receiver=receiver,
            publisher=publisher,
            sender=object(),
            snapshot_selection=lambda: None,
            executor=SimpleNamespace(submit=lambda manifest, sources: None),
        )
        pending = service.request_paste()

        service.on_manifest_response({
            "request_id": pending.request_id,
            "manifest": {
                "job_id": "a" * 32,
                "items": [{
                    "relative_path": "folder",
                    "item_type": "directory",
                    "size": 0,
                    "modified_ns": 0,
                    "sha256": None,
                }],
                "total_size": 0,
                "file_count": 0,
            },
        })

        self.assertTrue(cleanup_started.wait(1))
        self.assertTrue(service.destination_paste_active)
        release_cleanup.set()
        self.assertTrue(publisher.wait_until_idle(1))
        self.assertFalse(service.destination_paste_active)

    def test_control_failure_preserves_actionable_message_without_socket_jargon(self):
        message = (
            "Could not reach the server. Check its address, port, and that "
            "Conduit is running."
        )

        class Control:
            last_error = ServerUnavailable(message)

            def connect(self, host, port, callback):
                callback(False, message)

            def disconnect(self, **kwargs):
                return True

        client = ConduitClient.__new__(ConduitClient)
        client.control_network = Control()
        client.data_network = None
        client.file_network = SimpleNamespace(close=lambda: None)
        client._disconnecting = False
        results = []

        client.connect("192.0.2.1", 5000, lambda success, error: results.append((success, error)))

        self.assertEqual(results, [(False, message)])

    def test_failure_preserving_disconnect_retains_failed_attempt_phase(self):
        client = ConduitClient.__new__(ConduitClient)
        client._connect_lock = threading.RLock()
        client._disconnecting = False
        client.control_network = NetworkClient("secret")
        client.data_network = None
        client.file_network = SimpleNamespace(close=lambda: None)
        error = ConnectionError("lane binding failed")

        client.disconnect(preserve_failure=True, error=error)

        self.assertEqual(client.control_network.phase, ConnectionPhase.FAILED)
        self.assertIs(client.control_network.last_error, error)

    def test_lane_binding_timeout_closes_lanes_and_retains_typed_failure(self):
        class FileLane:
            def __init__(self):
                self.closes = 0

            def close(self):
                self.closes += 1

        client = ConduitClient.__new__(ConduitClient)
        client._connect_lock = threading.RLock()
        client._disconnecting = False
        client._connect_callback_done = False
        results = []
        client._connect_callback = lambda success, error: results.append((success, error))
        client._connect_deadline = None
        client.control_network = NetworkClient("secret")
        client.data_network = NetworkClient("unused", role="data")
        client.file_network = FileLane()

        with self.assertLogs("app.client", level="ERROR") as logs:
            client._on_lane_binding_timeout()

        self.assertEqual(
            results,
            [(
                False,
                "Connection timed out before setup finished. Check the network and try again.",
            )],
        )
        self.assertEqual(client.control_network.phase, ConnectionPhase.FAILED)
        self.assertIsInstance(client.control_network.last_error, TimeoutError)
        self.assertEqual(client.data_network.phase, ConnectionPhase.FAILED)
        self.assertIsInstance(client.data_network.last_error, TimeoutError)
        self.assertEqual(client.file_network.closes, 1)
        self.assertIn(
            f"server:{DEFAULT_FILE_PORT}",
            "\n".join(logs.output),
        )

    def test_disconnect_during_ready_transition_cannot_report_success(self):
        class Lifecycle:
            def __init__(self):
                self.starts = 0
                self.stops = 0

            def start(self):
                self.starts += 1

            def stop(self):
                self.stops += 1

        entered_commit = threading.Event()
        release_commit = threading.Event()

        class Control:
            connected = True
            disconnects = 0

            def commit_peer_trust(self):
                entered_commit.set()
                release_commit.wait(1)
                return True

            def disconnect(self, **kwargs):
                self.disconnects += 1
                self.connected = False

        class Lane:
            def __init__(self, sock=object()):
                self.connected = True
                self.sock = sock
                self.disconnects = 0

            def disconnect(self, **kwargs):
                self.disconnects += 1
                self.connected = False

            def close(self):
                self.disconnects += 1
                self.sock = None

        client = ConduitClient.__new__(ConduitClient)
        client._connect_lock = threading.RLock()
        client._connect_callback_done = False
        client._connect_callback = lambda success, error: results.append((success, error))
        client._connect_deadline = None
        client._ready_started = False
        client._disconnecting = False
        client.connect_error = None
        client.control_connected = True
        client.data_connected = True
        client.file_connected = True
        client.control_network = Control()
        client.data_network = Lane()
        client.file_network = Lane()
        client.clipboard = Lifecycle()
        client.hotkey_monitor = Lifecycle()
        results = []

        ready = threading.Thread(target=client._maybe_finish_connect)
        ready.start()
        self.assertTrue(entered_commit.wait(1))
        client.control_network.connected = False
        client.data_network.connected = False
        client.file_network.sock = None
        release_commit.set()
        ready.join(1)

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0][0])
        self.assertEqual(client.clipboard.starts, 0)
        self.assertEqual(client.hotkey_monitor.starts, 0)
        self.assertEqual(client.control_network.disconnects, 1)
        self.assertEqual(client.data_network.disconnects, 1)
        self.assertEqual(client.file_network.disconnects, 1)

    def test_partial_service_start_is_rolled_back_and_all_lanes_disconnect(self):
        class Lifecycle:
            def __init__(self, fail=False):
                self.fail = fail
                self.starts = 0
                self.stops = 0

            def start(self):
                self.starts += 1
                if self.fail:
                    raise RuntimeError("service start failed")

            def stop(self):
                self.stops += 1

        class Network:
            def __init__(self):
                self.connected = True
                self.disconnects = []

            def commit_peer_trust(self):
                return True

            def disconnect(self, **kwargs):
                self.disconnects.append(kwargs)
                self.connected = False

        class FileLane:
            def __init__(self):
                self.sock = object()
                self.closes = 0

            def close(self):
                self.closes += 1
                self.sock = None

        client = ConduitClient.__new__(ConduitClient)
        client._connect_lock = threading.RLock()
        client._connect_callback_done = False
        results = []
        client._connect_callback = lambda success, error: results.append((success, error))
        client._connect_deadline = None
        client._ready_started = False
        client._disconnecting = False
        client.connect_error = None
        client.control_connected = True
        client.data_connected = True
        client.file_connected = True
        client.control_network = Network()
        client.data_network = Network()
        client.file_network = FileLane()
        client.clipboard = Lifecycle()
        client.hotkey_monitor = Lifecycle(fail=True)

        self.assertFalse(client._maybe_finish_connect())

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0][0])
        self.assertEqual(client.clipboard.starts, 1)
        self.assertGreaterEqual(client.clipboard.stops, 1)
        self.assertGreaterEqual(client.hotkey_monitor.stops, 1)
        self.assertEqual(len(client.control_network.disconnects), 1)
        self.assertTrue(client.control_network.disconnects[0]["preserve_failure"])
        self.assertEqual(len(client.data_network.disconnects), 1)
        self.assertEqual(client.file_network.closes, 1)

    def test_server_offers_one_use_file_session_over_control_lane(self):
        server = ConduitServer.__new__(ConduitServer)
        server.control_network = RecordingControl()
        server.file_network = RecordingFileServer()
        server.data_network = SimpleNamespace(session_id="logical-session")

        server._offer_file_lane()

        self.assertEqual(
            server.control_network.messages,
            [{"type": "file_lane_offer", "port": 5002, "session_id": "logical-session"}],
        )
        self.assertEqual(server.file_network.offers, [("file-token", "logical-session")])

    def test_client_binds_offer_to_live_control_certificate(self):
        client = ConduitClient.__new__(ConduitClient)
        client.host = "192.0.2.1"
        client.control_network = RecordingControl()
        client.file_network = RecordingFileClient()

        client.control_connected = False
        client.data_connected = False
        client._connect_file_lane({"port": 5002, "session_id": "logical-session"})

        self.assertEqual(
            client.file_network.connections,
            [("192.0.2.1", 5002, "a" * 64, "file-token", "logical-session")],
        )


if __name__ == "__main__":
    unittest.main()
