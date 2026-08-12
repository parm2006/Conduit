import hashlib
import inspect
import queue
import tempfile
import unittest
import threading
import time
from pathlib import Path

from app.file_transfer.models import FileItem, ItemType, Manifest
from app.file_transfer.receiver import TransferReceiver
from app.file_transfer.receiver import TransferAbortedError
from app.file_transfer.controller import TransferController
from app.file_transfer.compression import MAX_CHUNK_SIZE
from app.file_transfer.status import TransferPhase


class TransferReceiverTests(unittest.TestCase):
    @staticmethod
    def _queued_progress(receiver):
        messages = []
        while True:
            try:
                messages.append(receiver._progress_queue.get_nowait())
            except queue.Empty:
                return messages

    def test_cancel_job_records_one_terminal_outcome_and_peer_notification(self):
        item = FileItem("cancel.bin", ItemType.FILE, 4, 1, "0" * 64)
        manifest = Manifest.create([item])
        controller = TransferController()
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory), controller=controller)
            receiver.lane = object()
            receiver.accept_manifest(manifest.to_wire())

            self.assertTrue(receiver.cancel_job(manifest.job_id))
            self.assertFalse(receiver.cancel_job(manifest.job_id))

            outcome_query = getattr(receiver, "terminal_outcome", None)
            self.assertIsNotNone(outcome_query)
            outcome = outcome_query(manifest.job_id)
            terminal_messages = [
                message for message in self._queued_progress(receiver)
                if message.get("phase") == TransferPhase.CANCELLED.value
            ]

        self.assertEqual(controller.status(manifest.job_id).phase, TransferPhase.CANCELLED)
        self.assertEqual(outcome.phase, TransferPhase.CANCELLED)
        self.assertEqual(outcome.reason_code, "UserCancelled")
        self.assertEqual(len(terminal_messages), 1)

    def test_first_terminal_failure_cannot_be_replaced_by_late_cancel(self):
        item = FileItem("failed.bin", ItemType.FILE, 4, 1, "0" * 64)
        manifest = Manifest.create([item])
        controller = TransferController()
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory), controller=controller)
            receiver.lane = object()
            receiver.accept_manifest(manifest.to_wire())

            self.assertTrue(
                receiver.fail_paste(manifest.job_id, "ExplorerStartTimeout")
            )
            self.assertFalse(receiver.cancel_job(manifest.job_id))

            outcome_query = getattr(receiver, "terminal_outcome", None)
            self.assertIsNotNone(outcome_query)
            outcome = outcome_query(manifest.job_id)
            terminal_messages = [
                message for message in self._queued_progress(receiver)
                if message.get("phase") in {
                    TransferPhase.CANCELLED.value,
                    TransferPhase.FAILED.value,
                }
            ]

        self.assertEqual(controller.status(manifest.job_id).phase, TransferPhase.FAILED)
        self.assertEqual(outcome.phase, TransferPhase.FAILED)
        self.assertEqual(outcome.reason_code, "ExplorerStartTimeout")
        self.assertEqual(len(terminal_messages), 1)

    def test_existing_controller_terminal_state_wins_over_late_receiver_cancel(self):
        item = FileItem("completed.bin", ItemType.FILE, 4, 1, "0" * 64)
        manifest = Manifest.create([item])
        controller = TransferController()
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory), controller=controller)
            receiver.lane = object()
            receiver.accept_manifest(manifest.to_wire())
            controller.update(
                manifest.job_id,
                TransferPhase.COMPLETED,
                "completed.bin",
                4,
                4,
            )

            self.assertFalse(receiver.cancel_job(manifest.job_id))
            outcome = receiver.terminal_outcome(manifest.job_id)

        self.assertEqual(controller.status(manifest.job_id).phase, TransferPhase.COMPLETED)
        self.assertEqual(outcome.phase, TransferPhase.COMPLETED)
        self.assertEqual(self._queued_progress(receiver), [])

    def test_shell_none_cancels_after_network_bytes_are_verified(self):
        content = b"data"
        item = FileItem(
            "copy.bin", ItemType.FILE, len(content), 1,
            hashlib.sha256(content).hexdigest(),
        )
        manifest = Manifest.create([item])
        controller = TransferController()
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory), controller=controller)
            receiver.accept_manifest(manifest.to_wire())
            receiver.accept_chunk({
                "job_id": manifest.job_id,
                "relative_path": item.relative_path,
                "offset": 0,
                "compressed": False,
                "original_size": len(content),
            }, content)
            receiver.complete_file(manifest.job_id, item.relative_path)
            receiver.complete_job(manifest.job_id)

            self.assertEqual(
                len(inspect.signature(receiver.record_performed_drop).parameters),
                2,
            )
            receiver.record_performed_drop(manifest.job_id, 0)

        self.assertEqual(
            controller.status(manifest.job_id).phase,
            TransferPhase.CANCELLED,
        )

    def test_shell_copy_waits_for_actual_stream_coverage(self):
        content = b"data"
        item = FileItem(
            "copy.bin", ItemType.FILE, len(content), 1,
            hashlib.sha256(content).hexdigest(),
        )
        manifest = Manifest.create([item])
        controller = TransferController()
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory), controller=controller)
            receiver.accept_manifest(manifest.to_wire())

            self.assertEqual(
                len(inspect.signature(receiver.record_performed_drop).parameters),
                2,
            )
            receiver.record_performed_drop(manifest.job_id, 1)

            self.assertFalse(receiver.is_paste_terminal(manifest.job_id))
            self.assertNotEqual(
                controller.status(manifest.job_id).phase,
                TransferPhase.CANCELLED,
            )
            receiver.cancel_job(manifest.job_id)

    def test_attached_receiver_acknowledges_a_persisted_chunk(self):
        class Lane:
            def __init__(self):
                self.callbacks = {}
                self.sent = []

            def register_callback(self, name, callback):
                self.callbacks.setdefault(name, []).append(callback)

            def send(self, metadata, payload=b""):
                self.sent.append((metadata, payload))

        with tempfile.TemporaryDirectory() as directory:
            content = b"acknowledged"
            item = FileItem(
                "safe.bin", ItemType.FILE, len(content), 1,
                hashlib.sha256(content).hexdigest(),
            )
            manifest = Manifest.create([item])
            lane = Lane()
            receiver = TransferReceiver(Path(directory))
            receiver.attach(lane)
            receiver.accept_manifest(manifest.to_wire())
            try:
                for callback in lane.callbacks["chunk"]:
                    callback({
                        "type": "chunk",
                        "job_id": manifest.job_id,
                        "relative_path": "safe.bin",
                        "offset": 0,
                        "compressed": False,
                        "original_size": len(content),
                    }, content)
                sent = list(lane.sent)
            finally:
                receiver.cancel_all("test cleanup")

            self.assertEqual(sent, [({
                "type": "chunk_received",
                "job_id": manifest.job_id,
                "relative_path": "safe.bin",
                "offset": 0,
            }, b"")])

    def test_active_manifest_count_is_bounded_and_cancel_releases_capacity(self):
        item = FileItem(
            "empty.bin", ItemType.FILE, 0, 1, hashlib.sha256(b"").hexdigest()
        )
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory))
            manifests = [Manifest.create([item]) for _ in range(9)]
            for manifest in manifests[:8]:
                receiver.accept_manifest(manifest.to_wire())

            with self.assertRaisesRegex(ValueError, "active transfer limit"):
                receiver.accept_manifest(manifests[8].to_wire())

            receiver.cancel_job(manifests[0].job_id)
            self.assertIsNotNone(receiver.accept_manifest(manifests[8].to_wire()))

    def test_rejects_zero_or_fragmented_production_chunks_without_staging(self):
        size = MAX_CHUNK_SIZE + 1
        item = FileItem("large.bin", ItemType.FILE, size, 1, "0" * 64)
        manifest = Manifest.create([item])
        metadata = {
            "job_id": manifest.job_id,
            "relative_path": item.relative_path,
            "offset": 0,
            "compressed": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receiver = TransferReceiver(root)
            receiver.accept_manifest(manifest.to_wire())

            try:
                with self.assertRaisesRegex(ValueError, "production chunk size"):
                    receiver.accept_chunk({**metadata, "original_size": 0}, b"")
                with self.assertRaisesRegex(ValueError, "production chunk size"):
                    receiver.accept_chunk({**metadata, "original_size": 1}, b"x")
                with self.assertRaisesRegex(ValueError, "offset"):
                    receiver.accept_chunk(
                        {
                            **metadata,
                            "offset": size - 1,
                            "original_size": 1,
                        },
                        b"x",
                    )

                self.assertFalse(list(root.rglob("*.partial")))
            finally:
                receiver.cancel_job(manifest.job_id)

    def test_empty_file_completes_without_a_content_chunk(self):
        item = FileItem(
            "empty.bin", ItemType.FILE, 0, 1, hashlib.sha256(b"").hexdigest()
        )
        manifest = Manifest.create([item])
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory))
            receiver.accept_manifest(manifest.to_wire())

            completed = receiver.complete_file(manifest.job_id, item.relative_path)
            receiver.complete_job(manifest.job_id)

            self.assertEqual(completed.read_available(0, 1), b"")

    def test_paste_failure_is_terminal_and_wakes_waiting_streams(self):
        item = FileItem("blocked.bin", ItemType.FILE, 4, 1, "0" * 64)
        manifest = Manifest.create([item])
        controller = TransferController()
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory), controller=controller)
            receiver.accept_manifest(manifest.to_wire())

            self.assertTrue(receiver.fail_paste(manifest.job_id, "ExplorerStartTimeout"))

            status = controller.status(manifest.job_id)
            self.assertEqual(status.phase, TransferPhase.FAILED)
            self.assertEqual(status.error_code, "ExplorerStartTimeout")
            with self.assertRaises(TransferAbortedError):
                receiver.read_range(manifest.job_id, item.relative_path, 0, 1)

    def test_early_last_stream_release_cancels_after_grace_period(self):
        callbacks = []

        class Timer:
            def __init__(self, delay, callback):
                callbacks.append(callback)

            def start(self):
                return None

        item = FileItem("copy.bin", ItemType.FILE, 4, 1, "0" * 64)
        manifest = Manifest.create([item])
        controller = TransferController()
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(
                Path(directory), controller=controller, timer_factory=Timer
            )
            receiver.accept_manifest(manifest.to_wire())
            receiver.record_stream_open(manifest.job_id, item.relative_path)
            receiver.record_stream_read(manifest.job_id, item.relative_path, 0, 2)
            receiver.record_stream_close(manifest.job_id, item.relative_path)
            callbacks.pop()()

        self.assertEqual(controller.status(manifest.job_id).phase, TransferPhase.CANCELLED)

    def test_stream_reopen_invalidates_pending_release_cancellation(self):
        callbacks = []

        class Timer:
            def __init__(self, delay, callback):
                callbacks.append(callback)

            def start(self):
                return None

        item = FileItem("copy.bin", ItemType.FILE, 4, 1, "0" * 64)
        manifest = Manifest.create([item])
        controller = TransferController()
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(
                Path(directory), controller=controller, timer_factory=Timer
            )
            receiver.accept_manifest(manifest.to_wire())
            receiver.record_stream_open(manifest.job_id, item.relative_path)
            receiver.record_stream_read(manifest.job_id, item.relative_path, 0, 2)
            receiver.record_stream_close(manifest.job_id, item.relative_path)
            receiver.record_stream_open(manifest.job_id, item.relative_path)
            callbacks.pop()()

        self.assertNotEqual(controller.status(manifest.job_id).phase, TransferPhase.CANCELLED)

    def test_opening_another_file_invalidates_pending_multi_file_cancellation(self):
        callbacks = []

        class Timer:
            def __init__(self, delay, callback):
                callbacks.append(callback)

            def start(self):
                return None

        items = [
            FileItem("first.bin", ItemType.FILE, 4, 1, "0" * 64),
            FileItem("second.bin", ItemType.FILE, 4, 1, "1" * 64),
        ]
        manifest = Manifest.create(items)
        controller = TransferController()
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(
                Path(directory), controller=controller, timer_factory=Timer
            )
            receiver.accept_manifest(manifest.to_wire())
            receiver.record_stream_open(manifest.job_id, "first.bin")
            receiver.record_stream_read(manifest.job_id, "first.bin", 0, 2)
            receiver.record_stream_close(manifest.job_id, "first.bin")
            receiver.record_stream_open(manifest.job_id, "second.bin")

            callbacks.pop(0)()

            self.assertIn(manifest.job_id, receiver._jobs)
            self.assertNotEqual(
                controller.status(manifest.job_id).phase,
                TransferPhase.CANCELLED,
            )
            receiver.cancel_job(manifest.job_id)

    def test_late_stream_open_uses_controlled_terminal_error(self):
        item = FileItem("copy.bin", ItemType.FILE, 4, 1, "0" * 64)
        manifest = Manifest.create([item])
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory))
            receiver.accept_manifest(manifest.to_wire())
            receiver.cancel_job(manifest.job_id)

            with self.assertRaises(TransferAbortedError):
                receiver.record_stream_open(manifest.job_id, item.relative_path)

    def test_late_stream_read_uses_controlled_terminal_error(self):
        item = FileItem("copy.bin", ItemType.FILE, 4, 1, "0" * 64)
        manifest = Manifest.create([item])
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory))
            receiver.accept_manifest(manifest.to_wire())
            receiver.cancel_job(manifest.job_id)

            with self.assertRaises(TransferAbortedError):
                receiver.record_stream_read(
                    manifest.job_id, item.relative_path, 0, 1
                )

    def test_paste_progress_updates_are_rate_limited(self):
        size = 2 * 1024 * 1024
        item = FileItem("large.bin", ItemType.FILE, size, 1, "0" * 64)
        manifest = Manifest.create([item])
        times = iter((0.0, 0.0, 0.0, 0.01, 0.01))
        controller = TransferController()
        observed = []
        controller.subscribe(observed.append)
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory), controller=controller, clock=lambda: next(times))
            receiver.accept_manifest(manifest.to_wire())
            receiver.record_stream_read(manifest.job_id, item.relative_path, 0, 1)
            receiver.record_stream_read(manifest.job_id, item.relative_path, 1, 1)

        pasting = [status for status in observed if status.phase is TransferPhase.PASTING]
        self.assertEqual(len(pasting), 1)
        self.assertEqual(pasting[0].bytes_done, 1)

    def test_stream_read_never_blocks_on_peer_progress_send(self):
        content = b"nonblocking"
        item = FileItem("safe.bin", ItemType.FILE, len(content), 1, hashlib.sha256(content).hexdigest())
        manifest = Manifest.create([item])

        class BlockingLane:
            def __init__(self):
                self.callbacks = {}
                self.entered = threading.Event()
                self.release = threading.Event()

            def register_callback(self, kind, callback):
                self.callbacks.setdefault(kind, []).append(callback)

            def send(self, metadata, payload=b""):
                if metadata["type"] == "paste_progress":
                    self.entered.set()
                    self.release.wait(1)

        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory))
            lane = BlockingLane()
            receiver.attach(lane)
            receiver.accept_manifest(manifest.to_wire())
            started = time.monotonic()
            receiver.record_stream_read(manifest.job_id, item.relative_path, 0, 1)
            elapsed = time.monotonic() - started
            self.assertTrue(lane.entered.wait(1))
            lane.release.set()

        self.assertLess(elapsed, 0.05)

    def test_network_receipt_does_not_drive_user_facing_progress(self):
        content = b"actual bytes"
        item = FileItem("speed.bin", ItemType.FILE, len(content), 1, hashlib.sha256(content).hexdigest())
        manifest = Manifest.create([item])
        controller = TransferController()
        times = iter((10.0, 12.0, 14.0))
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory), controller=controller, clock=lambda: next(times))
            receiver.accept_manifest(manifest.to_wire())
            receiver.accept_chunk({
                "job_id": manifest.job_id, "relative_path": "speed.bin", "offset": 0,
                "compressed": False, "original_size": len(content),
            }, content)

            status = controller.status(manifest.job_id)
            self.assertEqual(status.phase, TransferPhase.WAITING_FOR_EXPLORER)
            self.assertEqual(status.bytes_done, 0)
            self.assertEqual(status.bytes_per_second, 0)
            receiver.cancel_job(manifest.job_id)


    def test_reports_received_bytes_and_verified_completion(self):
        content = b"verified progress"
        item = FileItem("safe.txt", ItemType.FILE, len(content), 1, hashlib.sha256(content).hexdigest())
        manifest = Manifest.create([item])
        controller = TransferController()
        observed = []
        controller.subscribe(observed.append)
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory), controller=controller)
            receiver.accept_manifest(manifest.to_wire())
            receiver.accept_chunk({
                "job_id": manifest.job_id, "relative_path": "safe.txt", "offset": 0,
                "compressed": False, "original_size": len(content),
            }, content)
            receiver.complete_file(manifest.job_id, "safe.txt")
            receiver.complete_job(manifest.job_id)

        self.assertEqual(observed[0].phase, TransferPhase.WAITING_FOR_EXPLORER)
        self.assertNotIn(TransferPhase.TRANSFERRING, [status.phase for status in observed])
        self.assertNotIn(TransferPhase.VERIFYING, [status.phase for status in observed])
        self.assertEqual(observed[-1].phase, TransferPhase.WAITING_FOR_EXPLORER)

    def test_explorer_reads_drive_paste_progress_and_fallback_completion(self):
        content = b"explorer consumed bytes"
        item = FileItem("copy.bin", ItemType.FILE, len(content), 1, hashlib.sha256(content).hexdigest())
        manifest = Manifest.create([item])
        controller = TransferController()
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory), controller=controller)
            receiver.accept_manifest(manifest.to_wire())
            receiver.accept_chunk({
                "job_id": manifest.job_id, "relative_path": item.relative_path, "offset": 0,
                "compressed": False, "original_size": len(content),
            }, content)
            receiver.complete_file(manifest.job_id, item.relative_path)
            receiver.complete_job(manifest.job_id)
            receiver.record_stream_read(manifest.job_id, item.relative_path, 0, 8)
            self.assertEqual(controller.status(manifest.job_id).phase, TransferPhase.PASTING)
            self.assertEqual(controller.status(manifest.job_id).bytes_done, 8)
            receiver.record_stream_read(manifest.job_id, item.relative_path, 8, len(content) - 8)

        self.assertEqual(controller.status(manifest.job_id).phase, TransferPhase.COMPLETED)
        self.assertEqual(controller.status(manifest.job_id).bytes_done, len(content))

    def test_successful_drop_removes_completed_ciphertext_after_last_stream_closes(self):
        callbacks = []

        class Timer:
            def __init__(self, delay, callback):
                callbacks.append(callback)

            def start(self):
                return None

        content = b"encrypted cache must be temporary"
        item = FileItem(
            "copy.bin", ItemType.FILE, len(content), 1,
            hashlib.sha256(content).hexdigest(),
        )
        manifest = Manifest.create([item])
        controller = TransferController()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receiver = TransferReceiver(
                root, controller=controller, timer_factory=Timer
            )
            receiver.accept_manifest(manifest.to_wire())
            receiver.accept_chunk({
                "job_id": manifest.job_id,
                "relative_path": item.relative_path,
                "offset": 0,
                "compressed": False,
                "original_size": len(content),
            }, content)
            receiver.complete_file(manifest.job_id, item.relative_path)
            receiver.complete_job(manifest.job_id)
            receiver.record_stream_open(manifest.job_id, item.relative_path)
            receiver.record_stream_read(
                manifest.job_id, item.relative_path, 0, len(content)
            )
            receiver.record_performed_drop(manifest.job_id, 1)

            self.assertTrue(list((root / "completed").rglob("*.cache")))
            self.assertFalse(callbacks)

            receiver.record_stream_close(manifest.job_id, item.relative_path)
            callbacks.pop()()

            self.assertFalse(list((root / "completed").rglob("*.cache")))
            self.assertNotIn(manifest.job_id, receiver._jobs)
            self.assertEqual(
                controller.status(manifest.job_id).phase, TransferPhase.COMPLETED
            )

    def test_performed_drop_with_incomplete_coverage_cancels_paste(self):
        item = FileItem("copy.bin", ItemType.FILE, 4, 1, "0" * 64)
        manifest = Manifest.create([item])
        controller = TransferController()
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory), controller=controller)
            receiver.accept_manifest(manifest.to_wire())

            receiver.record_performed_drop(manifest.job_id, 0)

            self.assertNotIn(manifest.job_id, receiver._jobs)
            with self.assertRaises(TransferAbortedError):
                receiver.read_range(manifest.job_id, item.relative_path, 0, 1)

        self.assertEqual(controller.status(manifest.job_id).phase, TransferPhase.CANCELLED)

    def test_manifest_chunks_and_completion_publish_verified_file(self):
        content = b"DeskFlow received bytes"
        item = FileItem("folder/report.txt", ItemType.FILE, len(content), 123, hashlib.sha256(content).hexdigest())
        manifest = Manifest.create([item])
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory))

            receiver.accept_manifest(manifest.to_wire())
            receiver.accept_chunk(
                {
                    "job_id": manifest.job_id,
                    "relative_path": item.relative_path,
                    "offset": 0,
                    "compressed": False,
                    "original_size": len(content),
                },
                content,
            )
            completed = receiver.complete_file(manifest.job_id, item.relative_path)

            self.assertEqual(completed.read_available(0, len(content)), content)

    def test_rejects_chunks_for_unknown_job_or_path(self):
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory))
            metadata = {
                "job_id": "unknown",
                "relative_path": "safe.txt",
                "offset": 0,
                "compressed": False,
                "original_size": 1,
            }
            with self.assertRaises(KeyError):
                receiver.accept_chunk(metadata, b"x")

    def test_reader_waits_for_requested_bytes_while_transfer_grows(self):
        content = b"streamed"
        item = FileItem("stream.bin", ItemType.FILE, len(content), 1, hashlib.sha256(content).hexdigest())
        manifest = Manifest.create([item])
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory))
            receiver.accept_manifest(manifest.to_wire())
            result = []
            reader = threading.Thread(
                target=lambda: result.append(receiver.read_range(manifest.job_id, item.relative_path, 0, 4))
            )
            reader.start()
            time.sleep(0.02)
            self.assertTrue(reader.is_alive())

            receiver.accept_chunk(
                {
                    "job_id": manifest.job_id,
                    "relative_path": item.relative_path,
                    "offset": 0,
                    "compressed": False,
                    "original_size": len(content),
                },
                content,
            )
            reader.join(1)

            self.assertEqual(result, [b"stre"])
            receiver.cancel_job(manifest.job_id)

    def test_cancel_wakes_blocked_reader_with_error(self):
        item = FileItem("blocked.bin", ItemType.FILE, 4, 1, hashlib.sha256(b"data").hexdigest())
        manifest = Manifest.create([item])
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory))
            receiver.accept_manifest(manifest.to_wire())
            errors = []

            def read():
                try:
                    receiver.read_range(manifest.job_id, item.relative_path, 0, 4)
                except Exception as error:
                    errors.append(error)

            reader = threading.Thread(target=read)
            reader.start()
            time.sleep(0.02)
            receiver.cancel_job(manifest.job_id)
            reader.join(1)

            self.assertFalse(reader.is_alive())
            self.assertIsInstance(errors[0], TransferAbortedError)
            self.assertNotIn(manifest.job_id, receiver._jobs)

    def test_cancel_prevents_reads_from_already_completed_file(self):
        content = b"completed before cancel"
        item = FileItem("done.bin", ItemType.FILE, len(content), 1, hashlib.sha256(content).hexdigest())
        manifest = Manifest.create([item])
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory))
            receiver.accept_manifest(manifest.to_wire())
            receiver.accept_chunk({
                "job_id": manifest.job_id, "relative_path": item.relative_path,
                "offset": 0, "compressed": False, "original_size": len(content),
            }, content)
            receiver.complete_file(manifest.job_id, item.relative_path)
            receiver.cancel_job(manifest.job_id)

            with self.assertRaises(TransferAbortedError):
                receiver.read_range(manifest.job_id, item.relative_path, 0, len(content))

            self.assertNotIn(manifest.job_id, receiver._jobs)

    def test_failure_and_disconnect_release_job_keys_but_keep_bounded_errors(self):
        item = FileItem("blocked.bin", ItemType.FILE, 4, 1, "0" * 64)
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory))
            failed = Manifest.create([item])
            disconnected = Manifest.create([item])
            receiver.accept_manifest(failed.to_wire())
            receiver.accept_manifest(disconnected.to_wire())

            receiver.fail_paste(failed.job_id, "ExplorerStartTimeout")
            receiver.cancel_all("file lane disconnected")

            self.assertEqual(receiver._jobs, {})
            with self.assertRaisesRegex(TransferAbortedError, "Explorer"):
                receiver.read_range(failed.job_id, item.relative_path, 0, 1)
            with self.assertRaisesRegex(TransferAbortedError, "disconnected"):
                receiver.read_range(disconnected.job_id, item.relative_path, 0, 1)
            self.assertLessEqual(len(receiver._terminal_jobs), receiver._terminal_limit)

    def test_disconnect_terminalizes_waiting_controller_status(self):
        item = FileItem("waiting.bin", ItemType.FILE, 4, 1, "0" * 64)
        manifest = Manifest.create([item])
        with tempfile.TemporaryDirectory() as directory:
            controller = TransferController()
            receiver = TransferReceiver(Path(directory), controller=controller)
            receiver.accept_manifest(manifest.to_wire())
            controller.update(
                manifest.job_id,
                TransferPhase.WAITING_FOR_EXPLORER,
                "waiting.bin",
                0,
                4,
            )

            receiver.cancel_all("file lane disconnected")

            self.assertEqual(
                controller.status(manifest.job_id).phase,
                TransferPhase.CANCELLED,
            )

    def test_same_relative_path_completes_independently_in_two_jobs(self):
        content = b"same file"
        item = FileItem("same.txt", ItemType.FILE, len(content), 1, hashlib.sha256(content).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            receiver = TransferReceiver(Path(directory))
            completed = []
            for _ in range(2):
                manifest = Manifest.create([item])
                receiver.accept_manifest(manifest.to_wire())
                receiver.accept_chunk(
                    {
                        "job_id": manifest.job_id,
                        "relative_path": "same.txt",
                        "offset": 0,
                        "compressed": False,
                        "original_size": len(content),
                    },
                    content,
                )
                completed.append(receiver.complete_file(manifest.job_id, "same.txt"))

            self.assertNotEqual(completed[0], completed[1])
            self.assertEqual(
                [staged.read_available(0, len(content)) for staged in completed],
                [content, content],
            )


if __name__ == "__main__":
    unittest.main()
