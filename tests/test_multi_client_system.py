import gc
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

from app.clipboard_formats import (
    ClipboardEntry,
    ClipboardSnapshot,
    decode_clipboard_message,
    encode_clipboard_message,
)
from app.clipboard_hub import ClipboardHub
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
from app.file_transfer.paste_coordinator import PasteCoordinator
from app.file_transfer.transport import FileLaneClient, FileLaneServer
from app.network import NetworkClient, NetworkServer
from app.input_router import InputRouter, LocalServer
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


def _routing_candidate(version=7):
    return DraftTopology(
        "server",
        (
            PlacedMachine(_group("client-2", "ClientTwo"), -2, 0),
            PlacedMachine(_group("client-1", "ClientOne"), -1, 0),
            PlacedMachine(_group("server", "ParthPC"), 0, 0),
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


class _RoutingInputEffects:
    def __init__(self):
        self.captures = []
        self.restores = []
        self.releases = 0

    def release_local_input(self):
        self.releases += 1

    def begin_remote_capture(self, session_id):
        self.captures.append(session_id)
        return True

    def restore_local(self, position):
        self.restores.append(position)


class _ActiveMachineRouter:
    def __init__(self, machine_id, session_id):
        self.active_machine_id = machine_id
        self.active_session_id = session_id
        self.forwarded_keys = []

    def forward_key_press(self, key_data):
        self.forwarded_keys.append(("press", dict(key_data)))
        return True

    def forward_key_release(self, key_data):
        self.forwarded_keys.append(("release", dict(key_data)))
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

    def _ready_session_for_machine(self, machine_id):
        return next(
            session
            for session in self.registry.ready_sessions()
            if session.peer_identity == machine_id
        )

    def test_acknowledged_cursor_routes_cross_real_control_tls(self):
        topology = _routing_candidate()
        effects = _RoutingInputEffects()
        router = InputRouter(
            topology,
            session_for_machine=self._ready_session_for_machine,
            input_effects=effects,
        )
        received = {bundle.machine_id: [] for bundle in self.bundles}
        received_motion = {
            bundle.machine_id: [] for bundle in self.bundles
        }

        self.control_server.register_callback(
            "switch_ack",
            lambda data: router.acknowledge_handoff(
                handoff_id=data.get("handoff_id"),
                session_id=data.get("session_id"),
                machine_id=data.get("peer_identity"),
                topology_version=data.get("topology_version"),
            ),
        )
        for bundle in self.bundles:
            bundle.control.register_callback(
                "switch",
                lambda data, endpoint=bundle.control, machine_id=bundle.machine_id: (
                    received[machine_id].append(dict(data)),
                    endpoint.send_message({
                        "type": "switch_ack",
                        "handoff_id": data["handoff_id"],
                        "topology_version": data["topology_version"],
                    }),
                )[-1],
            )
            bundle.control.register_callback(
                "mouse_move_batch",
                lambda data, machine_id=bundle.machine_id: received_motion[
                    machine_id
                ].extend(tuple(delta) for delta in data["deltas"]),
            )

        first_session = self._session_for_machine("client-1")
        second_session = self._session_for_machine("client-2")
        self.assertTrue(router.handle_edge(
            "server",
            "server-primary",
            "left",
            0.5,
            topology_version=7,
        ))
        self.assertTrue(_wait_for(
            lambda: router.active_session_id == first_session
        ))
        self.assertEqual(effects.captures, [first_session])

        expected_motion = [(index, -index) for index in range(1, 71)]
        for dx, dy in expected_motion:
            self.assertTrue(router.forward_mouse_move(dx, dy))
        self.assertTrue(_wait_for(
            lambda: len(received_motion["client-1"]) == len(expected_motion)
        ))
        self.assertEqual(received_motion["client-1"], expected_motion)
        self.assertEqual(received_motion["client-2"], [])

        self.assertTrue(router.handle_edge(
            "client-1",
            "client-1-primary",
            "left",
            0.5,
            session_id=first_session,
            topology_version=7,
        ))
        self.assertTrue(_wait_for(
            lambda: router.active_session_id == second_session
        ))

        self.assertTrue(router.handle_edge(
            "client-2",
            "client-2-primary",
            "right",
            0.5,
            session_id=second_session,
            topology_version=7,
        ))
        self.assertTrue(_wait_for(
            lambda: router.active_session_id == first_session
        ))
        self.assertTrue(router.handle_edge(
            "client-1",
            "client-1-primary",
            "right",
            0.5,
            session_id=first_session,
            topology_version=7,
        ))
        self.assertIsInstance(router.state, LocalServer)
        self.assertEqual(
            {machine_id: len(messages) for machine_id, messages in received.items()},
            {"client-1": 2, "client-2": 1},
        )

    def test_abrupt_active_tls_destination_loss_does_not_block_input_caller(self):
        topology = _routing_candidate()
        effects = _RoutingInputEffects()
        failures = []
        failed = threading.Event()

        def on_failure(session_id, reason):
            failures.append((session_id, reason))
            failed.set()

        router = InputRouter(
            topology,
            session_for_machine=self._ready_session_for_machine,
            input_effects=effects,
            handoff_failed=on_failure,
        )
        self.control_server.register_callback(
            "switch_ack",
            lambda data: router.acknowledge_handoff(
                handoff_id=data.get("handoff_id"),
                session_id=data.get("session_id"),
                machine_id=data.get("peer_identity"),
                topology_version=data.get("topology_version"),
            ),
        )
        first = next(
            bundle for bundle in self.bundles if bundle.machine_id == "client-1"
        )
        first.control.register_callback(
            "switch",
            lambda data: first.control.send_message({
                "type": "switch_ack",
                "handoff_id": data["handoff_id"],
                "topology_version": data["topology_version"],
            }),
        )

        self.assertTrue(router.handle_edge(
            "server",
            "server-primary",
            "left",
            0.5,
            topology_version=7,
        ))
        self.assertTrue(_wait_for(
            lambda: router.active_session_id == first.session_id
        ))
        self.assertTrue(first.control.disconnect())
        self.assertTrue(_wait_for(
            lambda: self.control_server.connection(first.session_id) is None
        ))

        started = time.monotonic()
        self.assertTrue(router.forward_mouse_move(9, -4))
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.2)
        self.assertTrue(failed.wait(1))
        self.assertEqual(
            failures,
            [(
                first.session_id,
                "input dispatch failed: input send failed",
            )],
        )
        self.assertIsInstance(router.state, LocalServer)
        self.assertEqual(effects.restores[-1], (960, 540))

    def test_silent_real_tls_destination_recovers_before_heartbeat_timeout(self):
        topology = _routing_candidate()
        effects = _RoutingInputEffects()
        failures = []
        switch_received = threading.Event()
        failed = threading.Event()

        def on_failure(session_id, reason):
            failures.append((session_id, reason))
            self.control_server.disconnect(session_id=session_id)
            failed.set()

        router = InputRouter(
            topology,
            session_for_machine=self._ready_session_for_machine,
            input_effects=effects,
            handoff_failed=on_failure,
        )
        first = next(
            bundle for bundle in self.bundles if bundle.machine_id == "client-1"
        )
        first.control.register_callback(
            "switch",
            lambda data: switch_received.set(),
        )
        self.control_server.register_callback(
            "disconnected",
            lambda data: self.registry.close(data["session_id"]),
        )

        started = time.monotonic()
        self.assertTrue(router.handle_edge(
            "server",
            "server-primary",
            "left",
            0.5,
            topology_version=7,
        ))
        self.assertTrue(switch_received.wait(1))
        self.assertTrue(failed.wait(1.5))
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.5)
        self.assertEqual(
            failures,
            [(first.session_id, "handoff timeout")],
        )
        self.assertIsInstance(router.state, LocalServer)
        self.assertEqual(effects.captures, [])
        self.assertTrue(_wait_for(
            lambda: self.registry.get(first.session_id) is None
        ))

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
                source_domain=bundle.session_id,
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

        server = ConduitServer.__new__(ConduitServer)
        server.server_machine_id = "server"
        server.session_registry = self.registry
        server.control_network = self.control_server
        server.file_network = self.file_server
        server.clipboard_hub = hub
        server.clipboard = SimpleNamespace(inject=lambda payload: True)
        server.input_router = None
        server.pressed_keys = set()
        server.paste_coordinator = PasteCoordinator(
            server._request_remote_file_paste
        )
        server.cluster_file_router = ClusterFileRouter(
            "server",
            latest_offer=lambda: hub.latest_item,
            endpoint_available=server._cluster_endpoint_available,
            send_control=server._send_cluster_file_control,
            send_file=server._send_cluster_file_frame,
        )
        self.addCleanup(server.cluster_file_router.stop)
        server_delivery_errors = []

        def deliver_to_server(item):
            try:
                return server._deliver_clipboard_to_server(item)
            except Exception as error:
                server_delivery_errors.append(error)
                raise

        hub.register_endpoint("server", deliver_to_server)
        self.control_server.register_callback(
            "clipboard_offer",
            server.on_remote_clipboard_offer,
        )
        self.control_server.register_callback(
            "file_manifest_request",
            server.on_file_manifest_request,
        )
        self.control_server.register_callback(
            "file_manifest_response",
            server.on_file_manifest_response,
        )
        self.control_server.register_callback(
            "file_manifest_ack",
            server.on_file_manifest_ack,
        )
        self.file_server.register_callback(
            "chunk",
            lambda metadata, payload: server.cluster_file_router.relay_frame(
                metadata["peer_identity"],
                metadata,
                payload,
            ),
        )
        intents = {bundle.machine_id: [] for bundle in self.bundles}
        chunks = {bundle.machine_id: [] for bundle in self.bundles}
        acknowledgements = {bundle.machine_id: [] for bundle in self.bundles}

        for direction, (source, destination) in enumerate(
            (
                (self.bundles[0], self.bundles[1]),
                (self.bundles[1], self.bundles[0]),
            ),
            start=1,
        ):
            intent_counts = {
                machine_id: len(values) for machine_id, values in intents.items()
            }
            chunk_counts = {
                machine_id: len(values) for machine_id, values in chunks.items()
            }
            ack_count = len(acknowledgements[source.machine_id])
            request_id = str(direction) * 32
            transfer_id = str(direction + 2) * 32
            manifest = Manifest(
                transfer_id,
                (FileItem("file.txt", ItemType.FILE, 4, 1, "0" * 64),),
                4,
                1,
            )
            source_ack = threading.Event()

            source.control.register_callback(
                "file_manifest_request",
                lambda data, endpoint=source.control, value=manifest: endpoint.send_message({
                    "type": "file_manifest_response",
                    "request_id": data["request_id"],
                    "manifest": value.to_wire(),
                }),
            )
            source.control.register_callback(
                "file_manifest_ack",
                lambda data, machine_id=source.machine_id: (
                    acknowledgements[machine_id].append(dict(data)),
                    source_ack.set(),
                )[-1],
            )
            destination.control.register_callback(
                "file_paste_intent",
                lambda data, endpoint=destination.control,
                machine_id=destination.machine_id, value=request_id: (
                    intents[machine_id].append(dict(data)),
                    endpoint.send_message({
                        "type": "file_manifest_request",
                        "request_id": value,
                    }),
                )[-1],
            )
            destination.control.register_callback(
                "file_manifest_response",
                lambda data, endpoint=destination.control: endpoint.send_message({
                    "type": "file_manifest_ack",
                    "job_id": data["manifest"]["job_id"],
                }),
            )
            destination.file.register_callback(
                "chunk",
                lambda metadata, payload,
                machine_id=destination.machine_id: chunks[machine_id].append(
                    (metadata, payload)
                ),
            )

            server.input_router = _ActiveMachineRouter(
                destination.machine_id,
                destination.session_id,
            )
            self.assertTrue(source.control.send_message({
                "type": "clipboard_offer",
                "kind": "files",
                "sequence": 10 + direction,
            }))
            self.assertTrue(_wait_for(
                lambda: (
                    hub.latest_item is not None
                    and hub.latest_item.source_id == source.machine_id
                    and server.paste_coordinator.transfer_required
                )
            ), repr(server_delivery_errors))

            server.on_key_press({"type": "special", "value": "ctrl_l"})
            server.on_key_press({"type": "char", "value": "v"})
            server.on_key_release({"type": "char", "value": "v"})
            server.on_key_release({"type": "special", "value": "ctrl_l"})

            self.assertTrue(source_ack.wait(2))
            self.assertEqual(
                len(intents[destination.machine_id]),
                intent_counts[destination.machine_id] + 1,
            )
            self.assertEqual(
                len(intents[source.machine_id]),
                intent_counts[source.machine_id],
            )
            self.assertNotIn(
                "v",
                [
                    key_data["value"]
                    for action, key_data in server.input_router.forwarded_keys
                    if action == "press"
                ],
            )
            source.file.send(
                {
                    "type": "chunk",
                    "job_id": transfer_id,
                    "relative_path": "file.txt",
                    "offset": 0,
                },
                b"data",
            )

            self.assertTrue(_wait_for(
                lambda: len(chunks[destination.machine_id])
                == chunk_counts[destination.machine_id] + 1
            ))
            metadata, payload = chunks[destination.machine_id][-1]
            self.assertEqual(payload, b"data")
            self.assertEqual(metadata["source_id"], source.machine_id)
            self.assertEqual(metadata["destination_id"], destination.machine_id)
            self.assertEqual(
                len(chunks[source.machine_id]),
                chunk_counts[source.machine_id],
            )
            self.assertEqual(
                len(acknowledgements[source.machine_id]),
                ack_count + 1,
            )

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
