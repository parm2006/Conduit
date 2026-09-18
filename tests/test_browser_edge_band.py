import unittest

from app.browser_handoff.edge_band import (
    EdgeRegion,
    activation_band,
    configured_edge_region,
    window_in_activation_band,
)
from app.display_topology import NativeRect


class BrowserEdgeBandTests(unittest.TestCase):
    def setUp(self):
        self.display = NativeRect(100, 200, 2100, 1200)

    def test_full_edge_band_is_exactly_five_percent_on_all_sides(self):
        expected = {
            "left": NativeRect(100, 200, 200, 1200),
            "right": NativeRect(2000, 200, 2100, 1200),
            "top": NativeRect(100, 200, 2100, 250),
            "bottom": NativeRect(100, 1150, 2100, 1200),
        }

        for side, rectangle in expected.items():
            with self.subTest(side=side):
                region = configured_edge_region(self.display, side)
                self.assertEqual(activation_band(self.display, region), rectangle)

    def test_window_edge_on_band_boundaries_qualifies(self):
        cases = (
            ("left", NativeRect(100, 400, 900, 800)),
            ("right", NativeRect(1300, 400, 2100, 800)),
            ("top", NativeRect(700, 200, 1500, 900)),
            ("bottom", NativeRect(700, 500, 1500, 1200)),
        )

        for side, window in cases:
            with self.subTest(side=side):
                region = configured_edge_region(self.display, side)
                self.assertTrue(window_in_activation_band(window, self.display, region))

    def test_window_outside_band_does_not_qualify(self):
        cases = (
            ("left", NativeRect(201, 400, 900, 800)),
            ("right", NativeRect(1300, 400, 1999, 800)),
            ("top", NativeRect(700, 251, 1500, 900)),
            ("bottom", NativeRect(700, 500, 1500, 1149)),
        )

        for side, window in cases:
            with self.subTest(side=side):
                region = configured_edge_region(self.display, side)
                self.assertFalse(window_in_activation_band(window, self.display, region))

    def test_segment_requires_positive_parallel_overlap(self):
        region = configured_edge_region(self.display, "right", start=500, end=900)
        self.assertTrue(window_in_activation_band(NativeRect(2000, 600, 2100, 700), self.display, region))
        self.assertFalse(window_in_activation_band(NativeRect(2000, 900, 2100, 1000), self.display, region))

    def test_unconfigured_or_non_overlapping_regions_fail_closed(self):
        window = NativeRect(2000, 400, 2100, 800)
        self.assertFalse(window_in_activation_band(window, self.display, None))
        self.assertFalse(
            window_in_activation_band(
                window,
                self.display,
                EdgeRegion("right", 1300, 1400),
            )
        )

    def test_invalid_region_and_side_are_rejected(self):
        with self.assertRaises(ValueError):
            EdgeRegion("diagonal", 0, 10)
        with self.assertRaises(ValueError):
            configured_edge_region(self.display, "left", start=200, end=1300)
        with self.assertRaises(ValueError):
            configured_edge_region(self.display, "left", start=100, end=200,)

    def test_corner_touch_without_parallel_overlap_does_not_qualify(self):
        region = configured_edge_region(self.display, "left", start=500, end=900)
        self.assertFalse(
            window_in_activation_band(
                NativeRect(100, 900, 400, 1000),
                self.display,
                region,
            )
        )


if __name__ == "__main__":
    unittest.main()
