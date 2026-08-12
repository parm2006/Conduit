import unittest
from unittest.mock import MagicMock

from app.gui import ConduitGUI


class MockConduitGUI(ConduitGUI):
    def __init__(self):
        self._window_state = "normal"
        self._is_reloading = False
        self.server = MagicMock()
        self.client = MagicMock()
        self.server_stop_btn = MagicMock()
        self.server_start_btn = MagicMock()
        self.server_port_entry = MagicMock()
        self.client_stop_btn = MagicMock()
        self.client_start_btn = MagicMock()
        self.client_connect_btn = MagicMock()
        self.client_disconnect_btn = MagicMock()
        self.status_text = MagicMock()
        self.overlay = None
        self.withdraw_calls = 0
        self.deiconify_calls = 0
        self.lift_calls = 0
        self.focus_force_calls = 0
        self.close_calls = 0

    def after(self, delay, func):
        if delay == 0:
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

    def on_close(self):
        self.close_calls += 1

    def _set_status(self, message, color="gray", white_text=None, show_ip=None):
        pass


class DaemonModeTests(unittest.TestCase):
    def setUp(self):
        self.gui = MockConduitGUI()

    def test_toggle_daemon_mode_hides_and_restores_locally(self):
        # Initial state is visible ("normal")
        self.assertEqual(self.gui.state(), "normal")

        # First toggle hides GUI and sends sync
        self.gui.toggle_daemon_mode()
        self.assertEqual(self.gui._window_state, "withdrawn")
        self.assertEqual(self.gui.withdraw_calls, 1)

        # Second toggle restores GUI and sends sync
        self.gui.toggle_daemon_mode()
        self.assertEqual(self.gui._window_state, "normal")
        self.assertEqual(self.gui.deiconify_calls, 1)
        self.assertEqual(self.gui.lift_calls, 1)
        self.assertEqual(self.gui.focus_force_calls, 1)

    def test_toggle_daemon_mode_sends_network_sync_message(self):
        self.gui.server.control_connected = True
        self.gui.server.control_network = MagicMock()

        self.gui.toggle_daemon_mode()

        self.gui.server.control_network.send_message.assert_called_once_with(
            {'type': 'set_daemon_mode', 'hidden': True}
        )

    def test_remote_daemon_mode_sync_updates_local_window_state(self):
        # Hidden message received from peer
        self.gui._on_remote_daemon_mode({'hidden': True})
        self.assertEqual(self.gui._window_state, "withdrawn")

        # Unhidden message received from peer
        self.gui._on_remote_daemon_mode({'hidden': False})
        self.assertEqual(self.gui._window_state, "normal")

    def test_emergency_exit_notifies_server_peer_then_closes_local_gui(self):
        self.gui.client = None
        self.gui.server.control_connected = True
        self.gui._on_emergency_exit_global()

        self.gui.server.prepare_app_shutdown.assert_called_once_with()
        self.gui.server.control_network.send_message.assert_called_once_with(
            {'type': 'shutdown_app'}
        )
        self.assertEqual(self.gui.close_calls, 1)

    def test_emergency_exit_notifies_client_peer_then_closes_local_gui(self):
        self.gui.server = None
        self.gui.client.control_connected = True

        self.gui._on_emergency_exit_global()

        self.gui.client.prepare_app_shutdown.assert_called_once_with()
        self.gui.client.control_network.send_message.assert_called_once_with(
            {'type': 'shutdown_app'}
        )
        self.assertEqual(self.gui.close_calls, 1)

    def test_remote_shutdown_closes_without_echoing(self):
        self.gui.server.control_connected = True

        self.gui._on_remote_app_shutdown({})

        self.gui.server.control_network.send_message.assert_not_called()
        self.assertEqual(self.gui.close_calls, 1)

    def test_duplicate_shutdown_requests_close_once(self):
        self.gui.server.control_connected = True

        self.gui._on_emergency_exit_global()
        self.gui._on_emergency_exit_global()
        self.gui._on_remote_app_shutdown({})

        self.gui.server.control_network.send_message.assert_called_once_with(
            {'type': 'shutdown_app'}
        )
        self.assertEqual(self.gui.close_calls, 1)

    def test_emergency_exit_without_peer_closes_local_gui(self):
        self.gui.server = None
        self.gui.client = None

        self.gui._on_emergency_exit_global()

        self.assertEqual(self.gui.close_calls, 1)

    def test_reload_connection_maintains_invisible_state(self):
        self.gui._window_state = "withdrawn"

        self.gui._on_reload_connection_global()

        self.gui.server._reload_connection.assert_called_once()
        self.assertEqual(self.gui._window_state, "withdrawn")
        self.assertEqual(self.gui.deiconify_calls, 0)

    def test_reload_connection_prevents_disconnect_from_unhiding_window(self):
        self.gui._window_state = "withdrawn"
        self.gui._on_reload_connection_global()

        # Disconnect events that happen as a result of reload socket reset
        self.gui._on_server_client_disconnected({})
        self.gui._finish_client_disconnect(self.gui.client)
        self.gui._on_disconnect_notice({'reason': 'reload_connection'})

        self.assertEqual(self.gui._window_state, "withdrawn")
        self.assertEqual(self.gui.deiconify_calls, 0)

    def test_disconnect_restores_window_visibility(self):
        self.gui._window_state = "withdrawn"
        server_mock = MagicMock()
        server_mock.control_connected = True
        server_mock.control_network = MagicMock()
        self.gui.server = server_mock

        self.gui.stop_server()

        server_mock.control_network.send_message.assert_called_once_with(
            {'type': 'disconnect_notice', 'reason': 'server_stopping'}
        )
        self.assertEqual(self.gui._window_state, "normal")

    def test_disconnect_notice_received_from_peer_restores_visibility(self):
        self.gui._window_state = "withdrawn"

        self.gui._on_disconnect_notice({'reason': 'peer_disconnected'})

        self.assertEqual(self.gui._window_state, "normal")


if __name__ == "__main__":
    unittest.main()
