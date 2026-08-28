import unittest
from unittest.mock import MagicMock, patch
from pynput.keyboard import Key, KeyCode

from app.global_hotkey import GlobalHotkeyMonitor


class GlobalHotkeyMonitorTests(unittest.TestCase):
    @staticmethod
    def _tap_space(monitor):
        monitor._on_press(Key.space)
        monitor._on_release(Key.space)

    def test_monitor_waits_until_keyboard_hook_is_ready(self):
        events = []

        class Listener:
            daemon = False

            def __init__(self, **kwargs):
                events.append(("listener", tuple(sorted(kwargs))))

            def start(self):
                events.append("listener_started")

            def wait(self):
                events.append("listener_ready")

        monitor = GlobalHotkeyMonitor()
        with patch("app.global_hotkey.KeyboardListener", Listener):
            monitor.start()

        self.assertEqual(
            events,
            [
                ("listener", ("on_press", "on_release")),
                "listener_started",
                "listener_ready",
            ],
        )

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

    def test_ctrl_two_distinct_space_taps_returns_to_server(self):
        returned = []
        now = [10.0]
        monitor = GlobalHotkeyMonitor(
            on_return_to_server=lambda: returned.append(True),
            clock=lambda: now[0],
        )

        monitor._on_press(Key.ctrl_l)
        self._tap_space(monitor)
        now[0] = 10.75
        monitor._on_press(Key.space)

        self.assertEqual(returned, [True])

    def test_ctrl_space_auto_repeat_is_not_a_second_tap(self):
        returned = []
        monitor = GlobalHotkeyMonitor(
            on_return_to_server=lambda: returned.append(True),
            clock=lambda: 10.0,
        )

        monitor._on_press(Key.ctrl_r)
        monitor._on_press(Key.space)
        monitor._on_press(Key.space)

        self.assertEqual(returned, [])

    def test_ctrl_space_after_deadline_starts_a_new_sequence(self):
        returned = []
        now = [10.0]
        monitor = GlobalHotkeyMonitor(
            on_return_to_server=lambda: returned.append(True),
            clock=lambda: now[0],
            return_interval=0.75,
        )

        monitor._on_press(Key.ctrl)
        self._tap_space(monitor)
        now[0] = 10.751
        self._tap_space(monitor)
        now[0] = 11.0
        monitor._on_press(Key.space)

        self.assertEqual(returned, [True])

    def test_ctrl_release_cancels_partial_return_sequence(self):
        returned = []
        monitor = GlobalHotkeyMonitor(
            on_return_to_server=lambda: returned.append(True),
        )

        monitor._on_press(Key.ctrl_l)
        self._tap_space(monitor)
        monitor._on_release(Key.ctrl_l)
        monitor._on_press(Key.ctrl_l)
        self._tap_space(monitor)

        self.assertEqual(returned, [])

    def test_unrelated_key_cancels_partial_return_sequence(self):
        returned = []
        monitor = GlobalHotkeyMonitor(
            on_return_to_server=lambda: returned.append(True),
        )

        monitor._on_press(Key.ctrl_l)
        self._tap_space(monitor)
        monitor._on_press(KeyCode.from_char("x"))
        monitor._on_release(KeyCode.from_char("x"))
        self._tap_space(monitor)

        self.assertEqual(returned, [])

    def test_stop_cancels_partial_return_sequence(self):
        returned = []
        monitor = GlobalHotkeyMonitor(
            on_return_to_server=lambda: returned.append(True),
        )

        monitor._on_press(Key.ctrl_l)
        self._tap_space(monitor)
        monitor.stop()
        monitor._on_press(Key.ctrl_l)
        self._tap_space(monitor)

        self.assertEqual(returned, [])


if __name__ == "__main__":
    unittest.main()
