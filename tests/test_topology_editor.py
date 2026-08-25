import unittest

from app.display_topology import (
    Display,
    DraftTopology,
    MachineDisplayGroup,
    NativeRect,
    PlacedMachine,
)
from app.topology_editor import (
    CELL_SIZE,
    CLIENT_COLORS,
    SERVER_COLOR,
    TopologyEditor,
    TopologyEditorState,
)


def machine(machine_id, name):
    return MachineDisplayGroup(
        machine_id=machine_id,
        windows_name=name,
        displays=(
            Display(
                display_id=f"{machine_id}-display",
                rect=NativeRect(0, 0, 1920, 1080),
                dpi_percent=100,
                orientation=0,
                primary=True,
            ),
        ),
    )


class TopologyEditorStateTests(unittest.TestCase):
    def setUp(self):
        self.server = machine("server", "ParthPC")
        validated = DraftTopology(
            server_id="server",
            machines=(PlacedMachine(self.server, 0, 0),),
        ).validate().validated
        self.state = TopologyEditorState(validated.activate(version=1))

    def test_cells_are_exactly_40_pixels_and_use_initials_plus_slot_colors(self):
        self.assertEqual(CELL_SIZE, 40)
        self.state.add_client(machine("client", "ParthSurface"))

        cells = self.state.cell_views()

        self.assertEqual(cells[0].letter, "P")
        self.assertEqual(cells[0].color, SERVER_COLOR)
        self.assertFalse(cells[0].movable)
        self.assertEqual(cells[1].letter, "P")
        self.assertEqual(cells[1].color, CLIENT_COLORS[0])
        self.assertTrue(cells[1].movable)
        self.assertNotIn("192.168", "".join(cell.letter for cell in cells))

    def test_invalid_drag_marks_only_client_after_apply_and_cancel_restores_active(self):
        client = machine("client", "ParthSurface")
        self.state.add_client(client)
        self.assertTrue(self.state.apply().is_valid)
        active_before_edit = self.state.active

        self.assertTrue(self.state.move_machine("client", x=3, y=0))
        self.assertEqual(self.state.invalid_machine_ids, ())
        invalid = self.state.apply()

        self.assertFalse(invalid.is_valid)
        self.assertEqual(self.state.active, active_before_edit)
        self.assertEqual(self.state.invalid_machine_ids, ("client",))

        self.state.cancel()

        self.assertEqual(self.state.draft.machines, active_before_edit.machines)
        self.assertEqual(self.state.invalid_machine_ids, ())

    def test_server_cannot_move_or_receive_invalid_state(self):
        self.state.add_client(machine("client", "ParthSurface"))

        moved = self.state.move_machine("server", x=4, y=4)
        self.state.move_machine("client", x=4, y=4)
        self.state.apply()

        self.assertFalse(moved)
        server = next(
            placed for placed in self.state.draft.machines
            if placed.group.machine_id == "server"
        )
        self.assertEqual((server.x, server.y), (0, 0))
        self.assertNotIn("server", self.state.invalid_machine_ids)

    def test_repeated_inventory_updates_the_existing_client_group_in_place(self):
        original = machine("client", "ParthSurface")
        refreshed = MachineDisplayGroup(
            machine_id="client",
            windows_name="ParthSurface",
            displays=(
                original.displays[0],
                Display(
                    display_id="client-hdmi",
                    rect=NativeRect(1920, 0, 4480, 1440),
                    dpi_percent=150,
                    orientation=0,
                    primary=False,
                ),
            ),
        )
        self.state.add_client(original)

        updated = self.state.add_client(refreshed)

        client_groups = tuple(
            placed
            for placed in self.state.draft.machines
            if placed.group.machine_id == "client"
        )
        self.assertTrue(updated)
        self.assertEqual(len(client_groups), 1)
        self.assertEqual(len(client_groups[0].group.displays), 2)
        self.assertEqual((client_groups[0].x, client_groups[0].y), (1, 0))

    def test_server_rescan_updates_only_the_draft_and_keeps_the_anchor(self):
        refreshed = MachineDisplayGroup(
            machine_id="server",
            windows_name="ParthPC",
            displays=(
                self.server.displays[0],
                Display(
                    display_id="server-hdmi",
                    rect=NativeRect(1920, 200, 4480, 1640),
                    dpi_percent=150,
                    orientation=0,
                    primary=False,
                ),
            ),
        )
        active_before_rescan = self.state.active

        updated = self.state.refresh_machine(refreshed)

        self.assertTrue(updated)
        self.assertEqual(self.state.active, active_before_rescan)
        server = self.state.draft.machines[0]
        self.assertEqual((server.x, server.y), (0, 0))
        self.assertEqual(len(server.group.displays), 2)

    def test_three_identification_colors_include_a_temporary_candidate_color(self):
        self.assertEqual(CLIENT_COLORS[2], "#A855F7")

    def test_returning_client_restores_saved_position_into_the_current_draft(self):
        client = machine("client", "ParthSurface")
        active = DraftTopology(
            server_id="server",
            machines=(
                PlacedMachine(self.server, 0, 0),
                PlacedMachine(client, -1, 0),
            ),
        ).validate().validated.activate(version=4)
        current_draft = DraftTopology(
            server_id="server",
            machines=(PlacedMachine(self.server, 0, 0),),
        )
        state = TopologyEditorState(active, draft=current_draft)

        state.add_client(client)

        restored = next(
            placed
            for placed in state.draft.machines
            if placed.group.machine_id == "client"
        )
        self.assertEqual((restored.x, restored.y), (-1, 0))

    def test_disconnected_client_leaves_active_unchanged_but_is_removed_from_draft(self):
        client = machine("client", "ParthSurface")
        self.state.add_client(client)
        self.state.apply()
        active_before_disconnect = self.state.active

        self.state.remove_clients_from_draft()

        self.assertEqual(self.state.active, active_before_disconnect)
        self.assertEqual(
            tuple(placed.group.machine_id for placed in self.state.draft.machines),
            ("server",),
        )

    def test_client_inventory_cannot_replace_the_fixed_server_identity(self):
        spoofed = machine("server", "SpoofedClient")

        added = self.state.add_client(spoofed)

        self.assertFalse(added)
        self.assertEqual(self.state.draft.machines[0].group.windows_name, "ParthPC")


class TopologyEditorApplyFlowTests(unittest.TestCase):
    def test_editor_canvas_is_exactly_seven_cells_by_four_cells(self):
        self.assertEqual(TopologyEditor.GRID_WIDTH, CELL_SIZE * 7)
        self.assertEqual(TopologyEditor.GRID_HEIGHT, CELL_SIZE * 4)

    def test_apply_waits_when_the_display_rescan_is_asynchronous(self):
        calls = []
        editor = TopologyEditor.__new__(TopologyEditor)
        editor.on_rescan = lambda: False
        editor.apply_current_draft = lambda: calls.append("apply")

        editor._apply()

        self.assertEqual(calls, [])

    def test_apply_continues_immediately_when_rescan_is_complete(self):
        calls = []
        editor = TopologyEditor.__new__(TopologyEditor)
        editor.on_rescan = lambda: True
        editor.apply_current_draft = lambda: calls.append("apply")

        editor._apply()

        self.assertEqual(calls, ["apply"])


if __name__ == "__main__":
    unittest.main()
