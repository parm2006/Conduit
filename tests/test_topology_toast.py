import unittest

from app.display_topology import Display, MachineDisplayGroup, NativeRect
from app.topology_toast import topology_toast_rect, topology_toast_view


class TopologyToastTests(unittest.TestCase):
    @staticmethod
    def _group():
        return MachineDisplayGroup(
            machine_id="client",
            windows_name="ParthSurface",
            displays=(
                Display(
                    display_id="primary",
                    rect=NativeRect(-1920, 0, 0, 1080),
                    work_rect=NativeRect(-1920, 0, 0, 1040),
                    dpi_percent=125,
                    orientation=0,
                    primary=True,
                ),
                Display(
                    display_id="portrait",
                    rect=NativeRect(0, 0, 1440, 2560),
                    work_rect=NativeRect(0, 0, 1440, 2520),
                    dpi_percent=150,
                    orientation=90,
                    primary=False,
                ),
            ),
        )

    def test_view_uses_entire_client_color_and_contains_machine_display_info(self):
        view = topology_toast_view(self._group(), "#3B82F6", "connected")

        self.assertEqual(view.color, "#3B82F6")
        self.assertEqual(view.title, "ParthSurface")
        self.assertEqual(
            view.details,
            "2 displays · 1920×1080 + 1440×2560 · connected",
        )
        self.assertIsNone(view.hide_after_ms)

    def test_toast_targets_the_clients_primary_work_area(self):
        rect = topology_toast_rect(self._group(), window_size=(360, 104))

        self.assertEqual(rect, (-380, 916, -20, 1020))


if __name__ == "__main__":
    unittest.main()
