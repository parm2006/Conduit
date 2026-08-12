import unittest
from unittest.mock import patch

from app.clipboard_handler import ClipboardHandler
from app.clipboard_formats import (
    ClipboardEntry,
    ClipboardSnapshot,
    encode_clipboard_message,
)
from app.windows_clipboard import ClipboardAccessError


class FakePublishingAdapter:
    def __init__(self, publish_error=None, on_publish=None):
        self.published = []
        self.publish_error = publish_error
        self.on_publish = on_publish
        self.publish_calls = 0

    def publish_open_clipboard(self, snapshot):
        self.publish_calls += 1
        if self.on_publish:
            self.on_publish()
        if self.publish_error:
            raise self.publish_error
        self.published.append(snapshot)


def text_message(text):
    snapshot = ClipboardSnapshot(
        [ClipboardEntry("unicode_text", (text + "\0").encode("utf-16le"))]
    )
    return encode_clipboard_message(snapshot)


class FileClipboardAvailabilityTests(unittest.TestCase):
    def test_each_classified_sequence_emits_kind_and_sequence_offer(self):
        offers = []
        snapshot = ClipboardSnapshot(
            [ClipboardEntry("unicode_text", "new\0".encode("utf-16le"))]
        )
        handler = ClipboardHandler(lambda value: None)
        handler.on_clipboard_offer = (
            lambda kind, sequence: offers.append((kind, sequence))
        )

        with (
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                side_effect=[True, False],
            ),
            patch.object(handler, "_read_clipboard", return_value=snapshot),
        ):
            handler._process_clipboard_sequence(11)
            handler._process_clipboard_sequence(12)

        self.assertEqual(offers, [("files", 11), ("ordinary", 12)])

    def test_refresh_offer_detects_unpolled_copy_without_consuming_snapshot(self):
        offers = []
        handler = ClipboardHandler(
            lambda value: None,
            on_clipboard_offer=(
                lambda kind, sequence: offers.append((kind, sequence))
            ),
        )
        handler.last_sequence_num = 10
        handler.last_offer_sequence_num = 10
        refresh = getattr(handler, "refresh_offer", None)
        self.assertIsNotNone(refresh)

        with (
            patch(
                "app.clipboard_handler.win32clipboard.GetClipboardSequenceNumber",
                return_value=11,
            ),
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                return_value=True,
            ),
        ):
            self.assertEqual(refresh(), "files")
            self.assertEqual(refresh(), "files")

        self.assertEqual(offers, [("files", 11)])
        self.assertEqual(handler.last_sequence_num, 10)

    def test_start_announces_existing_file_clipboard_as_initial_offer(self):
        offers = []
        handler = ClipboardHandler(
            lambda value: None,
            on_clipboard_offer=(
                lambda kind, sequence: offers.append((kind, sequence))
            ),
        )

        with (
            patch(
                "app.clipboard_handler.win32clipboard.GetClipboardSequenceNumber",
                return_value=11,
            ),
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                return_value=True,
            ),
            patch("app.clipboard_handler.threading.Thread") as thread_type,
        ):
            handler.start()

        thread_type.return_value.start.assert_called_once_with()
        self.assertEqual(offers, [("files", 11)])

    def test_transient_file_classification_failure_keeps_sequence_pending(self):
        changes = []
        handler = ClipboardHandler(
            lambda snapshot: None,
            on_file_availability=changes.append,
        )
        handler.last_sequence_num = 10

        with (
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                side_effect=[OSError("PRIVATE-CLIPBOARD-LOCK"), True],
            ),
            patch.object(handler, "_read_clipboard", return_value=None),
            self.assertLogs("app.clipboard_handler", level="DEBUG") as logs,
        ):
            handler._process_clipboard_sequence(11)
            self.assertEqual(handler.last_sequence_num, 10)
            self.assertEqual(changes, [])

            handler._process_clipboard_sequence(11)

        self.assertEqual(handler.last_sequence_num, 11)
        self.assertEqual(changes, [True])
        self.assertNotIn("PRIVATE-CLIPBOARD-LOCK", "\n".join(logs.output))

    def test_each_file_clipboard_sequence_announces_a_fresh_local_selection(self):
        changes = []
        handler = ClipboardHandler(
            lambda snapshot: None,
            on_file_availability=changes.append,
        )

        with patch(
            "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
            return_value=True,
        ):
            handler._process_clipboard_sequence(11)
            handler._process_clipboard_sequence(12)

        self.assertEqual(changes, [True, True])

    def test_emits_boolean_only_when_file_availability_changes(self):
        changes = []
        handler = ClipboardHandler(lambda snapshot: None, on_file_availability=changes.append)

        with patch("app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable", side_effect=[True, True, False]):
            handler._update_file_availability()
            handler._update_file_availability()
            handler._update_file_availability()

        self.assertEqual(changes, [True, False])

    def test_file_detection_does_not_read_or_serialize_file_paths(self):
        changes = []
        handler = ClipboardHandler(lambda snapshot: None, on_file_availability=changes.append)

        with patch("app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable", return_value=True), patch(
            "app.clipboard_handler.win32clipboard.GetClipboardData"
        ) as get_data:
            handler._update_file_availability()

        get_data.assert_not_called()
        self.assertEqual(changes, [True])

    def test_remote_text_injection_announces_that_local_file_offer_was_replaced(self):
        changes = []
        adapter = FakePublishingAdapter()
        handler = ClipboardHandler(
            lambda snapshot: None,
            on_file_availability=changes.append,
            clipboard_adapter=adapter,
        )
        handler.file_availability = True

        with (
            patch("app.clipboard_handler.win32clipboard.OpenClipboard"),
            patch("app.clipboard_handler.win32clipboard.CloseClipboard"),
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                return_value=False,
            ),
            patch(
                "app.clipboard_handler.win32clipboard.GetClipboardSequenceNumber",
                return_value=11,
            ),
            patch("app.clipboard_handler.time.sleep"),
        ):
            handler.inject(text_message("replacement"))

        self.assertEqual(len(adapter.published), 1)
        self.assertFalse(handler.file_availability)
        self.assertEqual(changes, [False])

    def test_remote_injection_sequence_is_not_reannounced_as_local_offer(self):
        offers = []
        adapter = FakePublishingAdapter()
        handler = ClipboardHandler(
            lambda snapshot: None,
            on_clipboard_offer=(
                lambda kind, sequence: offers.append((kind, sequence))
            ),
            clipboard_adapter=adapter,
        )
        handler.last_sequence_num = 10
        handler.last_offer_sequence_num = 10

        with (
            patch("app.clipboard_handler.win32clipboard.OpenClipboard"),
            patch("app.clipboard_handler.win32clipboard.CloseClipboard"),
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                return_value=False,
            ),
            patch(
                "app.clipboard_handler.win32clipboard.GetClipboardSequenceNumber",
                return_value=11,
            ),
            patch("app.clipboard_handler.time.sleep"),
        ):
            handler.inject(text_message("remote"))
            self.assertEqual(handler.refresh_offer(), "ordinary")

        self.assertEqual(offers, [])
        self.assertEqual(handler.last_offer_sequence_num, 11)

    def test_virtual_clipboard_restore_is_not_announced_as_user_copy(self):
        offers = []
        handler = ClipboardHandler(
            lambda snapshot: None,
            on_clipboard_offer=(
                lambda kind, sequence: offers.append((kind, sequence))
            ),
        )
        handler.last_sequence_num = 10
        handler.last_offer_sequence_num = 10
        begin = getattr(handler, "begin_internal_change", None)
        end = getattr(handler, "end_internal_change", None)
        self.assertIsNotNone(begin)
        self.assertIsNotNone(end)

        begin()
        with patch(
            "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
            return_value=False,
        ):
            self.assertFalse(handler._process_clipboard_sequence(11))

        with (
            patch(
                "app.clipboard_handler.win32clipboard.GetClipboardSequenceNumber",
                return_value=12,
            ),
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                return_value=True,
            ),
        ):
            end()
            self.assertEqual(handler.refresh_offer(), "files")

        self.assertEqual(offers, [])
        self.assertEqual(handler.last_sequence_num, 12)
        self.assertEqual(handler.last_offer_sequence_num, 12)

    def test_new_user_owner_survives_skipped_virtual_restore(self):
        offers = []
        handler = ClipboardHandler(
            lambda snapshot: None,
            on_clipboard_offer=(
                lambda kind, sequence: offers.append((kind, sequence))
            ),
        )
        handler.last_sequence_num = 10
        handler.last_offer_sequence_num = 10
        handler.begin_internal_change()
        self.assertTrue(handler.end_internal_change(suppress_current=False))

        with patch(
            "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
            return_value=True,
        ):
            self.assertTrue(handler._process_clipboard_sequence(12))

        self.assertEqual(offers, [("files", 12)])

    def test_user_copy_during_remote_injection_remains_pending_for_poller(self):
        adapter = FakePublishingAdapter()
        handler = ClipboardHandler(
            lambda snapshot: None,
            clipboard_adapter=adapter,
        )
        handler.last_sequence_num = 10
        current_sequence = [11]

        def user_copies_while_injection_settles(_delay):
            current_sequence[0] = 12

        with (
            patch("app.clipboard_handler.win32clipboard.OpenClipboard"),
            patch("app.clipboard_handler.win32clipboard.CloseClipboard"),
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                return_value=False,
            ),
            patch(
                "app.clipboard_handler.win32clipboard.GetClipboardSequenceNumber",
                side_effect=lambda: current_sequence[0],
            ),
            patch(
                "app.clipboard_handler.time.sleep",
                side_effect=user_copies_while_injection_settles,
            ),
        ):
            handler.inject(text_message("remote"))

        self.assertEqual(len(adapter.published), 1)
        self.assertEqual(handler.last_sequence_num, 11)
        self.assertEqual(current_sequence[0], 12)

    def test_availability_callback_failure_does_not_wedge_injection(self):
        def fail_callback(_available):
            raise RuntimeError("network unavailable")

        adapter = FakePublishingAdapter()
        handler = ClipboardHandler(
            lambda snapshot: None,
            on_file_availability=fail_callback,
            clipboard_adapter=adapter,
        )
        handler.file_availability = True

        with (
            patch("app.clipboard_handler.win32clipboard.OpenClipboard"),
            patch("app.clipboard_handler.win32clipboard.CloseClipboard"),
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                return_value=False,
            ),
            patch(
                "app.clipboard_handler.win32clipboard.GetClipboardSequenceNumber",
                return_value=11,
            ),
            patch("app.clipboard_handler.time.sleep"),
        ):
            with self.assertRaises(RuntimeError):
                handler.inject(text_message("replacement"))

        self.assertFalse(handler.is_injecting)

    def test_invalid_message_does_not_open_or_publish_and_later_valid_copy_works(self):
        adapter = FakePublishingAdapter()
        handler = ClipboardHandler(
            lambda snapshot: None,
            clipboard_adapter=adapter,
        )
        invalid = text_message("invalid")
        invalid["version"] = 1

        with (
            patch("app.clipboard_handler.win32clipboard.OpenClipboard") as opened,
            self.assertLogs("app.clipboard_handler", level="WARNING"),
        ):
            handler.inject(invalid)

        opened.assert_not_called()
        self.assertEqual(adapter.published, [])
        self.assertFalse(handler.is_injecting)

        with (
            patch("app.clipboard_handler.win32clipboard.OpenClipboard"),
            patch("app.clipboard_handler.win32clipboard.CloseClipboard"),
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                return_value=False,
            ),
            patch(
                "app.clipboard_handler.win32clipboard.GetClipboardSequenceNumber",
                return_value=11,
            ),
            patch("app.clipboard_handler.time.sleep"),
        ):
            handler.inject(text_message("valid"))

        self.assertEqual(len(adapter.published), 1)

    def test_remote_snapshot_reaches_adapter_in_source_order(self):
        adapter = FakePublishingAdapter()
        handler = ClipboardHandler(
            lambda snapshot: None,
            clipboard_adapter=adapter,
        )
        snapshot = ClipboardSnapshot(
            [
                ClipboardEntry("png", b"png"),
                ClipboardEntry("html", b"html"),
                ClipboardEntry("dibv5", b"dibv5"),
            ]
        )

        with (
            patch("app.clipboard_handler.win32clipboard.OpenClipboard"),
            patch("app.clipboard_handler.win32clipboard.CloseClipboard"),
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                return_value=False,
            ),
            patch(
                "app.clipboard_handler.win32clipboard.GetClipboardSequenceNumber",
                return_value=11,
            ),
            patch("app.clipboard_handler.time.sleep"),
        ):
            handler.inject(encode_clipboard_message(snapshot))

        self.assertEqual(adapter.published, [snapshot])

    def test_publication_failure_clears_injection_guard(self):
        adapter = FakePublishingAdapter(ClipboardAccessError("publish failed"))
        changes = []
        handler = ClipboardHandler(
            lambda snapshot: None,
            on_file_availability=changes.append,
            clipboard_adapter=adapter,
        )
        handler.file_availability = True

        with (
            patch("app.clipboard_handler.win32clipboard.OpenClipboard"),
            patch("app.clipboard_handler.win32clipboard.CloseClipboard"),
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                return_value=False,
            ),
            patch("app.clipboard_handler.time.sleep"),
            self.assertLogs("app.clipboard_handler", level="WARNING"),
        ):
            handler.inject(text_message("remote"))

        self.assertEqual(adapter.publish_calls, 1)
        self.assertFalse(handler.is_injecting)
        self.assertFalse(handler.file_availability)
        self.assertEqual(changes, [False])

    def test_injected_sequence_is_read_before_user_can_copy_after_close(self):
        current_sequence = [10]

        def deskflow_publishes():
            current_sequence[0] = 11

        def user_copies_after_close():
            current_sequence[0] = 12

        adapter = FakePublishingAdapter(on_publish=deskflow_publishes)
        handler = ClipboardHandler(
            lambda snapshot: None,
            clipboard_adapter=adapter,
        )
        handler.last_sequence_num = 10

        with (
            patch("app.clipboard_handler.win32clipboard.OpenClipboard"),
            patch(
                "app.clipboard_handler.win32clipboard.CloseClipboard",
                side_effect=user_copies_after_close,
            ),
            patch(
                "app.clipboard_handler.win32clipboard.GetClipboardSequenceNumber",
                side_effect=lambda: current_sequence[0],
            ),
            patch(
                "app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable",
                return_value=False,
            ),
            patch("app.clipboard_handler.time.sleep"),
        ):
            handler.inject(text_message("remote"))

        self.assertEqual(handler.last_sequence_num, 11)
        self.assertEqual(current_sequence[0], 12)


if __name__ == "__main__":
    unittest.main()
