"""Integration and lifecycle tests for the native real-time video streaming pipeline."""
import base64
import os
import secrets
import sys
import threading
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.native_streamer import (
    is_native_streaming_supported,
    NativeStreamerSender,
    NativeStreamerReceiver,
    STREAMER_EVENT_FIRST_FRAME,
    STREAMER_EVENT_NEED_KEYFRAME,
    STREAMER_EVENT_ERROR,
)
from app.remote_video import ServerVideoReceiver, fit_rect
from app.input_router import RemoteClient, LocalServer


class NativeStreamIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
            cls.canvas = tk.Canvas(cls.root, width=320, height=240)
            cls.canvas.pack()
            cls.root.update_idletasks()
            cls.test_hwnd = cls.canvas.winfo_id()
        except Exception:
            cls.root = None
            cls.test_hwnd = 0

    @classmethod
    def tearDownClass(cls):
        if cls.root is not None:
            try:
                cls.root.destroy()
            except Exception:
                pass

    def setUp(self):
        self.server_control_callbacks = {}
        self.client_control_callbacks = {}
        self.server_sent_messages = []
        self.client_sent_messages = []
        self.auto_ack_stream = True

        def mock_server_send(msg, session_id=None):
            self.server_sent_messages.append(msg)
            if msg.get("type") == "stream_start" and getattr(self, "auto_ack_stream", False):
                threading.Thread(
                    target=lambda: [cb({"stream_id": msg["stream_id"], "success": True})
                                   for cb in self.server_control_callbacks.get("stream_started", [])],
                    daemon=True,
                ).start()
            return True

        self.mock_server_control = SimpleNamespace(
            register_callback=lambda evt, cb: self.server_control_callbacks.setdefault(evt, []).append(cb),
            send_message=mock_server_send,
            authenticated=True,
        )
        self.mock_server_data = SimpleNamespace(
            register_callback=lambda evt, cb: None,
            authenticated=True,
        )
        self.selection = RemoteClient("session-1", "client-1", "display-1", (0, 0), "handoff-1")
        self.mock_server = SimpleNamespace(
            control_network=self.mock_server_control,
            data_network=self.mock_server_data,
            input_router=SimpleNamespace(state=self.selection, topology=SimpleNamespace(version=1)),
            routing_suspended=False,
        )

    def test_native_streaming_starts_and_stops_cleanly_via_control_lane(self):
        """1 & 2: Native stream starts, exchanges key over TLS, and stops cleanly."""
        if not is_native_streaming_supported() or not self.test_hwnd:
            self.skipTest("Hardware streaming DLL or window not available in environment")

        receiver = ServerVideoReceiver(self.mock_server, (640, 480), viewport_hwnd=self.test_hwnd, stream_port=54910)
        try:
            # Wait for receiver loop to send stream_start
            deadline = time.monotonic() + 1.0
            stream_start_msgs = []
            while time.monotonic() < deadline:
                stream_start_msgs = [m for m in self.server_sent_messages if m.get("type") == "stream_start"]
                if stream_start_msgs:
                    break
                time.sleep(0.02)

            self.assertEqual(len(stream_start_msgs), 1)
            msg = stream_start_msgs[0]
            self.assertEqual(msg["session_id"], "session-1")
            self.assertEqual(msg["display_id"], "display-1")
            self.assertEqual(msg["udp_port"], 54910)
            key_bytes = base64.b64decode(msg["stream_key"])
            self.assertEqual(len(key_bytes), 32)

            # Simulate client acknowledging stream start
            stream_id = msg["stream_id"]
            for cb in self.server_control_callbacks.get("stream_started", []):
                cb({"stream_id": stream_id, "success": True})

            self.assertTrue(receiver.is_native_active())

            # Stop receiver
            receiver.stop()
            self.assertFalse(receiver.is_native_active())

            # Verify stream_stop was sent
            stream_stop_msgs = [m for m in self.server_sent_messages if m.get("type") == "stream_stop"]
            self.assertEqual(len(stream_stop_msgs), 1)
            self.assertEqual(stream_stop_msgs[0]["stream_id"], stream_id)
        finally:
            receiver.stop()

    def test_client_unauthenticated_rejects_stream_start(self):
        """12: Unauthenticated client cannot start native video stream."""
        from app.client import ConduitClient
        client = ConduitClient.__new__(ConduitClient)
        client.is_active = False # Inactive
        client.control_network = SimpleNamespace(authenticated=False, send_message=Mock())
        client._native_sender = None

        client.on_stream_start({
            "session_id": "session-1",
            "stream_id": "stream-1",
            "stream_key": base64.b64encode(b"K" * 32).decode("ascii"),
            "udp_port": 54911,
            "display_id": "display-1",
        })

        client.control_network.send_message.assert_called_once()
        args = client.control_network.send_message.call_args[0][0]
        self.assertFalse(args["success"])
        self.assertEqual(args["error"], "not_authenticated")
        self.assertIsNone(client._native_sender)

    def test_stream_key_validation_rejects_invalid_lengths(self):
        """13: Invalid or malformed stream keys are rejected."""
        from app.client import ConduitClient
        client = ConduitClient.__new__(ConduitClient)
        client.is_active = True
        client.control_network = SimpleNamespace(authenticated=True, send_message=Mock())
        client._native_sender = None

        # Key too short (16 bytes instead of 32)
        client.on_stream_start({
            "session_id": "session-1",
            "stream_id": "stream-1",
            "stream_key": base64.b64encode(b"short-key").decode("ascii"),
            "udp_port": 54912,
            "display_id": "display-1",
        })

        args = client.control_network.send_message.call_args[0][0]
        self.assertFalse(args["success"])
        self.assertIn("32 bytes", args["error"])

    def test_native_init_failure_triggers_automatic_gdi_fallback(self):
        """8: If native receiver fails to start, falls back to GDI/JPEG cleanly."""
        # viewport_hwnd=None forces native failure
        receiver = ServerVideoReceiver(self.mock_server, (640, 480), viewport_hwnd=None)
        try:
            self.assertFalse(receiver.is_native_active())
            deadline = time.monotonic() + 1.0
            gdi_requests = []
            while time.monotonic() < deadline:
                gdi_requests = [m for m in self.server_sent_messages if m.get("type") == "remote_video_request"]
                if gdi_requests:
                    break
                time.sleep(0.02)
            self.assertGreaterEqual(len(gdi_requests), 1)
        finally:
            receiver.stop()

    def test_native_runtime_stall_triggers_fallback_without_crash(self):
        """9 & 10: If native stream stops delivering frames, falls back to GDI/JPEG."""
        if not is_native_streaming_supported() or not self.test_hwnd:
            self.skipTest("Hardware streaming DLL not available in environment")

        receiver = ServerVideoReceiver(self.mock_server, (640, 480), viewport_hwnd=self.test_hwnd, stream_port=54914)
        try:
            # Wait for stream_start
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if any(m.get("type") == "stream_start" for m in self.server_sent_messages):
                    break
                time.sleep(0.02)

            stream_start_msgs = [m for m in self.server_sent_messages if m.get("type") == "stream_start"]
            self.assertEqual(len(stream_start_msgs), 1)
            stream_id = stream_start_msgs[0]["stream_id"]

            for cb in self.server_control_callbacks.get("stream_started", []):
                cb({"stream_id": stream_id, "success": True})

            self.assertTrue(receiver.is_native_active())

            # Simulate silence / stall (last frame was 1.5 seconds ago)
            receiver.last_frame_at = time.monotonic() - 1.5

            # Wait for receiver loop to detect stall and fall back
            deadline = time.monotonic() + 1.0
            gdi_requests = []
            while time.monotonic() < deadline:
                gdi_requests = [m for m in self.server_sent_messages if m.get("type") == "remote_video_request"]
                if gdi_requests:
                    break
                time.sleep(0.02)

            self.assertFalse(receiver.is_native_active())
            self.assertEqual(receiver._native_failed_selection, self.selection)
            self.assertGreaterEqual(len(gdi_requests), 1)
        finally:
            receiver.stop()

    def test_keyframe_request_flow_on_packet_corruption(self):
        """7 & 11: Decoder packet loss signals STREAMER_EVENT_NEED_KEYFRAME and sends control message."""
        if not is_native_streaming_supported() or not self.test_hwnd:
            self.skipTest("Hardware streaming DLL not available in environment")

        receiver = ServerVideoReceiver(self.mock_server, (640, 480), viewport_hwnd=self.test_hwnd, stream_port=54915)
        try:
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if any(m.get("type") == "stream_start" for m in self.server_sent_messages):
                    break
                time.sleep(0.02)

            stream_start_msgs = [m for m in self.server_sent_messages if m.get("type") == "stream_start"]
            self.assertEqual(len(stream_start_msgs), 1)
            stream_id = stream_start_msgs[0]["stream_id"]

            for cb in self.server_control_callbacks.get("stream_started", []):
                cb({"stream_id": stream_id, "success": True})

            # Simulate receiver callback firing NEED_KEYFRAME
            receiver._native_receiver._on_event(STREAMER_EVENT_NEED_KEYFRAME, "Need IDR")

            kf_requests = [m for m in self.server_sent_messages if m.get("type") == "stream_keyframe_request"]
            self.assertGreaterEqual(len(kf_requests), 1)
            self.assertEqual(kf_requests[0]["session_id"], "session-1")
            self.assertEqual(kf_requests[0]["stream_id"], stream_id)
        finally:
            receiver.stop()

    def test_monitor_display_change_switches_stream(self):
        """5 & 6: Changing active monitor stops previous stream and re-targets."""
        if not is_native_streaming_supported() or not self.test_hwnd:
            self.skipTest("Hardware streaming DLL not available in environment")

        receiver = ServerVideoReceiver(self.mock_server, (640, 480), viewport_hwnd=self.test_hwnd, stream_port=54916)
        try:
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if any(m.get("type") == "stream_start" for m in self.server_sent_messages):
                    break
                time.sleep(0.02)

            first_msgs = [m for m in self.server_sent_messages if m.get("type") == "stream_start"]
            self.assertEqual(len(first_msgs), 1)
            first_stream_id = first_msgs[0]["stream_id"]
            self.assertEqual(first_msgs[0]["display_id"], "display-1")

            # Acknowledge first stream
            for cb in self.server_control_callbacks.get("stream_started", []):
                cb({"stream_id": first_stream_id, "success": True})

            # Switch monitor to display-2
            new_selection = RemoteClient("session-1", "client-1", "display-2", (0, 0), "handoff-2")
            self.mock_server.input_router.state = new_selection

            # Wait for loop to pick up new selection and send stream_start for display-2
            deadline = time.monotonic() + 1.5
            second_msgs = []
            while time.monotonic() < deadline:
                second_msgs = [m for m in self.server_sent_messages if m.get("type") == "stream_start" and m.get("display_id") == "display-2"]
                if second_msgs:
                    break
                time.sleep(0.02)

            # Old stream must be stopped
            stop_msgs = [m for m in self.server_sent_messages if m.get("type") == "stream_stop"]
            self.assertGreaterEqual(len(stop_msgs), 1)
            self.assertEqual(stop_msgs[0]["stream_id"], first_stream_id)

            # New stream must be started for display-2
            self.assertEqual(len(second_msgs), 1)
        finally:
            receiver.stop()

    def test_cursor_return_to_local_stops_native_stream(self):
        """3 & 4: When cursor returns to LocalServer, stream stops cleanly."""
        if not is_native_streaming_supported() or not self.test_hwnd:
            self.skipTest("Hardware streaming DLL not available in environment")

        receiver = ServerVideoReceiver(self.mock_server, (640, 480), viewport_hwnd=self.test_hwnd, stream_port=54917)
        try:
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if any(m.get("type") == "stream_start" for m in self.server_sent_messages):
                    break
                time.sleep(0.02)

            stream_start_msgs = [m for m in self.server_sent_messages if m.get("type") == "stream_start"]
            self.assertEqual(len(stream_start_msgs), 1)
            stream_id = stream_start_msgs[0]["stream_id"]

            for cb in self.server_control_callbacks.get("stream_started", []):
                cb({"stream_id": stream_id, "success": True})

            self.assertTrue(receiver.is_native_active())

            # Cursor returns to server
            self.mock_server.input_router.state = LocalServer("server", (50, 50))

            deadline = time.monotonic() + 1.0
            stop_msgs = []
            while time.monotonic() < deadline:
                stop_msgs = [m for m in self.server_sent_messages if m.get("type") == "stream_stop"]
                if stop_msgs:
                    break
                time.sleep(0.02)

            self.assertFalse(receiver.is_native_active())
            self.assertEqual(len(stop_msgs), 1)
            self.assertEqual(stop_msgs[0]["stream_id"], stream_id)
        finally:
            receiver.stop()


if __name__ == "__main__":
    unittest.main()
