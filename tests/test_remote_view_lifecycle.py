import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from app.client import ConduitClient
from app.display_topology import NativeRect


class RemoteViewLifecycleTests(unittest.TestCase):
    def test_accepted_primary_change_updates_viewer_and_input_scale(self):
        from app.gui import ConduitGUI
        gui = ConduitGUI.__new__(ConduitGUI)
        gui.remote_view = Mock()
        gui.server = SimpleNamespace(input_router=SimpleNamespace(remote_viewport=(1920, 1080)))
        primary = NativeRect(0, 0, 2560, 1440)
        candidate = SimpleNamespace(server_id='server', machines=(SimpleNamespace(group=SimpleNamespace(
            machine_id='server', displays=(SimpleNamespace(enabled=True, primary=True, rect=primary),))),))
        self.assertTrue(hasattr(ConduitGUI, '_sync_remote_primary'))
        gui._sync_remote_primary(candidate)
        self.assertEqual(gui.server.remote_viewport, (2560, 1440))
        self.assertEqual(gui.server.input_router.remote_viewport, (2560, 1440))
        gui.remote_view.set_primary.assert_called_once_with(primary)

    def test_upscaled_view_preserves_fractional_mouse_motion(self):
        client = ConduitClient.__new__(ConduitClient)
        moves = []
        client.is_active = True
        client.speed_scale_x = client.speed_scale_y = .5
        client._remote_control_rect = NativeRect(0, 0, 100, 100)
        client.input_handler = SimpleNamespace(mouse=SimpleNamespace(position=(40, 40)),
                                               inject_move=lambda dx, dy: moves.append((dx, dy)))
        client.on_mouse_move({'dx': 1, 'dy': 0})
        client.on_mouse_move({'dx': 1, 'dy': 0})
        self.assertEqual(sum(int(dx) for dx, _dy in moves), 1)

    def test_client_remote_movement_clamps_to_logical_edge_before_handoff(self):
        client = ConduitClient.__new__(ConduitClient)
        moves = []
        client.is_active = True
        client.speed_scale_x = client.speed_scale_y = 1
        client._remote_control_rect = NativeRect(0, 0, 100, 100)
        client.input_handler = SimpleNamespace(mouse=SimpleNamespace(position=(98, 40)),
                                               inject_move=lambda dx, dy: moves.append((dx, dy)))
        client.on_mouse_move({'dx': 30, 'dy': 0})
        self.assertEqual(moves, [(1, 0)])

    def test_remote_view_has_explicit_close(self):
        self.assertIsNotNone(importlib.util.find_spec('app.remote_view'))
        from app.remote_view import RemoteView
        view = RemoteView.__new__(RemoteView)
        view.gui = SimpleNamespace(after_cancel=Mock(), overlay=Mock())
        view.receiver = Mock()
        view.map_window = Mock()
        view.canvas = Mock()
        view._timer = 'tick'
        view.close()
        view.receiver.stop.assert_called_once()
        view.map_window.close.assert_called_once()
        view.gui.after_cancel.assert_called_once_with('tick')
