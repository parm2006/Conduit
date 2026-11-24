"""Viewer cursor ownership without creating Tk or moving the real pointer."""
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from app.display_topology import NativeRect
from app.input_router import LocalServer, RemoteClient
from app.remote_view import RemoteView


class FakeWidget:
    def __init__(self, **options):
        self.options = dict(options)
        self.config = Mock(side_effect=self.options.update)
        for name in ('attributes', 'pack', 'update_idletasks', 'delete',
                     'create_image', 'destroy', 'deiconify', 'focus_force',
                     'grab_set', 'event_generate'):
            setattr(self, name, Mock())
        self.winfo_id = Mock(return_value=12)
        self.winfo_exists = Mock(return_value=True)


class RemoteCursorPolicyTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.selection = RemoteClient('session', 'client', 'display', (20, 30), 'handoff')
        self.overlay = FakeWidget()
        self.canvas = FakeWidget(cursor='arrow')
        self.receiver = Mock()
        self.receiver.stalled.return_value = False
        self.receiver.is_native_active.return_value = False
        self.receiver.cursor_in_frame.return_value = None
        self.receiver.take_frame.return_value = None
        self.map_window = Mock()
        self.server = SimpleNamespace(
            input_router=SimpleNamespace(state=self.selection),
            active_topology=SimpleNamespace(machines=(), server_id='server'),
            session_registry=SimpleNamespace(ready_sessions=lambda: ()),
        )
        self.gui = SimpleNamespace(
            overlay=self.overlay, server=self.server, overlay_active=True,
            after=Mock(return_value='timer'), after_cancel=Mock(),
            show_overlay=Mock(), hide_overlay=Mock(),
        )
        for target, replacement in (
            ('app.remote_view.tk.Canvas', lambda *args, **kwargs: self.canvas),
            ('app.remote_view.WindowsMapOverlay', lambda: self.map_window),
            ('app.remote_view.ServerVideoReceiver', lambda *args, **kwargs: self.receiver),
            ('app.remote_view.ImageTk.PhotoImage', lambda *args, **kwargs: object()),
            ('win32gui.GetAncestor', Mock(return_value=12)),
            ('win32gui.SetWindowPos', Mock()),
            ('win32gui.GetCursorPos', Mock(return_value=(900, 900))),
        ):
            self.stack.enter_context(patch(target, replacement))
        self.view = RemoteView(self.gui, self.server, NativeRect(100, 200, 1100, 1000))

    def assert_cursors(self, expected):
        self.assertEqual(self.overlay.options['cursor'], expected)
        self.assertEqual(self.canvas.options['cursor'], expected)
        self.assertEqual(getattr(self.view, 'viewer_cursor', None), expected)

    def jpeg_frame(self, cursor=(.05, .05)):
        return self.selection, Image.new('RGB', (1000, 800)), (0, 0), cursor

    def test_unknown_display_starts_with_local_arrow(self):
        self.assert_cursors('arrow')

    def test_native_policy_applies_to_both_widgets_and_does_not_reconfigure_each_tick(self):
        self.receiver.is_native_active.return_value = True
        for policy, expected in ((True, 'none'), (None, 'arrow'),
                                 (True, 'none'), (False, 'arrow')):
            with self.subTest(policy=policy):
                self.receiver.cursor_in_frame.return_value = policy
                self.view._update()
                self.assert_cursors(expected)
                self.overlay.config.reset_mock()
                self.canvas.config.reset_mock()
                self.view._update()
                self.overlay.config.assert_not_called()
                self.canvas.config.assert_not_called()

    def test_jpeg_hides_local_arrow_after_display_and_keeps_map_cursor_metadata(self):
        self.view._update()
        self.assert_cursors('arrow')
        self.map_window.reset_mock()
        self.receiver.take_frame.return_value = self.jpeg_frame()
        self.view._update()
        self.assert_cursors('none')
        self.assertEqual(self.view._cursor, (150, 240))
        self.map_window.hide.assert_called()
        self.map_window.show.assert_not_called()

    def test_native_to_jpeg_preserves_displayed_policy_until_jpeg_arrives(self):
        self.receiver.is_native_active.return_value = True
        self.receiver.cursor_in_frame.return_value = False
        self.view._update()
        self.assert_cursors('arrow')
        self.receiver.is_native_active.return_value = False
        self.view._update()
        self.assert_cursors('arrow')
        self.receiver.take_frame.return_value = self.jpeg_frame()
        self.view._update()
        self.assert_cursors('none')

    def test_new_selection_resets_previous_jpeg_cursor_policy(self):
        self.receiver.take_frame.return_value = self.jpeg_frame()
        self.view._update()
        self.server.input_router.state = RemoteClient('other', 'other-client', 'other-display', (0, 0))
        self.receiver.take_frame.return_value = None
        self.view._update()
        self.assert_cursors('arrow')
        self.assertIsNone(self.view._cursor)

    def test_local_return_resets_policy_and_hides_viewer(self):
        self.receiver.take_frame.return_value = self.jpeg_frame()
        self.view._update()
        self.server.input_router.state = LocalServer('server', (900, 900))
        self.view._update()
        self.assert_cursors('arrow')
        self.gui.hide_overlay.assert_called_once()
        self.assertIsNone(self.view._shown_selection)

    def test_close_restores_the_ordinary_hidden_capture_overlay(self):
        self.view.close()
        self.assertEqual(self.overlay.options['cursor'], 'none')
        self.canvas.destroy.assert_called_once()
        self.receiver.stop.assert_called_once()

    def test_show_overlay_respects_the_viewer_choice(self):
        from app.gui import ConduitGUI
        gui = object.__new__(ConduitGUI)
        gui.server = self.server
        gui.overlay = self.overlay
        gui.remote_view = SimpleNamespace(viewer_cursor='none')
        gui.overlay_center_x, gui.overlay_center_y = 500, 400
        gui.transfer_toast = SimpleNamespace(raise_if_visible=Mock())
        gui._get_remote_geometry_and_projection = lambda: (None, None, None)
        queued = []
        gui.after = lambda delay, callback: queued.append(callback)
        gui.show_overlay()
        queued.pop()()
        self.assertEqual(self.overlay.options['cursor'], 'none')


if __name__ == '__main__':
    unittest.main()
