"""Ctrl browser gestures must never take the ordinary KVM switch path."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.client import ConduitClient
from app.server import ConduitServer
from app.input_handler import InputHandler, TopologyEdgeRegion
from app.display_topology import NativeRect


REGION = TopologyEdgeRegion('server', 'screen', 'right', 'client', 'remote',
                            'left', NativeRect(0, 0, 1920, 1080), NativeRect(0, 0, 1920, 1080))


class CtrlEdgeTests(unittest.TestCase):
    def handler(self, ctrl=True, active=True):
        handler = InputHandler.__new__(InputHandler)
        handler.callbacks = {}
        handler.topology_edge_regions = (REGION,)
        handler.client_topology_edge_regions = (REGION,)
        handler._ctrl_drag_down = lambda: ctrl
        browser = Mock(return_value=active)
        normal = Mock()
        for event in ('browser_edge_hit', 'client_browser_edge_hit'):
            handler.register_callback(event, browser)
        for event in ('edge_hit', 'client_edge_hit'):
            handler.register_callback(event, normal)
        return handler, browser, normal

    def test_ctrl_inner_band_on_server_and_client_does_not_switch(self):
        for method in ('_on_move_edge', 'check_edge_hit'):
            with self.subTest(method=method):
                handler, browser, normal = self.handler()
                getattr(handler, method)(1840, 500)
                browser.assert_called_once()
                normal.assert_not_called()
                getattr(handler, method)(1919, 500)
                normal.assert_not_called()

    def test_without_ctrl_or_outside_band_browser_is_not_called(self):
        for ctrl, point in ((False, (1919, 500)), (True, (1800, 500)), (True, (1840, 1100))):
            handler, browser, normal = self.handler(ctrl=ctrl)
            handler._on_move_edge(*point)
            browser.assert_not_called()
            self.assertEqual(normal.call_count, int(not ctrl))

    def test_ctrl_non_browser_drag_keeps_ordinary_edge_behavior(self):
        handler, browser, normal = self.handler(active=False)
        handler._on_move_edge(1919, 500)
        normal.assert_called_once()

    def test_client_sends_browser_only_message_and_remains_active(self):
        client = ConduitClient.__new__(ConduitClient)
        client.is_active = True
        client.active_topology_config = {'version': 7}
        client.browser_handoff_coordinator = Mock()
        client.browser_handoff_coordinator.move_tracker.has_active_move.return_value = True
        client.browser_handoff_coordinator.claim_edge.return_value = 'gesture'
        client.control_network = Mock()
        self.assertTrue(client.on_client_browser_edge_hit('right', 0.5, REGION))
        message = client.control_network.send_message.call_args.args[0]
        self.assertEqual(message['type'], 'browser_handoff_edge')
        self.assertEqual(message['gesture_id'], 'gesture')
        self.assertTrue(client.is_active)
        self.assertTrue(client.browser_handoff_coordinator.claim_edge.call_args.kwargs['capture_active'])
        client.browser_handoff_coordinator.claim_edge.return_value = None
        client.on_client_browser_edge_hit('right', 0.5, REGION)
        client.control_network.send_message.assert_called_once()

    def test_server_remote_message_uses_authenticated_identity(self):
        server = ConduitServer.__new__(ConduitServer)
        server.input_router = Mock()
        message = dict(type='browser_handoff_edge', source_machine_id='forged',
                       peer_identity='client', session_id='session', source_display_id='screen',
                       source_side='left', ratio=0.5, topology_version=7, gesture_id='gesture')
        server.on_browser_handoff_edge(message)
        server.input_router.authorize_browser_edge.assert_called_once_with(
            'client', 'screen', 'left', 0.5, session_id='session', topology_version=7,
            gesture_id='gesture')
        server.input_router.handle_edge.assert_not_called()

    def test_server_local_callback_uses_browser_authorization(self):
        server = ConduitServer.__new__(ConduitServer)
        server.input_router = Mock(topology=SimpleNamespace(version=7))
        server.browser_handoff_coordinator = Mock()
        server.browser_handoff_coordinator.claim_edge.return_value = 'gesture'
        self.assertTrue(server.on_browser_edge_hit('right', 0.5, REGION))
        server.input_router.authorize_browser_edge.assert_called_once_with(
            'server', 'screen', 'right', 0.5, topology_version=7, gesture_id='gesture')
        server.input_router.handle_edge.assert_not_called()
