"""Unit tests for Ctrl+W window close and extension-free KVM fallback."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

from app.browser_handoff.coordinator import BrowserHandoffCoordinator, CorrelationTask
from app.browser_handoff.desktop import BrowserHandoffDesktop
from app.browser_handoff.windows_drag import MoveTracker, MoveToken, _MoveSession, PhysicalRect
from app.display_topology import NativeRect


class TestBrowserCtrlWClose(unittest.TestCase):
    def test_move_session_records_close_requested(self):
        tracker = MoveTracker()
        rect = PhysicalRect(100, 100, 500, 400)
        # Start move
        tracker.observe(
            0x000A,  # EVENT_SYSTEM_MOVESIZESTART
            hwnd=12345,
            process_id=999,
            process_created=1000,
            bounds=rect,
            timestamp=1.0,
            left_button_down=True,
            browser_move=True,
            close_requested=False,
        )
        # Location change with W down
        tracker.observe(
            0x800B,  # EVENT_OBJECT_LOCATIONCHANGE
            hwnd=12345,
            process_id=999,
            process_created=1000,
            bounds=PhysicalRect(110, 100, 510, 400),
            timestamp=1.1,
            left_button_down=True,
            close_requested=True,
        )
        token = tracker.claim_active_move(now=1.2, browser_only=True)
        self.assertIsNotNone(token)
        self.assertTrue(token.close_requested)

    def test_coordinator_closes_source_window_when_requested(self):
        desktop = Mock()
        desktop.has_connections = True
        desktop.close_window.return_value = True

        coordinator = BrowserHandoffCoordinator(
            desktop=desktop,
            move_tracker=Mock(),
            move_diagnostics=lambda: {},
            to_physical=lambda w: None,
            send_candidate=lambda c: True,
        )

        task = CorrelationTask(
            gesture_id="gesture1",
            token=MoveToken(12345, 999, 1000, PhysicalRect(0, 0, 100, 100), 1.0, close_requested=True),
            source_display_id="display1",
            source_side="left",
            topology_version=1,
            expires_at=10.0,
            close_source=True,
        )
        task.matched = SimpleNamespace(
            browser_instance_id="inst1",
            window_id=42,
            bridge_epoch="epoch1",
        )

        coordinator._close_source_window(task)
        desktop.close_window.assert_called_once_with("inst1", 42, expected_epoch="epoch1")
        self.assertTrue(task.stages.get("source_window_closed"))

    def test_desktop_has_connections_reflects_active_connections(self):
        desktop = BrowserHandoffDesktop(start_bridge=False)
        self.assertFalse(desktop.has_connections)

        connection = SimpleNamespace(
            hello=SimpleNamespace(browser_instance_id="inst1"),
            close=Mock(),
        )
        desktop.on_connected(connection)
        self.assertTrue(desktop.has_connections)

        desktop.on_disconnected(connection)
        self.assertFalse(desktop.has_connections)

    def test_desktop_close_window_sends_message(self):
        desktop = BrowserHandoffDesktop(start_bridge=False)
        conn = MagicMock()
        conn.epoch = "epoch1"
        conn.hello = SimpleNamespace(browser_instance_id="inst1")
        desktop.on_connected(conn)

        desktop.close_window("inst1", 42, expected_epoch="epoch1")
        conn.send.assert_called_once_with({
            "type": "browser_handoff_close_window",
            "window_id": 42,
        })

    def test_coordinator_claim_edge_rejects_when_no_extension_connected(self):
        desktop = Mock()
        desktop.has_connections = False

        tracker = Mock()
        coordinator = BrowserHandoffCoordinator(
            desktop=desktop,
            move_tracker=tracker,
            move_diagnostics=lambda: {},
            to_physical=lambda w: None,
            send_candidate=lambda c: True,
        )
        token = coordinator.claim_edge(
            display_rect=NativeRect(0, 0, 1920, 1080),
            edge_region=Mock(),
            source_display_id="disp",
            source_side="left",
            topology_version=1,
        )
        self.assertIsNone(token)
        tracker.claim_active_move.assert_not_called()

    def test_server_on_browser_edge_hit_falls_back_when_no_extension(self):
        from app.server import ConduitServer
        server = ConduitServer.__new__(ConduitServer)
        server.input_router = Mock(topology=SimpleNamespace(version=1))
        coordinator = Mock()
        coordinator.desktop.has_connections = False
        server.browser_handoff_coordinator = coordinator

        region = SimpleNamespace(
            source_rect=NativeRect(0, 0, 1920, 1080),
            source_display_id="disp",
            source_side="left",
            source_machine_id="server",
        )
        # Should return False so input_handler falls back to standard KVM edge_hit
        result = server.on_browser_edge_hit("left", 0.5, region)
        self.assertFalse(result)

    def test_client_on_client_browser_edge_hit_falls_back_when_no_extension(self):
        from app.client import ConduitClient
        client = ConduitClient.__new__(ConduitClient)
        client.is_active = True
        coordinator = Mock()
        coordinator.desktop.has_connections = False
        client.browser_handoff_coordinator = coordinator

        region = SimpleNamespace(
            source_rect=NativeRect(0, 0, 1920, 1080),
            source_display_id="disp",
            source_side="left",
            source_machine_id="client",
        )
        # Should return False so input_handler falls back to standard KVM edge_hit
        result = client.on_client_browser_edge_hit("left", 0.5, region)
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
