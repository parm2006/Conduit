import time
import unittest

from app.native_streamer import (
    is_native_streaming_supported,
    get_streamer_dll,
    NativeStreamerSender,
    NativeStreamerReceiver,
    STREAMER_EVENT_STARTED,
    STREAMER_EVENT_STOPPED,
)


class TestNativeStreamer(unittest.TestCase):
    def test_dll_load_and_version(self):
        dll = get_streamer_dll()
        self.assertIsNotNone(dll)
        self.assertEqual(dll.streamer_get_version(), 100)

    def test_hardware_support(self):
        supported = is_native_streaming_supported()
        self.assertTrue(supported)

    def test_sender_receiver_lifecycle(self):
        events_sender = []
        events_receiver = []

        session_key = b"A" * 32  # 32-byte key

        def on_sender_event(code, msg):
            events_sender.append((code, msg))

        def on_receiver_event(code, msg):
            events_receiver.append((code, msg))

        sender = NativeStreamerSender(on_event=on_sender_event)
        self.assertIsNotNone(sender)

        # Test sender lifecycle
        sender_started = sender.start(
            display_index=0,
            target_ip="127.0.0.1",
            target_port=54899,
            session_key=session_key,
            bitrate_kbps=5000,
            fps=30,
        )
        self.assertTrue(sender_started)
        sender.request_keyframe()
        time.sleep(0.1)
        sender.stop()
        sender.destroy()

    def test_loopback_stream_delivery(self):
        import win32gui
        import win32con

        # Register a simple test window class and create a hidden window
        wc = win32gui.WNDCLASS()
        wc.lpszClassName = "ConduitStreamerTestWnd"
        wc.lpfnWndProc = win32gui.DefWindowProc
        try:
            win32gui.RegisterClass(wc)
        except Exception:
            pass # Class already registered

        hwnd = win32gui.CreateWindowEx(
            0, "ConduitStreamerTestWnd", "TestWindow",
            win32con.WS_OVERLAPPEDWINDOW,
            0, 0, 640, 480,
            0, 0, 0, None
        )
        self.assertTrue(hwnd != 0)

        events_receiver = []
        session_key = b"K" * 32

        def on_recv_event(code, msg):
            events_receiver.append((code, msg))

        receiver = NativeStreamerReceiver(hwnd, on_event=on_recv_event)
        recv_started = receiver.start(listen_port=54895, session_key=session_key)
        self.assertTrue(recv_started)

        sender = NativeStreamerSender()
        send_started = sender.start(
            display_index=0,
            target_ip="127.0.0.1",
            target_port=54895,
            session_key=session_key,
            bitrate_kbps=8000,
            fps=30,
        )
        self.assertTrue(send_started)

        # Wait up to 3.0 seconds for first frame to be decoded and rendered
        deadline = time.monotonic() + 3.0
        first_frame = False
        while time.monotonic() < deadline:
            if any(code == 3 for code, _ in events_receiver) or receiver.get_frame_count() > 0:
                first_frame = True
                break
            time.sleep(0.05)

        recv_frames = receiver.get_frame_count()
        send_frames = sender.get_frame_count()

        sender.stop()
        sender.destroy()
        receiver.stop()
        receiver.destroy()
        win32gui.DestroyWindow(hwnd)

        self.assertTrue(first_frame, f"Expected first frame event, got: {events_receiver}")
        self.assertGreaterEqual(recv_frames, 1)
        self.assertGreaterEqual(send_frames, 1)


if __name__ == "__main__":
    unittest.main()
