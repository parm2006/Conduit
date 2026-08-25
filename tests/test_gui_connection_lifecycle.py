import unittest

from app.gui import ConduitGUI
from app.display_topology import Display, MachineDisplayGroup, NativeRect
from app.windows_displays import display_group_to_message


class Client:
    def __init__(self):
        self.disconnects = 0

    def disconnect(self):
        self.disconnects += 1


class GuiConnectionLifecycleTests(unittest.TestCase):
    def test_late_inventory_from_stopped_server_cannot_repopulate_editor(self):
        scheduled = []
        gui = ConduitGUI.__new__(ConduitGUI)
        old_server = object()
        gui.server = object()
        gui.after = lambda delay, callback: scheduled.append(callback)
        inventory = display_group_to_message(
            MachineDisplayGroup(
                "client",
                "ParthSurface",
                (
                    Display(
                        "display",
                        NativeRect(0, 0, 1920, 1080),
                        100,
                        0,
                        True,
                    ),
                ),
            )
        )

        gui._on_server_display_inventory(
            old_server,
            {"inventory": inventory},
        )

        self.assertEqual(scheduled, [])

    def test_late_apply_completion_from_old_server_cannot_commit_editor(self):
        commits = []
        gui = ConduitGUI.__new__(ConduitGUI)
        old_server = object()
        gui.server = object()
        gui.topology_editor = type(
            "Editor",
            (),
            {
                "state": type(
                    "State",
                    (),
                    {"commit": lambda self, value: commits.append(value)},
                )(),
            },
        )()

        gui._finish_topology_apply(old_server, object(), True)

        self.assertEqual(commits, [])

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

    def test_late_apply_notice_from_old_client_cannot_hide_replacement_toast(self):
        scheduled = []
        gui = ConduitGUI.__new__(ConduitGUI)
        old_client = Client()
        gui.client = Client()
        gui.after = lambda delay, callback: scheduled.append(callback)
        gui.topology_toast = type(
            "Toast",
            (),
            {"hide": lambda self: None},
        )()

        gui._hide_topology_toast(old_client)

        self.assertEqual(scheduled, [])


if __name__ == "__main__":
    unittest.main()
