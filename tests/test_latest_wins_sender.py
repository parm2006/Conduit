import threading
import unittest

from app.latest_wins_sender import LatestWinsSender


class LatestWinsSenderTests(unittest.TestCase):
    def test_active_send_finishes_and_only_latest_pending_payload_is_sent(self):
        first_send_started = threading.Event()
        release_first_send = threading.Event()
        sent = []

        def send(payload):
            sent.append(payload)
            if payload["text"] == "A":
                first_send_started.set()
                self.assertTrue(release_first_send.wait(timeout=1))

        sender = LatestWinsSender(send)
        self.addCleanup(sender.stop)

        sender.submit({"text": "A"})
        self.assertTrue(first_send_started.wait(timeout=1))
        sender.submit({"text": "B"})
        sender.submit({"text": "C"})
        sender.submit({"text": "D"})
        release_first_send.set()

        self.assertTrue(sender.wait_until_idle(timeout=1))
        self.assertEqual(sent, [{"text": "A"}, {"text": "D"}])

    def test_submit_snapshots_payload_before_worker_sends_it(self):
        release_send = threading.Event()
        sent = []

        def send(payload):
            self.assertTrue(release_send.wait(timeout=1))
            sent.append(payload)

        sender = LatestWinsSender(send)
        self.addCleanup(sender.stop)
        payload = {"text": "original"}

        sender.submit(payload)
        payload["text"] = "mutated"
        release_send.set()

        self.assertTrue(sender.wait_until_idle(timeout=1))
        self.assertEqual(sent, [{"text": "original"}])

    def test_send_exception_does_not_kill_worker_or_leave_sender_busy(self):
        attempted = threading.Event()
        sent = []

        def send(payload):
            if payload["text"] == "bad":
                attempted.set()
                raise RuntimeError("send failed")
            sent.append(payload)

        sender = LatestWinsSender(send)
        self.addCleanup(sender.stop)

        with self.assertLogs("app.latest_wins_sender", level="ERROR") as logs:
            sender.submit({"text": "bad"})
            self.assertTrue(attempted.wait(timeout=1))
            self.assertTrue(sender.wait_until_idle(timeout=1))
        self.assertIn("Latest-wins send failed", logs.output[0])
        sender.submit({"text": "good"})

        self.assertTrue(sender.wait_until_idle(timeout=1))
        self.assertEqual(sent, [{"text": "good"}])

    def test_stop_drops_pending_payload_and_rejects_new_submissions(self):
        first_send_started = threading.Event()
        release_first_send = threading.Event()
        sent = []

        def send(payload):
            sent.append(payload)
            first_send_started.set()
            self.assertTrue(release_first_send.wait(timeout=1))

        sender = LatestWinsSender(send)
        sender.submit({"text": "active"})
        self.assertTrue(first_send_started.wait(timeout=1))
        sender.submit({"text": "pending"})

        stop_thread = threading.Thread(target=sender.stop)
        stop_thread.start()
        release_first_send.set()
        stop_thread.join(timeout=1)

        self.assertFalse(stop_thread.is_alive())
        self.assertFalse(sender.submit({"text": "late"}))
        self.assertEqual(sent, [{"text": "active"}])

    def test_pause_holds_delivery_without_blocking_or_losing_latest_submit(self):
        sent = []
        sender = LatestWinsSender(sent.append)
        self.addCleanup(sender.stop)

        sender.pause()
        self.assertTrue(sender.submit({"text": "A"}))
        self.assertTrue(sender.submit({"text": "B"}))
        self.assertFalse(sender.wait_until_idle(timeout=0.02))
        self.assertEqual(sent, [])

        sender.resume()

        self.assertTrue(sender.wait_until_idle(timeout=1))
        self.assertEqual(sent, [{"text": "B"}])

    def test_pause_allows_active_send_to_finish_then_holds_only_newest_pending(self):
        first_started = threading.Event()
        release_first = threading.Event()
        sent = []

        def send(payload):
            sent.append(payload)
            if payload["text"] == "A":
                first_started.set()
                self.assertTrue(release_first.wait(timeout=1))

        sender = LatestWinsSender(send)
        self.addCleanup(sender.stop)
        sender.submit({"text": "A"})
        self.assertTrue(first_started.wait(timeout=1))

        sender.pause()
        sender.submit({"text": "B"})
        sender.submit({"text": "C"})
        release_first.set()
        self.assertFalse(sender.wait_until_idle(timeout=0.02))
        self.assertEqual(sent, [{"text": "A"}])

        sender.resume()

        self.assertTrue(sender.wait_until_idle(timeout=1))
        self.assertEqual(sent, [{"text": "A"}, {"text": "C"}])


if __name__ == "__main__":
    unittest.main()
