import sys
import unittest


@unittest.skipUnless(sys.platform == 'win32', 'Windows native overlay')
class WindowsRemoteTests(unittest.TestCase):
    def test_map_window_is_click_through_nonactivating_and_accepts_alpha_bitmap(self):
        import win32con
        import win32gui
        from app.remote_map import render_map
        from app.windows_map_overlay import WindowsMapOverlay
        overlay = WindowsMapOverlay()
        try:
            image = render_map([('server', 's', 0, 0, '#8F99A8')], ('server', 's'), 96)
            overlay.update(image, (0, 0))
            style = win32gui.GetWindowLong(overlay.hwnd, win32con.GWL_EXSTYLE)
            for flag in (win32con.WS_EX_TRANSPARENT, win32con.WS_EX_NOACTIVATE,
                         win32con.WS_EX_LAYERED, win32con.WS_EX_TOPMOST):
                self.assertTrue(style & flag)
            self.assertEqual(win32gui.GetWindowRect(overlay.hwnd), (0, 0, 96, 96))
        finally:
            overlay.close()

    def test_capture_returns_a_single_display_rectangle_and_cursor(self):
        from app.display_topology import NativeRect
        from app.windows_capture import capture_monitor
        image, cursor = capture_monitor(NativeRect(0, 0, 32, 24))
        self.assertEqual(image.size, (32, 24))
        self.assertEqual(image.mode, 'RGB')
        self.assertEqual(len(cursor), 2)

