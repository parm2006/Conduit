import struct
import unittest
import hashlib
import socket
import ssl
import threading

from app.crypto import IdentityStore
from pathlib import Path
import tempfile
from unittest.mock import patch
from app.file_transfer.models import FileItem, ItemType, Manifest
from app.file_transfer.receiver import TransferReceiver
from app.file_transfer.sender import TransferSender
from app.file_transfer.source import SourceFile
from app.session import SessionRegistry

from app.file_transfer.protocol import FrameError, encode_frame
from app.file_transfer.protocol import AuthenticationError, SessionAuthenticator
from app.file_transfer.transport import (
    FileLaneClient,
    FileLaneServer,
    authenticate_client_connection,
    authenticate_server_connection,
    read_frame,
    send_frame,
)


class FragmentedSocket:
    def __init__(self, incoming=b"", fragment_size=3):
        self.incoming = bytearray(incoming)
        self.fragment_size = fragment_size
        self.sent = bytearray()

    def recv(self, size):
        if not self.incoming:
            return b""
        count = min(size, self.fragment_size, len(self.incoming))
        data = bytes(self.incoming[:count])
        del self.incoming[:count]
        return data

    def sendall(self, data):
        self.sent.extend(data)

    def getpeercert(self, binary_form=False):
        return b"file peer certificate" if binary_form else {}


class TransportTests(unittest.TestCase):
    def test_closing_server_revokes_pending_control_session_file_token(self):
        coordinator = SessionRegistry("secret")
        offer = coordinator.authenticate_control(
            "secret",
            peer_identity="device-a",
            windows_name="DeviceA",
        )
        server = FileLaneServer(
            identity=self.identity, host="127.0.0.1", port=0,
            coordinator=coordinator,
        )
        server.offer_session(offer.file_token, offer.session_id)
        self.assertTrue(server.start())
        server.close()
        with self.identity.cert_path.open(encoding="ascii") as certificate_file:
            fingerprint = hashlib.sha256(
                ssl.PEM_cert_to_DER_cert(certificate_file.read())
            ).hexdigest()
        try:
            with self.assertRaises(Exception):
                FileLaneClient(peer_identity="device-a").connect(
                    "127.0.0.1", server.port, fingerprint,
                    offer.file_token, session_id=offer.session_id,
                )
        finally:
            server.stop()

    def setUp(self):
        self.identity_directory = tempfile.TemporaryDirectory()
        self.identity = IdentityStore(
            self.identity_directory.name, legacy_root=False
        ).load_or_create()

    def tearDown(self):
        self.identity_directory.cleanup()

    def test_reads_fragmented_frame_without_unbounded_allocation(self):
        sock = FragmentedSocket(encode_frame({"type": "status"}, b"ok"))

        metadata, payload = read_frame(sock)

        self.assertEqual(metadata, {"type": "status"})
        self.assertEqual(payload, b"ok")

    def test_rejects_oversized_header_before_reading_body(self):
        sock = FragmentedSocket(struct.pack(">II", 65_537, 0))
        with self.assertRaises(FrameError):
            read_frame(sock)

    def test_send_frame_uses_the_bounded_protocol(self):
        sock = FragmentedSocket()
        send_frame(sock, {"type": "status"}, b"ok")
        self.assertEqual(read_frame(FragmentedSocket(sock.sent)), ({"type": "status"}, b"ok"))

    def test_server_accepts_one_use_token_and_rejects_replay(self):
        authenticator = SessionAuthenticator("one-use-token")
        first = FragmentedSocket(encode_frame({"type": "authenticate", "token": "one-use-token"}))
        authenticate_server_connection(first, authenticator)
        self.assertEqual(read_frame(FragmentedSocket(first.sent))[0]["type"], "authenticated")

        replay = FragmentedSocket(encode_frame({"type": "authenticate", "token": "one-use-token"}))
        with self.assertRaises(AuthenticationError):
            authenticate_server_connection(replay, authenticator)

    def test_file_lane_token_is_bound_to_the_control_session(self):
        authenticator = SessionAuthenticator("one-use-token")
        wrong = FragmentedSocket(encode_frame({
            "type": "authenticate",
            "token": "one-use-token",
            "session_id": "wrong",
        }))
        with self.assertRaises(AuthenticationError):
            authenticate_server_connection(wrong, authenticator, "expected")

    def test_file_lane_rejects_wrong_peer_without_consuming_token(self):
        coordinator = SessionRegistry("secret")
        offer = coordinator.authenticate_control(
            "secret",
            peer_identity="device-a",
            windows_name="DeviceA",
            peer_address="192.0.2.10",
        )
        frame = encode_frame({
            "type": "authenticate",
            "token": offer.file_token,
            "session_id": offer.session_id,
            "peer_identity": "device-a",
        })

        with self.assertRaises(AuthenticationError):
            authenticate_server_connection(
                FragmentedSocket(frame),
                coordinator,
                offer.session_id,
                peer_address="192.0.2.11",
            )

        rightful = FragmentedSocket(frame)
        authenticate_server_connection(
            rightful,
            coordinator,
            offer.session_id,
            peer_address="192.0.2.10",
        )
        self.assertEqual(
            read_frame(FragmentedSocket(rightful.sent))[0]["type"],
            "authenticated",
        )

    def test_client_requires_file_lane_to_match_control_certificate(self):
        reply = encode_frame({"type": "authenticated"})
        expected = hashlib.sha256(b"file peer certificate").hexdigest()
        sock = FragmentedSocket(reply)
        authenticate_client_connection(sock, expected, "one-use-token")

        wrong = FragmentedSocket(reply)
        with self.assertRaises(AuthenticationError):
            authenticate_client_connection(wrong, "0" * 64, "one-use-token")

    def test_tls_file_lane_authenticates_and_delivers_bounded_event(self):
        received = []
        delivered = threading.Event()
        server = FileLaneServer(identity=self.identity, host="127.0.0.1", port=0)
        server.register_callback(
            "status",
            lambda metadata, payload: (received.append((metadata, payload)), delivered.set()),
        )
        token = server.issue_session()
        self.assertTrue(server.start())
        client = FileLaneClient()
        with self.identity.cert_path.open(encoding="ascii") as certificate_file:
            certificate_der = ssl.PEM_cert_to_DER_cert(certificate_file.read())
        fingerprint = hashlib.sha256(certificate_der).hexdigest()
        try:
            client.connect("127.0.0.1", server.port, fingerprint, token)
            client.send({"type": "status", "job_id": "safe"}, b"ok")
            self.assertTrue(delivered.wait(2))
            self.assertEqual(received, [({"type": "status", "job_id": "safe"}, b"ok")])
        finally:
            client.close()
            server.stop()

    def test_tls_file_server_rejects_another_source_without_consuming_token(self):
        coordinator = SessionRegistry("secret")
        offer = coordinator.authenticate_control(
            "secret",
            peer_identity="device-a",
            windows_name="DeviceA",
            peer_address="127.0.0.1",
        )
        server = FileLaneServer(
            identity=self.identity,
            host="127.0.0.1",
            port=0,
            coordinator=coordinator,
        )
        server.offer_session(offer.file_token, offer.session_id)
        self.assertTrue(server.start())
        with self.identity.cert_path.open(encoding="ascii") as certificate_file:
            fingerprint = hashlib.sha256(
                ssl.PEM_cert_to_DER_cert(certificate_file.read())
            ).hexdigest()

        def connect_from_other_peer(address, timeout):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.bind(("127.0.0.2", 0))
            sock.connect(address)
            return sock

        wrong = FileLaneClient(peer_identity="device-a")
        rightful = FileLaneClient(peer_identity="device-a")
        try:
            with patch(
                "app.file_transfer.transport.socket.create_connection",
                side_effect=connect_from_other_peer,
            ):
                with self.assertRaises(FrameError):
                    wrong.connect(
                        "127.0.0.1",
                        server.port,
                        fingerprint,
                        offer.file_token,
                        session_id=offer.session_id,
                    )

            rightful.connect(
                "127.0.0.1",
                server.port,
                fingerprint,
                offer.file_token,
                session_id=offer.session_id,
            )
            self.assertIsNotNone(rightful.sock)
        finally:
            wrong.close()
            rightful.close()
            server.stop()

    def test_tls_lane_transfers_file_with_end_to_end_hash_verification(self):
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as receive_directory:
            source_path = Path(source_directory) / "source.txt"
            source_path.write_bytes(b"verified over TLS")
            source = SourceFile.snapshot(source_path)
            manifest = Manifest.create([
                FileItem("received.txt", ItemType.FILE, source.size, source.modified_ns, source.sha256)
            ])
            server = FileLaneServer(identity=self.identity, host="127.0.0.1", port=0)
            receiver = TransferReceiver(Path(receive_directory))
            receiver.attach(server)
            token = server.issue_session()
            self.assertTrue(server.start())
            client = FileLaneClient()
            with self.identity.cert_path.open(encoding="ascii") as certificate_file:
                fingerprint = hashlib.sha256(
                    ssl.PEM_cert_to_DER_cert(certificate_file.read())
                ).hexdigest()
            try:
                client.connect("127.0.0.1", server.port, fingerprint, token)
                TransferSender(client).send_job(manifest, {"received.txt": source})
                completed_dir = Path(receive_directory) / "completed" / manifest.job_id
                for _ in range(100):
                    completed = list(completed_dir.glob("*.cache"))
                    if completed:
                        break
                    threading.Event().wait(0.01)
                self.assertEqual(
                    receiver.read_range(
                        manifest.job_id, "received.txt", 0, source.size
                    ),
                    b"verified over TLS",
                )
                self.assertNotEqual(completed[0].read_bytes(), b"verified over TLS")
            finally:
                client.close()
                server.stop()

    def test_file_lane_accepts_fresh_session_after_client_disconnect(self):
        server = FileLaneServer(identity=self.identity, host="127.0.0.1", port=0)
        self.assertTrue(server.start())
        with self.identity.cert_path.open(encoding="ascii") as certificate_file:
            fingerprint = hashlib.sha256(
                ssl.PEM_cert_to_DER_cert(certificate_file.read())
            ).hexdigest()
        first = FileLaneClient()
        second = FileLaneClient()
        try:
            first.connect("127.0.0.1", server.port, fingerprint, server.issue_session())
            first.close()
            for _ in range(100):
                if server.sock is None:
                    break
                threading.Event().wait(0.01)

            second.connect("127.0.0.1", server.port, fingerprint, server.issue_session())

            self.assertIsNotNone(second.sock)
            self.assertIsNotNone(server.sock)
        finally:
            first.close()
            second.close()
            server.stop()


if __name__ == "__main__":
    unittest.main()
