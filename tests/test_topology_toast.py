import unittest
from unittest.mock import patch

from app.display_topology import Display, MachineDisplayGroup, NativeRect
from app.file_transfer.toast import TOAST_HEIGHT, TOAST_WIDTH
from app.topology_toast import (
    DisplayChangeWarningToast,
    TopologyIdentificationToast,
    connection_lost_warning_view,
    display_change_warning_view,
    topology_toast_rect,
    topology_toast_view,
)


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

    def test_display_change_warning_is_normal_sized_and_explains_apply(self):
        view = display_change_warning_view(self._group())

        self.assertEqual(view.title, "ParthSurface displays changed")
        self.assertEqual(
            view.details,
            "2 displays detected · Apply to rebuild mouse routing",
        )
        self.assertEqual(view.color, "#D97706")
        self.assertEqual(view.hide_after_ms, 5000)
        self.assertEqual(
            DisplayChangeWarningToast.WINDOW_SIZE,
            (TOAST_WIDTH, TOAST_HEIGHT),
        )

    def test_connection_lost_warning_names_machine_without_network_details(self):
        view = connection_lost_warning_view("ParthSurface")

        self.assertEqual(view.title, "ParthSurface disconnected")
        self.assertEqual(
            view.details,
            "Removed from the draft · Active routing stays unchanged",
        )
        self.assertEqual(view.color, "#D97706")
        self.assertEqual(view.hide_after_ms, 5000)

    def test_fixed_size_toasts_wrap_monitor_details(self):
        self.assertLessEqual(
            TopologyIdentificationToast.DETAILS_WRAP_LENGTH,
            TOAST_WIDTH - 24,
        )
        self.assertEqual(
            DisplayChangeWarningToast.DETAILS_WRAP_LENGTH,
            TopologyIdentificationToast.DETAILS_WRAP_LENGTH,
        )

    def test_identification_toast_never_adopts_transient_tk_window_dimensions(self):
        class Widget:
            def configure(self, **kwargs):
                pass

        class Window:
            def __init__(self):
                self.geometries = []

            def update_idletasks(self):
                pass

            def winfo_width(self):
                return 3840

            def winfo_height(self):
                return 2160

            def winfo_id(self):
                return 1

            def geometry(self, value):
                self.geometries.append(value)

            def deiconify(self):
                pass

            def lift(self):
                pass

        toast = TopologyIdentificationToast.__new__(TopologyIdentificationToast)
        toast.window = Window()
        toast.body = Widget()
        toast.title = Widget()
        toast.details = Widget()

        with patch("app.input_geometry.place_windows_window_in_work_area"):
            toast.show(self._group(), "#3B82F6")

        self.assertTrue(
            toast.window.geometries[-1].startswith(
                f"{TOAST_WIDTH}x{TOAST_HEIGHT}"
            )
        )


if __name__ == "__main__":
    unittest.main()
