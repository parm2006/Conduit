import hashlib
import tempfile
import unittest
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from app.file_transfer.cancellation import TransferCancellation
from app.file_transfer.controller import TransferController
from app.file_transfer.models import FileItem, ItemType, Manifest
from app.file_transfer.receiver import TransferReceiver
from app.file_transfer.publisher import VirtualPastePublisher
from app.file_transfer.sender import TransferSender
from app.file_transfer.status import TransferPhase
from app.file_transfer.toast import TransferToast

JOB_ONE = "1" * 32
JOB_TWO = "2" * 32
CANCEL_ONE = "c" * 32


class Lane:
    def __init__(self):
        self.callbacks = {}
        self.sent = []

    def register_callback(self, name, callback):
        self.callbacks.setdefault(name, []).append(callback)

    def send(self, metadata, payload=b""):
        self.sent.append(metadata)

    def emit(self, metadata):
        for callback in self.callbacks.get(metadata["type"], ()):
            callback(metadata, b"")


class LinkedLane(Lane):
    supports_chunk_ack = False

    def __init__(self):
        super().__init__()
        self.peer = None

    def send(self, metadata, payload=b""):
        self.sent.append(metadata)
        if self.peer is not None:
            self.peer.emit_with_payload(metadata, payload)

    def emit_with_payload(self, metadata, payload):
        for callback in tuple(self.callbacks.get(metadata["type"], ())):
            callback(metadata, payload)


class CorrelatedSession:
    decision_pending = True

    def __init__(self):
        self.infer_cancel = threading.Event()
        self.published = threading.Event()
        self.dismissals = 0
        self.cleanups = 0
        self.terminals = []

    def observe(self):
        return self.infer_cancel.is_set()

    def record_stream_open(self):
        return None

    def record_performed_effect(self, effect):
        return None

    def request_cancel(self):
        self.dismissals += 1
        self.decision_pending = False
        return True

    def cleanup_cancelled_empty_directories(self):
        self.cleanups += 1
        return {}

    def record_terminal(self, phase):
        self.terminals.append(phase)
        return True


class CancellationTests(unittest.TestCase):
    @staticmethod
    def _wait_for(predicate, timeout=1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.005)
        return bool(predicate())

    def _paired_paste(self, directory):
        source_lane = LinkedLane()
        destination_lane = LinkedLane()
        source_lane.peer = destination_lane
        destination_lane.peer = source_lane
        source_controller = TransferController()
        destination_controller = TransferController()
        source_receiver = TransferReceiver(
            directory / "source", controller=source_controller
        )
        destination_receiver = TransferReceiver(
            directory / "destination", controller=destination_controller
        )
        source_receiver.attach(source_lane)
        destination_receiver.attach(destination_lane)
        source_sender = TransferSender(source_lane, controller=source_controller)
        TransferSender(destination_lane, controller=destination_controller)
        source_cancellation = TransferCancellation(
            source_lane, source_controller, source_receiver
        )
        destination_cancellation = TransferCancellation(
            destination_lane, destination_controller, destination_receiver
        )
        item = FileItem(
            "copy.bin", ItemType.FILE, 1, 0, hashlib.sha256(b"x").hexdigest()
        )
        manifest = Manifest(JOB_ONE, (item,), 1, 1)
        destination_receiver.accept_manifest(manifest.to_wire())
        source_controller.update(
            JOB_ONE, TransferPhase.TRANSFERRING, "copy.bin", 0, 1
        )
        with source_sender._paste_lock:
            source_sender._paste_jobs[JOB_ONE] = ("copy.bin", 1)
        session = CorrelatedSession()

        def publish(file_set, on_performed_drop=None):
            session.published.set()
            return object()

        publisher = VirtualPastePublisher(
            publish=publish,
            inject=lambda keyboard: None,
            release=lambda owner: True,
            keyboard_factory=object,
            explorer_start_timeout=0.1,
            session_factory=lambda wire_manifest: session,
        )
        publisher.publish_and_paste(manifest.to_wire(), destination_receiver)
        self.assertTrue(session.published.wait(1))
        return SimpleNamespace(
            source_lane=source_lane,
            destination_lane=destination_lane,
            source_controller=source_controller,
            destination_controller=destination_controller,
            source_receiver=source_receiver,
            destination_receiver=destination_receiver,
            source_sender=source_sender,
            source_cancellation=source_cancellation,
            destination_cancellation=destination_cancellation,
            publisher=publisher,
            session=session,
        )

    @staticmethod
    def _click_toast_cancel(protocol, events):
        toast = TransferToast.__new__(TransferToast)
        toast.job_id = JOB_ONE
        toast._dismissed_job_id = None
        toast.on_cancel = protocol.request
        toast._hide = lambda: events.append("hidden")
        toast._cancel()

    def test_toast_cancel_from_either_peer_closes_only_destination_session(self):
        for initiator in ("source", "destination"):
            with self.subTest(initiator=initiator), tempfile.TemporaryDirectory() as directory:
                pair = self._paired_paste(Path(directory))
                events = []
                protocol = (
                    pair.source_cancellation
                    if initiator == "source"
                    else pair.destination_cancellation
                )

                self._click_toast_cancel(protocol, events)

                self.assertTrue(pair.publisher.wait_until_idle(1))
                self.assertEqual(events, ["hidden"])
                self.assertEqual(
                    pair.source_controller.status(JOB_ONE).phase,
                    TransferPhase.CANCELLED,
                )
                self.assertEqual(
                    pair.destination_controller.status(JOB_ONE).phase,
                    TransferPhase.CANCELLED,
                )
                self.assertEqual(pair.session.dismissals, 1)
                self.assertEqual(pair.session.cleanups, 1)
                self.assertEqual(pair.publisher.retained_owner_count, 0)
                self.assertTrue(
                    self._wait_for(
                        lambda: JOB_ONE not in pair.source_sender._paste_jobs
                    )
                )
                original = pair.destination_receiver.terminal_outcome(JOB_ONE)
                self.assertFalse(
                    pair.destination_receiver.record_performed_drop(JOB_ONE, 1)
                )
                self.assertFalse(pair.destination_receiver.accept_chunk({
                    "job_id": JOB_ONE,
                    "relative_path": "copy.bin",
                    "offset": 0,
                    "compressed": False,
                    "original_size": 1,
                }, b"x"))
                self.assertEqual(
                    pair.destination_receiver.terminal_outcome(JOB_ONE),
                    original,
                )

    def test_explorer_cancel_first_wins_over_late_toast_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            pair = self._paired_paste(Path(directory))
            pair.session.infer_cancel.set()

            self.assertTrue(pair.publisher.wait_until_idle(1))
            self.assertTrue(
                self._wait_for(
                    lambda: pair.source_controller.status(JOB_ONE).phase
                    is TransferPhase.CANCELLED
                )
            )
            destination_outcome = pair.destination_receiver.terminal_outcome(JOB_ONE)
            cancel_messages_before = len([
                message for message in pair.source_lane.sent
                if message.get("type") == "cancel_job"
            ])

            self.assertFalse(pair.source_cancellation.request(JOB_ONE))

            self.assertEqual(destination_outcome.phase, TransferPhase.CANCELLED)
            self.assertEqual(destination_outcome.reason_code, "ExplorerCancelled")
            self.assertEqual(
                pair.destination_receiver.terminal_outcome(JOB_ONE),
                destination_outcome,
            )
            terminal_messages = [
                message
                for message in pair.destination_lane.sent
                if message.get("type") == "paste_progress"
                and message.get("job_id") == JOB_ONE
                and message.get("phase") == TransferPhase.CANCELLED.value
            ]
            self.assertEqual(len(terminal_messages), 1)
            self.assertEqual(
                len([
                    message for message in pair.source_lane.sent
                    if message.get("type") == "cancel_job"
                ]),
                cancel_messages_before,
            )

    def test_cancelled_pair_accepts_and_completes_a_fresh_job_without_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            pair = self._paired_paste(Path(directory))
            self._click_toast_cancel(pair.source_cancellation, [])
            self.assertTrue(pair.publisher.wait_until_idle(1))
            next_item = FileItem(
                "next.bin", ItemType.FILE, 1, 0, hashlib.sha256(b"y").hexdigest()
            )
            next_manifest = Manifest(JOB_TWO, (next_item,), 1, 1)
            pair.destination_receiver.accept_manifest(next_manifest.to_wire())
            pair.source_controller.update(
                JOB_TWO, TransferPhase.TRANSFERRING, "next.bin", 0, 1
            )
            with pair.source_sender._paste_lock:
                pair.source_sender._paste_jobs[JOB_TWO] = ("next.bin", 1)

            self.assertTrue(pair.destination_receiver.accept_chunk({
                "job_id": JOB_TWO,
                "relative_path": "next.bin",
                "offset": 0,
                "compressed": False,
                "original_size": 1,
            }, b"y"))
            pair.destination_receiver.complete_file(JOB_TWO, "next.bin")
            pair.destination_receiver.record_stream_open(JOB_TWO, "next.bin")
            pair.destination_receiver.record_stream_read(JOB_TWO, "next.bin", 0, 1)
            pair.destination_receiver.complete_job(JOB_TWO)

            self.assertTrue(
                self._wait_for(
                    lambda: pair.source_controller.status(JOB_TWO).phase
                    is TransferPhase.COMPLETED
                )
            )
            self.assertEqual(
                pair.destination_receiver.terminal_outcome(JOB_TWO).phase,
                TransferPhase.COMPLETED,
            )

    def test_invalid_remote_identifiers_are_not_retained_or_acknowledged(self):
        with tempfile.TemporaryDirectory() as directory:
            lane, controller, receiver, protocol = self._active(Path(directory))
            before = len(lane.sent)

            lane.emit({
                "type": "cancel_job",
                "job_id": "not-a-job-id",
                "cancellation_id": "x" * 32,
            })
            lane.emit({
                "type": "cancel_job",
                "job_id": JOB_ONE,
                "cancellation_id": "not-a-cancellation-id",
            })

            self.assertEqual(len(lane.sent), before)
            self.assertEqual(protocol._handled, {})
            self.assertNotIn("not-a-job-id", receiver._terminal_jobs)

    def test_chunk_decoding_race_cannot_recreate_staging_after_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            lane, controller, receiver, protocol = self._active(Path(directory))
            decoding = threading.Event()
            release = threading.Event()
            results = []

            def delayed_decode(payload, compressed, original_size):
                decoding.set()
                release.wait(1)
                return payload

            metadata = {
                "job_id": JOB_ONE, "relative_path": "x", "offset": 0,
                "compressed": False, "original_size": 1,
            }
            with patch("app.file_transfer.receiver.decode_chunk", delayed_decode):
                worker = threading.Thread(
                    target=lambda: results.append(
                        receiver.accept_chunk(metadata, b"x")
                    )
                )
                worker.start()
                self.assertTrue(decoding.wait(1))
                protocol.request(JOB_ONE)
                release.set()
                worker.join(1)

            self.assertEqual(results, [False])
            self.assertNotIn(JOB_ONE, receiver._jobs)
            self.assertEqual(list(Path(directory).rglob("*.partial")), [])

    def _active(self, root, job_id=JOB_ONE):
        lane = Lane()
        controller = TransferController()
        receiver = TransferReceiver(root, controller=controller)
        receiver.attach(lane)
        item = FileItem("x", ItemType.FILE, 1, 0, hashlib.sha256(b"x").hexdigest())
        manifest = Manifest(job_id, (item,), 1, 1)
        receiver.accept_manifest(manifest.to_wire())
        controller.update(job_id, TransferPhase.TRANSFERRING, "x", 0, 1)
        protocol = TransferCancellation(lane, controller, receiver)
        return lane, controller, receiver, protocol

    def test_local_cancel_has_one_operation_and_matching_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            lane, controller, receiver, protocol = self._active(Path(directory))
            self.assertTrue(protocol.request(JOB_ONE))
            request = lane.sent[-1]
            self.assertEqual(request["type"], "cancel_job")
            lane.emit({
                "type": "cancel_ack", "job_id": JOB_ONE,
                "cancellation_id": request["cancellation_id"],
            })
            self.assertEqual(controller.status(JOB_ONE).phase, TransferPhase.CANCELLED)
            self.assertFalse(protocol.request(JOB_ONE))

    def test_remote_duplicates_ack_but_apply_once_and_late_frames_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            lane, controller, receiver, protocol = self._active(Path(directory))
            cancel = {"type": "cancel_job", "job_id": JOB_ONE, "cancellation_id": CANCEL_ONE}
            lane.emit(cancel)
            lane.emit(cancel)
            self.assertEqual([m["type"] for m in lane.sent[-2:]], ["cancel_ack", "cancel_ack"])
            self.assertEqual(controller.status(JOB_ONE).phase, TransferPhase.CANCELLED)
            self.assertFalse(receiver.accept_chunk({
                "job_id": JOB_ONE, "relative_path": "x", "offset": 0,
                "compressed": False, "original_size": 1,
            }, b"x"))
            self.assertFalse(receiver.complete_file(JOB_ONE, "x"))
            self.assertFalse(receiver.complete_job(JOB_ONE))

    def test_cancelled_job_does_not_poison_next_job(self):
        with tempfile.TemporaryDirectory() as directory:
            lane, controller, receiver, protocol = self._active(Path(directory), JOB_ONE)
            protocol.request(JOB_ONE)
            item = FileItem("x", ItemType.FILE, 1, 0, hashlib.sha256(b"x").hexdigest())
            next_manifest = Manifest(JOB_TWO, (item,), 1, 1)
            self.assertIsNotNone(receiver.accept_manifest(next_manifest.to_wire()))
            self.assertTrue(receiver.accept_chunk({
                "job_id": JOB_TWO, "relative_path": "x", "offset": 0,
                "compressed": False, "original_size": 1,
            }, b"x"))
            receiver.complete_file(JOB_TWO, "x")
            receiver.complete_job(JOB_TWO)


if __name__ == "__main__":
    unittest.main()
