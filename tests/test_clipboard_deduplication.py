import unittest
from unittest.mock import MagicMock, patch

from app.clipboard_formats import ClipboardEntry, ClipboardSnapshot, encode_clipboard_message
from app.clipboard_handler import ClipboardHandler


class ClipboardDeduplicationTests(unittest.TestCase):
    def test_snapshot_fingerprint_is_deterministic_and_order_independent(self):
        entry_text = ClipboardEntry("unicode_text", "Hello World\0".encode("utf-16le"))
        entry_html = ClipboardEntry("html", b"<b>Hello World</b>")

        snap1 = ClipboardSnapshot((entry_text, entry_html))
        snap2 = ClipboardSnapshot((entry_html, entry_text))

        self.assertEqual(snap1.fingerprint(), snap2.fingerprint())
        self.assertIsInstance(snap1.fingerprint(), str)
        self.assertEqual(len(snap1.fingerprint()), 64)

    def test_single_use_echo_suppression(self):
        forwarded = []
        handler = ClipboardHandler(
            on_clipboard_change=lambda snap: forwarded.append(snap)
        )
        
        entry = ClipboardEntry("unicode_text", "Test Payload\0".encode("utf-16le"))
        snapshot = ClipboardSnapshot((entry,))
        payload = encode_clipboard_message(snapshot)

        # Mock adapter read to return our snapshot when poller reads
        handler.clipboard_adapter = MagicMock()
        handler.clipboard_adapter.capture_open_clipboard.return_value = snapshot
        handler.clipboard_adapter.publish_open_clipboard = MagicMock()

        # Keep this unit test independent from the user's live Windows
        # clipboard, which may currently contain a CF_HDROP file selection.
        with (
            patch("app.clipboard_handler.win32clipboard.OpenClipboard"),
            patch("app.clipboard_handler.win32clipboard.CloseClipboard"),
            patch(
                "app.clipboard_handler.win32clipboard.GetClipboardSequenceNumber",
                return_value=100,
            ),
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                return_value=False,
            ),
        ):
            # Inject payload
            handler.inject(payload)

            self.assertEqual(
                handler.last_injected_fingerprint,
                snapshot.fingerprint(),
            )

            # First poller run: detects sequence change and reads injected snapshot
            handler._process_clipboard_sequence(101)

            # First run must suppress echo and CLEAR last_injected_fingerprint
            self.assertEqual(len(forwarded), 0)
            self.assertIsNone(handler.last_injected_fingerprint)

            # Second poller run (e.g. user manually copies identical text later):
            # Because last_injected_fingerprint was cleared, it forwards normally
            handler._process_clipboard_sequence(102)
            self.assertEqual(len(forwarded), 1)
            self.assertEqual(forwarded[0].fingerprint(), snapshot.fingerprint())


if __name__ == "__main__":
    unittest.main()
