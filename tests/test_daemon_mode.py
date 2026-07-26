import unittest
from unittest.mock import MagicMock

from app.gui import DeskFlowGUI


class MockDeskFlowGUI(DeskFlowGUI):
    def __init__(self):
        self._window_state = "normal"
        self.server = MagicMock()
        self.client = MagicMock()
        self.overlay = None
        self.withdraw_calls = 0
        self.deiconify_calls = 0
        self.lift_calls = 0
        self.focus_force_calls = 0

    def after(self, delay, func):
        func()

    def state(self):
        return self._window_state

    def withdraw(self):
        self._window_state = "withdrawn"
        self.withdraw_calls += 1

    def deiconify(self):
        self._window_state = "normal"
        self.deiconify_calls += 1

    def lift(self):
        self.lift_calls += 1

    def focus_force(self):
        self.focus_force_calls += 1

    def hide_overlay(self):
        pass


class DaemonModeTests(unittest.TestCase):
    def setUp(self):
        self.gui = MockDeskFlowGUI()

    def test_toggle_daemon_mode_hides_and_restores(self):
        # Initial state is visible ("normal")
        self.assertEqual(self.gui.state(), "normal")

        # First toggle hides GUI
        self.gui.toggle_daemon_mode()
        self.assertEqual(self.gui._window_state, "withdrawn")
        self.assertEqual(self.gui.withdraw_calls, 1)

        # Second toggle restores GUI
        self.gui.toggle_daemon_mode()
        self.assertEqual(self.gui._window_state, "normal")
        self.assertEqual(self.gui.deiconify_calls, 1)
        self.assertEqual(self.gui.lift_calls, 1)
        self.assertEqual(self.gui.focus_force_calls, 1)

    def test_emergency_exit_restores_visibility_when_hidden(self):
        # Set to hidden mode
        self.gui._window_state = "withdrawn"

        self.gui._on_emergency_exit_global()

        # Must disconnect and restore window visibility
        self.gui.server._on_emergency_exit.assert_called_once()
        self.gui.client.disconnect.assert_called_once()
        self.assertEqual(self.gui._window_state, "normal")
        self.assertEqual(self.gui.deiconify_calls, 1)

    def test_reload_connection_maintains_invisible_state(self):
        # Set to hidden mode
        self.gui._window_state = "withdrawn"

        self.gui._on_reload_connection_global()

        # Must reload connection without deiconifying
        self.gui.server._reload_connection.assert_called_once()
        self.assertEqual(self.gui._window_state, "withdrawn")
        self.assertEqual(self.gui.deiconify_calls, 0)


if __name__ == "__main__":
    unittest.main()
