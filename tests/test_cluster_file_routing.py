import unittest
import threading

from app.clipboard_hub import ClipboardHubItem
from app.file_transfer.cluster_router import ClusterFileRouter, ClusterJobPhase
from app.file_transfer.transport import _FileServerConnection
from app.file_transfer.models import FileItem, ItemType, Manifest


MACHINES = ("server", "client-1", "client-2")


def manifest(job_id="a" * 32):
    return Manifest(
        job_id,
        (FileItem("file.txt", ItemType.FILE, 4, 1, "0" * 64),),
        4,
        1,
    )


class ClusterFileRouterTests(unittest.TestCase):
    def setUp(self):
        self.offer = ClipboardHubItem(5, "client-1", 17, "files")
        self.control = []
        self.frames = []
        self.available = set(MACHINES)
        self.router = ClusterFileRouter(
            "server",
            latest_offer=lambda: self.offer,
            endpoint_available=lambda endpoint_id: endpoint_id in self.available,
            send_control=lambda endpoint_id, message: self.control.append(
                (endpoint_id, message)
            ) or True,
            send_file=lambda endpoint_id, metadata, payload: self.frames.append(
                (endpoint_id, metadata, payload)
            ) or True,
        )

    def route(self, source, destination, suffix):
        self.offer = ClipboardHubItem(5, source, 17, "files")
        request_id = f"{suffix:032x}"
        job = self.router.request_paste(destination, request_id)
        self.assertEqual(self.control[-1][0], source)
        self.assertEqual(self.control[-1][1]["request_id"], request_id)
        response = {
            "type": "file_manifest_response",
            "request_id": request_id,
            "manifest": manifest(f"{suffix + 100:032x}").to_wire(),
        }
        self.assertTrue(self.router.on_manifest_response(source, response))
        self.assertEqual(self.control[-1][0], destination)
        self.assertTrue(self.router.on_manifest_ack(
            destination,
            {"job_id": response["manifest"]["job_id"]},
        ))
        self.assertEqual(self.control[-1][0], source)
        return job, response["manifest"]["job_id"]

    def test_all_six_source_destination_pairs_use_same_server_job_contract(self):
        suffix = 1
        for source in MACHINES:
            for destination in MACHINES:
                if source == destination:
                    continue
                with self.subTest(source=source, destination=destination):
                    job, transfer_job_id = self.route(source, destination, suffix)
                    suffix += 1
                    self.assertEqual(job.source_id, source)
                    self.assertEqual(job.destination_id, destination)
                    self.assertEqual(job.offer_revision, 5)
                    self.assertEqual(job.selection_sequence, 17)
                    self.assertEqual(job.manifest.job_id, transfer_job_id)
                    self.assertEqual(job.phase, ClusterJobPhase.TRANSFERRING)

                    self.assertTrue(self.router.relay_frame(
                        source,
                        {
                            "type": "chunk",
                            "job_id": transfer_job_id,
                            "relative_path": "file.txt",
                            "offset": 0,
                        },
                        b"data",
                    ))
                    self.assertEqual(self.frames[-1][0], destination)
                    self.assertEqual(self.frames[-1][2], b"data")
                    self.assertNotIn("session_id", self.frames[-1][1])
                    self.assertNotIn("peer_identity", self.frames[-1][1])

    def test_new_offer_changes_future_job_but_not_latched_active_job(self):
        active, transfer_job_id = self.route("client-1", "client-2", 20)
        self.offer = ClipboardHubItem(6, "server", 18, "files")

        future = self.router.request_paste("client-2", f"{21:032x}")

        self.assertEqual(active.source_id, "client-1")
        self.assertEqual(active.manifest.job_id, transfer_job_id)
        self.assertEqual(future.source_id, "server")
        self.assertEqual(future.offer_revision, 6)

    def test_spoofed_manifest_or_frame_origin_is_rejected(self):
        job = self.router.request_paste("client-2", f"{30:032x}")
        response = {
            "request_id": job.request_id,
            "manifest": manifest(f"{130:032x}").to_wire(),
        }

        self.assertFalse(self.router.on_manifest_response("server", response))
        self.assertTrue(self.router.on_manifest_response("client-1", response))
        self.assertFalse(self.router.relay_frame(
            "server",
            {"type": "chunk", "job_id": f"{130:032x}"},
            b"private",
        ))

    def test_disconnect_cancels_only_jobs_involving_endpoint(self):
        affected, _ = self.route("client-1", "client-2", 40)
        untouched, _ = self.route("server", "client-2", 41)
        self.control.clear()

        cancelled = self.router.endpoint_disconnected("client-1")

        self.assertEqual(cancelled, (affected.job_id,))
        self.assertIsNone(self.router.job(affected.job_id))
        self.assertIs(self.router.job(untouched.job_id), untouched)
        self.assertEqual([endpoint for endpoint, _, _ in self.frames], ["client-2"])
        self.assertEqual(self.frames[0][1]["type"], "cancel_job")

    def test_pause_rejects_new_jobs_without_mutating_active_job(self):
        active, transfer_job_id = self.route("client-1", "client-2", 50)
        self.router.pause()

        self.assertIsNone(self.router.request_paste("server", f"{51:032x}"))
        self.assertIs(self.router.job(active.job_id), active)
        completed = threading.Event()
        relay = threading.Thread(target=lambda: (
            self.router.relay_frame(
                "client-1",
                {"type": "chunk", "job_id": transfer_job_id},
                b"bounded",
            ),
            completed.set(),
        ))
        relay.start()
        self.assertFalse(completed.wait(0.02))
        self.router.resume()
        self.assertTrue(completed.wait(1))
        relay.join(1)
        self.assertIsNotNone(self.router.request_paste("server", f"{52:032x}"))

    def test_terminal_feedback_goes_only_to_counterpart_then_job_is_cleaned(self):
        job, transfer_job_id = self.route("client-1", "client-2", 60)
        self.frames.clear()

        self.assertTrue(self.router.relay_frame(
            "client-2",
            {
                "type": "paste_progress",
                "job_id": transfer_job_id,
                "phase": "completed",
            },
        ))

        self.assertEqual([endpoint for endpoint, _, _ in self.frames], ["client-1"])
        self.assertIsNone(self.router.job(job.job_id))


class FileLaneIdentityBoundaryTests(unittest.TestCase):
    def test_authenticated_connection_overwrites_claimed_job_origin(self):
        captured = []

        class Owner:
            heartbeat_interval = 2
            heartbeat_timeout = 6

            def _trigger_callbacks(self, event_type, metadata, payload):
                captured.append((event_type, metadata, payload))

        connection = _FileServerConnection(
            Owner(),
            "real-session",
            "real-machine",
            ("192.0.2.1", 1234),
        )

        connection._trigger_callbacks(
            "chunk",
            {
                "session_id": "spoofed-session",
                "peer_identity": "spoofed-machine",
                "job_id": "a" * 32,
            },
            b"bytes",
        )

        self.assertEqual(captured[0][1]["session_id"], "real-session")
        self.assertEqual(captured[0][1]["peer_identity"], "real-machine")


if __name__ == "__main__":
    unittest.main()
