import unittest

from app.gui import ConduitGUI


class Client:
    def __init__(self):
        self.disconnects = 0

    def disconnect(self):
        self.disconnects += 1


class GuiConnectionLifecycleTests(unittest.TestCase):
    def test_late_server_disconnect_status_cannot_overwrite_stopped_status(self):
        scheduled = []
        statuses = []
        gui = ConduitGUI.__new__(ConduitGUI)
        gui.server = object()
        gui.server_port_entry = type(
            "Entry",
            (),
            {"get": lambda self: "28903"},
        )()
        gui.after = lambda delay, callback: scheduled.append(callback)
        gui._set_status = (
            lambda message, color: statuses.append((message, color))
        )
        gui.ensure_visible = lambda: None

        gui._on_server_client_disconnected({})
        gui.server = None
        scheduled.pop(0)()

        self.assertEqual(statuses, [])

    def test_late_disconnect_from_old_client_cannot_disconnect_replacement(self):
        scheduled = []
        gui = ConduitGUI.__new__(ConduitGUI)
        old_client = Client()
        replacement = Client()
        gui.client = replacement
        gui.after = lambda delay, callback: scheduled.append(callback)

        gui._on_client_disconnected_event(old_client, {})
        scheduled.pop(0)()

        self.assertIs(gui.client, replacement)
        self.assertEqual(replacement.disconnects, 0)


if __name__ == "__main__":
    unittest.main()
