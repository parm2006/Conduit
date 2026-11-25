import unittest
from app.display_topology import EDGE_HIT_TOLERANCE, NativeRect, edge_entry_point
from app.input_geometry import (
    expected_entry_side,
    project_safe_viewport_entry,
    viewport_point_matches_entry_side,
)
from app.remote_video import fit_rect


class RemoteEntryProjectionTests(unittest.TestCase):
    def test_downscaled_entry_does_not_round_back_into_return_edge(self):
        rect = NativeRect(0, 0, 1000, 1000)
        for position in ((3, 400), (996, 400), (400, 3), (400, 996)):
            x, y = project_safe_viewport_entry(rect, (0, 0, 400, 400), position)
            mapped = round(x / .4), round(y / .4)
            self.assertGreater(mapped[0], 2)
            self.assertLess(mapped[0], 997)
            self.assertGreater(mapped[1], 2)
            self.assertLess(mapped[1], 997)

    def test_all_four_edges_preserve_entry_side_and_ratio_across_scales_and_origins(self):
        cases = (
            (NativeRect(0, 0, 1920, 1080), (2496, 1664)),
            (NativeRect(0, 0, 2496, 1664), (1920, 1080)),
            (NativeRect(-1920, -1080, 0, 0), (2496, 1664)),
            (NativeRect(0, 0, 3840, 2160), (1920, 1080)),
        )
        for rect, viewport in cases:
            client_w = rect.right - rect.left
            client_h = rect.bottom - rect.top
            projection = fit_rect(viewport, (client_w, client_h))
            dest_x, dest_y, fitted_w, fitted_h = projection
            for side in ("left", "right", "top", "bottom"):
                for ratio in (0.1, 0.35, 0.5, 0.85):
                    entry_pos = edge_entry_point(rect, side, ratio)
                    self.assertEqual(expected_entry_side(rect, entry_pos), side)
                    vx, vy = project_safe_viewport_entry(rect, projection, entry_pos)
                    self.assertTrue(viewport_point_matches_entry_side(projection, (vx, vy), side))
                    # Inverse mapping (matching on_overlay_motion) stays outside EDGE_HIT_TOLERANCE.
                    cx = rect.left + round(((vx - dest_x) / fitted_w) * client_w)
                    cy = rect.top + round(((vy - dest_y) / fitted_h) * client_h)
                    cx = max(rect.left, min(rect.right - 1, cx))
                    cy = max(rect.top, min(rect.bottom - 1, cy))
                    if side == "left":
                        self.assertGreater(cx - rect.left, EDGE_HIT_TOLERANCE)
                        self.assertAlmostEqual((vy - dest_y) / fitted_h, ratio, delta=0.02)
                    elif side == "right":
                        self.assertGreater((rect.right - 1) - cx, EDGE_HIT_TOLERANCE)
                        self.assertAlmostEqual((vy - dest_y) / fitted_h, ratio, delta=0.02)
                    elif side == "top":
                        self.assertGreater(cy - rect.top, EDGE_HIT_TOLERANCE)
                        self.assertAlmostEqual((vx - dest_x) / fitted_w, ratio, delta=0.02)
                    elif side == "bottom":
                        self.assertGreater((rect.bottom - 1) - cy, EDGE_HIT_TOLERANCE)
                        self.assertAlmostEqual((vx - dest_x) / fitted_w, ratio, delta=0.02)
