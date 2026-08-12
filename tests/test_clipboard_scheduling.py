import unittest
from unittest.mock import patch

from app.clipboard_handler import ClipboardHandler
from app.clipboard_formats import (
    ClipboardEntry,
    ClipboardPayloadError as V2ClipboardPayloadError,
    ClipboardSnapshot,
    decode_clipboard_message,
    encode_clipboard_message,
)
from app.client import ConduitClient
from app.file_transfer.paste_coordinator import PasteCoordinator
from app.server import ConduitServer
from app.windows_clipboard import ClipboardAccessError


class RecordingSender:
    def __init__(self):
        self.submitted = []

    def submit(self, payload):
        self.submitted.append(payload)
        return True


class RecordingNetwork:
    def __init__(self):
        self.messages = []

    def send_message(self, message):
        self.messages.append(message)
        return True


class SessionNetwork(RecordingNetwork):
    def __init__(self, session_id="session-one"):
        super().__init__()
        self.session_id = session_id
        self.session_info = {"session_id": session_id}


class RecordingClipboard:
    def __init__(self):
        self.injected = []

    def inject(self, payload):
        self.injected.append(payload)


class FakeClipboardAdapter:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.capture_calls = 0

    def capture_open_clipboard(self):
        self.capture_calls += 1
        value = self.snapshots.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class ClipboardSequenceTests(unittest.TestCase):
    def test_capture_trace_omits_clipboard_content(self):
        snapshot = ClipboardSnapshot([
            ClipboardEntry("unicode_text", b"PRIVATE-TEXT-CONTENT"),
        ])
        handler = ClipboardHandler(
            lambda _snapshot: None,
            clipboard_adapter=FakeClipboardAdapter([snapshot]),
        )

        with (
            patch("app.clipboard_handler.win32clipboard.OpenClipboard"),
            patch("app.clipboard_handler.win32clipboard.CloseClipboard"),
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                return_value=False,
            ),
            self.assertLogs("app.clipboard_handler", level="INFO") as logs,
        ):
            handler._process_clipboard_sequence(11)

        output = "\n".join(logs.output)
        self.assertIn("Clipboard snapshot captured", output)
        self.assertNotIn("PRIVATE-TEXT-CONTENT", output)

    def test_every_allowed_snapshot_kind_and_repeat_sequence_is_forwarded(self):
        same = ClipboardSnapshot([ClipboardEntry("html", b"same")])
        snapshots = [
            ClipboardSnapshot([ClipboardEntry("html", b"html")]),
            ClipboardSnapshot([ClipboardEntry("rtf", b"rtf")]),
            ClipboardSnapshot([ClipboardEntry("png", b"png")]),
            ClipboardSnapshot([ClipboardEntry("dibv5", b"dibv5")]),
            ClipboardSnapshot(
                [
                    ClipboardEntry("png", b"mixed-png"),
                    ClipboardEntry("html", b"mixed-html"),
                ]
            ),
            same,
            same,
        ]
        forwarded = []
        adapter = FakeClipboardAdapter(snapshots)
        handler = ClipboardHandler(forwarded.append, clipboard_adapter=adapter)

        with (
            patch("app.clipboard_handler.win32clipboard.OpenClipboard"),
            patch("app.clipboard_handler.win32clipboard.CloseClipboard"),
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                return_value=False,
            ),
        ):
            for sequence in range(11, 18):
                handler._process_clipboard_sequence(sequence)

        self.assertEqual(forwarded, snapshots)
        self.assertEqual(adapter.capture_calls, len(snapshots))

    def test_file_sequence_skips_ordinary_capture(self):
        adapter = FakeClipboardAdapter(
            [ClipboardSnapshot([ClipboardEntry("html", b"fallback")])]
        )
        forwarded = []
        file_changes = []
        handler = ClipboardHandler(
            forwarded.append,
            on_file_availability=file_changes.append,
            clipboard_adapter=adapter,
        )

        with patch(
            "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
            return_value=True,
        ):
            handler._process_clipboard_sequence(11)

        self.assertEqual(file_changes, [True])
        self.assertEqual(forwarded, [])
        self.assertEqual(adapter.capture_calls, 0)

    def test_invalid_snapshot_is_rejected_once_without_lock_retries(self):
        adapter = FakeClipboardAdapter(
            [V2ClipboardPayloadError("PRIVATE-CONTENT-MARKER")]
        )
        handler = ClipboardHandler(lambda _snapshot: None, clipboard_adapter=adapter)

        with (
            patch("app.clipboard_handler.win32clipboard.OpenClipboard"),
            patch("app.clipboard_handler.win32clipboard.CloseClipboard"),
            patch("app.clipboard_handler.time.sleep") as sleep,
            self.assertLogs("app.clipboard_handler", level="WARNING") as logs,
        ):
            self.assertIsNone(handler._read_clipboard())

        self.assertEqual(adapter.capture_calls, 1)
        sleep.assert_not_called()
        self.assertNotIn("PRIVATE-CONTENT-MARKER", " ".join(logs.output))

    def test_transient_open_failure_keeps_bounded_retry_behavior(self):
        snapshot = ClipboardSnapshot([ClipboardEntry("html", b"html")])
        adapter = FakeClipboardAdapter([snapshot])
        handler = ClipboardHandler(lambda _snapshot: None, clipboard_adapter=adapter)

        with (
            patch(
                "app.clipboard_handler.win32clipboard.OpenClipboard",
                side_effect=[OSError("locked"), OSError("locked"), None],
            ),
            patch("app.clipboard_handler.win32clipboard.CloseClipboard"),
            patch("app.clipboard_handler.time.sleep") as sleep,
        ):
            self.assertEqual(handler._read_clipboard(), snapshot)

        self.assertEqual(adapter.capture_calls, 1)
        self.assertEqual(sleep.call_count, 2)

    def test_adapter_access_failure_is_not_retried_as_lock_contention(self):
        adapter = FakeClipboardAdapter(
            [ClipboardAccessError("PRIVATE-OPERATING-SYSTEM-DETAIL")]
        )
        handler = ClipboardHandler(lambda _snapshot: None, clipboard_adapter=adapter)

        with (
            patch("app.clipboard_handler.win32clipboard.OpenClipboard"),
            patch("app.clipboard_handler.win32clipboard.CloseClipboard"),
            patch("app.clipboard_handler.time.sleep") as sleep,
            self.assertLogs("app.clipboard_handler", level="WARNING") as logs,
        ):
            self.assertIsNone(handler._read_clipboard())

        self.assertEqual(adapter.capture_calls, 1)
        sleep.assert_not_called()
        self.assertNotIn(
            "PRIVATE-OPERATING-SYSTEM-DETAIL",
            " ".join(logs.output),
        )


class PeerClipboardSchedulingTests(unittest.TestCase):
    def test_stale_remote_snapshot_cannot_overwrite_newer_local_file_offer(self):
        server = ConduitServer.__new__(ConduitServer)
        server.switching_to_client = False
        server.control_network = SessionNetwork()
        server.paste_coordinator = PasteCoordinator(lambda: None)
        server.clipboard = RecordingClipboard()
        remote_offer = {
            "type": "clipboard_offer",
            "session_id": "session-one",
            "revision": 1,
            "source": "client",
            "kind": "ordinary",
            "sequence": 20,
        }
        server.on_remote_clipboard_offer(remote_offer)
        server.on_local_clipboard_offer("files", 30)
        payload = encode_clipboard_message(
            ClipboardSnapshot([ClipboardEntry("html", b"stale")])
        )
        payload["offer"] = remote_offer

        self.assertFalse(server.on_remote_copy(payload))

        self.assertEqual(server.clipboard.injected, [])
        self.assertEqual(server.clipboard_offer_state.current_offer.source, "server")

    def test_snapshot_can_establish_matching_offer_when_data_arrives_first(self):
        server = ConduitServer.__new__(ConduitServer)
        server.switching_to_client = False
        server.control_network = SessionNetwork()
        server.paste_coordinator = PasteCoordinator(lambda: None)
        server.clipboard = RecordingClipboard()
        remote_offer = {
            "type": "clipboard_offer",
            "session_id": "session-one",
            "revision": 1,
            "source": "client",
            "kind": "ordinary",
            "sequence": 20,
        }
        snapshot = ClipboardSnapshot([ClipboardEntry("html", b"fresh")])
        payload = encode_clipboard_message(snapshot)
        payload["offer"] = remote_offer

        server.on_remote_copy(payload)

        self.assertEqual(len(server.clipboard.injected), 1)
        self.assertEqual(
            decode_clipboard_message(server.clipboard.injected[0]), snapshot
        )
        self.assertEqual(server.clipboard_offer_state.current_offer.source, "client")

    def test_local_snapshot_is_queued_with_its_authoritative_offer(self):
        client = ConduitClient.__new__(ConduitClient)
        client.is_active = True
        client.control_network = SessionNetwork()
        client.paste_coordinator = PasteCoordinator(lambda: None)
        client.clipboard_sender = RecordingSender()
        client.on_local_clipboard_offer("ordinary", 20)
        snapshot = ClipboardSnapshot([ClipboardEntry("rtf", b"fresh")])

        self.assertTrue(client.on_local_copy(snapshot))

        work = client.clipboard_sender.submitted[0]
        self.assertEqual(work["snapshot"], snapshot)
        self.assertEqual(work["offer"].source, "client")
        self.assertEqual(work["offer"].revision, 1)

    def test_client_submission_trace_omits_clipboard_content(self):
        client = ConduitClient.__new__(ConduitClient)
        client.clipboard_sender = RecordingSender()
        snapshot = ClipboardSnapshot([
            ClipboardEntry("rtf", b"PRIVATE-RTF-CONTENT"),
        ])

        with self.assertLogs("app.client", level="INFO") as logs:
            self.assertTrue(client.on_local_copy(snapshot))

        output = "\n".join(logs.output)
        self.assertIn("Clipboard snapshot queued", output)
        self.assertNotIn("PRIVATE-RTF-CONTENT", output)

    def test_client_to_server_trace_identifies_boundaries_without_payload_content(self):
        client = ConduitClient.__new__(ConduitClient)
        client.data_network = RecordingNetwork()
        server = ConduitServer.__new__(ConduitServer)
        server.clipboard = RecordingClipboard()
        snapshot = ClipboardSnapshot([
            ClipboardEntry("html", b"PRIVATE-CLIPBOARD-CONTENT"),
        ])

        with self.assertLogs(level="INFO") as logs:
            self.assertTrue(client._send_clipboard_snapshot({"snapshot": snapshot}))
            server.on_remote_copy(client.data_network.messages[0])

        output = "\n".join(logs.output)
        self.assertIn("Clipboard snapshot sent", output)
        self.assertIn("Clipboard snapshot received", output)
        self.assertNotIn("PRIVATE-CLIPBOARD-CONTENT", output)
        self.assertEqual(
            decode_clipboard_message(server.clipboard.injected[0]),
            snapshot,
        )

    def test_client_submits_snapshot_without_mutating_it(self):
        client = ConduitClient.__new__(ConduitClient)
        client.clipboard_sender = RecordingSender()
        snapshot = ClipboardSnapshot([ClipboardEntry("html", b"hello")])

        self.assertTrue(client.on_local_copy(snapshot))

        self.assertEqual(snapshot.entries[0].data, b"hello")
        self.assertEqual(
            client.clipboard_sender.submitted,
            [{"snapshot": snapshot}],
        )

    def test_server_submits_snapshot_without_mutating_it(self):
        server = ConduitServer.__new__(ConduitServer)
        server.clipboard_sender = RecordingSender()
        snapshot = ClipboardSnapshot([ClipboardEntry("png", b"png")])

        self.assertTrue(server.on_local_copy(snapshot))

        self.assertEqual(snapshot.entries[0].data, b"png")
        self.assertEqual(
            server.clipboard_sender.submitted,
            [{"snapshot": snapshot}],
        )

    def test_client_encodes_snapshot_and_preserves_message_type(self):
        client = ConduitClient.__new__(ConduitClient)
        client.data_network = RecordingNetwork()
        snapshot = ClipboardSnapshot([ClipboardEntry("dib", b"dib")])

        self.assertTrue(client._send_clipboard_snapshot({"snapshot": snapshot}))

        message = client.data_network.messages[0]
        self.assertEqual(decode_clipboard_message(message), snapshot)

    def test_server_encodes_snapshot_and_preserves_message_type(self):
        server = ConduitServer.__new__(ConduitServer)
        server.data_network = RecordingNetwork()
        snapshot = ClipboardSnapshot(
            [
                ClipboardEntry("png", b"png"),
                ClipboardEntry("html", b"<p>x</p>"),
            ]
        )

        self.assertTrue(server._send_clipboard_snapshot({"snapshot": snapshot}))

        message = server.data_network.messages[0]
        self.assertEqual(decode_clipboard_message(message), snapshot)


if __name__ == "__main__":
    unittest.main()
