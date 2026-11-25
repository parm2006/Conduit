import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.clipboard_hub import ClipboardHubItem
from app.file_transfer.cancellation import TransferCancellation
from app.file_transfer.cluster_router import ClusterFileRouter, ServerClusterFileLane
from app.file_transfer.controller import TransferController
from app.file_transfer.models import FileItem, ItemType, Manifest
from app.file_transfer.publisher import VirtualPastePublisher
from app.file_transfer.receiver import TransferAbortedError, TransferReceiver
from app.file_transfer.status import TransferPhase
from app.windows_virtual_files import CallbackStream


JOB = "a" * 32


class Lane:
    def __init__(self):
        self.callbacks = {}
        self.sent = []

    def register_callback(self, name, callback):
        self.callbacks.setdefault(name, []).append(callback)

    def send(self, metadata, payload=b""):
        self.sent.append(metadata)
        return True

    def emit(self, metadata):
        for callback in tuple(self.callbacks.get(metadata["type"], ())):
            callback(metadata, b"")


def manifest(job_id=JOB):
    return Manifest(job_id, (FileItem("copy.bin", ItemType.FILE, 8, 0, "0" * 64),), 8, 1)


class DisconnectCleanupTests(unittest.TestCase):
    def make_receiver(self, root, **options):
        controller = TransferController()
        receiver = TransferReceiver(root, controller=controller, **options)
        receiver.accept_manifest(manifest().to_wire())
        return controller, receiver

    def test_missing_disconnect_read_stall_aborts_with_windows_cancelled_error(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, receiver = self.make_receiver(
                Path(directory), read_timeout_seconds=0.02,
            )
            receiver.lane = Lane()
            errors = []
            stream = CallbackStream(
                lambda offset, count: receiver.read_range(JOB, "copy.bin", offset, count), 8,
            )

            def read():
                try:
                    stream.Read(4)
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=read)
            worker.start()
            try:
                worker.join(0.3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors[0].scode, -2147023673)
                self.assertEqual(controller.status(JOB).phase, TransferPhase.FAILED)
                self.assertEqual(controller.status(JOB).error_code, "TransferStalledTimeout")
                self.assertNotIn(JOB, receiver._jobs)
                progress = receiver._progress_queue.get_nowait()
                self.assertEqual(progress["error_code"], "TransferStalledTimeout")
            finally:
                receiver.cancel_job(JOB)
                worker.join(1)

    def test_waiting_read_progress_resets_stall_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, receiver = self.make_receiver(Path(directory))
            receiver.read_timeout_seconds = 0.02
            now = [0.0]
            receiver.clock = lambda: now[0]
            staged = SimpleNamespace(
                received_size=0, read_available=lambda offset, count: b"x",
                abort=lambda: None,
            )
            job = receiver._jobs[JOB]
            job["staged"]["copy.bin"] = staged

            def progress(timeout=None):
                now[0] += 0.015
                staged.received_size += 1

            with patch.object(job["condition"], "wait", progress):
                data = receiver.read_range(JOB, "copy.bin", 7, 1)

            self.assertEqual(data, b"x")
            self.assertGreater(now[0], receiver.read_timeout_seconds * 5)
            self.assertFalse(controller.status(JOB).is_terminal)

    def test_spurious_read_wakes_do_not_reset_stall_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, receiver = self.make_receiver(Path(directory))
            receiver.read_timeout_seconds = 0.02
            now = [0.0]
            receiver.clock = lambda: now[0]
            job = receiver._jobs[JOB]

            def spurious_wake(timeout=None):
                now[0] += 0.011
                self.assertLess(now[0], 0.05, "read did not enforce its stall budget")

            with patch.object(job["condition"], "wait", spurious_wake):
                with self.assertRaises(TransferAbortedError):
                    receiver.read_range(JOB, "copy.bin", 0, 1)

            self.assertEqual(controller.status(JOB).error_code, "TransferStalledTimeout")

    def test_default_read_stall_budget_is_fifteen_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, receiver = self.make_receiver(Path(directory))
            self.assertEqual(getattr(receiver, "read_timeout_seconds", None), 15.0)

    def test_disconnect_wakes_read_even_when_controller_failed_first(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, receiver = self.make_receiver(Path(directory))
            job = receiver._jobs[JOB]
            waiting = threading.Event()
            errors = []
            original_wait = job["condition"].wait

            def wait(timeout=None):
                waiting.set()
                return original_wait(timeout)

            def read():
                try:
                    receiver.read_range(JOB, "copy.bin", 0, 4)
                except Exception as error:
                    errors.append(error)

            with patch.object(job["condition"], "wait", wait):
                worker = threading.Thread(target=read)
                worker.start()
                try:
                    self.assertTrue(waiting.wait(1))
                    controller.update(JOB, TransferPhase.FAILED, "copy.bin", 0, 8,
                                      error_code="ConnectionError")
                    receiver.cancel_all("file lane disconnected")
                    worker.join(0.2)
                    self.assertFalse(worker.is_alive())
                    self.assertIsInstance(errors[0], TransferAbortedError)
                    self.assertNotIn(JOB, receiver._jobs)
                    self.assertEqual(receiver.terminal_outcome(JOB).phase, TransferPhase.FAILED)
                finally:
                    with job["condition"]:
                        job["error"] = "test cleanup"
                        job["condition"].notify_all()
                    worker.join(1)

    def test_lane_disconnect_terminalizes_source_only_job_without_cancel_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            lane = Lane()
            controller = TransferController()
            receiver = TransferReceiver(Path(directory), controller=controller)
            TransferCancellation(lane, controller, receiver)
            controller.update(JOB, TransferPhase.TRANSFERRING, "source.bin", 4, 8)

            lane.emit({"type": "disconnected"})

            self.assertEqual(controller.status(JOB).phase, TransferPhase.CANCELLED)
            self.assertEqual(lane.sent, [])

    def test_cancel_during_staging_read_returns_windows_cancelled_error(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, receiver = self.make_receiver(Path(directory))
            reading = threading.Event()
            release_read = threading.Event()
            errors = []
            results = []

            def read_available(offset, count):
                reading.set()
                release_read.wait(1)
                return b"data"

            receiver._jobs[JOB]["staged"]["copy.bin"] = SimpleNamespace(
                received_size=8, read_available=read_available,
                abort=release_read.set,
            )
            stream = CallbackStream(
                lambda offset, count: receiver.read_range(JOB, "copy.bin", offset, count), 8,
            )

            def read():
                try:
                    results.append(stream.Read(4))
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=read)
            worker.start()
            try:
                self.assertTrue(reading.wait(1))
                receiver.cancel_job(JOB)
                worker.join(1)
                self.assertEqual(results, [])
                self.assertEqual(errors[0].scode, -2147023673)
            finally:
                release_read.set()
                worker.join(1)

    def test_file_lane_only_loss_cancels_scoped_local_receiver(self):
        with tempfile.TemporaryDirectory() as directory:
            network = Lane()
            controller, receiver = self.make_receiver(Path(directory))
            frames = []
            lane = None
            offer = [ClipboardHubItem(1, "client-a", 1, "files")]

            def deliver(endpoint, metadata, payload):
                frames.append((endpoint, metadata))
                return lane.deliver_local(metadata, payload) if endpoint == "server" else True

            router = ClusterFileRouter(
                "server", latest_offer=lambda: offer[0],
                endpoint_available=lambda endpoint: True,
                send_control=lambda endpoint, message: True, send_file=deliver,
            )
            lane = ServerClusterFileLane("server", network, router)
            receiver.attach(lane)
            TransferCancellation(lane, controller, receiver)
            job = router.request_paste("server", "c" * 32)
            router.on_manifest_response("client-a", {
                "request_id": job.request_id, "manifest": manifest().to_wire(),
            })
            offer[0] = ClipboardHubItem(2, "client-z", 2, "files")
            unaffected = router.request_paste("client-b", "d" * 32)

            network.emit({"type": "disconnected", "peer_identity": "client-a"})

            self.assertEqual(controller.status(JOB).phase, TransferPhase.CANCELLED)
            self.assertIsNone(router.job(job.job_id))
            self.assertIs(router.job(unaffected.job_id), unaffected)

    def test_blocked_com_read_unwinds_publisher_after_disconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, receiver = self.make_receiver(Path(directory))
            lane = Lane()
            receiver.attach(lane)
            TransferCancellation(lane, controller, receiver)
            reading = threading.Event()
            session = SimpleNamespace(
                decision_pending=False, observe=lambda: False,
                record_performed_effect=lambda effect: None,
                record_terminal=lambda phase: None,
                cleanup_cancelled_empty_directories=lambda: None,
            )
            dismissed = []
            session.request_cancel = lambda: dismissed.append(True)

            def publish(file_set, on_performed_drop):
                on_performed_drop(1)
                return object()

            def pump():
                if not reading.is_set():
                    reading.set()
                    receiver.read_range(JOB, "copy.bin", 0, 4)

            publisher = VirtualPastePublisher(
                publish=publish, inject=lambda keyboard: None,
                release=lambda owner: True, keyboard_factory=object,
                session_factory=lambda wire: session,
            )
            job = receiver._jobs[JOB]
            with patch("app.file_transfer.publisher.pythoncom.PumpWaitingMessages", pump):
                publisher.publish_and_paste(manifest().to_wire(), receiver)
                try:
                    self.assertTrue(reading.wait(1))
                    controller.cancel(JOB)
                    controller.confirm_cancelled(JOB)
                    lane.emit({"type": "disconnected"})
                    self.assertTrue(publisher.wait_until_idle(0.3))
                    self.assertEqual(dismissed, [True])
                    self.assertEqual(publisher.retained_owner_count, 0)
                finally:
                    with job["condition"]:
                        job["error"] = "test cleanup"
                        job["condition"].notify_all()
                    publisher.wait_until_idle(1)


if __name__ == "__main__":
    unittest.main()
