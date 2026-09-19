from dataclasses import replace
import threading
import unittest

from app.browser_handoff.coordinator import BrowserHandoffCoordinator
from app.browser_handoff.window_match import BrowserWindowCandidate, PhysicalRect
from app.browser_handoff.windows_drag import (
    MoveTracker, EVENT_SYSTEM_MOVESIZESTART, EVENT_SYSTEM_MOVESIZEEND,
    EVENT_OBJECT_LOCATIONCHANGE,
)


class Clock:
    def __init__(self):
        self.time = 100.0
        self.events = []

    def now(self):
        return self.time

    def wait(self, event, seconds):
        target = self.time + seconds
        while self.events and self.events[0][0] <= target:
            at, callback = self.events.pop(0)
            self.time = at
            callback()
        self.time = target


class Desktop:
    def __init__(self, clock):
        self.clock = clock
        self.on_snapshot = lambda *_: None
        self.refreshes = {}
        self.requests = []
        self.refresh_count = 0
        self.delay = 0
        self.live = True
        self.answer_snapshot = True
        self.submit = True
        self.snapshot_changes = {}
        self.on_request = lambda: None
        self.candidate = BrowserWindowCandidate(
            "browser-1", 7, 11, 12, PhysicalRect(500, 200, 1300, 900), True, 100.0, 3, "bridge-1")
        self.candidates = lambda attempt: [self.candidate]

    def request_metadata_refresh(self, request_id):
        self.refresh_count += 1
        self.refreshes[request_id] = (self.clock.now() + self.delay, self.refresh_count)
        return self.live

    def browser_candidates(self, converter, *, refresh_id):
        if not self.live:
            raise ConnectionError()
        ready, attempt = self.refreshes[refresh_id]
        if self.clock.now() < ready:
            return None
        return [replace(candidate, observed_at=ready) for candidate in self.candidates(attempt)]

    def release_metadata_refresh(self, request_id):
        self.refreshes.pop(request_id, None)

    def bridge_is_current(self, instance, epoch):
        return self.live and (instance, epoch) == ("browser-1", "bridge-1")

    def message(self, request_id):
        snapshot = {"window_id": 7, "revision": 3, "incognito": False,
                    "total_count": 1, "entries": [{"source_index": 0, "url": "https://example.test", "active": True}],
                    "complete_capture": True}
        snapshot.update(self.snapshot_changes)
        return {"type": "browser_handoff_snapshot", "request_id": request_id,
                "epoch": "browser-1", "_bridge_epoch": "bridge-1", "snapshot": snapshot}

    def request_snapshot(self, instance, window, request_id, *, expected_epoch=None):
        if not self.bridge_is_current(instance, expected_epoch):
            return False
        self.requests.append((instance, window, request_id))
        if self.answer_snapshot:
            self.on_snapshot(instance, self.message(request_id))
        self.on_request()
        return self.submit


class BrowserHandoffCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.desktop = Desktop(self.clock)
        self.tracker = MoveTracker()
        self.work = []
        self.sent = []
        self.reads = []
        self.coordinator = BrowserHandoffCoordinator(
            desktop=self.desktop, move_tracker=self.tracker, to_physical=lambda window: window,
            send_candidate=lambda candidate: self.sent.append(candidate) or True,
            now=self.clock.now, wait=self.clock.wait, spawn=self.work.append,
            read_bounds=self.read_bounds)

    def read_bounds(self, token):
        self.reads.append(self.clock.now())
        return self.desktop.candidate.bounds

    def observe(self, event, bounds=None):
        self.tracker.observe(event, hwnd=1, process_id=11, process_created=12,
                             bounds=bounds or self.desktop.candidate.bounds,
                             timestamp=self.clock.now(), left_button_down=True)

    def start(self, completed=True):
        self.observe(EVENT_SYSTEM_MOVESIZESTART, PhysicalRect(0, 0, 800, 700))
        self.observe(EVENT_OBJECT_LOCATIONCHANGE)
        if completed:
            self.observe(EVENT_SYSTEM_MOVESIZEEND)

    def claim(self):
        return self.coordinator.claim_edge(display_rect=None, edge_region=None,
            source_display_id="display-1", source_side="left", topology_version=4)

    def run_task(self):
        with self.assertLogs("app.browser_handoff.coordinator", level="INFO") as logs:
            self.work.pop(0)()
        self.assertEqual(self.desktop.refreshes, {})
        self.assertEqual(self.coordinator._pending, {})
        self.assertEqual(self.coordinator._tasks, {})
        self.assertEqual(sum("stage=gesture_summary" in line for line in logs.output), 1)
        return "\n".join(logs.output)

    def test_missing_move_logs_diagnostics(self):
        with self.assertLogs("app.browser_handoff.coordinator", level="INFO") as logs:
            self.assertIsNone(self.claim())
        self.assertIn("move_token_missing", str(logs.output))
        self.assertEqual(self.work, [])

    def test_completed_move_returns_gesture_before_any_io_and_publishes_once(self):
        self.start()
        gesture = self.claim()
        self.assertIsInstance(gesture, str)
        self.assertEqual(self.desktop.refresh_count, 0)
        self.assertEqual(self.reads, [])
        self.assertIsNone(self.claim())
        logs = self.run_task()
        self.assertEqual(self.sent[0]["gesture_id"], gesture)
        self.assertIn("move_end_to_revision_ms", logs)
        self.assertIn("edge_delta", logs)
        self.assertNotIn("example.test", logs)
        self.assertIn("outcome=candidate_sent", logs)

    def test_active_move_waits_for_end_then_reads_rect_then_refreshes(self):
        self.start(completed=False)
        self.claim()
        def end():
            self.assertEqual(self.reads, [])
            self.assertEqual(self.desktop.refresh_count, 0)
            self.observe(EVENT_SYSTEM_MOVESIZEEND)
        self.clock.events.append((100.1, end))
        self.run_task()
        self.assertGreaterEqual(self.reads[0], 100.1)
        self.assertEqual(len(self.sent), 1)
        self.assertIsNone(self.claim())

    def test_second_edge_does_not_unclaim_or_schedule_another_task(self):
        self.start(completed=False)
        self.claim()
        self.assertIsNone(self.claim())
        self.assertEqual(len(self.work), 1)
        self.observe(EVENT_SYSTEM_MOVESIZEEND)
        self.assertIsNone(self.claim())
        self.run_task()
        self.assertEqual(len(self.sent), 1)

    def test_no_move_end_times_out_without_metadata_io(self):
        self.start(completed=False)
        self.claim()
        self.assertIn("outcome=deadline_expired", self.run_task())
        self.assertEqual(self.desktop.refresh_count, 0)

    def test_metadata_arrives_before_deadline(self):
        self.start()
        self.desktop.delay = 0.2
        self.claim()
        self.run_task()
        self.assertEqual(len(self.sent), 1)

    def test_match_uses_reread_rect_not_the_claimed_token_rect(self):
        self.start()
        self.claim()
        self.desktop.candidate = replace(self.desktop.candidate, bounds=PhysicalRect(900, 400, 1700, 1100))
        self.run_task()
        self.assertEqual(len(self.sent), 1)

    def test_capacity_rejection_does_not_schedule_extra_work(self):
        self.coordinator.max_pending = 1
        self.start()
        self.claim()
        self.start()
        self.assertIsNone(self.claim())
        self.assertEqual(len(self.work), 1)
        self.run_task()

    def test_failed_metadata_send_cleans_up_without_snapshot(self):
        self.start()
        self.desktop.live = False
        self.claim()
        self.assertIn("outcome=metadata_request_failed", self.run_task())
        self.assertEqual(self.desktop.requests, [])

    def test_metadata_late_or_missing_never_publishes(self):
        for delay in (1.1, float("inf")):
            with self.subTest(delay=delay):
                self.setUp()
                self.start()
                self.desktop.delay = delay
                self.claim()
                self.assertIn("outcome=deadline_expired", self.run_task())
                self.assertEqual(self.sent, [])
                self.assertEqual(self.desktop.requests, [])

    def test_stale_geometry_refreshes_at_most_twice_more(self):
        self.start()
        stale = replace(self.desktop.candidate, bounds=PhysicalRect(0, 0, 800, 700))
        self.desktop.candidates = lambda attempt: [stale if attempt < 3 else self.desktop.candidate]
        self.claim()
        self.run_task()
        self.assertEqual(self.desktop.refresh_count, 3)
        self.assertEqual(len(self.sent), 1)

    def test_ambiguous_matches_abstain_without_guessing(self):
        self.start()
        self.desktop.candidates = lambda attempt: [self.desktop.candidate, replace(self.desktop.candidate, window_id=8)]
        self.claim()
        self.assertIn("outcome=window_match_rejected", self.run_task())
        self.assertEqual(self.desktop.refresh_count, 3)
        self.assertEqual(self.sent, [])

    def test_bridge_loss_while_waiting_abstains(self):
        self.start()
        self.desktop.delay = 0.5
        self.clock.events.append((100.1, lambda: setattr(self.desktop, "live", False)))
        self.claim()
        self.assertIn("outcome=bridge_lost", self.run_task())
        self.assertEqual(self.sent, [])

    def test_topology_replacement_cancels_metadata_wait(self):
        self.start()
        self.desktop.delay = 0.5
        self.clock.events.append((100.1, self.coordinator.cancel))
        self.claim()
        self.assertIn("outcome=cancelled", self.run_task())
        self.assertEqual(self.sent, [])

    def test_cancel_after_snapshot_before_publication(self):
        self.start()
        self.desktop.on_request = self.coordinator.cancel
        self.claim()
        self.assertIn("outcome=cancelled", self.run_task())
        self.assertEqual(self.sent, [])

    def test_bridge_loss_after_snapshot_before_publication(self):
        self.start()
        self.desktop.on_request = lambda: setattr(self.desktop, "live", False)
        self.claim()
        self.assertIn("outcome=bridge_lost", self.run_task())
        self.assertEqual(self.sent, [])

    def test_cancel_before_worker_starts_does_no_io(self):
        self.start()
        self.claim()
        self.coordinator.cancel()
        self.run_task()
        self.assertEqual(self.reads, [])

    def test_snapshot_wrong_window_revision_or_privacy_abstains(self):
        for changes in ({"window_id": 8}, {"revision": 4}, {"incognito": True}, {"complete_capture": False}):
            with self.subTest(changes=changes):
                self.setUp()
                self.start()
                self.desktop.snapshot_changes = changes
                self.claim()
                self.assertIn("snapshot_rejected", self.run_task())
                self.assertEqual(self.sent, [])

    def test_late_snapshot_is_rejected_and_capacity_freed(self):
        self.start()
        self.desktop.answer_snapshot = False
        self.claim()
        self.assertIn("outcome=deadline_expired", self.run_task())
        request_id = self.desktop.requests[0][2]
        self.assertFalse(self.coordinator.handle_snapshot("browser-1", self.desktop.message(request_id)))
        self.assertEqual(self.sent, [])

    def test_failed_snapshot_submission_is_not_retried(self):
        self.start()
        self.desktop.submit = False
        self.claim()
        self.assertIn("outcome=snapshot_request_failed", self.run_task())
        self.assertEqual(len(self.desktop.requests), 1)
        self.assertEqual(self.sent, [])

    def test_new_move_before_old_end_invalidates_owned_token(self):
        self.start(completed=False)
        self.claim()
        self.observe(EVENT_SYSTEM_MOVESIZESTART)
        self.assertIn("outcome=move_invalidated", self.run_task())
        self.assertEqual(self.reads, [])

    def test_transport_does_not_hold_input_lock_or_block_cancellation(self):
        self.start()
        self.claim()
        entered, release, callback_done = threading.Event(), threading.Event(), threading.Event()
        def send(candidate):
            entered.set()
            release.wait(2)
            return True
        self.coordinator.send_candidate = send
        worker = threading.Thread(target=self.work.pop(0))
        worker.start()
        callback = None
        try:
            self.assertTrue(entered.wait(0.5))
            def edge_and_cancel():
                self.claim()
                self.coordinator.cancel()
                callback_done.set()
            callback = threading.Thread(target=edge_and_cancel)
            callback.start()
            self.assertTrue(callback_done.wait(0.5))
        finally:
            release.set()
            worker.join(2)
            if callback is not None:
                callback.join(2)

    def test_identity_change_at_move_end_invalidates_task(self):
        self.start(completed=False)
        self.claim()
        self.tracker.observe(EVENT_SYSTEM_MOVESIZEEND, hwnd=1, process_id=99, process_created=100,
                             bounds=self.desktop.candidate.bounds, timestamp=self.clock.now(), left_button_down=False)
        logs = self.run_task()
        self.assertIn("outcome=move_invalidated", logs)
        self.assertIn("process_identity_changed", logs)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
