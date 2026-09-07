"""Real sockets reproduce the Windows installed/source port collision."""
import socket
import tempfile
import unittest

from app.crypto import IdentityStore
from app.file_transfer.transport import FileLaneServer
from app.network import NetworkServer
from app.session import SessionRegistry
from test_security_full_session import FakeProtector


class ListenerExclusivityTests(unittest.TestCase):
    def test_running_listener_cannot_be_stolen_and_can_restart_after_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = IdentityStore(directory, legacy_root=False, protector=FakeProtector()).load_or_create()
            for role in ('control', 'data', 'file'):
                with self.subTest(role=role):
                    registry = SessionRegistry('test')
                    if role == 'file':
                        server = FileLaneServer(identity=identity, host='127.0.0.1', port=0, coordinator=registry)
                    else:
                        server = NetworkServer('test', '127.0.0.1', 0, role=role, identity=identity, coordinator=registry)
                    try:
                        self.assertTrue(server.start())
                        with socket.socket() as duplicate:
                            duplicate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                            with self.assertRaises(OSError):
                                duplicate.bind(('127.0.0.1', server.port))
                                duplicate.listen()
                        server.stop()
                        self.assertTrue(server.start(), 'Clean stop must release the exclusive listener')
                    finally:
                        server.stop()

    def test_each_lane_refuses_a_port_owned_by_a_legacy_reuse_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = IdentityStore(directory, legacy_root=False, protector=FakeProtector()).load_or_create()
            for role in ('control', 'data', 'file'):
                with self.subTest(role=role), socket.socket() as legacy:
                    legacy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    legacy.bind(('127.0.0.1', 0))
                    legacy.listen()
                    port = legacy.getsockname()[1]
                    registry = SessionRegistry('test')
                    if role == 'file':
                        server = FileLaneServer(identity=identity, host='127.0.0.1', port=port, coordinator=registry)
                    else:
                        server = NetworkServer('test', '127.0.0.1', port, role=role, identity=identity, coordinator=registry)
                    try:
                        self.assertFalse(server.start(), 'A second Server must not report a successful start')
                    finally:
                        server.stop()
