import threading
import time
from dataclasses import replace
import unittest

from app.browser_handoff.coordinator import BrowserHandoffCoordinator
from app.browser_handoff.edge_band import configured_edge_region
from app.browser_handoff.window_match import BrowserWindowCandidate, PhysicalRect
from app.browser_handoff.windows_drag import MoveToken


class FakeTracker:
    def __init__(self, token):
        self.token = token
        self.claims = 0

    def claim_active_move(self, *, now):
        self.claims += 1
        token, self.token = self.token, None
        return token


class DiagnosticTracker(FakeTracker):
    def diagnostic_snapshot(self):
        return {
            "events": {"move_start": 2, "location_change": 12, "move_end": 2, "other": 0},
            "events_without_session": 4,
            "active_sessions": 0,
            "tokens_pending": 0,
            "tokens_created": 0,
            "tokens_consumed": 0,
            "tokens_expired": 0,
            "last_decision": "rejected_resize",
        }


class CompletedOnlyTracker:
    def __init__(self, token):
        self.token = token

    def claim_active_move(self, *, now):
        return None

    def consume_eligible_move(self, *, now):
        token, self.token = self.token, None
        return token


class FakeDesktop:
    def __init__(self):
        self.on_snapshot = lambda instance, message: None
        self.requests = []
        self.request_result = True
        self.candidate = BrowserWindowCandidate(
            browser_instance_id="browser-1", window_id=7,
            process_id=11, process_created=12,
            bounds=PhysicalRect(1900, 200, 2000, 900), focused=True,
            observed_at=100.0, metadata_revision=3, bridge_epoch="bridge-1",
        )

    def browser_candidates(self, to_physical, *, received_at=None):
        return [self.candidate]

    def request_snapshot(self, instance_id, window_id, request_id):
        self.requests.append((instance_id, window_id, request_id))
        return self.request_result


class BrowserHandoffCoordinatorTests(unittest.TestCase):
    def test_logs_when_no_active_move_token_is_available(self):
        desktop = FakeDesktop()
        coordinator = BrowserHandoffCoordinator(
            desktop=desktop,
            move_tracker=FakeTracker(None),
            to_physical=lambda window: window,
            send_candidate=lambda candidate: None,
            now=lambda: 100.0,
        )
        with self.assertLogs("app.browser_handoff.coordinator", level="INFO") as captured:
            result = coordinator.claim_edge(
                display_rect=PhysicalRect(0, 0, 2000, 1000),
                edge_region=configured_edge_region(PhysicalRect(0, 0, 2000, 1000), "right"),
                source_display_id="display-1", source_side="right", topology_version=4,
            )
        self.assertIsNone(result)
        self.assertIn("move_token_missing", "\n".join(captured.output))

    def test_logs_move_tracker_diagnostics_when_no_active_move_token_is_available(self):
        desktop = FakeDesktop()
        coordinator = BrowserHandoffCoordinator(
            desktop=desktop,
            move_tracker=DiagnosticTracker(None),
            to_physical=lambda window: window,
            send_candidate=lambda candidate: None,
            now=lambda: 100.0,
        )

        with self.assertLogs("app.browser_handoff.coordinator", level="INFO") as captured:
            coordinator.claim_edge(
                display_rect=PhysicalRect(0, 0, 2000, 1000),
                edge_region=configured_edge_region(PhysicalRect(0, 0, 2000, 1000), "right"),
                source_display_id="display-1", source_side="right", topology_version=4,
            )

        output = "\n".join(captured.output)
        self.assertIn("move_token_missing", output)
        self.assertIn("rejected_resize", output)

    def test_matches_exact_window_and_publishes_after_snapshot_without_blocking_claim(self):
        desktop = FakeDesktop()
        published = []
        done = threading.Event()
        coordinator = BrowserHandoffCoordinator(
            desktop=desktop,
            move_tracker=FakeTracker(MoveToken(1, 11, 12, desktop.candidate.bounds, 100.0)),
            to_physical=lambda window: window,
            send_candidate=lambda candidate: (published.append(candidate), done.set()),
            now=lambda: 100.0,
        )
        gesture_id = coordinator.claim_edge(
            display_rect=PhysicalRect(0, 0, 2000, 1000),
            edge_region=configured_edge_region(PhysicalRect(0, 0, 2000, 1000), "right"),
            source_display_id="display-1", source_side="right", topology_version=4,
        )

        self.assertIsInstance(gesture_id, str)
        self.assertEqual(desktop.requests[0][0:2], ("browser-1", 7))
        request_id = desktop.requests[0][2]
        desktop.on_snapshot("browser-1", {
            "type": "browser_handoff_snapshot", "request_id": request_id, "epoch": "browser-1", "_bridge_epoch": "bridge-1",
            "snapshot": {
                "window_id": 7, "revision": 3, "incognito": False,
                "total_count": 1,
                "entries": [{"source_index": 0, "url": "https://example.test", "active": True}],
                "complete_capture": True,
            },
        })

        self.assertTrue(done.wait(0.5))
        self.assertEqual(published[0]["gesture_id"], gesture_id)
        self.assertEqual(published[0]["entries"][0]["url"], "https://example.test")

    def test_cursor_edge_claim_does_not_require_browser_window_edge_geometry(self):
        desktop = FakeDesktop()
        desktop.candidate = replace(
            desktop.candidate,
            bounds=PhysicalRect(500, 200, 1300, 900),
        )
        published = []
        coordinator = BrowserHandoffCoordinator(
            desktop=desktop,
            move_tracker=FakeTracker(MoveToken(1, 11, 12, desktop.candidate.bounds, 100.0)),
            to_physical=lambda window: window,
            send_candidate=published.append,
            now=lambda: 100.0,
        )

        gesture_id = coordinator.claim_edge(
            display_rect=PhysicalRect(0, 0, 2000, 1000),
            edge_region=configured_edge_region(PhysicalRect(0, 0, 2000, 1000), "left"),
            source_display_id="display-1", source_side="left", topology_version=4,
        )

        self.assertIsInstance(gesture_id, str)
        self.assertEqual(desktop.requests[0][0:2], ("browser-1", 7))

    def test_logs_geometry_when_exact_window_match_is_rejected(self):
        desktop = FakeDesktop()
        desktop.candidate = replace(
            desktop.candidate,
            bounds=PhysicalRect(1000, 200, 1800, 900),
        )
        coordinator = BrowserHandoffCoordinator(
            desktop=desktop,
            move_tracker=FakeTracker(MoveToken(
                1, 11, 12, PhysicalRect(0, 0, 800, 700), 100.0,
            )),
            to_physical=lambda window: window,
            send_candidate=lambda candidate: None,
            now=lambda: 100.0,
        )

        with self.assertLogs("app.browser_handoff.coordinator", level="INFO") as captured:
            result = coordinator.claim_edge(
                display_rect=PhysicalRect(0, 0, 2000, 1000),
                edge_region=configured_edge_region(PhysicalRect(0, 0, 2000, 1000), "left"),
                source_display_id="display-1", source_side="left", topology_version=4,
            )

        self.assertIsNone(result)
        output = "\n".join(captured.output)
        self.assertIn("native_bounds=(0, 0, 800, 700)", output)
        self.assertIn("candidate_bounds=[(1000, 200, 1800, 900)]", output)

    def test_consumes_completed_move_when_edge_arrives_after_source_release(self):
        desktop = FakeDesktop()
        token = MoveToken(1, 11, 12, desktop.candidate.bounds, 100.0)
        coordinator = BrowserHandoffCoordinator(
            desktop=desktop,
            move_tracker=CompletedOnlyTracker(token),
            to_physical=lambda window: window,
            send_candidate=lambda candidate: None,
            now=lambda: 100.0,
        )

        gesture_id = coordinator.claim_edge(
            display_rect=PhysicalRect(0, 0, 2000, 1000),
            edge_region=configured_edge_region(PhysicalRect(0, 0, 2000, 1000), "right"),
            source_display_id="display-1", source_side="right", topology_version=4,
        )

        self.assertIsInstance(gesture_id, str)
        self.assertEqual(desktop.requests[0][0:2], ("browser-1", 7))

    def test_rejects_snapshot_for_different_window_or_revision(self):
        desktop = FakeDesktop()
        published = []
        coordinator = BrowserHandoffCoordinator(
            desktop=desktop,
            move_tracker=FakeTracker(MoveToken(1, 11, 12, desktop.candidate.bounds, 100.0)),
            to_physical=lambda window: window,
            send_candidate=published.append,
            now=lambda: 100.0,
        )
        coordinator.claim_edge(
            display_rect=PhysicalRect(0, 0, 2000, 1000),
            edge_region=configured_edge_region(PhysicalRect(0, 0, 2000, 1000), "right"),
            source_display_id="display-1", source_side="right", topology_version=4,
        )
        request_id = desktop.requests[0][2]
        self.assertFalse(coordinator.handle_snapshot("browser-1", {
            "request_id": request_id, "epoch": "browser-1", "_bridge_epoch": "bridge-1",
            "snapshot": {"window_id": 8, "revision": 3, "complete_capture": True},
        }))
        self.assertEqual(published, [])

    def test_expired_pending_capture_does_not_block_new_claim(self):
        desktop = FakeDesktop()
        clock = [100.0]
        coordinator = BrowserHandoffCoordinator(
            desktop=desktop,
            move_tracker=FakeTracker(MoveToken(1, 11, 12, desktop.candidate.bounds, 100.0)),
            to_physical=lambda window: window,
            send_candidate=lambda candidate: None,
            now=lambda: clock[0],
            token_ttl_seconds=1.0,
            max_pending=1,
        )
        self.assertIsNotNone(coordinator.claim_edge(
            display_rect=PhysicalRect(0, 0, 2000, 1000),
            edge_region=configured_edge_region(PhysicalRect(0, 0, 2000, 1000), "right"),
            source_display_id="display-1", source_side="right", topology_version=4,
        ))
        clock[0] = 101.5
        desktop.candidate = replace(desktop.candidate, observed_at=101.5)
        coordinator.move_tracker.token = MoveToken(2, 11, 12, desktop.candidate.bounds, 101.5)
        self.assertIsNotNone(coordinator.claim_edge(
            display_rect=PhysicalRect(0, 0, 2000, 1000),
            edge_region=configured_edge_region(PhysicalRect(0, 0, 2000, 1000), "right"),
            source_display_id="display-1", source_side="right", topology_version=4,
        ))

    def test_failed_snapshot_submission_releases_pending_capture(self):
        desktop = FakeDesktop()
        desktop.request_result = False
        coordinator = BrowserHandoffCoordinator(
            desktop=desktop,
            move_tracker=FakeTracker(MoveToken(1, 11, 12, desktop.candidate.bounds, 100.0)),
            to_physical=lambda window: window,
            send_candidate=lambda candidate: None,
            now=lambda: 100.0,
            max_pending=1,
        )
        self.assertIsNotNone(coordinator.claim_edge(
            display_rect=PhysicalRect(0, 0, 2000, 1000),
            edge_region=configured_edge_region(PhysicalRect(0, 0, 2000, 1000), "right"),
            source_display_id="display-1", source_side="right", topology_version=4,
        ))
        request_id = desktop.requests[0][2]
        for _ in range(20):
            if not coordinator._pending:
                break
            time.sleep(0.005)
        self.assertNotIn(request_id, coordinator._pending)


if __name__ == "__main__":
    unittest.main()
