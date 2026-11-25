"""Software pointer fallback using fake Win32 windows; no desktop access."""
import importlib
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class FakeDesktop:
    def __init__(self):
        self.flags = 0
        self.position = (100, 200)
        self.window = None
        self.calls = []
        self.painted_at = []
        self.changed = threading.Condition()

    def record(self, name):
        self.calls.append((name, threading.get_ident()))

    def GetCursorInfo(self):
        self.record('info')
        if self.flags is None:
            raise OSError('input desktop unavailable')
        return self.flags, 123, self.position

    def GetCursorPos(self):
        self.record('position')
        return self.position

    def PumpWaitingMessages(self):
        self.record('pump')

    def IsWindowVisible(self, hwnd):
        return bool(self.window and self.window.hwnd == hwnd and self.window.visible)

    def GetWindowRect(self, hwnd):
        if not self.window or self.window.hwnd != hwnd:
            raise OSError('invalid window')
        x, y = self.window.position
        return x, y, x + self.window.image.width, y + self.window.image.height

    def create_window(self):
        desktop = self
        self.record('create')

        class Window:
            hwnd = 72
            visible = False
            position = (0, 0)
            image = None

            def update(self, image, position):
                with desktop.changed:
                    desktop.record('update')
                    desktop.painted_at.append(time.monotonic())
                    self.image, self.position = image, position
                    desktop.changed.notify_all()

            def show(self):
                with desktop.changed:
                    desktop.record('show')
                    self.visible = True
                    desktop.changed.notify_all()

            def hide(self):
                with desktop.changed:
                    desktop.record('hide')
                    self.visible = False
                    desktop.changed.notify_all()

            def close(self):
                with desktop.changed:
                    desktop.record('close')
                    self.visible = False
                    self.hwnd = None
                    desktop.changed.notify_all()

        self.window = Window()
        return self.window

    def wait_for(self, predicate):
        with self.changed:
            return self.changed.wait_for(predicate, timeout=1)


class WindowsCursorOverlayTests(unittest.TestCase):
    def setUp(self):
        # Import after patching would still exercise the public module import.
        self.module = importlib.import_module('app.windows_cursor_overlay')
        self.desktop = FakeDesktop()
        self.win32 = patch.dict(sys.modules, {'win32gui': self.desktop})
        self.win32.start()
        self.addCleanup(self.win32.stop)
        self.factory = patch.object(self.module, 'WindowsMapOverlay', self.desktop.create_window)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.overlay = self.module.WindowsCursorOverlay()
        self.addCleanup(self.overlay.stop)

    def test_hidden_and_suppressed_flags_need_fallback(self):
        for flags, expected in ((0, True), (1, False), (2, True), (3, True)):
            self.assertIs(self.module.system_cursor_hidden(flags), expected)

    def test_optional_startup_wait_cannot_consume_handoff_ack_budget(self):
        release = threading.Event()
        with patch.object(self.overlay, '_run', side_effect=lambda: release.wait(1)):
            try:
                with patch.object(self.overlay._ready, 'wait', return_value=False) as wait:
                    self.assertFalse(self.overlay.start())
                    wait.assert_called_once_with(.1)
                    self.assertTrue(self.overlay._stop.is_set())
            finally:
                release.set()
                self.overlay.stop()

    def test_hidden_cursor_draws_on_worker_and_follows_logical_position(self):
        self.assertTrue(self.overlay.start())
        self.assertTrue(self.desktop.wait_for(lambda: self.desktop.window.visible))
        self.assertEqual(self.overlay.hwnd, 72)
        self.assertTrue(self.overlay.visible_in_rect(SimpleNamespace(left=0, top=0, right=500, bottom=500)))
        self.assertFalse(self.overlay.visible_in_rect((500, 500, 600, 600)))
        self.desktop.position = (300, 400)
        self.assertTrue(self.desktop.wait_for(lambda: self.desktop.window.position == (300, 400)))
        self.assertTrue(self.overlay.stop())
        self.assertEqual(self.overlay.hwnd, 0)
        self.assertFalse(self.overlay.visible_in_rect((0, 0, 500, 500)))
        worker_threads = {thread for _name, thread in self.desktop.calls}
        self.assertEqual(len(worker_threads), 1)
        self.assertNotIn(threading.get_ident(), worker_threads)
        self.assertIn('close', [name for name, _thread in self.desktop.calls])

    def test_typing_and_hotplug_visibility_changes_never_duplicate_system_cursor(self):
        self.desktop.flags = 1
        self.assertTrue(self.overlay.start())
        self.assertFalse(self.desktop.window.visible)
        for flags, visible in ((0, True), (1, False), (2, True), (1, False)):
            self.desktop.flags = flags
            self.assertTrue(self.desktop.wait_for(lambda: self.desktop.window.visible is visible))

    def test_stationary_fallback_refreshes_without_repainting_at_poll_frequency(self):
        self.assertTrue(self.overlay.start())
        self.assertTrue(self.desktop.wait_for(lambda: self.desktop.window.visible))
        self.assertTrue(self.desktop.wait_for(
            lambda: [name for name, _thread in self.desktop.calls].count('update') >= 2))
        self.assertGreaterEqual(self.desktop.painted_at[1] - self.desktop.painted_at[0], .20)

    def test_unknown_cursor_state_hides_fallback_then_recovers(self):
        self.assertTrue(self.overlay.start())
        self.assertTrue(self.desktop.wait_for(lambda: self.desktop.window.visible))
        self.desktop.flags = None
        self.assertTrue(self.desktop.wait_for(lambda: not self.desktop.window.visible))
        self.desktop.flags = 0
        self.assertTrue(self.desktop.wait_for(lambda: self.desktop.window.visible))

    def test_start_and_stop_are_idempotent_and_can_restart(self):
        self.assertTrue(self.overlay.start())
        self.assertTrue(self.overlay.start())
        self.assertEqual([name for name, _thread in self.desktop.calls].count('create'), 1)
        self.assertTrue(self.overlay.stop())
        self.assertTrue(self.overlay.stop())
        self.assertTrue(self.overlay.start())
        self.assertEqual([name for name, _thread in self.desktop.calls].count('create'), 2)

    def test_window_creation_failure_is_bounded_and_returns_no_handle(self):
        with patch.object(self.module, 'WindowsMapOverlay', side_effect=OSError('window unavailable')), \
                self.assertLogs('app.windows_cursor_overlay', level='WARNING'):
            self.assertFalse(self.overlay.start())
        self.assertEqual(self.overlay.hwnd, 0)
        self.assertTrue(self.overlay.stop())

    def test_shared_window_query_rejects_hidden_invalid_and_nonintersecting_windows(self):
        self.assertTrue(self.overlay.start())
        self.assertTrue(self.desktop.wait_for(lambda: self.desktop.window.visible))
        query = self.module.overlay_visible_in_rect
        self.assertTrue(query(72, (99, 199, 101, 201)))
        self.assertFalse(query(72, (-200, -200, 0, 0)))
        self.assertFalse(query(0, (0, 0, 500, 500)))
        self.assertFalse(query(99, (0, 0, 500, 500)))
        self.desktop.flags = 1
        self.assertTrue(self.desktop.wait_for(lambda: not self.desktop.window.visible))
        self.assertFalse(query(72, (0, 0, 500, 500)))


if __name__ == '__main__':
    unittest.main()
