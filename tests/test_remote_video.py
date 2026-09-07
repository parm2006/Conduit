import base64
import importlib.util
import unittest
from io import BytesIO
from types import SimpleNamespace
import threading
from PIL import Image


class RemoteVideoTests(unittest.TestCase):
    def test_monitor_handoff_during_capture_serves_newest_request(self):
        video = self.module()
        from app.display_topology import NativeRect
        from unittest.mock import Mock
        entered, release, delivered = threading.Event(), threading.Event(), threading.Event()
        sent = []
        lane = SimpleNamespace(authenticated=True, send_message=lambda frame: (sent.append(frame), delivered.set()))
        client = SimpleNamespace(is_active=True, data_network=lane, display_group=SimpleNamespace(
            display=lambda _: SimpleNamespace(enabled=True, rect=NativeRect(0, 0, 32, 24))),
            active_topology_config={'destination_display_id': 'first', 'handoff_id': 'first-ack', 'topology_version': 1})
        calls = []
        def capture(_rect):
            calls.append(True)
            if len(calls) == 1:
                entered.set()
                release.wait(2)
            return Image.new('RGB', (32, 24)), (.5, .5)
        producer = video.ClientVideoCapture(client, capture)
        request = dict(request_id='first-request', display_id='first', handoff_id='first-ack',
                       topology_version=1, max_width=32, max_height=24)
        try:
            self.assertTrue(producer.request(request))
            self.assertTrue(entered.wait(1))
            client.active_topology_config.update(destination_display_id='second', handoff_id='second-ack')
            self.assertTrue(producer.request(dict(request, request_id='second-request',
                                                 display_id='second', handoff_id='second-ack')))
            release.set()
            self.assertTrue(delivered.wait(1))
            self.assertEqual([frame['request_id'] for frame in sent], ['second-request'])
        finally:
            release.set()
            producer.cancel()

    def receiver(self):
        video = self.module()
        from app.input_router import RemoteClient
        selection = RemoteClient('session', 'client', 'display', (3, 40), 'handoff')
        receiver = video.ServerVideoReceiver.__new__(video.ServerVideoReceiver)
        receiver._lock = threading.Lock()
        receiver._stop = threading.Event()
        receiver._wake = threading.Event()
        receiver.server = SimpleNamespace(input_router=SimpleNamespace(state=selection))
        receiver._expected = ('request', selection, 10)
        receiver._incoming = None
        receiver.selection = selection
        receiver.started_at = 10
        receiver.last_frame_at = 10.5
        receiver.failed_selection = None
        return receiver

    def test_foreign_or_stale_frames_cannot_refresh_stream(self):
        receiver = self.receiver()
        frame = dict(session_id='session', peer_identity='client', request_id='request',
                     handoff_id='handoff', display_id='display')
        for key in frame:
            self.assertFalse(receiver.receive(dict(frame, **{key: 'wrong'})))
            self.assertIsNone(receiver._incoming)
        self.assertTrue(receiver.receive(frame))
        receiver._stop.set()
        self.assertFalse(receiver.receive(frame))

    def test_watchdog_uses_fresh_frames_and_only_current_remote_owner(self):
        receiver = self.receiver()
        self.assertFalse(receiver.stalled(11.4))
        self.assertTrue(receiver.stalled(11.5))
        from app.input_router import LocalServer
        receiver.server.input_router.state = LocalServer('server', (20, 20))
        self.assertFalse(receiver.stalled(30))

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('app.remote_video'))
        from app import remote_video
        return remote_video

    def test_letterbox_preserves_aspect_and_upscales(self):
        video = self.module()
        self.assertEqual(video.fit_rect((1920, 1080), (1000, 1000)), (420, 0, 1080, 1080))
        self.assertEqual(video.fit_rect((1920, 1080), (3840, 2160)), (0, 0, 1920, 1080))

    def test_capture_requires_active_matching_handoff_and_display(self):
        video = self.module()
        client = SimpleNamespace(is_active=True, active_topology_config={
            'destination_display_id': 'screen', 'handoff_id': 'handoff', 'topology_version': 2})
        request = dict(display_id='screen', handoff_id='handoff', topology_version=2,
                       request_id='request', max_width=1920, max_height=1080)
        self.assertTrue(video.capture_authorized(client, request))
        for key, value in [('display_id', 'other'), ('handoff_id', 'old'), ('max_width', 100000)]:
            self.assertFalse(video.capture_authorized(client, dict(request, **{key: value})))
        client.is_active = False
        self.assertFalse(video.capture_authorized(client, request))

    def test_decoder_rejects_mismatched_and_oversized_images(self):
        video = self.module()
        output = BytesIO()
        Image.new('RGB', (32, 24)).save(output, 'JPEG')
        frame = dict(width=32, height=24, jpeg=base64.b64encode(output.getvalue()).decode())
        self.assertEqual(video.decode_frame(frame).size, (32, 24))
        with self.assertRaises(ValueError):
            video.decode_frame(dict(frame, width=100000))
        with self.assertRaises(ValueError):
            video.decode_frame(dict(frame, width=64))
        with self.assertRaises(ValueError):
            video.decode_frame(dict(frame, jpeg='not base64'))
