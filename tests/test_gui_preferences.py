import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.gui import (
    DeskFlowGUI, configure_main_window, restore_saved_role, write_status_message,
)
from app.preferences import UserPreferences


class Button:
    def __init__(self):
        self.state = None

    def configure(self, **values):
        self.state = values.get("state", self.state)

    def pack(self, **values):
        return None

    def pack_forget(self):
        return None


class PreferencesTests(unittest.TestCase):
    def test_client_position_round_trips_and_preserves_saved_role(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = UserPreferences(root)
            store.save_role("server")
            store.save_client_position("left")

            reloaded = UserPreferences(root)
            self.assertEqual(reloaded.load_role(), "server")
            self.assertEqual(reloaded.load_client_position(), "left")

            reloaded.save_role("client")
            self.assertEqual(UserPreferences(root).load_client_position(), "left")

    def test_client_position_defaults_to_right_and_rejects_invalid_values(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UserPreferences(Path(directory))

            self.assertEqual(store.load_client_position(), "right")
            with self.assertRaises(ValueError):
                store.save_client_position("diagonal")

    def test_server_port_persistence_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UserPreferences(Path(directory))
            self.assertEqual(store.load_server_port(), 5000)

            store.save_server_port(5005)
            self.assertEqual(UserPreferences(Path(directory)).load_server_port(), 5005)

            with self.assertRaises(ValueError):
                store.save_server_port(70000)

    def test_role_store_round_trips_only_supported_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UserPreferences(Path(directory))
            self.assertIsNone(store.load_role())
            store.save_role("client")
            self.assertEqual(UserPreferences(Path(directory)).load_role(), "client")
            with self.assertRaises(ValueError):
                store.save_role("daemon")

    def test_corrupt_preferences_fall_back_without_exposing_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            path.write_text("private invalid data", encoding="utf-8")

            with self.assertLogs("app.preferences", level="ERROR") as logs:
                self.assertIsNone(UserPreferences(Path(directory)).load_role())
            self.assertNotIn("private invalid data", "\n".join(logs.output))


class SuccessfulRoleTimingTests(unittest.TestCase):
    def test_selecting_client_position_updates_buttons_and_saves_immediately(self):
        saved = []

        class LayoutButton:
            def __init__(self):
                self.values = {}

            def configure(self, **values):
                self.values.update(values)

        gui = DeskFlowGUI.__new__(DeskFlowGUI)
        gui.layout_btns = {
            position: LayoutButton()
            for position in ("top", "left", "right", "bottom")
        }
        gui.preferences = type(
            "Preferences",
            (),
            {"save_client_position": lambda self, value: saved.append(value)},
        )()

        gui.set_layout_position("bottom")

        self.assertEqual(gui.layout_position, "bottom")
        self.assertEqual(saved, ["bottom"])
        self.assertEqual(gui.layout_btns["bottom"].values["text"], "C")
        self.assertEqual(gui.layout_btns["bottom"].values["fg_color"], "white")
        self.assertEqual(gui.layout_btns["right"].values["text"], "")

    def test_position_save_failure_keeps_selection_and_redacts_private_detail(self):
        class LayoutButton:
            def configure(self, **values):
                return None

        gui = DeskFlowGUI.__new__(DeskFlowGUI)
        gui.layout_btns = {
            position: LayoutButton()
            for position in ("top", "left", "right", "bottom")
        }
        gui.preferences = type(
            "Preferences",
            (),
            {
                "save_client_position": lambda self, value: (_ for _ in ()).throw(
                    PermissionError("private preference path")
                ),
            },
        )()

        with self.assertLogs("app.gui", level="ERROR") as logs:
            gui.set_layout_position("top")

        self.assertEqual(gui.layout_position, "top")
        self.assertNotIn("private preference path", "\n".join(logs.output))

    def test_invalid_ports_show_actionable_status_without_starting_or_connecting(self):
        class Entry:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

        statuses = []
        server_gui = DeskFlowGUI.__new__(DeskFlowGUI)
        server_gui.server_port_entry = Entry("not-a-port")
        server_gui.server_password_entry = Entry("secret")
        server_gui._set_status = lambda message, color, **kwargs: statuses.append((message, color))

        server_gui.start_server()

        client_gui = DeskFlowGUI.__new__(DeskFlowGUI)
        client_gui.client_ip_entry = Entry("192.0.2.1")
        client_gui.client_port_entry = Entry("70000")
        client_gui.client_password_entry = Entry("secret")
        client_gui._set_status = lambda message, color, **kwargs: statuses.append((message, color))

        client_gui.connect_client()

        self.assertEqual(
            statuses,
            [
                ("Status: Invalid port\nEnter a number from 1 to 65535.", "red"),
                ("Status: Invalid port\nEnter a number from 1 to 65535.", "red"),
            ],
        )

    def test_client_role_is_saved_only_after_successful_full_connection(self):
        roles = []
        source = object()
        gui = DeskFlowGUI.__new__(DeskFlowGUI)
        gui.client = source
        gui.preferences = type("Preferences", (), {"save_role": lambda self, role: roles.append(role)})()
        gui.client_connect_btn = Button()
        gui.client_disconnect_btn = Button()
        gui._set_status = lambda message, color, **kwargs: None
        gui.save_known_host = lambda ip, port: None

        gui._handle_connect_result(source, False, "Incorrect password", "host", 5000)
        self.assertEqual(roles, [])

        gui._handle_connect_result(source, True, None, "host", 5000)
        self.assertEqual(roles, ["client"])

    def test_unwritable_preferences_do_not_break_a_successful_connection(self):
        statuses = []
        source = object()
        gui = DeskFlowGUI.__new__(DeskFlowGUI)
        gui.client = source
        gui.preferences = type(
            "Preferences", (),
            {
                "save_role": lambda self, role: (_ for _ in ()).throw(
                    PermissionError("private path detail")
                )
            },
        )()
        gui.client_connect_btn = Button()
        gui.client_disconnect_btn = Button()
        gui._set_status = lambda message, color, **kwargs: statuses.append((message, color))
        gui.save_known_host = lambda ip, port: None

        with self.assertLogs("app.gui", level="ERROR") as logs:
            gui._handle_connect_result(source, True, None, "host", 5000)

        self.assertEqual(statuses, [("Status: Connected to host:5000", "green")])
        self.assertNotIn("private path detail", "\n".join(logs.output))

    def test_server_role_is_saved_only_after_listener_starts(self):
        roles = []
        statuses = []

        class Entry:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

        class Network:
            def register_callback(self, event, callback):
                return None

        class Server:
            def __init__(self, starts):
                self.starts = starts
                self.control_network = Network()
                self.identity = type("Identity", (), {"cert_path": "cert", "recovered": False})()

            def set_screen_size(self, width, height):
                return None

            def start(self):
                return self.starts

            def stop(self):
                return None

        gui = DeskFlowGUI.__new__(DeskFlowGUI)
        gui.server_port_entry = Entry("5000")
        gui.server_password_entry = Entry("secret")
        gui.server = None
        gui.overlay = object()
        gui.layout_position = "right"
        gui.show_overlay = lambda: None
        gui.hide_overlay = lambda: None
        gui._on_transfer_status = lambda status: None
        gui._on_server_client_connected = lambda data: None
        gui._on_server_client_disconnected = lambda data: None
        gui.winfo_screenwidth = lambda: 1920
        gui.winfo_screenheight = lambda: 1080
        gui._set_status = lambda message, color, **kwargs: statuses.append((message, color))
        gui.server_start_btn = Button()
        gui.server_stop_btn = Button()
        gui.preferences = type("Preferences", (), {"save_role": lambda self, role: roles.append(role)})()

        with patch("app.gui.DeskFlowServer", return_value=Server(False)):
            gui.start_server()
        self.assertEqual(roles, [])
        self.assertEqual(
            statuses[-1],
            (
                "Status: Could not start server\n"
                "Check whether the selected port is already in use.",
                "red",
            ),
        )

        with (
            patch("app.gui.DeskFlowServer", return_value=Server(True)),
            patch("app.gui.certificate_fingerprint", return_value="ab" * 32),
        ):
            gui.start_server()
        self.assertEqual(roles, ["server"])


class FixedWindowConfigurationTests(unittest.TestCase):
    def test_pairing_code_segment_is_white_inside_colored_status(self):
        class Textbox:
            def __init__(self):
                self.calls = []

            def configure(self, **kwargs):
                self.calls.append(("configure", kwargs))

            def delete(self, start, end):
                self.calls.append(("delete", start, end))

            def insert(self, index, text, tags=None):
                self.calls.append(("insert", text, tags))

            def tag_config(self, name, **kwargs):
                self.calls.append(("tag", name, kwargs))

        textbox = Textbox()

        write_status_message(
            textbox,
            "Status: Identity recovered\nPairing code: ABCD-1234",
            "orange",
            white_text="ABCD-1234",
        )

        self.assertIn(("tag", "pairing_code", {"foreground": "white"}), textbox.calls)
        self.assertIn(("insert", "ABCD-1234", "pairing_code"), textbox.calls)

    def test_root_configuration_fits_action_buttons_and_remains_fixed(self):
        class Window:
            def __init__(self):
                self.geometry_value = None
                self.resizable_value = None

            def geometry(self, value):
                self.geometry_value = value

            def resizable(self, width, height):
                self.resizable_value = (width, height)

        window = Window()

        configure_main_window(window)

        self.assertEqual(window.geometry_value, "400x600")
        self.assertEqual(window.resizable_value, (False, False))

    def test_saved_role_selects_the_matching_tab_and_invalid_values_do_nothing(self):
        class Tabs:
            def __init__(self):
                self.selected = []

            def set(self, name):
                self.selected.append(name)

        tabs = Tabs()
        restore_saved_role(tabs, "client")
        restore_saved_role(tabs, "server")
        restore_saved_role(tabs, None)

        self.assertEqual(tabs.selected, ["Client", "Server (Host)"])


if __name__ == "__main__":
    unittest.main()
