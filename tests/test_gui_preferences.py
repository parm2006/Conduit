import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.firewall import FirewallInspection, FirewallState
from app.firewall_onboarding import FirewallSetupOutcome, FirewallSetupResult
from app.gui import (
    DeskFlowGUI, _firewall_conflict_text, configure_main_window, parse_port,
    restore_saved_role,
    write_status_message,
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


class ConfigWidget:
    def __init__(self):
        self.values = {}
        self.visible = True

    def configure(self, **values):
        self.values.update(values)

    def pack(self, **values):
        self.visible = True

    def pack_forget(self):
        self.visible = False


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
            self.assertEqual(store.load_server_port(), 28903)

            store.save_server_port(5005)
            self.assertEqual(UserPreferences(Path(directory)).load_server_port(), 5005)

            with self.assertRaises(ValueError):
                store.save_server_port(70000)

    def test_server_port_is_limited_to_three_consecutive_lanes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UserPreferences(Path(directory))
            for port in (1, 5000, 65533):
                with self.subTest(port=port):
                    store.save_server_port(port)
                    self.assertEqual(store.load_server_port(), port)
            for port in (65534, 65535):
                with self.subTest(port=port):
                    with self.assertRaises(ValueError):
                        store.save_server_port(port)

    def test_loaded_out_of_range_server_port_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "preferences.json").write_text(
                '{"server_port":65534}',
                encoding="utf-8",
            )

            self.assertEqual(UserPreferences(root).load_server_port(), 28903)

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
    def test_conflict_copy_discloses_exact_scope_and_shared_python_effect(self):
        from app.firewall import FirewallRuleSpec

        spec = FirewallRuleSpec(r"C:\Python314\python.exe", 28903)

        message = _firewall_conflict_text(spec)

        self.assertIn(spec.executable_path, message)
        self.assertIn("28903-28905", message)
        self.assertIn("Private", message)
        self.assertIn("LocalSubnet", message)
        self.assertIn("Public networks remain blocked", message)
        self.assertIn("other Python applications may share", message)

    def test_server_and_client_base_port_parser_reserves_three_lanes(self):
        for port in (1, 5000, 65533):
            with self.subTest(port=port):
                self.assertEqual(parse_port(str(port)), port)
        for port in (0, 65534, 65535, 70000):
            with self.subTest(port=port):
                self.assertIsNone(parse_port(str(port)))

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
                (
                    "Status: Invalid port\n"
                    "Enter a base port from 1 to 65533.",
                    "red",
                ),
                (
                    "Status: Invalid port\n"
                    "Enter a base port from 1 to 65533.",
                    "red",
                ),
            ],
        )

    def test_firewall_status_row_renders_all_states_and_actions(self):
        expected = {
            FirewallState.READY: ("Firewall: Ready", None),
            FirewallState.MISSING: ("Firewall: Setup required", "Configure"),
            FirewallState.STALE: ("Firewall: Repair required", "Repair"),
            FirewallState.DEVELOPMENT: (
                "Firewall: Development rule",
                "View help",
            ),
            FirewallState.CONFLICT: (
                "Firewall: Connection blocked",
                "Repair",
            ),
            FirewallState.PUBLIC_ONLY: (
                "Firewall: Blocked on Public network",
                "View help",
            ),
            FirewallState.MANAGED: (
                "Firewall: Managed by administrator",
                "View help",
            ),
            FirewallState.UNAVAILABLE: (
                "Firewall: Unavailable",
                "View help",
            ),
        }
        for state, (label, action) in expected.items():
            with self.subTest(state=state):
                gui = DeskFlowGUI.__new__(DeskFlowGUI)
                gui.firewall_status_label = ConfigWidget()
                gui.firewall_action_btn = ConfigWidget()
                gui._render_firewall_inspection(
                    FirewallInspection(state, "safe_reason")
                )
                self.assertEqual(
                    gui.firewall_status_label.values["text"],
                    label,
                )
                if action is None:
                    self.assertFalse(gui.firewall_action_btn.visible)
                else:
                    self.assertEqual(
                        gui.firewall_action_btn.values["text"],
                        action,
                    )
                    self.assertTrue(gui.firewall_action_btn.visible)

    def test_valid_port_edit_schedules_inspection_without_configuration(self):
        calls = []
        gui = DeskFlowGUI.__new__(DeskFlowGUI)
        gui.server_port_entry = type(
            "Entry",
            (),
            {"get": lambda self: "5000"},
        )()
        gui._firewall_refresh_token = None
        gui.after = lambda delay, callback: calls.append(("after", delay, callback)) or 12
        gui.after_cancel = lambda token: calls.append(("cancel", token))
        gui._refresh_firewall_status = lambda: calls.append(("refresh",))

        gui._schedule_firewall_refresh()

        self.assertEqual(calls[0][:2], ("after", 250))
        self.assertNotIn(("refresh",), calls)
        calls[0][2]()
        self.assertIn(("refresh",), calls)

    def test_firewall_start_choices_latch_the_requested_behavior(self):
        class Entry:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

        for choice, expected_starts, expected_configures in (
            ("configure", 1, 1),
            ("without_setup", 1, 0),
            ("cancel", 0, 0),
        ):
            with self.subTest(choice=choice):
                starts = []
                configures = []

                class Onboarding:
                    busy = False
                    executable_path = (
                        r"C:\Program Files\DeskFlow\DeskFlow.exe"
                    )
                    inspection = FirewallInspection(
                        FirewallState.MISSING,
                        "rule_missing",
                    )

                    def refresh(self, port):
                        return None

                    def configure_async(
                        self,
                        port,
                        *,
                        consent,
                        on_complete=None,
                        on_ready=None,
                    ):
                        configures.append(port)
                        self.inspection = FirewallInspection(
                            FirewallState.READY,
                            "rule_ready",
                        )
                        if on_ready:
                            on_ready()
                        result = FirewallSetupResult(
                            FirewallSetupOutcome.READY,
                            self.inspection,
                        )
                        if on_complete:
                            on_complete(result)
                        return result

                gui = DeskFlowGUI.__new__(DeskFlowGUI)
                gui.server_port_entry = Entry("5000")
                gui.server_password_entry = Entry("secret")
                gui.firewall_onboarding = Onboarding()
                gui.server_start_btn = Button()
                gui._render_firewall_inspection = lambda value: None
                gui._set_status = lambda *args, **kwargs: None
                gui._start_server_after_firewall = (
                    lambda port, password: starts.append((port, password))
                )

                with patch(
                    "app.gui.ask_firewall_start_choice",
                    return_value=choice,
                ):
                    gui.start_server()

                self.assertEqual(len(starts), expected_starts)
                self.assertEqual(len(configures), expected_configures)
                if choice == "without_setup":
                    self.assertTrue(gui._firewall_start_warning)

    def test_conflict_allows_only_repair_or_cancel_and_latches_start_values(self):
        class Entry:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

        starts = []
        configures = []

        class Onboarding:
            busy = False
            executable_path = r"C:\Python314\python.exe"
            inspection = FirewallInspection(
                FirewallState.CONFLICT,
                "block_conflict",
            )

            def refresh(self, port):
                return None

            def configure_async(
                self,
                port,
                *,
                consent,
                on_complete=None,
                on_ready=None,
            ):
                configures.append((port, consent(None)))
                self.inspection = FirewallInspection(
                    FirewallState.READY,
                    "rule_ready",
                )
                if on_ready:
                    on_ready()
                result = FirewallSetupResult(
                    FirewallSetupOutcome.READY,
                    self.inspection,
                )
                if on_complete:
                    on_complete(result)
                return result

        gui = DeskFlowGUI.__new__(DeskFlowGUI)
        gui.server_port_entry = Entry("28903")
        gui.server_password_entry = Entry("latched-secret")
        gui.firewall_onboarding = Onboarding()
        gui.server_start_btn = Button()
        gui._render_firewall_inspection = lambda value: None
        gui._set_status = lambda *args, **kwargs: None
        gui._start_server_after_firewall = (
            lambda port, password: starts.append((port, password))
        )

        with patch(
            "app.gui.ask_firewall_start_choice",
            return_value="repair",
        ) as choice:
            gui.start_server()

        self.assertEqual(configures, [(28903, True)])
        self.assertEqual(starts, [(28903, "latched-secret")])
        self.assertTrue(choice.call_args.kwargs["repair_required"])

    def test_conflict_cancel_never_starts_or_configures(self):
        class Entry:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

        class Onboarding:
            busy = False
            executable_path = r"C:\Program Files\DeskFlow\DeskFlow.exe"
            inspection = FirewallInspection(
                FirewallState.CONFLICT,
                "block_conflict",
            )

            def refresh(self, port):
                return None

        starts = []
        gui = DeskFlowGUI.__new__(DeskFlowGUI)
        gui.server_port_entry = Entry("28903")
        gui.server_password_entry = Entry("secret")
        gui.firewall_onboarding = Onboarding()
        gui._render_firewall_inspection = lambda value: None
        gui._start_server_after_firewall = lambda *args: starts.append(args)

        with patch(
            "app.gui.ask_firewall_start_choice",
            return_value="cancel",
        ) as choice:
            gui.start_server()

        self.assertEqual(starts, [])
        self.assertTrue(choice.call_args.kwargs["repair_required"])

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

        self.assertEqual(window.geometry_value, "400x650")
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
