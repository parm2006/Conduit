from pathlib import Path
import subprocess
import sys
import unittest
from io import BytesIO

from app.browser_handoff.window_match import (
    BrowserWindowCandidate,
    NativeWindowObservation,
    PhysicalRect,
    match_window,
)
from app.browser_handoff.windows_drag import (
    EVENT_OBJECT_LOCATIONCHANGE,
    EVENT_SYSTEM_MOVESIZEEND,
    EVENT_SYSTEM_MOVESIZESTART,
    MoveToken,
    MoveTracker,
)
import scripts.probe_browser_handoff as browser_probe
from scripts.probe_browser_handoff import (
    FrameError,
    candidates_from_metadata,
    ProbeMetadataStore,
    host_ready_message,
    native_host_manifest,
    read_native_message,
    write_native_message,
)

ProbeHostDiagnostics = getattr(browser_probe, "ProbeHostDiagnostics", None)


class BrowserWindowMatchTests(unittest.TestCase):
    def setUp(self):
        self.rect = PhysicalRect(100, 100, 900, 700)
        self.native = NativeWindowObservation(
            hwnd=42,
            process_id=101,
            process_created=1234,
            bounds=self.rect,
            observed_at=10.0,
        )

    def candidate(self, **overrides):
        values = {
            "browser_instance_id": "instance-a",
            "window_id": 7,
            "process_id": 101,
            "process_created": 1234,
            "bounds": self.rect,
            "focused": True,
            "observed_at": 10.0,
        }
        values.update(overrides)
        return BrowserWindowCandidate(**values)

    def test_matches_one_fresh_geometry_candidate_despite_unverified_process_identity(self):
        result = match_window(
            self.native,
            [self.candidate(process_id=202, process_created=5678)],
            now=10.1,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.browser_instance_id, "instance-a")
        self.assertEqual(result.window_id, 7)

    def test_abstains_when_two_candidates_are_equally_plausible(self):
        result = match_window(
            self.native,
            [
                self.candidate(window_id=7, process_id=202, process_created=5678),
                self.candidate(window_id=8, process_id=303, process_created=6789),
            ],
            now=10.1,
        )

        self.assertIsNone(result)

    def test_abstains_when_metadata_is_stale(self):
        stale = self.candidate(observed_at=6.0)

        self.assertIsNone(match_window(self.native, [stale], now=10.1))

    def test_abstains_when_bounds_do_not_overlap_after_dpi_conversion(self):
        candidate = self.candidate(bounds=PhysicalRect(1600, 100, 2400, 700))

        self.assertIsNone(match_window(self.native, [candidate], now=10.1))


class MoveTrackerTests(unittest.TestCase):
    def test_diagnostics_explain_a_move_rejected_without_a_mouse_button(self):
        tracker = MoveTracker(token_ttl_seconds=1.0)
        start = PhysicalRect(0, 0, 800, 600)
        end = PhysicalRect(40, 0, 840, 600)

        tracker.observe(
            EVENT_SYSTEM_MOVESIZESTART,
            hwnd=42,
            process_id=101,
            process_created=1234,
            bounds=start,
            timestamp=10.0,
            left_button_down=False,
        )
        tracker.observe(
            EVENT_OBJECT_LOCATIONCHANGE,
            hwnd=42,
            process_id=101,
            process_created=1234,
            bounds=end,
            timestamp=10.1,
            left_button_down=False,
        )
        tracker.observe(
            EVENT_SYSTEM_MOVESIZEEND,
            hwnd=42,
            process_id=101,
            process_created=1234,
            bounds=end,
            timestamp=10.2,
            left_button_down=False,
        )
        tracker.observe(
            EVENT_OBJECT_LOCATIONCHANGE,
            hwnd=99,
            process_id=202,
            process_created=5678,
            bounds=end,
            timestamp=10.25,
            left_button_down=False,
        )

        self.assertTrue(hasattr(tracker, "diagnostic_snapshot"))
        diagnostics = tracker.diagnostic_snapshot()

        self.assertEqual(diagnostics["events"]["move_start"], 1)
        self.assertEqual(diagnostics["events"]["location_change"], 2)
        self.assertEqual(diagnostics["events"]["move_end"], 1)
        self.assertEqual(diagnostics["tokens_created"], 0)
        self.assertEqual(diagnostics["last_decision"], "rejected_no_left_button")
        self.assertEqual(diagnostics["active_sessions"], 0)

    def test_accepts_a_left_button_observed_during_an_asynchronous_move(self):
        tracker = MoveTracker(token_ttl_seconds=1.0)
        start = PhysicalRect(0, 0, 800, 600)
        end = PhysicalRect(40, 0, 840, 600)

        tracker.observe(
            EVENT_SYSTEM_MOVESIZESTART,
            hwnd=42,
            process_id=101,
            process_created=1234,
            bounds=start,
            timestamp=10.0,
            left_button_down=False,
        )
        tracker.observe(
            EVENT_OBJECT_LOCATIONCHANGE,
            hwnd=42,
            process_id=101,
            process_created=1234,
            bounds=end,
            timestamp=10.1,
            left_button_down=True,
        )
        tracker.observe(
            EVENT_SYSTEM_MOVESIZEEND,
            hwnd=42,
            process_id=101,
            process_created=1234,
            bounds=end,
            timestamp=10.2,
            left_button_down=False,
        )

        self.assertIsNotNone(tracker.consume_eligible_move(now=10.3))

    def test_retains_completed_left_button_window_move_until_edge_consumes_it(self):
        tracker = MoveTracker(token_ttl_seconds=1.0)
        start = PhysicalRect(0, 0, 800, 600)
        end = PhysicalRect(40, 0, 840, 600)

        tracker.observe(
            EVENT_SYSTEM_MOVESIZESTART,
            hwnd=42,
            process_id=101,
            process_created=1234,
            bounds=start,
            timestamp=10.0,
            left_button_down=True,
        )
        tracker.observe(
            EVENT_OBJECT_LOCATIONCHANGE,
            hwnd=42,
            process_id=101,
            process_created=1234,
            bounds=end,
            timestamp=10.1,
            left_button_down=True,
        )
        tracker.observe(
            EVENT_SYSTEM_MOVESIZEEND,
            hwnd=42,
            process_id=101,
            process_created=1234,
            bounds=end,
            timestamp=10.2,
            left_button_down=False,
        )

        token = tracker.consume_eligible_move(now=10.3)

        self.assertIsNotNone(token)
        self.assertEqual(token.hwnd, 42)
        self.assertEqual(token.bounds, end)
        self.assertIsNone(tracker.consume_eligible_move(now=10.3))

    def test_claims_active_move_before_button_release_and_never_duplicates(self):
        tracker = MoveTracker(token_ttl_seconds=1.0)
        start = PhysicalRect(0, 0, 800, 600)
        end = PhysicalRect(40, 0, 840, 600)
        tracker.observe(EVENT_SYSTEM_MOVESIZESTART, hwnd=42, process_id=101, process_created=1234,
                        bounds=start, timestamp=10.0, left_button_down=True)
        tracker.observe(EVENT_OBJECT_LOCATIONCHANGE, hwnd=42, process_id=101, process_created=1234,
                        bounds=end, timestamp=10.1, left_button_down=True)

        token = tracker.claim_active_move(now=10.2)

        self.assertIsNotNone(token)
        self.assertEqual(token.bounds, end)
        self.assertIsNone(tracker.claim_active_move(now=10.2))
        tracker.observe(EVENT_SYSTEM_MOVESIZEEND, hwnd=42, process_id=101, process_created=1234,
                        bounds=end, timestamp=10.3, left_button_down=False)
        self.assertIsNone(tracker.consume_eligible_move(now=10.4))


class ProbeNativeFramingTests(unittest.TestCase):
    def test_host_diagnostics_report_each_pipeline_boundary_without_urls(self):
        self.assertIsNotNone(ProbeHostDiagnostics)
        diagnostics = ProbeHostDiagnostics()
        diagnostics.record_message("probe_hello")
        diagnostics.record_message("window_metadata")
        diagnostics.record_metadata(accepted=True, window_count=2)
        diagnostics.record_correlation("ambiguous_or_bounds_mismatch")

        message = diagnostics.message(
            {
                "running": True,
                "hooks_installed": 2,
                "raw_callbacks": 7,
                "accepted_callbacks": 5,
                "tracker": {"last_decision": "token_created"},
            }
        )

        self.assertEqual(message["type"], "probe_diagnostics")
        self.assertEqual(message["host"]["messages_received"], 2)
        self.assertEqual(message["host"]["metadata_accepted"], 1)
        self.assertEqual(message["host"]["windows_in_latest_metadata"], 2)
        self.assertEqual(message["host"]["correlations_emitted"], 1)
        self.assertEqual(message["observer"]["raw_callbacks"], 7)
        self.assertNotIn("url", repr(message).lower())

    def test_probe_native_host_has_its_queue_dependency(self):
        self.assertTrue(hasattr(browser_probe, "queue"))

    def test_probe_correlation_reports_only_opaque_raw_match_evidence(self):
        self.assertTrue(hasattr(browser_probe, "correlation_evidence"))

        result = browser_probe.correlation_evidence(
            MoveToken(
                hwnd=42,
                process_id=101,
                process_created=1234,
                bounds=PhysicalRect(100, 50, 900, 650),
                completed_at=10.1,
            ),
            {
                "browser_instance_id": "opaque-instance",
                "windows": [{
                    "window_id": 7,
                    "browser_process_id": 101,
                    "browser_process_created": 1234,
                    "left": 100,
                    "top": 50,
                    "width": 800,
                    "height": 600,
                }],
            },
            received_at=10.0,
            now=10.2,
        )

        self.assertEqual(result["type"], "probe_correlation")
        self.assertEqual(result["status"], "unique_raw_match")
        self.assertEqual(result["matching_window_id"], 7)
        self.assertNotIn("url", repr(result).lower())

    def test_probe_reports_a_unique_geometry_match_when_host_parent_identity_differs(self):
        result = browser_probe.correlation_evidence(
            MoveToken(
                hwnd=42,
                process_id=101,
                process_created=1234,
                bounds=PhysicalRect(100, 50, 900, 650),
                completed_at=10.1,
            ),
            {
                "browser_instance_id": "opaque-instance",
                "windows": [{
                    "window_id": 7,
                    "browser_process_id": 202,
                    "browser_process_created": 5678,
                    "left": 100,
                    "top": 50,
                    "width": 800,
                    "height": 600,
                }],
            },
            received_at=10.0,
            now=10.2,
        )

        self.assertEqual(result["status"], "unique_geometry_match_process_unverified")
        self.assertEqual(result["matching_window_id"], 7)
        self.assertEqual(result["candidates"][0]["process_id"], 202)

    def test_probe_abstains_when_two_geometry_matches_have_unverified_processes(self):
        result = browser_probe.correlation_evidence(
            MoveToken(
                hwnd=42,
                process_id=101,
                process_created=1234,
                bounds=PhysicalRect(100, 50, 900, 650),
                completed_at=10.1,
            ),
            {
                "browser_instance_id": "opaque-instance",
                "windows": [
                    {"window_id": 7, "browser_process_id": 202, "browser_process_created": 5678, "left": 100, "top": 50, "width": 800, "height": 600},
                    {"window_id": 8, "browser_process_id": 303, "browser_process_created": 6789, "left": 100, "top": 50, "width": 800, "height": 600},
                ],
            },
            received_at=10.0,
            now=10.2,
        )

        self.assertEqual(result["status"], "ambiguous_geometry_match_process_unverified")
        self.assertNotIn("matching_window_id", result)

    def test_probe_metadata_store_rejects_url_bearing_messages(self):
        store = ProbeMetadataStore()

        accepted = store.record(
            {
                "browser_instance_id": "opaque-id",
                "windows": [{"window_id": 7, "url": "https://example.test"}],
            },
            received_at=10.0,
        )

        self.assertFalse(accepted)

    def test_probe_consumes_each_native_move_token_once(self):
        self.assertTrue(hasattr(browser_probe, "consume_probe_correlation"))
        store = ProbeMetadataStore()
        store.record(
            {
                "browser_instance_id": "opaque-instance",
                "windows": [{
                    "window_id": 7,
                    "browser_process_id": 101,
                    "browser_process_created": 1234,
                    "left": 100,
                    "top": 50,
                    "width": 800,
                    "height": 600,
                }],
            },
            received_at=10.0,
        )

        class Tracker:
            def __init__(self):
                self.token = MoveToken(
                    hwnd=42,
                    process_id=101,
                    process_created=1234,
                    bounds=PhysicalRect(100, 50, 900, 650),
                    completed_at=10.1,
                )

            def consume_eligible_move(self, *, now):
                token, self.token = self.token, None
                return token

        tracker = Tracker()

        self.assertEqual(
            browser_probe.consume_probe_correlation(store, tracker, now=10.2)["status"],
            "unique_raw_match",
        )
        self.assertIsNone(browser_probe.consume_probe_correlation(store, tracker, now=10.2))

    def test_probe_emits_a_completed_move_without_waiting_for_an_idle_loop(self):
        self.assertTrue(hasattr(browser_probe, "emit_probe_correlation"))
        store = ProbeMetadataStore()
        store.record(
            {
                "browser_instance_id": "opaque-instance",
                "windows": [{
                    "window_id": 7,
                    "browser_process_id": 101,
                    "browser_process_created": 1234,
                    "left": 100,
                    "top": 50,
                    "width": 800,
                    "height": 600,
                }],
            },
            received_at=10.0,
        )

        class Tracker:
            def consume_eligible_move(self, *, now):
                return MoveToken(
                    hwnd=42,
                    process_id=101,
                    process_created=1234,
                    bounds=PhysicalRect(100, 50, 900, 650),
                    completed_at=10.1,
                )

        emitted = []

        self.assertTrue(
            browser_probe.emit_probe_correlation(
                store, Tracker(), emitted.append, now=10.2
            )
        )
        self.assertEqual(emitted[0]["status"], "unique_raw_match")

    def test_probe_script_runs_directly_from_the_repository_root(self):
        root = Path(__file__).resolve().parents[1]

        result = subprocess.run(
            [sys.executable, "scripts/probe_browser_handoff.py", "--help"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("passively observe native move tokens", result.stdout)

    def test_round_trips_a_bounded_url_free_metadata_message(self):
        stream = BytesIO()
        message = {
            "type": "window_metadata",
            "browser_instance_id": "opaque-id",
            "windows": [{"window_id": 7, "left": 0, "top": 0, "width": 800, "height": 600}],
        }

        write_native_message(stream, message)

        self.assertEqual(read_native_message(BytesIO(stream.getvalue())), message)

    def test_rejects_truncated_and_oversized_native_frames_before_json_decoding(self):
        with self.assertRaises(FrameError):
            read_native_message(BytesIO(b"\x01\x00"))
        with self.assertRaises(FrameError):
            read_native_message(BytesIO((65537).to_bytes(4, "little")))

    def test_native_host_manifest_allows_one_exact_extension_origin(self):
        extension_id = "a" * 32

        manifest = native_host_manifest(r"C:\probe-host.exe", extension_id)

        self.assertEqual(manifest["name"], "com.conduit.browser_handoff_probe")
        self.assertEqual(manifest["path"], r"C:\probe-host.exe")
        self.assertEqual(manifest["allowed_origins"], [f"chrome-extension://{extension_id}/"])

    def test_native_host_manifest_rejects_non_chromium_extension_ids(self):
        with self.assertRaises(ValueError):
            native_host_manifest(r"C:\probe-host.exe", "not-an-extension-id")

    def test_native_host_ready_message_carries_process_identity_for_correlation(self):
        message = host_ready_message("opaque-id", 20, 10, 1234)

        self.assertEqual(
            message,
            {
                "type": "probe_host_ready",
                "host_pid": 20,
                "browser_process_id": 10,
                "browser_process_created": 1234,
                "received_instance_id": "opaque-id",
            },
        )

    def test_converts_received_extension_metadata_to_fresh_physical_candidates(self):
        metadata = {
            "browser_instance_id": "opaque-id",
            "windows": [
                {
                    "window_id": 7,
                    "browser_process_id": 101,
                    "browser_process_created": 1234,
                    "focused": True,
                    "left": 100,
                    "top": 50,
                    "width": 800,
                    "height": 600,
                }
            ],
        }

        candidates = candidates_from_metadata(
            metadata,
            received_at=10.0,
            to_physical=lambda item: PhysicalRect(
                item["left"], item["top"], item["left"] + item["width"], item["top"] + item["height"]
            ),
        )

        self.assertEqual(candidates[0].window_id, 7)
        self.assertEqual(candidates[0].observed_at, 10.0)
        self.assertEqual(candidates[0].bounds, PhysicalRect(100, 50, 900, 650))

    def test_metadata_store_replaces_an_instance_snapshot_in_memory(self):
        store = ProbeMetadataStore()
        store.record(
            {
                "browser_instance_id": "opaque-id",
                "windows": [{"window_id": 7, "browser_process_id": 101, "browser_process_created": 1234, "focused": True, "left": 0, "top": 0, "width": 800, "height": 600}],
            },
            received_at=10.0,
        )
        store.record(
            {
                "browser_instance_id": "opaque-id",
                "windows": [{"window_id": 8, "browser_process_id": 101, "browser_process_created": 1234, "focused": True, "left": 10, "top": 0, "width": 800, "height": 600}],
            },
            received_at=10.1,
        )

        candidates = store.candidates(
            to_physical=lambda item: PhysicalRect(item["left"], item["top"], item["left"] + item["width"], item["top"] + item["height"])
        )

        self.assertEqual([candidate.window_id for candidate in candidates], [8])
        self.assertEqual(candidates[0].observed_at, 10.1)

    def test_rejects_resize_and_keyboard_move_sequences(self):
        tracker = MoveTracker(token_ttl_seconds=1.0)
        start = PhysicalRect(0, 0, 800, 600)
        resized = PhysicalRect(0, 0, 820, 600)

        tracker.observe(
            EVENT_SYSTEM_MOVESIZESTART,
            hwnd=42,
            process_id=101,
            process_created=1234,
            bounds=start,
            timestamp=10.0,
            left_button_down=False,
        )
        tracker.observe(
            EVENT_OBJECT_LOCATIONCHANGE,
            hwnd=42,
            process_id=101,
            process_created=1234,
            bounds=resized,
            timestamp=10.1,
            left_button_down=False,
        )
        tracker.observe(
            EVENT_SYSTEM_MOVESIZEEND,
            hwnd=42,
            process_id=101,
            process_created=1234,
            bounds=resized,
            timestamp=10.2,
            left_button_down=False,
        )

        self.assertIsNone(tracker.consume_eligible_move(now=10.3))


if __name__ == "__main__":
    unittest.main()
