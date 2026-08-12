import unittest
from unittest.mock import patch

import pythoncom

from app.file_transfer.publisher import (
    VirtualPastePublisher, build_virtual_file_set, inject_paste_shortcut,
    release_virtual_clipboard_owner, restore_virtual_clipboard_owner,
)


class RecordingReceiver:
    def __init__(self):
        self.reads = []
        self.consumed = []

    def read_range(self, job_id, path, offset, count):
        self.reads.append((job_id, path, offset, count))
        return b"data"[offset:offset + count]

    def record_stream_read(self, job_id, path, offset, count):
        self.consumed.append((job_id, path, offset, count))

    def record_stream_open(self, job_id, path):
        return None

    def record_stream_close(self, job_id, path):
        return None


class VirtualPastePublisherTests(unittest.TestCase):
    def test_restore_uses_wrapped_interface_from_production_owner_tuple(self):
        data_object = object()
        wrapped_interface = object()
        previous_owner = object()
        checked = []
        restored = []

        self.assertTrue(
            restore_virtual_clipboard_owner(
                (data_object, wrapped_interface),
                previous_owner,
                is_current=lambda candidate: (
                    checked.append(candidate) or candidate is wrapped_interface
                ),
                restore=restored.append,
            )
        )

        self.assertEqual(checked, [wrapped_interface])
        self.assertEqual(restored, [previous_owner])

    def test_release_uses_wrapped_interface_from_production_owner_tuple(self):
        data_object = object()
        wrapped_interface = object()
        checked = []
        cleared = []

        self.assertTrue(
            release_virtual_clipboard_owner(
                (data_object, wrapped_interface),
                is_current=lambda candidate: (
                    checked.append(candidate) or candidate is wrapped_interface
                ),
                clear=lambda: cleared.append(True),
            )
        )

        self.assertEqual(checked, [wrapped_interface])
        self.assertEqual(cleared, [True])

    def test_release_clears_only_the_matching_current_clipboard_owner(self):
        owner = object()
        cleared = []

        self.assertFalse(
            release_virtual_clipboard_owner(
                owner,
                is_current=lambda candidate: False,
                clear=lambda: cleared.append("cleared"),
            )
        )
        self.assertEqual(cleared, [])

        self.assertTrue(
            release_virtual_clipboard_owner(
                owner,
                is_current=lambda candidate: candidate is owner,
                clear=lambda: cleared.append("cleared"),
            )
        )
        self.assertEqual(cleared, ["cleared"])

    def test_default_explorer_acceptance_deadline_is_fifteen_seconds(self):
        publisher = VirtualPastePublisher()

        self.assertEqual(publisher.explorer_start_timeout, 15.0)

    def test_successful_virtual_paste_restores_the_clipboard_it_temporarily_replaced(self):
        receiver = self.make_receiver()
        previous_owner = object()
        virtual_owner = object()
        restored = []

        def publish(file_set, on_performed_drop=None):
            on_performed_drop(1)
            return virtual_owner

        publisher = VirtualPastePublisher(
            publish=publish,
            inject=lambda keyboard: None,
            capture=lambda: previous_owner,
            restore=lambda owner, previous: restored.append((owner, previous)),
            keyboard_factory=object,
        )

        self.assertTrue(
            publisher._process(self.manifest("A"), receiver, object())
        )
        self.assertEqual(restored, [(virtual_owner, previous_owner)])
        self.assertEqual(publisher.retained_owner_count, 0)

    def test_performed_drop_effect_is_forwarded_to_the_receiver(self):
        receiver = self.make_receiver()

        def publish(file_set, on_performed_drop=None):
            on_performed_drop(0)
            return object()

        publisher = VirtualPastePublisher(
            publish=publish,
            inject=lambda keyboard: None,
            release=lambda owner: None,
            keyboard_factory=object,
        )

        publisher._process(self.manifest("A"), receiver, object())
        self.assertEqual(receiver.drops, [("A", 0)])

    def test_restore_failure_retains_virtual_owner_handle(self):
        owner = (object(), object())

        def fail_restore(current, previous):
            raise ValueError("not a COM object")

        publisher = VirtualPastePublisher(restore=fail_restore)
        publisher._owner = owner

        with self.assertLogs(
            "app.file_transfer.publisher", level="ERROR"
        ) as logs:
            self.assertFalse(
                publisher._restore_owner(owner, object(), retain=False)
            )

        self.assertEqual(publisher.retained_owner_count, 1)
        self.assertIn("ValueError", "\n".join(logs.output))

    def test_publisher_brackets_virtual_clipboard_changes_as_internal(self):
        receiver = self.make_receiver()
        events = []

        def publish(file_set, on_performed_drop=None):
            events.append("publish")
            on_performed_drop(1)
            return (object(), object())

        def restore(owner, previous):
            events.append("restore")
            return True

        publisher = VirtualPastePublisher(
            publish=publish,
            inject=lambda keyboard: None,
            capture=lambda: object(),
            restore=restore,
            keyboard_factory=object,
        )
        publisher._on_clipboard_change_begin = lambda: events.append("begin")
        publisher._on_clipboard_change_end = (
            lambda suppress: events.append(("end", suppress))
        )

        self.assertTrue(
            publisher._process(self.manifest("A"), receiver, object())
        )

        self.assertEqual(
            events,
            ["begin", "publish", "restore", ("end", True)],
        )

    def test_newer_user_clipboard_owner_is_not_suppressed_after_restore_skip(self):
        receiver = self.make_receiver()
        ended = []

        def publish(file_set, on_performed_drop=None):
            on_performed_drop(1)
            return (object(), object())

        publisher = VirtualPastePublisher(
            publish=publish,
            inject=lambda keyboard: None,
            capture=lambda: object(),
            restore=lambda owner, previous: False,
            keyboard_factory=object,
            on_clipboard_change_end=ended.append,
        )

        self.assertTrue(
            publisher._process(self.manifest("A"), receiver, object())
        )

        self.assertEqual(ended, [False])
        self.assertEqual(publisher.retained_owner_count, 0)

    def test_accepted_paste_retires_owner_only_after_receiver_is_terminal(self):
        receiver = self.make_receiver()
        owner = (object(), object())
        released = []
        terminal_checks = []

        def publish(file_set, on_performed_drop=None):
            on_performed_drop(1)
            return owner

        def is_terminal(job_id):
            terminal_checks.append(job_id)
            return len(terminal_checks) >= 2

        receiver.is_paste_terminal = is_terminal
        publisher = VirtualPastePublisher(
            publish=publish,
            inject=lambda keyboard: None,
            release=released.append,
            keyboard_factory=object,
        )

        with patch(
            "app.file_transfer.publisher.pythoncom.PumpWaitingMessages"
        ) as pump:
            self.assertTrue(
                publisher._process(self.manifest("A"), receiver, object())
            )

        self.assertEqual(terminal_checks, ["A", "A"])
        pump.assert_called_once_with()
        self.assertEqual(released, [owner])
        self.assertEqual(publisher.retained_owner_count, 0)

    def test_default_terminal_cleanup_releases_virtual_owner_instead_of_restoring_old_owner(self):
        receiver = self.make_receiver()
        owner = (object(), object())

        def publish(file_set, on_performed_drop=None):
            on_performed_drop(1)
            return owner

        with (
            patch(
                "app.file_transfer.publisher.release_virtual_clipboard_owner",
                return_value=True,
            ) as release,
            patch(
                "app.file_transfer.publisher.restore_virtual_clipboard_owner",
                return_value=True,
            ) as restore,
        ):
            publisher = VirtualPastePublisher(
                publish=publish,
                inject=lambda keyboard: None,
                capture=lambda: object(),
                keyboard_factory=object,
            )

            self.assertTrue(
                publisher._process(self.manifest("A"), receiver, object())
            )

        release.assert_called_once_with(owner)
        restore.assert_not_called()
        self.assertEqual(publisher.retained_owner_count, 0)

    def test_default_cleanup_does_not_capture_an_old_ole_owner(self):
        receiver = self.make_receiver()

        def publish(file_set, on_performed_drop=None):
            on_performed_drop(1)
            return (object(), object())

        with (
            patch(
                "app.file_transfer.publisher.capture_clipboard_owner"
            ) as capture,
            patch(
                "app.file_transfer.publisher.release_virtual_clipboard_owner",
                return_value=True,
            ),
        ):
            publisher = VirtualPastePublisher(
                publish=publish,
                inject=lambda keyboard: None,
                keyboard_factory=object,
            )
            self.assertTrue(
                publisher._process(self.manifest("A"), receiver, object())
            )

        capture.assert_not_called()

    def test_terminal_release_retries_a_transient_ole_clipboard_error(self):
        receiver = self.make_receiver()
        owner = (object(), object())
        attempts = []

        def publish(file_set, on_performed_drop=None):
            on_performed_drop(1)
            return owner

        def release(candidate):
            attempts.append(candidate)
            if len(attempts) == 1:
                raise pythoncom.com_error(-1, "clipboard busy", None, None)
            return True

        publisher = VirtualPastePublisher(
            publish=publish,
            inject=lambda keyboard: None,
            release=release,
            keyboard_factory=object,
        )

        with patch("app.file_transfer.publisher.time.sleep") as sleep:
            self.assertTrue(
                publisher._process(self.manifest("A"), receiver, object())
            )

        self.assertEqual(attempts, [owner, owner])
        sleep.assert_called_once_with(0.05)
        self.assertEqual(publisher.retained_owner_count, 0)

    @staticmethod
    def manifest(job_id):
        return {
            "job_id": job_id,
            "items": [
                {
                    "relative_path": f"{job_id}.txt",
                    "item_type": "file",
                    "size": 4,
                    "modified_ns": 0,
                    "sha256": "0" * 64,
                }
            ],
            "total_size": 4,
            "file_count": 1,
        }

    def make_receiver(self):
        class Receiver(RecordingReceiver):
            def __init__(self):
                super().__init__()
                self.drops = []
                self.failures = []
                self.terminals = set()

            def record_performed_drop(self, job_id, effect):
                self.drops.append((job_id, effect))
                self.terminals.add(job_id)
                return True

            def fail_paste(self, job_id, error_code):
                self.failures.append((job_id, error_code))
                return True

            def is_paste_terminal(self, job_id):
                return job_id in self.terminals

        return Receiver()

    def test_worker_survives_one_failed_paste_and_processes_the_next(self):
        receiver = self.make_receiver()
        injected = []

        def publish(file_set, on_performed_drop=None):
            owner = object()
            on_performed_drop(1)
            return owner

        def inject(keyboard):
            injected.append(len(injected))
            if len(injected) == 1:
                raise RuntimeError("first injection failed")

        publisher = VirtualPastePublisher(
            publish=publish,
            inject=inject,
            release=lambda owner: None,
            keyboard_factory=object,
            explorer_start_timeout=0.1,
        )

        with self.assertLogs("app.file_transfer.publisher", level="ERROR") as logs:
            publisher.publish_and_paste(self.manifest("A"), receiver)
            publisher.publish_and_paste(self.manifest("B"), receiver)
            self.assertTrue(publisher.wait_until_idle(1))
        self.assertEqual(receiver.failures, [("A", "PasteInjectionFailed")])
        self.assertEqual(receiver.drops, [("A", 1), ("B", 1)])
        self.assertEqual(len(injected), 2)
        self.assertIn("RuntimeError", "\n".join(logs.output))

    def test_explorer_never_consumes_times_out_and_next_job_can_run(self):
        receiver = self.make_receiver()
        publish_count = 0
        owners = []
        released = []

        def publish(file_set, on_performed_drop=None):
            nonlocal publish_count
            publish_count += 1
            if publish_count == 2:
                on_performed_drop(1)
            owner = object()
            owners.append(owner)
            return owner

        publisher = VirtualPastePublisher(
            publish=publish,
            inject=lambda keyboard: None,
            release=released.append,
            keyboard_factory=object,
            explorer_start_timeout=0.01,
        )

        publisher.publish_and_paste(self.manifest("A"), receiver)
        publisher.publish_and_paste(self.manifest("B"), receiver)

        self.assertTrue(publisher.wait_until_idle(1))
        self.assertEqual(receiver.failures, [("A", "ExplorerStartTimeout")])
        self.assertEqual(receiver.drops, [("B", 1)])
        self.assertEqual(released, owners)
        self.assertEqual(publisher.retained_owner_count, 0)

    def test_cancelled_wait_does_not_block_the_next_paste(self):
        receiver = self.make_receiver()
        publish_count = 0

        def publish(file_set, on_performed_drop=None):
            nonlocal publish_count
            publish_count += 1
            if publish_count == 2:
                on_performed_drop(1)
            return object()

        def inject(keyboard):
            if publish_count == 1:
                receiver.terminals.add("A")

        publisher = VirtualPastePublisher(
            publish=publish,
            inject=inject,
            release=lambda owner: None,
            keyboard_factory=object,
            explorer_start_timeout=0.5,
        )

        publisher.publish_and_paste(self.manifest("A"), receiver)
        publisher.publish_and_paste(self.manifest("B"), receiver)

        self.assertTrue(publisher.wait_until_idle(0.1))
        self.assertEqual(receiver.failures, [])
        self.assertEqual(receiver.drops, [("B", 1)])

    def test_paste_injection_releases_ctrl_when_key_press_fails(self):
        class Keyboard:
            def __init__(self):
                self.events = []

            def press(self, key):
                self.events.append(("press", key))
                if key == "v":
                    raise RuntimeError("injection failed")

            def release(self, key):
                self.events.append(("release", key))

        keyboard = Keyboard()

        with self.assertRaises(RuntimeError):
            inject_paste_shortcut(keyboard, ctrl_key="ctrl")

        self.assertEqual(keyboard.events[-1], ("release", "ctrl"))

    def test_manifest_becomes_directory_and_growing_file_streams(self):
        receiver = RecordingReceiver()
        manifest = {
            "job_id": "job-A",
            "items": [
                {"relative_path": "folder", "item_type": "directory", "size": 0, "modified_ns": 0, "sha256": None},
                {"relative_path": "folder/file.txt", "item_type": "file", "size": 4, "modified_ns": 0, "sha256": "0" * 64},
            ],
            "total_size": 4,
            "file_count": 1,
        }

        file_set = build_virtual_file_set(manifest, receiver)

        self.assertTrue(file_set.files[0].is_directory)
        stream = file_set.files[1].open_stream()
        self.assertEqual(stream.Read(4), b"data")
        self.assertEqual(receiver.reads, [("job-A", "folder/file.txt", 0, 4)])
        self.assertEqual(receiver.consumed, [("job-A", "folder/file.txt", 0, 4)])


if __name__ == "__main__":
    unittest.main()
