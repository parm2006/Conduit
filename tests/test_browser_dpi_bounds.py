import unittest
from dataclasses import dataclass

from app.browser_handoff.bounds import DpiBoundsConverter
from app.browser_handoff.window_match import (
    BrowserWindowCandidate,
    NativeWindowObservation,
    PhysicalRect,
    match_window,
)
from app.display_topology import NativeRect


@dataclass(frozen=True)
class MockDisplayRecord:
    stable_id: str
    rect: tuple[int, int, int, int]
    work_rect: tuple[int, int, int, int]
    dpi: int
    primary: bool
    enabled: bool = True


class MockDisplayBackend:
    def __init__(self, records):
        self.records = records

    def snapshot(self):
        return tuple(self.records)


class BrowserDpiBoundsTests(unittest.TestCase):
    def test_single_monitor_100_percent_dpi_maps_one_to_one(self):
        backend = MockDisplayBackend([
            MockDisplayRecord("mon-1", (0, 0, 1920, 1080), (0, 0, 1920, 1040), 96, primary=True),
        ])
        converter = DpiBoundsConverter(backend)
        res = converter.to_physical({"left": 100, "top": 200, "width": 800, "height": 600})
        self.assertEqual(res, NativeRect(100, 200, 900, 800))

    def test_single_monitor_150_percent_dpi_scales_surface_laptop(self):
        backend = MockDisplayBackend([
            MockDisplayRecord("mon-surface", (0, 0, 2496, 1664), (0, 0, 2496, 1592), 144, primary=True),
        ])
        converter = DpiBoundsConverter(backend)
        # 144 DPI / 96 DPI = 1.5x scale
        res = converter.to_physical({"left": 100, "top": 200, "width": 1000, "height": 600})
        self.assertEqual(res, NativeRect(150, 300, 1650, 1200))

    def test_surface_left_edge_drag_negative_coords(self):
        backend = MockDisplayBackend([
            MockDisplayRecord("mon-surface", (0, 0, 2496, 1664), (0, 0, 2496, 1592), 144, primary=True),
        ])
        converter = DpiBoundsConverter(backend)
        # Real coordinates observed on the Surface Laptop during edge drag
        res = converter.to_physical({"left": -606, "top": 475, "width": 2264, "height": 568})
        # -606 * 1.5 = -909
        # 475 * 1.5 = 712.5 -> 712
        # 2264 * 1.5 = 3396 -> -909 + 3396 = 2487
        # 568 * 1.5 = 852 -> 712 + 852 = 1564
        self.assertEqual(res.left, -909)
        self.assertEqual(res.top, 712)
        self.assertEqual(res.right, 2487)
        self.assertEqual(res.bottom, 1564)

    def test_multi_monitor_different_dpis(self):
        backend = MockDisplayBackend([
            # Primary 100% DPI at (0, 0, 1920, 1080)
            MockDisplayRecord("mon-1", (0, 0, 1920, 1080), (0, 0, 1920, 1040), 96, primary=True),
            # Secondary 150% DPI at (1920, 0, 4416, 1664)
            MockDisplayRecord("mon-2", (1920, 0, 4416, 1664), (1920, 0, 4416, 1592), 144, primary=False),
        ])
        converter = DpiBoundsConverter(backend)
        # Window on primary monitor
        on_prim = converter.to_physical({"left": 100, "top": 100, "width": 800, "height": 600})
        self.assertEqual(on_prim, NativeRect(100, 100, 900, 700))

        # Window on secondary monitor (DIP left around 1920 + 100 = 2020)
        on_sec = converter.to_physical({"left": 2020, "top": 100, "width": 1000, "height": 600})
        self.assertGreaterEqual(on_sec.left, 1920)
        self.assertEqual(on_sec.right - on_sec.left, 1500)
        self.assertEqual(on_sec.bottom - on_sec.top, 900)

    def test_fallback_when_no_backend(self):
        converter = DpiBoundsConverter(None)
        res = converter.to_physical({"left": 50, "top": 60, "width": 700, "height": 400})
        self.assertEqual(res, NativeRect(50, 60, 750, 460))

    def test_match_window_matches_dpi_converted_candidate(self):
        # Native bounds from Windows GetWindowRect on Surface Laptop (150% DPI)
        native = NativeWindowObservation(
            hwnd=1234,
            process_id=5678,
            process_created=9999,
            bounds=PhysicalRect(-909, 712, 2487, 1564),
            observed_at=10.0,
        )
        # Candidate bounds produced by DpiBoundsConverter: (-909, 712, 2487, 1564)
        converted_candidate = BrowserWindowCandidate(
            browser_instance_id="inst-1",
            window_id=101,
            process_id=5678,
            process_created=9999,
            bounds=PhysicalRect(-909, 712, 2487, 1564),
            focused=True,
            observed_at=10.0,
        )
        other_candidate = BrowserWindowCandidate(
            browser_instance_id="inst-1",
            window_id=102,
            process_id=5678,
            process_created=9999,
            bounds=PhysicalRect(0, 0, 1000, 800),
            focused=False,
            observed_at=10.0,
        )
        matched = match_window(native, [converted_candidate, other_candidate], now=10.1)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.window_id, 101)
