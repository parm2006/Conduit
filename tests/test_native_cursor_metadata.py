import threading
import unittest
from types import SimpleNamespace

from app.native_streamer import NativeStreamerReceiver
from app.remote_video import ServerVideoReceiver


class NativeCursorMetadataTests(unittest.TestCase):
    def receiver(self, value=None):
        receiver = NativeStreamerReceiver.__new__(NativeStreamerReceiver)
        receiver._handle = 123
        receiver._lifecycle_lock = threading.Lock()
        receiver._dll = SimpleNamespace()
        if value is not None:
            receiver._dll.streamer_receiver_get_cursor_in_frame = lambda handle: value
        # This fixture owns no native allocation.
        self.addCleanup(setattr, receiver, '_handle', None)
        return receiver

    def test_absent_export_and_unknown_peer_do_not_hide_the_only_cursor(self):
        for value in (None, -1):
            self.assertIsNone(self.receiver(value).get_cursor_in_frame())

    def test_native_presented_cursor_policy_is_tristate(self):
        self.assertIs(self.receiver(0).get_cursor_in_frame(), False)
        self.assertIs(self.receiver(1).get_cursor_in_frame(), True)

    def test_gui_query_does_not_wait_for_renderer_destruction(self):
        receiver = self.receiver(1)
        with receiver._lifecycle_lock:
            self.assertIsNone(receiver.get_cursor_in_frame())

    def test_video_receiver_uses_only_the_active_native_renderer_policy(self):
        receiver = ServerVideoReceiver.__new__(ServerVideoReceiver)
        receiver._stop = threading.Event()
        receiver._native_active = True
        receiver._native_receiver = self.receiver(1)
        self.assertIs(receiver.cursor_in_frame(), True)
        receiver._native_active = False
        self.assertIsNone(receiver.cursor_in_frame())
        receiver._native_active = True
        receiver._stop.set()
        self.assertIsNone(receiver.cursor_in_frame())
