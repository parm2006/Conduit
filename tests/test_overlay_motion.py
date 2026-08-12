import unittest
from types import SimpleNamespace

from app.gui import ConduitGUI


class OverlayMotionTests(unittest.TestCase):
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
