import unittest
from unittest.mock import MagicMock
from pynput.keyboard import Key, KeyCode

from app.global_hotkey import GlobalHotkeyMonitor


class GlobalHotkeyMonitorTests(unittest.TestCase):
    def test_detects_emergency_exit_hotkey(self):
        exit_called = []
        monitor = GlobalHotkeyMonitor(
            on_emergency_exit=lambda: exit_called.append(True)
        )

        monitor._on_press(Key.ctrl_l)
        monitor._on_press(Key.alt_l)
        monitor._on_press(Key.shift_l)
        monitor._on_press(Key.esc)

        self.assertEqual(len(exit_called), 1)

    def test_detects_reload_connection_hotkey(self):
        reload_called = []
        monitor = GlobalHotkeyMonitor(
            on_reload_connection=lambda: reload_called.append(True)
        )

        monitor._on_press(Key.ctrl)
        monitor._on_press(Key.alt)
        monitor._on_press(Key.shift)
        monitor._on_press(KeyCode.from_char("r"))

        self.assertEqual(len(reload_called), 1)

    def test_detects_toggle_daemon_hotkey(self):
        daemon_called = []
        monitor = GlobalHotkeyMonitor(
            on_toggle_daemon=lambda: daemon_called.append(True)
        )

        monitor._on_press(Key.ctrl)
        monitor._on_press(Key.alt)
        monitor._on_press(Key.shift)
        monitor._on_press(KeyCode.from_char("b"))

        self.assertEqual(len(daemon_called), 1)


if __name__ == "__main__":
    unittest.main()

