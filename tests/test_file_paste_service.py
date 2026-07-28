import time
import unittest

from app.file_transfer.paste_service import FilePasteService


JOB_A = "a" * 32
JOB_B = "b" * 32
VALID_REQUEST = "1" * 32


class RecordingControl:
    def __init__(self):
        self.messages = []

    def send_message(self, message):
        self.messages.append(message)
        return True


class RecordingReceiver:
    def __init__(self):
        self.manifests = []

    def accept_manifest(self, manifest):
        self.manifests.append(manifest)


class RecordingPublisher:
    def __init__(self):
        self.jobs = []
        self.has_pending_paste = False

    def publish_and_paste(self, manifest, receiver):
        self.jobs.append(manifest["job_id"])


class RecordingSender:
    def __init__(self):
        self.jobs = []

    def send_job(self, manifest, sources, announce_manifest=True):
        self.jobs.append((manifest.job_id, tuple(sources), announce_manifest))


class ImmediateExecutor:
    def __init__(self, sender):
        self.sender = sender

    def submit(self, manifest, sources):
        self.sender.send_job(manifest, sources, announce_manifest=False)


class Manifest:
    def __init__(self, job_id):
        self.job_id = job_id

    def to_wire(self):
        return {"job_id": self.job_id, "items": [], "total_size": 0, "file_count": 0}


class FilePasteServiceTests(unittest.TestCase):
    def test_destination_acknowledges_before_async_paste_can_report_failure(self):
        events = []

        class Control(RecordingControl):
            def send_message(self, message):
                events.append(("send", message["type"]))
                return super().send_message(message)

        class Publisher(RecordingPublisher):
            def publish_and_paste(self, manifest, receiver):
                events.append(("publish", manifest["job_id"]))
                return super().publish_and_paste(manifest, receiver)

        control = Control()
        receiver = RecordingReceiver()
        publisher = Publisher()
        sender = RecordingSender()
        service = FilePasteService(
            control=control,
            receiver=receiver,
            publisher=publisher,
            sender=sender,
            snapshot_selection=lambda: None,
            executor=ImmediateExecutor(sender),
        )
        pending = service.request_paste()

        service.on_manifest_response({
            "request_id": pending.request_id,
            "manifest": {"job_id": JOB_A},
        })

        self.assertEqual(
            events,
            [("send", "file_manifest_request"), ("send", "file_manifest_ack"), ("publish", JOB_A)],
        )

    def test_destination_latch_spans_request_through_explorer_paste(self):
        service, _, _, publisher, _ = self.make_service([])

        pending = service.request_paste()
        self.assertTrue(service.destination_paste_active)

        publisher.has_pending_paste = True
        service.on_manifest_response({
            "request_id": pending.request_id,
            "manifest": {"job_id": JOB_A},
        })
        self.assertTrue(service.destination_paste_active)

        publisher.has_pending_paste = False
        self.assertFalse(service.destination_paste_active)

    def make_service(self, snapshots):
        control = RecordingControl()
        receiver = RecordingReceiver()
        publisher = RecordingPublisher()
        sender = RecordingSender()
        service = FilePasteService(
            control=control,
            receiver=receiver,
            publisher=publisher,
            sender=sender,
            snapshot_selection=lambda: snapshots.pop(0),
            executor=ImmediateExecutor(sender),
        )
        return service, control, receiver, publisher, sender

    def test_source_does_not_snapshot_until_manifest_request_arrives(self):
        snapshots = [(Manifest(JOB_A), {"a.txt": object()})]
        service, control, _, _, _ = self.make_service(snapshots)

        self.assertEqual(len(snapshots), 1)
        service.on_manifest_request({"request_id": VALID_REQUEST})

        self.assertEqual(snapshots, [])
        self.assertEqual(control.messages[0]["type"], "file_manifest_response")
        self.assertEqual(control.messages[0]["request_id"], VALID_REQUEST)

    def test_invalid_request_id_is_rejected_before_snapshotting(self):
        snapshots = [(Manifest(JOB_A), {"a.txt": object()})]
        service, control, _, _, _ = self.make_service(snapshots)

        self.assertFalse(service.on_manifest_request({"request_id": "not-valid"}))

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(control.messages, [])

    def test_outgoing_snapshots_are_bounded_before_more_hashing_occurs(self):
        snapshots = [
            (Manifest(f"{number:032x}"), {f"{number}.txt": object()})
            for number in range(9)
        ]
        service, control, _, _, _ = self.make_service(snapshots)

        for number in range(8):
            self.assertTrue(
                service.on_manifest_request({"request_id": f"{number + 1:032x}"})
            )
        self.assertFalse(service.on_manifest_request({"request_id": "f" * 32}))

        self.assertEqual(len(service._outgoing), 8)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(control.messages[-1]["type"], "file_manifest_failed")

    def test_unacknowledged_snapshot_expires_and_releases_sources(self):
        snapshots = [(Manifest(JOB_A), {"a.txt": object()})]
        service, _, _, _, _ = self.make_service(snapshots)
        service._outgoing_timeout = 0.02
        service.on_manifest_request({"request_id": VALID_REQUEST})

        deadline = time.monotonic() + 1.0
        while service._outgoing and time.monotonic() < deadline:
            time.sleep(0.005)

        self.assertEqual(service._outgoing, {})

    def test_destination_accepts_each_response_publishes_fifo_and_acknowledges(self):
        service, control, receiver, publisher, _ = self.make_service([])
        first = service.request_paste()
        second = service.request_paste()

        service.on_manifest_response({"request_id": first.request_id, "manifest": {"job_id": JOB_A}})
        service.on_manifest_response({"request_id": second.request_id, "manifest": {"job_id": JOB_B}})

        self.assertEqual(publisher.jobs, [JOB_A, JOB_B])
        self.assertEqual(receiver.manifests, [{"job_id": JOB_A}, {"job_id": JOB_B}])
        self.assertEqual(
            [message["job_id"] for message in control.messages if message["type"] == "file_manifest_ack"],
            [JOB_A, JOB_B],
        )

    def test_source_starts_exact_snapshot_only_after_ack(self):
        snapshots = [(Manifest(JOB_A), {"a.txt": object()})]
        service, _, _, _, sender = self.make_service(snapshots)
        service.on_manifest_request({"request_id": VALID_REQUEST})
        self.assertEqual(sender.jobs, [])

        service.on_manifest_ack({"job_id": JOB_A})

        self.assertEqual(sender.jobs, [(JOB_A, ("a.txt",), False)])


if __name__ == "__main__":
    unittest.main()
