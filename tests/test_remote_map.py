import importlib.util
import math
import unittest


class RemoteMapTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('app.remote_map'))
        from app import remote_map
        return remote_map

    def test_bounds_and_hide_region_follow_primary_width(self):
        module = self.module()
        self.assertEqual(module.map_bounds((0, 0, 1920, 1080)), (0, 0, 192, 192))
        self.assertTrue(module.cursor_near_map((250, 96), (0, 0, 192, 192)))
        self.assertFalse(module.cursor_near_map((270, 96), (0, 0, 192, 192)))

    def test_squircle_uses_requested_exponent(self):
        module = self.module()
        points = module.squircle_points(-1, -1, 1, 1)
        for x, y in zip(points[::2], points[1::2]):
            self.assertAlmostEqual(abs(x)**3.888 + abs(y)**3.888, 1, places=6)

    def test_map_alpha_is_bounded_and_has_no_rectangular_background(self):
        module = self.module()
        self.assertEqual(module.MAP_OPACITY, 179)
        cells = [('server', 's', 0, 0, '#8F99A8'), ('client', 'c', 1, 0, '#3B82F6')]
        rendered = module.render_map(cells, ('client', 'c'), 192)
        self.assertEqual(rendered.size, (192, 192))
        self.assertLessEqual(rendered.getchannel('A').getextrema()[1], 179)
        self.assertEqual(rendered.getpixel((0, 0))[3], 0)
        # Server is grey and client is blue; inactive tile retains full visible saturation.
        self.assertEqual(rendered.getpixel((60, 96))[:3], (143, 153, 168))
        self.assertEqual(rendered.getpixel((130, 96))[:3], (59, 130, 246))
