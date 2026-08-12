import tempfile
import threading
import unittest
from pathlib import Path

from app.file_transfer.models import FileItem, ItemType, Manifest
from app.file_transfer.receiver import TransferReceiver
from app.file_transfer.sender import DestinationPasteError, TransferSender
from app.file_transfer.source import SourceFile
from app.file_transfer.controller import TransferCancelled, TransferController
from app.file_transfer.status import TransferPhase

JOB_ID = "a" * 32


class LoopbackLane:
    def __init__(self, receiver):
        self.receiver = receiver
        self.callbacks = {}
        receiver.attach(self)

    def register_callback(self, event_type, callback):
        self.callbacks.setdefault(event_type, []).append(callback)

    def send(self, metadata, payload=b""):
        if metadata["type"] == "manifest":
            self.receiver.accept_manifest(metadata["manifest"])
        elif metadata["type"] == "chunk":
            self.receiver.accept_chunk(metadata, payload)
        elif metadata["type"] == "file_complete":
            self.receiver.complete_file(metadata["job_id"], metadata["relative_path"])
        elif metadata["type"] == "job_complete":
            self.receiver.complete_job(metadata["job_id"])
        elif metadata["type"] == "job_verified":
            for callback in self.callbacks.get("job_verified", ()):
                callback(metadata, payload)
        elif metadata["type"] == "paste_progress":
            for callback in self.callbacks.get("paste_progress", ()):
                callback(metadata, payload)

    def emit(self, metadata, payload=b""):
        for callback in self.callbacks.get(metadata["type"], ()):
            callback(metadata, payload)


class TransferSenderTests(unittest.TestCase):
    def test_sliding_window_bounds_bytes_without_stop_and_wait(self):
        class AcknowledgingLane:
            supports_chunk_ack = True

            def __init__(self):
                self.callbacks = {}
                self.sent = []
                self.chunk_sent = threading.Event()

            def register_callback(self, name, callback):
                self.callbacks.setdefault(name, []).append(callback)

            def send(self, metadata, payload=b""):
                self.sent.append((metadata, payload))
                if metadata["type"] == "chunk":
                    self.chunk_sent.set()
                elif metadata["type"] == "job_complete":
                    for callback in self.callbacks.get("job_verified", ()):
                        callback({
                            "type": "job_verified",
                            "job_id": metadata["job_id"],
                        }, b"")

            def acknowledge(self, metadata):
                for callback in self.callbacks.get("chunk_received", ()):
                    callback({
                        "type": "chunk_received",
                        "job_id": metadata["job_id"],
                        "relative_path": metadata["relative_path"],
                        "offset": metadata["offset"],
                    }, b"")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paced.bin"
            path.write_bytes(b"abcdefghijkl")
            source = SourceFile.snapshot(path, chunk_size=4)
            manifest = Manifest.create([
                FileItem(
                    "paced.bin", ItemType.FILE, source.size,
                    source.modified_ns, source.sha256,
                )
            ])
            lane = AcknowledgingLane()
            sender = TransferSender(lane, max_in_flight_bytes=8)
            errors = []
            worker = threading.Thread(
                target=lambda: self._capture_error(
                    errors,
                    lambda: sender.send_job(manifest, {"paced.bin": source}),
                )
            )

            worker.start()
            self.assertTrue(lane.chunk_sent.wait(1))
            for _ in range(100):
                chunks = [
                    entry for entry in lane.sent
                    if entry[0]["type"] == "chunk"
                ]
                if len(chunks) >= 2:
                    break
                threading.Event().wait(0.005)
            self.assertEqual(len(chunks), 2)

            lane.chunk_sent.clear()
            lane.acknowledge(chunks[0][0])
            self.assertTrue(lane.chunk_sent.wait(1))
            chunks = [entry for entry in lane.sent if entry[0]["type"] == "chunk"]
            self.assertEqual(len(chunks), 3)

            lane.acknowledge(chunks[1][0])
            lane.acknowledge(chunks[2][0])
            worker.join(1)

            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])

    def test_multi_file_source_failure_identifies_the_failed_file(self):
        class Lane:
            def __init__(self):
                self.callbacks = {}

            def register_callback(self, name, callback):
                self.callbacks.setdefault(name, []).append(callback)

            def send(self, metadata, payload=b""):
                return None

        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.bin"
            failed_path = Path(directory) / "failed.bin"
            first_path.write_bytes(b"first")
            failed_path.write_bytes(b"failed")
            first_source = SourceFile.snapshot(first_path)
            failed_source = SourceFile.snapshot(failed_path)
            manifest = Manifest.create([
                FileItem(
                    "folder/first.bin",
                    ItemType.FILE,
                    first_source.size,
                    first_source.modified_ns,
                    first_source.sha256,
                ),
                FileItem(
                    "private/folder/failed.bin",
                    ItemType.FILE,
                    failed_source.size + 1,
                    failed_source.modified_ns,
                    failed_source.sha256,
                ),
            ])
            controller = TransferController()
            sender = TransferSender(Lane(), controller=controller)

            with self.assertRaises(ValueError):
                sender.send_job(
                    manifest,
                    {
                        "folder/first.bin": first_source,
                        "private/folder/failed.bin": failed_source,
                    },
                )

        status = controller.status(manifest.job_id)
        self.assertEqual(status.phase, TransferPhase.FAILED)
        self.assertEqual(status.label, "failed.bin")

    def test_invalid_unknown_progress_job_id_is_not_retained(self):
        lane = type(
            "Lane", (),
            {
                "callbacks": {},
                "register_callback": lambda self, kind, cb: self.callbacks.setdefault(kind, []).append(cb),
            },
        )()
        sender = TransferSender(lane)

        handled = sender._on_paste_progress({
            "job_id": "not-a-job-id",
            "phase": "failed",
            "bytes_done": 0,
            "bytes_total": 0,
            "bytes_per_second": 0,
        }, b"")

        self.assertFalse(handled)
        self.assertEqual(sender._early_paste_terminal, {})

    def test_early_destination_failure_is_replayed_when_source_job_registers(self):
        class Lane:
            def __init__(self):
                self.callbacks = {}
                self.sent = []

            def register_callback(self, name, callback):
                self.callbacks.setdefault(name, []).append(callback)

            def send(self, metadata, payload=b""):
                self.sent.append(metadata)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.txt"
            path.write_bytes(b"")
            source = SourceFile.snapshot(path)
            manifest = Manifest.create([
                FileItem("empty.txt", ItemType.FILE, 0, source.modified_ns, source.sha256)
            ])
            lane = Lane()
            controller = TransferController()
            sender = TransferSender(lane, controller=controller)

            self.assertTrue(sender._on_paste_progress({
                "job_id": manifest.job_id,
                "phase": "failed",
                "bytes_done": 0,
                "bytes_total": 0,
                "bytes_per_second": 0,
                "error_code": "PasteInjectionFailed",
            }, b""))

            with self.assertRaises(DestinationPasteError):
                sender.send_job(
                    manifest, {"empty.txt": source}, announce_manifest=False
                )

        self.assertEqual(controller.status(manifest.job_id).phase, TransferPhase.FAILED)
        self.assertEqual(controller.status(manifest.job_id).error_code, "PasteInjectionFailed")
        self.assertEqual(lane.sent, [])

    def test_cancellation_during_verification_is_not_treated_as_success(self):
        class WaitingLane:
            def __init__(self):
                self.callbacks = {}
                self.waiting = threading.Event()

            def register_callback(self, name, callback):
                self.callbacks.setdefault(name, []).append(callback)

            def send(self, metadata, payload=b""):
                if metadata["type"] == "job_complete":
                    self.waiting.set()

        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "empty.bin"
            source_path.write_bytes(b"")
            source = SourceFile.snapshot(source_path)
            item = FileItem(
                "empty.bin", ItemType.FILE, source.size,
                source.modified_ns, source.sha256,
            )
            manifest = Manifest.create([item])
            lane = WaitingLane()
            controller = TransferController()
            errors = []
            worker = threading.Thread(
                target=lambda: self._capture_error(
                    errors,
                    lambda: TransferSender(lane, controller=controller).send_job(
                        manifest, {"empty.bin": source}
                    ),
                )
            )
            worker.start()
            self.assertTrue(lane.waiting.wait(1))
            controller.cancel(manifest.job_id)
            worker.join(1)

            self.assertFalse(worker.is_alive())
            self.assertIsInstance(errors[0], TransferCancelled)

    @staticmethod
    def _capture_error(errors, action):
        try:
            action()
        except Exception as error:
            errors.append(error)

    def test_sends_manifest_and_bounded_chunks_that_receiver_verifies(self):
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as receive_directory:
            source_path = Path(source_directory) / "report.txt"
            source_path.write_bytes(b"Conduit" * 200_000)
            source = SourceFile.snapshot(source_path)
            item = FileItem("report.txt", ItemType.FILE, source.size, source.modified_ns, source.sha256)
            manifest = Manifest.create([item])
            receiver = TransferReceiver(Path(receive_directory))

            TransferSender(LoopbackLane(receiver)).send_job(manifest, {"report.txt": source})

            completed = list((Path(receive_directory) / "completed" / manifest.job_id).glob("*.cache"))
            self.assertEqual(len(completed), 1)
            self.assertEqual(
                receiver.read_range(manifest.job_id, "report.txt", 0, source.size),
                source_path.read_bytes(),
            )
            self.assertNotEqual(completed[0].read_bytes(), source_path.read_bytes())

    def test_waits_for_destination_owned_explorer_progress_after_network_verification(self):
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as receive_directory:
            source_path = Path(source_directory) / "report.txt"
            source_path.write_bytes(b"progress bytes")
            source = SourceFile.snapshot(source_path)
            manifest = Manifest.create([
                FileItem("report.txt", ItemType.FILE, source.size, source.modified_ns, source.sha256)
            ])
            controller = TransferController()
            observed = []
            controller.subscribe(observed.append)
            receiver = TransferReceiver(Path(receive_directory))

            lane = LoopbackLane(receiver)
            TransferSender(lane, controller=controller).send_job(
                manifest, {"report.txt": source}
            )

            self.assertEqual(observed[0].phase, TransferPhase.WAITING_FOR_EXPLORER)
            self.assertNotIn(TransferPhase.TRANSFERRING, [status.phase for status in observed])
            self.assertNotIn(TransferPhase.VERIFYING, [status.phase for status in observed])
            self.assertEqual(observed[-1].phase, TransferPhase.WAITING_FOR_EXPLORER)
            lane.emit({
                "type": "paste_progress", "job_id": manifest.job_id,
                "phase": "completed", "bytes_done": source.size,
                "bytes_total": source.size, "bytes_per_second": source.size,
            })
            self.assertEqual(observed[-1].phase, TransferPhase.COMPLETED)
            self.assertEqual(observed[-1].bytes_done, source.size)

    def test_rejects_impossible_or_regressing_remote_paste_progress(self):
        controller = TransferController()
        lane = type("Lane", (), {"callbacks": {}, "register_callback": lambda self, kind, cb: self.callbacks.setdefault(kind, []).append(cb)})()
        sender = TransferSender(lane, controller=controller)
        sender._paste_jobs[JOB_ID] = ("file.bin", 100)
        controller.update(JOB_ID, TransferPhase.PASTING, "file.bin", 50, 100, 10)

        self.assertFalse(sender._on_paste_progress({
            "job_id": JOB_ID, "phase": "pasting", "bytes_done": 40,
            "bytes_total": 100, "bytes_per_second": 10,
        }, b""))
        self.assertFalse(sender._on_paste_progress({
            "job_id": JOB_ID, "phase": "pasting", "bytes_done": 60,
            "bytes_total": 101, "bytes_per_second": 10,
        }, b""))
        self.assertEqual(controller.status(JOB_ID).bytes_done, 50)

    def test_remote_cancelled_paste_closes_source_status(self):
        controller = TransferController()
        lane = type("Lane", (), {"callbacks": {}, "register_callback": lambda self, kind, cb: self.callbacks.setdefault(kind, []).append(cb)})()
        sender = TransferSender(lane, controller=controller)
        sender._paste_jobs[JOB_ID] = ("file.bin", 100)
        controller.update(JOB_ID, TransferPhase.PASTING, "file.bin", 40, 100)

        handled = sender._on_paste_progress({
            "job_id": JOB_ID, "phase": "cancelled", "bytes_done": 40,
            "bytes_total": 100, "bytes_per_second": 0,
        }, b"")

        self.assertTrue(handled)
        self.assertEqual(controller.status(JOB_ID).phase, TransferPhase.CANCELLED)

    def test_remote_explorer_failure_closes_source_status_with_safe_code(self):
        controller = TransferController()
        lane = type(
            "Lane", (),
            {
                "callbacks": {},
                "register_callback": lambda self, kind, cb: self.callbacks.setdefault(kind, []).append(cb),
            },
        )()
        sender = TransferSender(lane, controller=controller)
        sender._paste_jobs[JOB_ID] = ("file.bin", 100)
        controller.update(JOB_ID, TransferPhase.WAITING_FOR_EXPLORER, "file.bin", 0, 0)

        handled = sender._on_paste_progress({
            "job_id": JOB_ID, "phase": "failed", "bytes_done": 0,
            "bytes_total": 100, "bytes_per_second": 0,
            "error_code": "ExplorerStartTimeout",
        }, b"")

        self.assertTrue(handled)
        self.assertEqual(controller.status(JOB_ID).phase, TransferPhase.FAILED)
        self.assertEqual(
            controller.status(JOB_ID).error_code, "ExplorerStartTimeout"
        )

    def test_unknown_remote_failure_code_is_normalized_before_status(self):
        controller = TransferController()
        lane = type(
            "Lane", (),
            {
                "callbacks": {},
                "register_callback": lambda self, kind, cb: self.callbacks.setdefault(kind, []).append(cb),
            },
        )()
        sender = TransferSender(lane, controller=controller)
        sender._paste_jobs[JOB_ID] = ("file.bin", 100)
        controller.update(JOB_ID, TransferPhase.WAITING_FOR_EXPLORER, "file.bin", 0, 0)

        sender._on_paste_progress({
            "job_id": JOB_ID, "phase": "failed", "bytes_done": 0,
            "bytes_total": 100, "bytes_per_second": 0,
            "error_code": r"C:\Users\private\secret.txt",
        }, b"")

        self.assertEqual(
            controller.status(JOB_ID).error_code, "DestinationPasteFailed"
        )

    def test_late_network_verification_cannot_reset_active_explorer_progress(self):
        controller = TransferController()
        lane = type("Lane", (), {"callbacks": {}, "register_callback": lambda self, kind, cb: self.callbacks.setdefault(kind, []).append(cb)})()
        sender = TransferSender(lane, controller=controller)
        manifest = Manifest.create([FileItem("file.bin", ItemType.FILE, 100, 1, "0" * 64)])
        controller.update(manifest.job_id, TransferPhase.PASTING, "file.bin", 40, 100, 10)

        sender._waiting(manifest, "file.bin")

        self.assertEqual(controller.status(manifest.job_id).phase, TransferPhase.PASTING)
        self.assertEqual(controller.status(manifest.job_id).bytes_done, 40)

if __name__ == "__main__":
    unittest.main()
