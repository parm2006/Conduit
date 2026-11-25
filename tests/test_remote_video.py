import base64
import importlib.util
import unittest
from io import BytesIO
from types import SimpleNamespace
import threading
from PIL import Image


class RemoteVideoTests(unittest.TestCase):
    def test_replacement_waits_for_previous_cleanup_without_blocking_gui(self):
        from unittest.mock import Mock
        receiver = self.receiver()
        receiver._previous_cleanup = threading.Event()
        receiver._run_loop = Mock()
        receiver._stop_native = Mock()
        worker = threading.Thread(target=receiver._run, daemon=True)
        worker.start()
        try:
            receiver._run_loop.assert_not_called()
            receiver.stop()
            # Even canceled replacements preserve the cleanup chain so a
            # third viewer cannot overtake the first receiver's port release.
            self.assertTrue(worker.is_alive())
        finally:
            receiver._previous_cleanup.set()
            worker.join(1)
        self.assertFalse(worker.is_alive())
        receiver._stop_native.assert_called_once_with()

    def test_gui_resize_and_stop_never_call_native_renderer(self):
        from unittest.mock import Mock
        receiver = self.receiver()
        receiver._latest = None
        native = receiver._native_receiver = Mock()
        receiver._native_stream_id = None
        receiver.resize(800, 600)
        receiver.stop()
        native.resize.assert_not_called()
        native.stop.assert_not_called()
        self.assertTrue(receiver._stop.is_set())
        self.assertEqual(receiver.viewport, (800, 600))

    def test_video_worker_applies_latest_resize_and_cleans_up_on_exit(self):
        from unittest.mock import Mock
        receiver = self.receiver()
        receiver._native_receiver = Mock()
        receiver.resize(800, 600)
        receiver.resize(1024, 768)
        receiver._apply_native_resize()
        receiver._native_receiver.resize.assert_called_once_with(1024, 768)
        receiver._apply_native_resize()
        receiver._native_receiver.resize.assert_called_once()
        receiver._run_loop = Mock(side_effect=RuntimeError('worker failed'))
        receiver._stop_native = Mock()
        with self.assertRaises(RuntimeError):
            receiver._run()
        receiver._stop_native.assert_called_once_with()

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

    def test_native_ack_preserves_initial_startup_window_before_first_frame(self):
        from unittest.mock import Mock, patch
        import app.native_streamer as native_streamer
        video = self.module()
        receiver = self.receiver()
        receiver.viewport = (2496, 1664)
        receiver.viewport_hwnd = 12345
        receiver.stream_port = 28906
        receiver._native_receiver = None
        receiver._native_active = False
        receiver._native_stream_id = None
        receiver._native_failed_selection = None
        receiver._native_started_event = threading.Event()
        receiver._native_started_success = False
        receiver._selection_started_at = 10.0
        receiver.last_frame_at = 0.0
        receiver.server.control_network = SimpleNamespace(
            send_message=lambda msg, session_id=None: (
                receiver._on_stream_started({'stream_id': msg['stream_id'], 'success': True}),
                True,
            )[1]
        )
        fake_native = Mock()
        fake_native.start.return_value = True
        with patch.object(native_streamer, 'is_native_streaming_supported', return_value=True), \
                patch.object(native_streamer, 'NativeStreamerReceiver', return_value=fake_native):
            self.assertTrue(receiver._start_native(receiver.selection))
        self.assertEqual(receiver.last_frame_at, 0.0)
        self.assertTrue(receiver._native_active)

    def test_stalled_gives_native_and_jpeg_fallback_separate_windows(self):
        video = self.module()
        receiver = self.receiver()
        receiver.viewport_hwnd = 12345
        receiver._native_failed_selection = None
        receiver._selection_started_at = 10.0
        receiver.started_at = 10.0
        receiver.last_frame_at = 0.0
        # While native is starting/falling back, stalled() does not fire at 1.0s or 2.6s.
        self.assertFalse(receiver.stalled(11.1))
        self.assertFalse(receiver.stalled(12.6))
        # Once native fallback moves to JPEG, JPEG gets its own startup window.
        receiver._native_failed_selection = receiver.selection
        receiver.started_at = 12.6
        receiver.last_frame_at = 0.0
        self.assertFalse(receiver.stalled(13.6))
        self.assertTrue(receiver.stalled(12.6 + video.INITIAL_FRAME_TIMEOUT))

    def test_client_capture_discovers_displays_when_missing(self):
        video = self.module()
        from app.display_topology import NativeRect
        sent = []
        lane = SimpleNamespace(authenticated=True, send_message=lambda frame: sent.append(frame))
        discovered_group = SimpleNamespace(
            display=lambda did: SimpleNamespace(enabled=True, rect=NativeRect(0, 0, 32, 24))
        )
        client = SimpleNamespace(
            is_active=True,
            data_network=lane,
            machine_id='client-id',
            windows_name='client-pc',
            display_group=None,
            display_discovery=SimpleNamespace(discover=lambda m, w: discovered_group),
            active_topology_config={'destination_display_id': 'display-1', 'handoff_id': 'h1', 'topology_version': 1},
        )
        producer = video.ClientVideoCapture(client, lambda _r: (Image.new('RGB', (32, 24)), (0.5, 0.5)))
        self.assertTrue(producer._produce(
            dict(request_id='r1', display_id='display-1', handoff_id='h1', topology_version=1, max_width=32, max_height=24),
            lane,
            producer._generation,
        ) is None)
        self.assertEqual(len(sent), 1)
        self.assertIs(client.display_group, discovered_group)
