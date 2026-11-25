from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace

from app.gui import ConduitGUI
from app.input_router import LocalServer, RemoteClient, Transitioning


class OverlayMotionTests(unittest.TestCase):
    def remote_gui(self):
        from app.display_topology import NativeRect
        gui = object.__new__(ConduitGUI)
        gui.remote_view = SimpleNamespace(viewer_cursor='arrow',canvas=SimpleNamespace(
            winfo_rootx=lambda: 101, winfo_rooty=lambda: 201))
        gui.overlay_active = True
        gui._remote_overlay_ready = True
        gui._remote_overlay_handoff = 'current'
        gui.server = SimpleNamespace(input_router=SimpleNamespace(
            state=RemoteClient('session', 'client', 'display', (3, 570), 'current')),
            positions=[], moves=[])
        gui.server.on_mouse_position = lambda x,y: gui.server.positions.append((x,y))
        gui.server.on_mouse_move = lambda x,y: gui.server.moves.append((x,y))
        gui.warp_count = 0
        gui.last_x, gui.last_y = 4, 871
        gui._get_remote_geometry_and_projection = lambda: (
            NativeRect(0, 0, 1920, 1080), (0, 130, 2496, 1404), (3,570))
        return gui

    def test_remote_hidden_or_replaced_capture_drops_queued_motion(self):
        for hidden in (False, True):
            with self.subTest(hidden=hidden):
                gui = self.remote_gui()
                if hidden:
                    gui.overlay_active = False
                else:
                    gui._remote_overlay_handoff = 'old'
                gui.on_overlay_motion(SimpleNamespace(x=0,y=130))
                self.assertEqual(gui.server.positions, [])

    def test_remote_waits_for_window_setup_instead_of_counting_mapping_events(self):
        gui = self.remote_gui()
        gui._remote_overlay_ready = False
        # Extra mapping motions precede the actual warp; their number varies
        # with DPI/window-manager timing. None is intentional remote input.
        for x,y in ((2495,879), (0,130), (1919,576), (4,871), (4,871)):
            gui.on_overlay_motion(SimpleNamespace(x=x,y=y))
        self.assertEqual(gui.server.positions, [])
        gui._remote_overlay_ready = True
        gui.on_overlay_motion(SimpleNamespace(x=4,y=871))
        self.assertEqual(gui.server.positions, [])
        gui.on_overlay_motion(SimpleNamespace(x=7,y=873))
        self.assertEqual(gui.server.positions, [(5,572)])

    def test_remote_motion_uses_canvas_coordinates_even_when_event_bubbles(self):
        gui = self.remote_gui()
        # event.x/y belong to the widget emitting the event, not necessarily
        # the video Canvas on which the image is fitted.
        gui.on_overlay_motion(SimpleNamespace(x=0,y=0,x_root=108,y_root=1074))
        self.assertEqual(gui.server.positions, [(5,572)])

    def test_remote_projection_uses_native_video_canvas_dimensions(self):
        from app.display_topology import NativeRect
        gui = self.remote_gui()
        gui.server.input_router.topology = SimpleNamespace(machines=[SimpleNamespace(
            group=SimpleNamespace(machine_id='client', displays=[SimpleNamespace(
                display_id='display',rect=NativeRect(0,0,1920,1080))]))])
        gui.overlay = SimpleNamespace(winfo_width=lambda: 2496,winfo_height=lambda:1664)
        gui.remote_view.canvas.winfo_width=lambda: 2494
        gui.remote_view.canvas.winfo_height=lambda: 1662
        _,projection,_ = ConduitGUI._get_remote_geometry_and_projection(gui)
        self.assertEqual(projection, (0,129,2494,1403))

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
            warp=True, x=4, y=871, when='tail'))])
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

    @unittest.skipUnless(sys.platform == 'win32', 'Windows Tk event regression')
    def test_tk_coalesced_startup_motion_cannot_return_ownership(self):
        # Other tests collect cycles on worker threads. Keep Tcl's interpreter
        # and its finalization in a dedicated process owned by the main thread.
        probe = subprocess.run([
            sys.executable, '-c',
            'import unittest; '
            'from tests.test_overlay_motion import OverlayMotionTests; '
            'suite = unittest.TestSuite([OverlayMotionTests("_check_tk_startup_motion")]); '
            'result = unittest.TextTestRunner().run(suite); '
            'raise SystemExit(not result.wasSuccessful())',
        ], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
        self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)

    def _check_tk_startup_motion(self):
        import tkinter as tk
        from app.display_topology import NativeRect
        from app.input_handler import InputHandler, TopologyEdgeRegion

        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        overlay = tk.Toplevel(root)
        overlay.overrideredirect(True)
        # Exercise real Canvas bindtags without taking focus or moving the
        # user's pointer. Synthetic motions deliberately omit warp=True.
        overlay.geometry('800x500+20000+20000')
        canvas = tk.Canvas(overlay, highlightthickness=0, borderwidth=0)
        canvas.pack(fill='both', expand=True, padx=7, pady=11)
        root.update()
        gui = self.remote_gui()
        gui.remote_view.canvas = canvas
        gui._remote_overlay_handoff = 'old'
        gui.last_x, gui.last_y = 3, 200
        gui._remote_overlay_ready = False
        gui._get_remote_geometry_and_projection = lambda: (
            NativeRect(0, 0, 800, 500), (0, 0, 800, 500), (3, 200))
        returned = []
        handler = InputHandler.__new__(InputHandler)
        handler.callbacks = {'client_edge_hit': [lambda *args: returned.append(args)]}
        handler.set_client_topology_edge(TopologyEdgeRegion(
            'client', 'display', 'left', 'server', 'display', 'right',
            NativeRect(0, 0, 800, 500), NativeRect(0, 0, 800, 500)))
        gui.server.on_mouse_position = lambda x, y: handler.check_edge_hit(x, y)
        overlay.bind('<Motion>', gui.on_overlay_motion)
        gui.overlay = SimpleNamespace(
            winfo_exists=lambda: True, deiconify=lambda: None,
            focus_force=lambda: None, grab_set=lambda: None, config=lambda **kwargs: None)
        gui.transfer_toast = SimpleNamespace(raise_if_visible=lambda: None)
        gui.overlay_center_x, gui.overlay_center_y = 400, 250
        gui.after = root.after
        generate = canvas.event_generate

        def generate_without_pointer_warp(name, **kwargs):
            if name == '<Motion>':
                kwargs.pop('warp')
                for x, y in ((799, 200), (0, 0), (3, 200), (2, 200)):
                    generate(name, x=x, y=y, when='tail')
            else:
                generate(name, **kwargs)

        canvas.event_generate = generate_without_pointer_warp
        gui.show_overlay()
        root.update()
        self.assertTrue(gui._remote_overlay_ready)
        self.assertEqual(returned, [])
        generate('<Motion>', x=7, y=202, when='tail')
        root.update()
        self.assertEqual(returned, [])
        # A deliberate later move back to the actual edge must still work.
        generate('<Motion>', x=2, y=202, when='tail')
        root.update()
        self.assertEqual(len(returned), 1)

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

    def test_remote_entry_logs_wrong_side_placement_separately(self):
        from app.display_topology import NativeRect
        gui = self.remote_gui()
        gui._remote_overlay_handoff = 'old'
        queued, warps, markers = [], [], {}
        gui.after = lambda delay, callback: queued.append(callback)
        gui.overlay = SimpleNamespace(
            winfo_exists=lambda: True, deiconify=lambda: None,
            focus_force=lambda: None, grab_set=lambda: None,
            config=lambda **kwargs: None,
        )
        gui.transfer_toast = SimpleNamespace(raise_if_visible=lambda: None)
        gui.overlay_center_x, gui.overlay_center_y = 1248, 832
        gui.remote_view.canvas.update_idletasks = lambda: None
        gui.remote_view.canvas.event_generate = lambda *args, **kwargs: warps.append((args, kwargs))
        gui.remote_view.canvas.bind = lambda name, callback: markers.setdefault(name, callback)
        gui.remote_view.canvas.unbind = lambda *args: None
        gui._warp_remote_entry_cursor = lambda canvas, x, y: ((x, y), (2490, y))

        with self.assertLogs('app.gui', level='WARNING') as logs:
            gui.show_overlay()
            queued.pop(0)()
        self.assertTrue(any('Wrong-side placement' in entry for entry in logs.output))
        markers.popitem()[1](None)
        self.assertTrue(gui._remote_overlay_ready)
        with self.assertLogs('app.gui', level='WARNING') as motion_logs:
            gui.on_overlay_motion(SimpleNamespace(x=2490, y=871, x_root=2591, y_root=1072))
        self.assertTrue(any('First motion on wrong side' in entry for entry in motion_logs.output))


if __name__ == "__main__":
    unittest.main()
