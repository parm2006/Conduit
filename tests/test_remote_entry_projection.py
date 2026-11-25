import unittest
from app.display_topology import NativeRect


class RemoteEntryProjectionTests(unittest.TestCase):
    def test_downscaled_entry_does_not_round_back_into_return_edge(self):
        from app.input_geometry import project_safe_viewport_entry
        rect = NativeRect(0, 0, 1000, 1000)
        for position in ((3, 400), (996, 400), (400, 3), (400, 996)):
            x, y = project_safe_viewport_entry(rect, (0, 0, 400, 400), position)
            mapped = round(x / .4), round(y / .4)
            self.assertGreater(mapped[0], 2)
            self.assertLess(mapped[0], 997)
            self.assertGreater(mapped[1], 2)
            self.assertLess(mapped[1], 997)
