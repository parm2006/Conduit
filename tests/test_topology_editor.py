import unittest

import app.topology_editor as topology_editor_module

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

    def test_disconnecting_one_client_removes_only_its_draft_group(self):
        self.assertTrue(hasattr(self.state, "remove_client"))
        first = machine("client-a", "ParthSurface")
        second = machine("client-b", "TravelPC")
        self.state.add_client(first)
        self.state.add_client(second)
        active_before = self.state.active

        removed = self.state.remove_client("client-a")

        self.assertEqual(removed.group.machine_id, "client-a")
        self.assertEqual(self.state.active, active_before)
        self.assertEqual(
            {placed.group.machine_id for placed in self.state.draft.machines},
            {"server", "client-b"},
        )

    def test_cancel_after_active_client_disconnect_keeps_editor_renderable(self):
        client = machine("client", "ParthSurface")
        self.state.add_client(client)
        self.assertTrue(self.state.apply().is_valid)

        self.state.remove_client("client")
        self.state.cancel()

        cells = self.state.cell_views()
        client_cell = next(cell for cell in cells if cell.machine_id == "client")
        self.assertIn(client_cell.color, CLIENT_COLORS)

    def test_authoritative_reconcile_restores_server_and_preserves_survivor(self):
        first = machine("client-a", "ParthSurface")
        second = machine("client-b", "TravelPC")
        self.state.add_client(first)
        self.state.add_client(second)
        self.state.move_machine("client-b", -1, 0)
        refreshed_server = machine("server", "ParthPC")
        refreshed_second = machine("client-b", "TravelPC")
        self.state.draft = DraftTopology("server", ())

        self.state.reconcile_draft(
            refreshed_server,
            (refreshed_second,),
            placements={"client-b": (-1, 0)},
        )

        placed = {
            item.group.machine_id: item
            for item in self.state.draft.machines
        }
        self.assertEqual(set(placed), {"server", "client-b"})
        self.assertEqual((placed["server"].x, placed["server"].y), (0, 0))
        self.assertEqual((placed["client-b"].x, placed["client-b"].y), (-1, 0))
        self.assertIs(placed["server"].group, refreshed_server)
        self.assertIs(placed["client-b"].group, refreshed_second)

    def test_reconcile_can_replace_both_cached_clients_without_running_out_of_colors(self):
        self.state.add_client(machine("old-a", "OldA"))
        self.state.add_client(machine("old-b", "OldB"))
        self.state.apply()

        self.state.reconcile_draft(
            self.server,
            (machine("new-a", "NewA"), machine("new-b", "NewB")),
        )

        colors = {
            cell.machine_id: cell.color
            for cell in self.state.cell_views()
            if cell.machine_id != "server"
        }
        self.assertEqual(set(colors), {"new-a", "new-b"})
        self.assertEqual(len(set(colors.values())), 2)

    def test_registry_color_can_override_automatic_editor_color(self):
        self.assertTrue(hasattr(self.state, "set_client_color"))
        client = machine("client", "ParthSurface")
        self.state.add_client(client)

        self.assertTrue(self.state.set_client_color("client", CLIENT_COLORS[2]))

        client_cell = next(
            cell for cell in self.state.cell_views()
            if cell.machine_id == "client"
        )
        self.assertEqual(client_cell.color, CLIENT_COLORS[2])

    def test_client_inventory_cannot_replace_the_fixed_server_identity(self):
        spoofed = machine("server", "SpoofedClient")

        added = self.state.add_client(spoofed)

        self.assertFalse(added)
        self.assertEqual(self.state.draft.machines[0].group.windows_name, "ParthPC")


class TopologyEditorApplyFlowTests(unittest.TestCase):
    def test_editor_canvas_is_exactly_seven_cells_by_four_cells(self):
        self.assertEqual(TopologyEditor.GRID_WIDTH, CELL_SIZE * 7)
        self.assertEqual(TopologyEditor.GRID_HEIGHT, CELL_SIZE * 4)

    def test_grid_geometry_fills_scaled_canvas_at_common_windows_dpi_sizes(self):
        geometry_type = getattr(
            topology_editor_module,
            "TopologyGridGeometry",
            None,
        )
        self.assertIsNotNone(geometry_type)

        for width, height in ((280, 160), (350, 200), (420, 240), (560, 320)):
            with self.subTest(size=(width, height)):
                geometry = geometry_type(width, height)
                self.assertEqual(len(geometry.x_boundaries), 8)
                self.assertEqual(len(geometry.y_boundaries), 5)
                self.assertEqual(geometry.x_boundaries[0], 0)
                self.assertEqual(geometry.x_boundaries[-1], width)
                self.assertEqual(geometry.y_boundaries[0], 0)
                self.assertEqual(geometry.y_boundaries[-1], height)
                self.assertLessEqual(
                    max(
                        right - left
                        for left, right in zip(
                            geometry.x_boundaries,
                            geometry.x_boundaries[1:],
                        )
                    )
                    - min(
                        right - left
                        for left, right in zip(
                            geometry.x_boundaries,
                            geometry.x_boundaries[1:],
                        )
                    ),
                    1,
                )

    def test_scaled_grid_hit_testing_round_trips_every_visible_cell(self):
        geometry_type = getattr(
            topology_editor_module,
            "TopologyGridGeometry",
            None,
        )
        self.assertIsNotNone(geometry_type)

        for width, height in ((280, 160), (350, 200), (420, 240), (560, 320)):
            geometry = geometry_type(width, height)
            for logical_y in range(-2, 2):
                for logical_x in range(-3, 4):
                    with self.subTest(
                        size=(width, height),
                        cell=(logical_x, logical_y),
                    ):
                        left, top, right, bottom = geometry.cell_bounds(
                            logical_x,
                            logical_y,
                        )
                        point = ((left + right) // 2, (top + bottom) // 2)
                        self.assertEqual(
                            geometry.event_grid(*point),
                            (logical_x, logical_y),
                        )

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
