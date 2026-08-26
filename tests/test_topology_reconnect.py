import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.display_topology import (
    Display,
    MachineDisplayGroup,
    NativeRect,
)
from app.gui import ConduitGUI
from app.server import ConduitServer
from app.windows_displays import display_group_to_message


def _group(machine_id, name="Client"):
    return MachineDisplayGroup(
        machine_id,
        name,
        (
            Display(
                f"{machine_id}-display",
                NativeRect(0, 0, 1920, 1080),
                100,
                0,
                True,
            ),
        ),
    )


class TopologyReconnectTests(unittest.TestCase):
    def test_saved_machine_is_not_routable_until_current_session_is_applied(self):
        session = SimpleNamespace(
            session_id="new-session",
            peer_identity="client-1",
            ready=True,
        )
        server = ConduitServer.__new__(ConduitServer)
        server.session_registry = SimpleNamespace(
            active_sessions=lambda: (session,),
        )
        server._active_topology_session_ids = set()

        self.assertIsNone(server._session_for_machine("client-1"))

        server._active_topology_session_ids.add("new-session")

        self.assertIs(server._session_for_machine("client-1"), session)

    def test_rescan_targets_every_ready_client_session(self):
        sent = []
        timeouts = []
        sessions = (
            SimpleNamespace(session_id="session-1"),
            SimpleNamespace(session_id="session-2"),
        )
        server = SimpleNamespace(
            control_connected=True,
            session_registry=SimpleNamespace(
                ready_sessions=lambda: sessions,
            ),
            control_network=SimpleNamespace(
                send_message=lambda message, session_id=None: sent.append(
                    (session_id, message)
                ) or True,
            ),
        )
        gui = ConduitGUI.__new__(ConduitGUI)
        gui.server = server
        gui.topology_editor = SimpleNamespace(
            state=SimpleNamespace(
                draft=SimpleNamespace(server_id="server", machines=()),
            ),
        )
        gui._set_status = lambda message, color: None
        gui.after = lambda delay, callback: timeouts.append(callback)

        with patch(
            "app.gui.WindowsDisplayDiscovery.discover",
            return_value=_group("server", "ParthPC"),
        ):
            result = gui._begin_topology_rescan()

        self.assertFalse(result)
        self.assertEqual(
            [session_id for session_id, _message in sent],
            ["session-1", "session-2"],
        )
        self.assertEqual(
            gui._pending_topology_rescan["waiting"],
            frozenset({"session-1", "session-2"}),
        )
        self.assertEqual(
            gui._pending_topology_rescan["server_group"].machine_id,
            "server",
        )
        self.assertEqual(gui._pending_topology_rescan["placements"], {})
        self.assertEqual(len(timeouts), 1)

    def test_rescan_applies_only_after_every_inventory_arrives(self):
        applied = []
        reconciled = []
        rendered = []
        colors = []
        sessions = {
            "session-1": SimpleNamespace(
                peer_identity="client-1",
                color="#3B82F6",
                display_inventory=None,
                draft_placement=None,
            ),
            "session-2": SimpleNamespace(
                peer_identity="client-2",
                color="#34D399",
                display_inventory=None,
                draft_placement=None,
            ),
        }
        server = SimpleNamespace(
            control_connected=True,
            session_registry=SimpleNamespace(
                get=lambda session_id: sessions.get(session_id),
            ),
            control_network=SimpleNamespace(
                send_message=lambda message, session_id=None: True,
            ),
        )
        editor = SimpleNamespace(
            add_client=lambda group: True,
            set_client_color=lambda machine_id, color: True,
            apply_current_draft=lambda: applied.append(True),
            _render=lambda: rendered.append(True),
            state=SimpleNamespace(
                active=SimpleNamespace(version=1, machines=(object(),)),
                client_color=lambda machine_id: "unused",
                reconcile_draft=lambda server_group, client_groups, placements=None: reconciled.append(
                    (
                        server_group,
                        tuple(client_groups),
                        dict(placements or {}),
                    )
                ),
                set_client_color=lambda machine_id, color: colors.append(
                    (machine_id, color)
                )
                or True,
            ),
        )
        gui = ConduitGUI.__new__(ConduitGUI)
        gui.server = server
        gui.topology_editor = editor
        gui.after = lambda delay, callback: callback()
        gui._pending_topology_rescan = {
            "source": server,
            "waiting": frozenset(sessions),
            "received": set(),
            "server_group": _group("server", "ParthPC"),
            "inventories": {},
            "placements": {"client-1": (-1, 0)},
        }

        gui._on_server_display_inventory(
            server,
            {
                "session_id": "session-1",
                "inventory": display_group_to_message(_group("client-1")),
            },
        )
        self.assertEqual(applied, [])

        gui._on_server_display_inventory(
            server,
            {
                "session_id": "session-2",
                "inventory": display_group_to_message(_group("client-2")),
            },
        )

        self.assertEqual(applied, [True])
        self.assertEqual(rendered, [True])
        self.assertEqual(
            {group.machine_id for group in reconciled[0][1]},
            {"client-1", "client-2"},
        )
        self.assertEqual(reconciled[0][2], {"client-1": (-1, 0)})
        self.assertEqual(
            set(colors),
            {("client-1", "#3B82F6"), ("client-2", "#34D399")},
        )
        self.assertIsNone(gui._pending_topology_rescan)

    def test_old_rescan_timeout_cannot_cancel_a_newer_rescan(self):
        statuses = []
        gui = ConduitGUI.__new__(ConduitGUI)
        old = {"source": object()}
        current = {"source": object()}
        gui._pending_topology_rescan = current
        gui._set_status = lambda message, color: statuses.append((message, color))

        gui._expire_topology_rescan(old)

        self.assertIs(gui._pending_topology_rescan, current)
        self.assertEqual(statuses, [])

    def test_apply_and_cancel_notices_target_both_ready_clients(self):
        sent = []
        sessions = (
            SimpleNamespace(session_id="session-1"),
            SimpleNamespace(session_id="session-2"),
        )
        server = SimpleNamespace(
            session_registry=SimpleNamespace(
                ready_sessions=lambda: sessions,
            ),
            control_network=SimpleNamespace(
                send_message=lambda message, session_id=None: sent.append(
                    (session_id, dict(message))
                ) or True,
            ),
        )

        self.assertTrue(
            ConduitGUI._notify_ready_clients(
                server,
                {"type": "topology_applied"},
            )
        )

        self.assertEqual(
            sent,
            [
                ("session-1", {"type": "topology_applied"}),
                ("session-2", {"type": "topology_applied"}),
            ],
        )


if __name__ == "__main__":
    unittest.main()
