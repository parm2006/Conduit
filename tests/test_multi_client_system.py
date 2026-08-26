import gc
import tempfile
import threading
import unittest
from types import SimpleNamespace

from app.clipboard_formats import (
    ClipboardEntry,
    ClipboardSnapshot,
    decode_clipboard_message,
    encode_clipboard_message,
)
from app.clipboard_hub import ClipboardHub, ClipboardHubItem
from app.crypto import IdentityStore
from app.display_topology import (
    Display,
    DraftTopology,
    MachineDisplayGroup,
    NativeRect,
    PlacedMachine,
)
from app.file_transfer.cluster_router import (
    ClusterCommandBroadcaster,
    ClusterFileRouter,
)
from app.file_transfer.models import FileItem, ItemType, Manifest
from app.file_transfer.transport import FileLaneClient, FileLaneServer
from app.network import NetworkClient, NetworkServer
from app.server import ConduitServer
from app.session import SessionRegistry
from app.trust import PeerTrustStore


class _Protector:
    def protect(self, value):
        return b"p:" + bytes(value)[::-1]

    def unprotect(self, value):
        value = bytes(value)
        if not value.startswith(b"p:"):
            raise ValueError("invalid protected value")
        return value[2:][::-1]


def _connect(client, port):
    finished = threading.Event()
    result = []
    client.connect(
        "127.0.0.1",
        port,
        lambda success, error: (
            result.append((success, error)),
            finished.set(),
        ),
    )
    if not finished.wait(3):
        raise AssertionError("network connection did not finish")
    return result[0]


def _wait_for(predicate, timeout=2):
    finished = threading.Event()
    deadline_steps = max(1, round(timeout / 0.01))
    for _ in range(deadline_steps):
        if predicate():
            return True
        finished.wait(0.01)
    return bool(predicate())


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


def _candidate(version=1):
    return DraftTopology(
        "server",
        (
            PlacedMachine(_group("client-1", "ClientOne"), -1, 0),
            PlacedMachine(_group("server", "ParthPC"), 0, 0),
            PlacedMachine(_group("client-2", "ClientTwo"), 1, 0),
        ),
    ).validate().validated.activate(version)


class _Input:
    screen_width = 1920
    screen_height = 1080

    def release_all_injected_input(self):
        return True

    def stop(self):
        return True

    def inject_position(self, x, y):
        return True


class MultiClientTlsSystemTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.identity_directory = tempfile.TemporaryDirectory()
        self.trust_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.trust_directory.cleanup)
        self.addCleanup(self.identity_directory.cleanup)
        self.identity = IdentityStore(
            self.identity_directory.name,
            legacy_root=False,
            protector=_Protector(),
        ).load_or_create()
        self.registry = SessionRegistry("secret", candidate_timeout=0.05)
        self.control_server = NetworkServer(
            "secret",
            "127.0.0.1",
            0,
            role="control",
            coordinator=self.registry,
            identity=self.identity,
        )
        self.data_server = NetworkServer(
            "secret",
            "127.0.0.1",
            0,
            role="data",
            coordinator=self.registry,
            identity=self.identity,
        )
        self.file_server = FileLaneServer(
            identity=self.identity,
            host="127.0.0.1",
            port=0,
            coordinator=self.registry,
        )
        self.assertTrue(self.control_server.start())
        self.assertTrue(self.data_server.start())
        self.assertTrue(self.file_server.start())
        self.addCleanup(self.control_server.stop)
        self.addCleanup(self.data_server.stop)
        self.addCleanup(self.file_server.stop)
        self.trust = PeerTrustStore(
            self.trust_directory.name,
            protector=_Protector(),
        )
        self.bundles = []
        self.addCleanup(self._close_bundles)
        self._add_bundle("client-1", "ClientOne")
        self._add_bundle("client-2", "ClientTwo")

    def _add_bundle(self, machine_id, windows_name):
        control = NetworkClient(
            "secret",
            role="control",
            trust_store=self.trust,
            fingerprint_approval=lambda fingerprint, peer: True,
            peer_identity=machine_id,
            windows_name=windows_name,
        )
        self.assertEqual(
            _connect(control, self.control_server.port),
            (True, None),
        )
        offer = control.session_info
        fingerprint = control.peer_certificate_fingerprint()
        data = NetworkClient(
            "unused",
            role="data",
            trust_store=self.trust,
            expected_fingerprint=fingerprint,
            lane_token=offer["data_token"],
            session_id=offer["session_id"],
            peer_identity=machine_id,
            windows_name=windows_name,
        )
        self.assertEqual(_connect(data, self.data_server.port), (True, None))
        file_lane = FileLaneClient(peer_identity=machine_id)
        self.file_server.offer_session(
            offer["file_token"],
            offer["session_id"],
        )
        file_lane.connect(
            "127.0.0.1",
            self.file_server.port,
            fingerprint,
            offer["file_token"],
            session_id=offer["session_id"],
        )
        control.commit_peer_trust()
        bundle = SimpleNamespace(
            machine_id=machine_id,
            control=control,
            data=data,
            file=file_lane,
            session_id=offer["session_id"],
        )
        self.bundles.append(bundle)
        return bundle

    def _close_bundles(self):
        for bundle in reversed(self.bundles):
            bundle.file.close()
            bundle.data.disconnect()
            bundle.control.disconnect()
        self.bundles.clear()

    def _session_for_machine(self, machine_id):
        return next(
            bundle.session_id
            for bundle in self.bundles
            if bundle.machine_id == machine_id
        )

    def test_atomic_apply_and_cluster_commands_cross_real_control_tls(self):
        clipboard_hub = ClipboardHub("server")
        self.addCleanup(clipboard_hub.stop)
        file_router = SimpleNamespace(
            pause=lambda: True,
            resume=lambda: True,
        )
        server = ConduitServer.__new__(ConduitServer)
        server.session_registry = self.registry
        server.control_network = self.control_server
        server.input_handler = _Input()
        server.input_router = None
        server.pressed_keys = set()
        server.forwarded_keys = {}
        server.on_capture_stop = None
        server.clipboard_hub = clipboard_hub
        server.cluster_file_router = file_router
        server._topology_ack_lock = threading.Lock()
        server._topology_transaction = None
        server._active_topology_session_ids = set()
        server._install_topology = lambda topology: setattr(
            server,
            "active_topology",
            topology,
        )
        self.control_server.register_callback(
            "topology_ack",
            server.on_topology_ack,
        )
        self.control_server.register_callback(
            "topology_commit_ack",
            server.on_topology_commit_ack,
        )
        self.control_server.register_callback(
            "topology_rollback_ack",
            server.on_topology_rollback_ack,
        )
        for bundle in self.bundles:
            bundle.control.register_callback(
                "topology_apply",
                lambda data, endpoint=bundle.control: endpoint.send_message({
                    "type": "topology_ack",
                    "version": data["version"],
                }),
            )
            bundle.control.register_callback(
                "topology_commit",
                lambda data, endpoint=bundle.control: endpoint.send_message({
                    "type": "topology_commit_ack",
                    "version": data["version"],
                }),
            )
            bundle.control.register_callback(
                "topology_rollback",
                lambda data, endpoint=bundle.control: endpoint.send_message({
                    "type": "topology_rollback_ack",
                    "version": data["version"],
                }),
            )

        completed = threading.Event()
        outcomes = []
        self.assertTrue(
            server.apply_topology_candidate(
                _candidate(),
                on_persist=lambda topology: True,
                on_complete=lambda success: outcomes.append(success) or completed.set(),
                timeout=1,
            )
        )
        self.assertTrue(completed.wait(2))
        self.assertEqual(outcomes, [True])
        self.assertEqual(
            server._active_topology_session_ids,
            {bundle.session_id for bundle in self.bundles},
        )

        received = {bundle.machine_id: [] for bundle in self.bundles}
        for bundle in self.bundles:
            for command in ("reload_connection", "shutdown_app", "set_daemon_mode"):
                bundle.control.register_callback(
                    command,
                    lambda data, machine_id=bundle.machine_id: received[
                        machine_id
                    ].append(data["type"]),
                )
        broadcaster = ClusterCommandBroadcaster(
            ready_sessions=self.registry.ready_sessions,
            send=lambda session_id, message: self.control_server.send_message(
                message,
                session_id=session_id,
            ),
        )
        for command in ("reload_connection", "shutdown_app", "set_daemon_mode"):
            result = broadcaster.broadcast(command)
            self.assertEqual(set(result.delivered), {
                bundle.session_id for bundle in self.bundles
            })
        self.assertTrue(
            _wait_for(
                lambda: all(len(commands) == 3 for commands in received.values())
            )
        )

    def test_global_clipboard_and_client_to_client_file_frame_use_real_lanes(self):
        machine_to_session = {
            bundle.machine_id: bundle.session_id for bundle in self.bundles
        }
        hub = ClipboardHub("server")
        self.addCleanup(hub.stop)
        local_items = []
        hub.register_endpoint("server", lambda item: local_items.append(item) or True)
        clipboard_received = {bundle.machine_id: [] for bundle in self.bundles}
        for bundle in self.bundles:
            bundle.data.register_callback(
                "cluster_clipboard",
                lambda data, machine_id=bundle.machine_id: clipboard_received[
                    machine_id
                ].append(data["revision"]),
            )
            hub.register_endpoint(
                bundle.machine_id,
                lambda item, session_id=bundle.session_id: self.data_server.send_message(
                    {
                        "type": "cluster_clipboard",
                        "revision": item.revision,
                    },
                    session_id=session_id,
                ),
            )

        sequences = {bundle.machine_id: 0 for bundle in self.bundles}

        def accept_clipboard(data):
            machine_id = data["peer_identity"]
            sequences[machine_id] += 1
            message = {
                key: data[key]
                for key in ("type", "version", "formats")
            }
            hub.accept_ordinary(
                machine_id,
                sequences[machine_id],
                decode_clipboard_message(message),
            )

        self.data_server.register_callback("clipboard_sync", accept_clipboard)
        first, second = self.bundles
        self.assertTrue(first.data.send_message(encode_clipboard_message(
            ClipboardSnapshot((ClipboardEntry("unicode_text", b"first"),))
        )))
        self.assertTrue(_wait_for(lambda: hub.revision == 1))
        self.assertEqual(hub.latest_item.snapshot.entries[0].data, b"first")
        self.assertTrue(second.data.send_message(encode_clipboard_message(
            ClipboardSnapshot((ClipboardEntry("unicode_text", b"newest"),))
        )))
        self.assertTrue(_wait_for(lambda: hub.revision == 2))
        self.assertEqual(hub.latest_item.snapshot.entries[0].data, b"newest")
        self.assertTrue(
            _wait_for(
                lambda: bool(local_items) and local_items[-1].revision == 2
            )
        )
        self.assertTrue(_wait_for(
            lambda: (
                1 in clipboard_received["client-2"]
                and 2 in clipboard_received["client-1"]
            )
        ))

        offer = [ClipboardHubItem(3, "client-1", 9, "files")]
        router = ClusterFileRouter(
            "server",
            latest_offer=lambda: offer[0],
            endpoint_available=lambda machine_id: machine_id in machine_to_session,
            send_control=lambda machine_id, message: self.control_server.send_message(
                message,
                session_id=machine_to_session[machine_id],
            ),
            send_file=lambda machine_id, metadata, payload: self.file_server.send(
                metadata,
                payload,
                session_id=machine_to_session[machine_id],
            ),
        )
        self.addCleanup(router.stop)
        self.control_server.register_callback(
            "file_manifest_response",
            lambda data: router.on_manifest_response(data["peer_identity"], data),
        )
        self.control_server.register_callback(
            "file_manifest_ack",
            lambda data: router.on_manifest_ack(data["peer_identity"], data),
        )
        self.file_server.register_callback(
            "chunk",
            lambda metadata, payload: router.relay_frame(
                metadata["peer_identity"],
                metadata,
                payload,
            ),
        )
        transfer_id = "b" * 32
        request_id = "a" * 32
        manifest = Manifest(
            transfer_id,
            (FileItem("file.txt", ItemType.FILE, 4, 1, "0" * 64),),
            4,
            1,
        )
        source_ack = threading.Event()
        destination_chunk = []
        first.control.register_callback(
            "file_manifest_request",
            lambda data: first.control.send_message({
                "type": "file_manifest_response",
                "request_id": data["request_id"],
                "manifest": manifest.to_wire(),
            }),
        )
        first.control.register_callback(
            "file_manifest_ack",
            lambda data: source_ack.set(),
        )
        second.control.register_callback(
            "file_manifest_response",
            lambda data: second.control.send_message({
                "type": "file_manifest_ack",
                "job_id": data["manifest"]["job_id"],
            }),
        )
        second.file.register_callback(
            "chunk",
            lambda metadata, payload: destination_chunk.append(
                (metadata, payload)
            ),
        )

        self.assertIsNotNone(router.request_paste("client-2", request_id))
        self.assertTrue(source_ack.wait(2))
        first.file.send(
            {
                "type": "chunk",
                "job_id": transfer_id,
                "relative_path": "file.txt",
                "offset": 0,
            },
            b"data",
        )

        self.assertTrue(_wait_for(lambda: len(destination_chunk) == 1))
        self.assertEqual(destination_chunk[0][1], b"data")
        self.assertEqual(destination_chunk[0][0]["source_id"], "client-1")
        self.assertEqual(destination_chunk[0][0]["destination_id"], "client-2")

    def test_third_real_control_candidate_times_out_without_disturbing_two_sessions(self):
        third = NetworkClient(
            "secret",
            role="control",
            trust_store=self.trust,
            fingerprint_approval=lambda fingerprint, peer: True,
            peer_identity="client-3",
            windows_name="ClientThree",
        )
        self.addCleanup(third.disconnect)

        success, error = _connect(third, self.control_server.port)

        self.assertFalse(success)
        self.assertIn("two Clients", error)
        self.assertEqual(len(self.registry.ready_sessions()), 2)
        self.assertEqual(len(self.control_server.connections), 2)


if __name__ == "__main__":
    unittest.main()
