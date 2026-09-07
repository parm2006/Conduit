"""Exercise real paired TLS lanes; use deterministic pixels instead of a desktop."""
import gc
import tempfile
import time
import unittest
from types import SimpleNamespace
from PIL import Image

from app.crypto import IdentityStore
from app.display_topology import Display, MachineDisplayGroup, NativeRect
from app.input_router import RemoteClient, LocalServer
from app.network import NetworkClient, NetworkServer
from app.remote_video import ClientVideoCapture, ServerVideoReceiver
from app.session import SessionRegistry
from app.trust import PeerTrustStore
from test_security_full_session import FakeProtector, connect_network


class VideoTransportTests(unittest.TestCase):
    def test_frames_cross_paired_tls_and_stop_when_control_returns_local(self):
        gc.collect()
        with tempfile.TemporaryDirectory() as root:
            identity = IdentityStore(root + '/identity', legacy_root=False, protector=FakeProtector()).load_or_create()
            registry = SessionRegistry('video-test')
            control = NetworkServer('video-test', '127.0.0.1', 0, role='control', coordinator=registry, identity=identity)
            data = NetworkServer('video-test', '127.0.0.1', 0, role='data', coordinator=registry, identity=identity)
            trust = PeerTrustStore(root + '/trust', protector=FakeProtector())
            peer_control = NetworkClient('video-test', role='control', trust_store=trust,
                                         fingerprint_approval=lambda *_: True, peer_identity='client')
            peer_data = receiver = None
            try:
                self.assertTrue(control.start())
                self.assertTrue(data.start())
                self.assertEqual(connect_network(peer_control, '127.0.0.1', control.port), (True, None))
                session = peer_control.session_info
                peer_data = NetworkClient('', role='data', trust_store=trust,
                                          expected_fingerprint=peer_control.peer_certificate_fingerprint(),
                                          lane_token=session['data_token'], session_id=session['session_id'],
                                          peer_identity='client')
                self.assertEqual(connect_network(peer_data, '127.0.0.1', data.port), (True, None))
                display = Display('screen', NativeRect(0, 0, 64, 48), 100, 0, True)
                client = SimpleNamespace(is_active=True, data_network=peer_data,
                                         display_group=MachineDisplayGroup('client', 'Client', (display,)),
                                         active_topology_config={'destination_display_id': 'screen',
                                                                 'handoff_id': 'acknowledged', 'topology_version': 1})
                captures = []
                def capture(rect):
                    captures.append(rect)
                    return Image.new('RGB', (64, 48), '#2470b0'), (.5, .5)
                producer = ClientVideoCapture(client, capture=capture)
                peer_control.register_callback('remote_video_request', producer.request)
                router = SimpleNamespace(state=RemoteClient(session['session_id'], 'client', 'screen', (3, 20), 'acknowledged'),
                                         topology=SimpleNamespace(version=1))
                server = SimpleNamespace(input_router=router, data_network=data, control_network=control)
                receiver = ServerVideoReceiver(server, (128, 96))
                frame = None
                deadline = time.monotonic() + 3
                while frame is None and time.monotonic() < deadline:
                    frame = receiver.take_frame()
                    time.sleep(.01)
                self.assertIsNotNone(frame)
                self.assertEqual(frame[1].size, (128, 96))
                self.assertEqual(frame[2], (0, 0))
                self.assertTrue(captures)
                self.assertFalse(receiver.stalled())
                client.is_active = False
                router.state = LocalServer('server-primary', (100, 100))
                time.sleep(.1)
                count = len(captures)
                time.sleep(.15)
                self.assertEqual(len(captures), count)
                self.assertIsNone(receiver.take_frame())
            finally:
                if receiver:
                    receiver.stop()
                if peer_data:
                    peer_data.disconnect()
                peer_control.disconnect()
                data.stop()
                control.stop()
