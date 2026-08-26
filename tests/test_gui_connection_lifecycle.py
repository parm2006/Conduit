import unittest
from types import SimpleNamespace

from app.gui import ConduitGUI
from app.display_topology import Display, MachineDisplayGroup, NativeRect
from app.windows_displays import display_group_to_message
from app.session import CandidateDecision, SessionRegistry


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

    def test_inventory_uses_its_session_color_and_targets_only_that_client(self):
        sent = []
        colors = []
        group = MachineDisplayGroup(
            "device-a",
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
        session = SimpleNamespace(
            peer_identity="device-a",
            color="#3B82F6",
            display_inventory=None,
            draft_placement=None,
        )
        server = SimpleNamespace(
            control_connected=True,
            session_registry=SimpleNamespace(
                get=lambda session_id: session if session_id == "session-a" else None
            ),
            control_network=SimpleNamespace(
                send_message=lambda message, session_id=None: sent.append(
                    (session_id, message)
                ) or True
            ),
        )
        editor = SimpleNamespace(
            add_client=lambda value: True,
            set_client_color=lambda machine_id, color: colors.append(
                (machine_id, color)
            ) or True,
            state=SimpleNamespace(
                active=SimpleNamespace(version=1, machines=(object(),)),
                client_color=lambda machine_id: "#3B82F6",
            ),
        )
        gui = ConduitGUI.__new__(ConduitGUI)
        gui.server = server
        gui.topology_editor = editor
        gui.after = lambda delay, callback: callback()

        gui._on_server_display_inventory(
            server,
            {
                "session_id": "session-a",
                "inventory": display_group_to_message(group),
            },
        )

        self.assertEqual(session.display_inventory, group)
        self.assertEqual(colors, [("device-a", "#3B82F6")])
        self.assertEqual(sent[0][0], "session-a")
        self.assertEqual(sent[0][1]["type"], "topology_identify")

    def test_changed_client_displays_update_only_draft_and_warn_on_server(self):
        warnings = []
        refreshed = []
        active = object()
        group = MachineDisplayGroup(
            "device-a",
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
        session = SimpleNamespace(
            peer_identity="device-a",
            color="#3B82F6",
            display_inventory=None,
            draft_placement=None,
        )
        server = SimpleNamespace(
            control_connected=True,
            session_registry=SimpleNamespace(get=lambda session_id: session),
            control_network=SimpleNamespace(send_message=lambda *args, **kwargs: True),
        )
        editor = SimpleNamespace(
            add_client=lambda value: refreshed.append(value) or True,
            set_client_color=lambda *args: True,
            state=SimpleNamespace(active=active),
        )
        gui = ConduitGUI.__new__(ConduitGUI)
        gui.server = server
        gui.topology_editor = editor
        gui.after = lambda delay, callback: callback()
        gui._show_display_change_warning = warnings.append
        gui._record_topology_rescan_inventory = lambda *args: False

        gui._on_server_display_inventory(
            server,
            {
                "session_id": "session-a",
                "reason": "display_changed",
                "inventory": display_group_to_message(group),
            },
        )

        self.assertEqual(refreshed, [group])
        self.assertEqual(warnings, [group])
        self.assertIs(editor.state.active, active)

    def test_local_server_display_change_updates_only_draft_and_warns(self):
        warnings = []
        refreshed = []
        active = object()
        source = object()
        group = MachineDisplayGroup(
            "server",
            "ParthPC",
            (
                Display(
                    "primary",
                    NativeRect(0, 0, 2560, 1440),
                    100,
                    0,
                    True,
                ),
            ),
        )
        gui = ConduitGUI.__new__(ConduitGUI)
        gui.server = source
        gui.topology_editor = SimpleNamespace(
            refresh_machine=lambda value: refreshed.append(value) or True,
            state=SimpleNamespace(active=active),
        )
        gui._show_display_change_warning = warnings.append

        gui._apply_local_display_change(source, group)

        self.assertEqual(refreshed, [group])
        self.assertEqual(warnings, [group])
        self.assertIs(gui.topology_editor.state.active, active)

    def test_server_disconnect_removes_only_that_client_from_the_draft(self):
        removed = []
        warnings = []
        group = MachineDisplayGroup(
            "device-a",
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
        gui = ConduitGUI.__new__(ConduitGUI)
        server = object()
        gui.server = server
        gui.topology_editor = SimpleNamespace(
            remove_client=lambda machine_id: removed.append(machine_id),
            remove_clients_from_draft=lambda: removed.append("all"),
            state=SimpleNamespace(
                draft=SimpleNamespace(
                    machines=(SimpleNamespace(group=group),),
                ),
            ),
        )
        gui.after = lambda delay, callback: callback()
        gui._set_status = lambda message, color: None
        gui.ensure_visible = lambda: None
        gui.server_port_entry = SimpleNamespace(get=lambda: "28903")
        gui._show_client_disconnect_warning = warnings.append

        gui._on_server_client_disconnected(
            {"peer_identity": "device-a", "session_id": "session-a"}
        )

        self.assertEqual(removed, ["device-a"])
        self.assertEqual(warnings, ["ParthSurface"])

    def test_replacement_choice_preserves_freed_draft_placement_for_candidate(self):
        self.assertTrue(hasattr(ConduitGUI, "_resolve_replacement_candidate"))
        registry = SessionRegistry("secret")

        def ready(identity, address):
            admission = registry.authenticate_control(
                "secret",
                peer_identity=identity,
                windows_name=identity,
                peer_address=address,
                lane=Client(),
            )
            registry.bind_lane(
                admission.data_token,
                "data",
                admission.session_id,
                peer_identity=identity,
                peer_address=address,
                lane=Client(),
            )
            registry.bind_lane(
                admission.file_token,
                "file",
                admission.session_id,
                peer_identity=identity,
                peer_address=address,
                lane=Client(),
            )
            return admission

        first = ready("device-a", "192.0.2.10")
        ready("device-b", "192.0.2.11")
        pending = registry.authenticate_control(
            "secret",
            peer_identity="device-c",
            windows_name="device-c",
            peer_address="192.0.2.12",
            lane=Client(),
        )
        removed = []
        placed = SimpleNamespace(
            group=SimpleNamespace(machine_id="device-a"),
            x=-1,
            y=0,
        )
        editor = SimpleNamespace(
            state=SimpleNamespace(draft=SimpleNamespace(machines=(placed,))),
            remove_client=lambda machine_id: removed.append(machine_id),
        )
        lost = []
        server = SimpleNamespace(
            session_registry=registry,
            input_router=SimpleNamespace(
                destination_lost=lambda session_id: lost.append(session_id)
            ),
        )
        gui = ConduitGUI.__new__(ConduitGUI)
        gui.server = server
        gui.topology_editor = editor

        resolution = gui._resolve_replacement_candidate(
            server,
            pending.session_id,
            CandidateDecision.REPLACE,
            replace_session_id=first.session_id,
        )

        promoted = registry.get(resolution.session_id)
        self.assertEqual(promoted.draft_placement, (-1, 0))
        self.assertEqual(removed, ["device-a"])
        self.assertEqual(lost, [first.session_id])

    def test_successful_apply_promotes_replacement_to_its_inherited_color(self):
        registry = SessionRegistry("secret")

        def ready(identity, address):
            admission = registry.authenticate_control(
                "secret",
                peer_identity=identity,
                windows_name=identity,
                peer_address=address,
                lane=Client(),
            )
            registry.bind_lane(
                admission.data_token,
                "data",
                admission.session_id,
                peer_identity=identity,
                peer_address=address,
                lane=Client(),
            )
            registry.bind_lane(
                admission.file_token,
                "file",
                admission.session_id,
                peer_identity=identity,
                peer_address=address,
                lane=Client(),
            )
            return admission

        replaced = ready("device-a", "192.0.2.10")
        ready("device-b", "192.0.2.11")
        pending = registry.authenticate_control(
            "secret",
            peer_identity="device-c",
            windows_name="device-c",
            peer_address="192.0.2.12",
            lane=Client(),
        )
        resolution = registry.resolve_candidate(
            CandidateDecision.REPLACE,
            replace_session_id=replaced.session_id,
        )
        replacement = registry.get(resolution.session_id)
        inherited_color = replacement.replacement_color
        colors = []
        candidate = SimpleNamespace(
            server_id="server",
            machines=(
                SimpleNamespace(group=SimpleNamespace(machine_id="server")),
                SimpleNamespace(group=SimpleNamespace(machine_id="device-c")),
            ),
            edge_mappings=(
                SimpleNamespace(
                    source_machine_id="server",
                    destination_machine_id="device-c",
                ),
            ),
        )
        editor = SimpleNamespace(
            state=SimpleNamespace(commit=lambda topology: None),
            set_client_color=lambda machine_id, color: colors.append(
                (machine_id, color)
            ),
            _render=lambda: None,
        )
        server = SimpleNamespace(
            session_registry=registry,
            control_connected=False,
        )
        gui = ConduitGUI.__new__(ConduitGUI)
        gui.server = server
        gui.topology_editor = editor
        gui._set_status = lambda message, color: None

        gui._finish_topology_apply(server, candidate, True)

        self.assertEqual(replacement.color, inherited_color)
        self.assertIsNone(replacement.replacement_color)
        self.assertEqual(colors, [("device-c", inherited_color)])


if __name__ == "__main__":
    unittest.main()
