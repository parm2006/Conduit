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


if __name__ == "__main__":
    unittest.main()
