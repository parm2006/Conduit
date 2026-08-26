import gc
import tempfile
import threading
import unittest

from app.crypto import IdentityStore
from app.file_transfer.transport import FileLaneClient, FileLaneServer
from app.network import ConnectionPhase, NetworkClient, NetworkServer
from app.session import SessionRegistry
from app.trust import PeerTrustStore


class FakeProtector:
    def protect(self, value):
        return b"p:" + bytes(value)[::-1]

    def unprotect(self, value):
        value = bytes(value)
        if not value.startswith(b"p:"):
            raise ValueError("invalid protected value")
        return value[2:][::-1]


def connect_network(client, host, port):
    finished = threading.Event()
    result = []
    client.connect(
        host, port,
        lambda success, error: (result.append((success, error)), finished.set()),
    )
    if not finished.wait(3):
        raise AssertionError("network connection did not finish")
    return result[0]


class FullSecuritySessionTests(unittest.TestCase):
    def test_one_control_session_owns_authenticated_data_and_file_lanes(self):
        # Finalize any Tk objects left by earlier GUI tests on the main thread.
        # Tk destructors can otherwise run during a networking worker's GC.
        gc.collect()
        with (
            tempfile.TemporaryDirectory() as identity_directory,
            tempfile.TemporaryDirectory() as trust_directory,
        ):
            identity = IdentityStore(
                identity_directory, legacy_root=False,
                protector=FakeProtector(),
            ).load_or_create()
            coordinator = SessionRegistry("secret")
            control_server = NetworkServer(
                "secret", "127.0.0.1", 0, role="control",
                coordinator=coordinator, identity=identity,
            )
            data_server = NetworkServer(
                "secret", "127.0.0.1", 0, role="data",
                coordinator=coordinator, identity=identity,
            )
            file_server = FileLaneServer(
                identity=identity, host="127.0.0.1", port=0,
                coordinator=coordinator,
            )
            self.assertTrue(control_server.start())
            self.assertTrue(data_server.start())
            self.assertTrue(file_server.start())
            trust = PeerTrustStore(trust_directory, protector=FakeProtector())
            control_client = NetworkClient(
                "secret", role="control", trust_store=trust,
                fingerprint_approval=lambda fingerprint, peer: True,
                peer_identity="device-a",
                windows_name="DeviceA",
            )
            data_client = None
            file_client = FileLaneClient(peer_identity="device-a")
            try:
                self.assertEqual(
                    connect_network(control_client, "127.0.0.1", control_server.port),
                    (True, None),
                )
                session = control_client.session_info
                fingerprint = control_client.peer_certificate_fingerprint()
                data_client = NetworkClient(
                    "unused", role="data", trust_store=trust,
                    expected_fingerprint=fingerprint,
                    lane_token=session["data_token"],
                    session_id=session["session_id"],
                    peer_identity="device-a",
                    windows_name="DeviceA",
                )
                self.assertEqual(
                    connect_network(data_client, "127.0.0.1", data_server.port),
                    (True, None),
                )
                file_server.offer_session(
                    session["file_token"], session["session_id"]
                )
                file_client.connect(
                    "127.0.0.1", file_server.port, fingerprint,
                    session["file_token"], session_id=session["session_id"],
                )

                data_received = threading.Event()
                file_received = threading.Event()
                data_server.register_callback(
                    "probe", lambda message: data_received.set()
                )
                file_server.register_callback(
                    "probe", lambda metadata, payload: file_received.set()
                )
                self.assertTrue(data_client.send_message({"type": "probe"}))
                file_client.send({"type": "probe"}, b"authenticated")
                self.assertTrue(data_received.wait(1))
                self.assertTrue(file_received.wait(1))

                self.assertEqual(
                    control_server.session_id, data_server.session_id
                )
                self.assertEqual(
                    control_server.session_id, session["session_id"]
                )
                self.assertEqual(
                    control_client.phase, ConnectionPhase.BINDING_LANES
                )
                self.assertTrue(control_client.commit_peer_trust())
                self.assertEqual(
                    control_client.phase, ConnectionPhase.CONNECTED
                )
                peer = trust.peer_id("127.0.0.1", control_server.port)
                self.assertEqual(trust.load(peer), fingerprint)
            finally:
                file_client.close()
                if data_client is not None:
                    data_client.disconnect()
                control_client.disconnect()
                file_server.stop()
                data_server.stop()
                control_server.stop()

    def test_two_complete_session_bundles_share_the_three_listeners(self):
        gc.collect()
        with (
            tempfile.TemporaryDirectory() as identity_directory,
            tempfile.TemporaryDirectory() as trust_directory,
        ):
            identity = IdentityStore(
                identity_directory,
                legacy_root=False,
                protector=FakeProtector(),
            ).load_or_create()
            registry = SessionRegistry("secret")
            control_server = NetworkServer(
                "secret",
                "127.0.0.1",
                0,
                role="control",
                coordinator=registry,
                identity=identity,
            )
            data_server = NetworkServer(
                "secret",
                "127.0.0.1",
                0,
                role="data",
                coordinator=registry,
                identity=identity,
            )
            file_server = FileLaneServer(
                identity=identity,
                host="127.0.0.1",
                port=0,
                coordinator=registry,
            )
            self.assertTrue(control_server.start())
            self.assertTrue(data_server.start())
            self.assertTrue(file_server.start())
            trust = PeerTrustStore(trust_directory, protector=FakeProtector())
            bundles = []
            try:
                for number in (1, 2):
                    identity_name = f"device-{number}"
                    control = NetworkClient(
                        "secret",
                        role="control",
                        trust_store=trust,
                        fingerprint_approval=lambda fingerprint, peer: True,
                        peer_identity=identity_name,
                        windows_name=f"TestPC{number}",
                    )
                    self.assertEqual(
                        connect_network(
                            control,
                            "127.0.0.1",
                            control_server.port,
                        ),
                        (True, None),
                    )
                    offer = control.session_info
                    fingerprint = control.peer_certificate_fingerprint()
                    data = NetworkClient(
                        "unused",
                        role="data",
                        trust_store=trust,
                        expected_fingerprint=fingerprint,
                        lane_token=offer["data_token"],
                        session_id=offer["session_id"],
                        peer_identity=identity_name,
                        windows_name=f"TestPC{number}",
                    )
                    self.assertEqual(
                        connect_network(data, "127.0.0.1", data_server.port),
                        (True, None),
                    )
                    file_lane = FileLaneClient()
                    file_lane.peer_identity = identity_name
                    file_server.offer_session(
                        offer["file_token"],
                        offer["session_id"],
                    )
                    file_lane.connect(
                        "127.0.0.1",
                        file_server.port,
                        fingerprint,
                        offer["file_token"],
                        session_id=offer["session_id"],
                    )
                    bundles.append((control, data, file_lane, offer))

                session_ids = {bundle[3]["session_id"] for bundle in bundles}
                self.assertEqual(set(control_server.connections), session_ids)
                self.assertEqual(set(data_server.connections), session_ids)
                self.assertEqual(
                    set(getattr(file_server, "connections", {})),
                    session_ids,
                )
                self.assertEqual(
                    {session.session_id for session in registry.ready_sessions()},
                    session_ids,
                )

                received = []
                file_server.register_callback(
                    "probe",
                    lambda metadata, payload: received.append(
                        (metadata["session_id"], payload)
                    ),
                )
                for _control, _data, file_lane, offer in bundles:
                    file_lane.send({"type": "probe"}, offer["session_id"].encode())
                deadline = threading.Event()
                deadline.wait(0.1)
                self.assertEqual(
                    set(received),
                    {
                        (offer["session_id"], offer["session_id"].encode())
                        for _control, _data, _file, offer in bundles
                    },
                )
            finally:
                for control, data, file_lane, _offer in reversed(bundles):
                    file_lane.close()
                    data.disconnect()
                    control.disconnect()
                file_server.stop()
                data_server.stop()
                control_server.stop()


if __name__ == "__main__":
    unittest.main()
