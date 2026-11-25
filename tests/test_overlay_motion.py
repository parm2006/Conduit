import unittest
from types import SimpleNamespace

from app.gui import ConduitGUI
from app.input_router import LocalServer, RemoteClient, Transitioning


class OverlayMotionTests(unittest.TestCase):
    def test_focus_loss_uses_local_recovery_not_unauthenticated_edge(self):
        calls = []
        gui = object.__new__(ConduitGUI)
        gui.overlay_active = True
        gui.server = SimpleNamespace(
            control_connected=True,
            on_switch_back=lambda data: calls.append(("edge", data)),
            _return_cursor_to_server=lambda: calls.append(("local", None)),
        )
        gui.on_overlay_focus_out(None)
        self.assertEqual(calls, [("local", None)])

    def test_queued_overlay_show_does_not_recapture_after_return_to_server(self):
        queued, shown = [], []
        gui = object.__new__(ConduitGUI)
        gui.server = SimpleNamespace(input_router=SimpleNamespace(
            state=RemoteClient("session", "client", "display", (1, 2))))
        gui.after = lambda delay, callback: queued.append(callback)
        gui.overlay = SimpleNamespace(winfo_exists=lambda: True,
                                      deiconify=lambda: shown.append(True))
        gui.show_overlay()
        gui.server.input_router.state = LocalServer("server", (10, 20))
        queued.pop()()
        self.assertEqual(shown, [])

    def test_overlay_waits_for_the_same_acknowledged_handoff_to_commit(self):
        queued, shown = [], []
        gui = object.__new__(ConduitGUI)
        gui.server = SimpleNamespace(input_router=SimpleNamespace(state=Transitioning(
            LocalServer("source", (0, 0)), "session", "client", "display", (1, 2),
            7, "handoff", True, True, acknowledged=True)))
        gui.after = lambda delay, callback: queued.append(callback)
        gui.overlay = SimpleNamespace(winfo_exists=lambda: True,
                                      deiconify=lambda: shown.append(True))
        gui.show_overlay()
        queued.pop(0)()
        self.assertEqual(shown, [])
        self.assertEqual(len(queued), 1)
        gui.server.input_router.state = RemoteClient("session", "client", "display", (1, 2), "handoff")
        queued.pop(0)()
        self.assertEqual(shown, [True])

    def test_queued_overlay_show_does_not_capture_a_replacement_handoff(self):
        queued, shown = [], []
        gui = object.__new__(ConduitGUI)
        gui.server = SimpleNamespace(input_router=SimpleNamespace(
            state=RemoteClient("session", "client", "display", (1, 2), "old")))
        gui.after = lambda delay, callback: queued.append(callback)
        gui.overlay = SimpleNamespace(winfo_exists=lambda: True,
                                      deiconify=lambda: shown.append(True))
        gui.show_overlay()
        gui.server.input_router.state = RemoteClient("session", "client", "display", (1, 2), "new")
        queued.pop()()
        self.assertEqual(shown, [])

    def test_remote_view_focus_loss_keeps_capture(self):
        calls = []
        gui = object.__new__(ConduitGUI)
        gui.remote_view = object()
        gui.overlay_active = True
        gui.server = SimpleNamespace(control_connected=True,
            _return_cursor_to_server=lambda: calls.append(True))
        gui.on_overlay_focus_out(None)
        self.assertEqual(calls, [])

    def test_initial_overlay_mapping_and_warp_events_are_not_forwarded(self):
        class Server:
            def __init__(self):
                self.moves = []

            def on_mouse_move(self, dx, dy):
                self.moves.append((dx, dy))

        gui = object.__new__(ConduitGUI)
        gui.server = Server()
        gui.overlay_center_x = 500
        gui.overlay_center_y = 400
        gui.last_x = 500
        gui.last_y = 400
        gui.warp_count = 2
        gui.overlay = SimpleNamespace(event_generate=lambda *args, **kwargs: None)

        gui.on_overlay_motion(SimpleNamespace(x=500, y=368))
        gui.on_overlay_motion(SimpleNamespace(x=500, y=400))
        gui.on_overlay_motion(SimpleNamespace(x=503, y=404))

        self.assertEqual(gui.server.moves, [(3, 4)])
        self.assertEqual(gui.warp_count, 0)

    def remote_gui(self):
        from app.display_topology import NativeRect
        gui = object.__new__(ConduitGUI)
        gui.remote_view = SimpleNamespace(viewer_cursor='arrow', canvas=SimpleNamespace(
            winfo_rootx=lambda: 101, winfo_rooty=lambda: 201))
        gui.overlay_active = True
        gui._remote_overlay_ready = True
        gui._remote_overlay_handoff = 'current'
        gui.server = SimpleNamespace(
            input_router=SimpleNamespace(
                state=RemoteClient('session', 'client', 'display', (3, 570), 'current')
            ),
            positions=[],
            moves=[],
        )
        gui.server.on_mouse_position = lambda x, y: gui.server.positions.append((x, y))
        gui.server.on_mouse_move = lambda x, y: gui.server.moves.append((x, y))
        gui.warp_count = 0
        gui.last_x, gui.last_y = 4, 871
        gui._get_remote_geometry_and_projection = lambda: (
            NativeRect(0, 0, 1920, 1080), (0, 130, 2496, 1404), (3, 570)
        )
        return gui

    def test_remote_waits_for_window_setup_instead_of_dropping_live_entry(self):
        gui = self.remote_gui()
        gui._remote_overlay_ready = False
        for x, y in ((0, 130), (1919, 576), (4, 871)):
            gui.on_overlay_motion(SimpleNamespace(x=x, y=y))
        self.assertEqual(gui.server.positions, [])
        gui._remote_overlay_ready = True
        gui.on_overlay_motion(SimpleNamespace(x=4, y=871))
        self.assertEqual(gui.server.positions, [])
        gui.on_overlay_motion(SimpleNamespace(x=7, y=873))
        self.assertEqual(gui.server.positions, [(5, 572)])

    def test_remote_motion_uses_canvas_coordinates_even_when_event_bubbles(self):
        gui = self.remote_gui()
        gui.on_overlay_motion(SimpleNamespace(x=0, y=0, x_root=108, y_root=1074))
        self.assertEqual(gui.server.positions, [(5, 572)])

    def test_remote_show_blocks_mapping_events_and_warps_video_canvas(self):
        gui = self.remote_gui()
        gui._remote_overlay_handoff = 'old'
        queued, warps, markers = [], [], {}
        gui.after = lambda delay, callback: queued.append(callback)
        mapping_motion = lambda: gui.on_overlay_motion(SimpleNamespace(x=0, y=130))
        gui.overlay = SimpleNamespace(
            winfo_exists=lambda: True, deiconify=mapping_motion,
            focus_force=mapping_motion, grab_set=lambda: None,
            config=lambda **kwargs: None)
        gui.transfer_toast = SimpleNamespace(raise_if_visible=lambda: None)
        gui.overlay_center_x, gui.overlay_center_y = 1248, 832
        gui.remote_view.canvas.update_idletasks = mapping_motion
        gui.remote_view.canvas.event_generate = lambda *args, **kwargs: warps.append((args, kwargs))
        gui.remote_view.canvas.bind = lambda name, callback: markers.setdefault(name, callback)
        gui.remote_view.canvas.unbind = lambda *args: None

        gui.show_overlay()
        queued.pop(0)()

        self.assertEqual(gui.server.positions, [])
        self.assertEqual(warps[:1], [(('<Motion>',), dict(
            warp=True, x=5, y=871, when='tail'))])
        self.assertEqual(gui._remote_overlay_handoff, 'current')
        self.assertFalse(gui._remote_overlay_ready)
        name, callback = markers.popitem()
        self.assertEqual(warps[1], ((name,), dict(when='tail')))
        callback(None)
        self.assertTrue(gui._remote_overlay_ready)
        gui.show_overlay()
        queued.pop(0)()
        self.assertEqual(len(warps), 2)

    def test_remote_hide_immediately_stops_input_before_tk_callback(self):
        gui = self.remote_gui()
        queued = []
        gui.after = lambda delay, callback: queued.append(callback)
        gui.hide_overlay()
        gui.on_overlay_motion(SimpleNamespace(x=0, y=130))
        self.assertEqual(gui.server.positions, [])
        self.assertEqual(len(queued), 1)


if __name__ == "__main__":
    unittest.main()
