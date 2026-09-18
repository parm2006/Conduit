import threading
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


class FakeDesktop:
    def __init__(self):
        self.on_snapshot = lambda instance, message: None
        self.requests = []
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
        return True


class BrowserHandoffCoordinatorTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
