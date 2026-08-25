import unittest

from app.display_topology import (
    Display,
    DraftTopology,
    MachineDisplayGroup,
    NativeRect,
    PlacedMachine,
    edge_entry_point,
    edge_ratio,
)


class MachineDisplayGroupTests(unittest.TestCase):
    @staticmethod
    def _display(display_id, primary=False, enabled=True):
        return Display(
            display_id=display_id,
            rect=NativeRect(0, 0, 1920, 1080),
            dpi_percent=100,
            orientation=0,
            primary=primary,
            enabled=enabled,
        )

    def test_uneven_horizontal_displays_normalize_to_equal_adjacent_cells(self):
        primary = Display(
            display_id="display-primary",
            rect=NativeRect(0, 0, 1920, 1080),
            dpi_percent=100,
            orientation=0,
            primary=True,
        )
        secondary = Display(
            display_id="display-secondary",
            rect=NativeRect(1920, 0, 4480, 1440),
            dpi_percent=150,
            orientation=0,
            primary=False,
        )

        group = MachineDisplayGroup(
            machine_id="machine-server",
            windows_name="ParthPC",
            displays=(primary, secondary),
        )

        cells = {cell.display_id: (cell.x, cell.y) for cell in group.cells}

        self.assertEqual(
            cells,
            {
                "display-primary": (0, 0),
                "display-secondary": (1, 0),
            },
        )
        self.assertEqual(group.display("display-primary").rect, primary.rect)
        self.assertEqual(group.display("display-secondary").rect, secondary.rect)

    def test_staggered_side_by_side_displays_remain_horizontal_neighbors(self):
        primary = Display(
            display_id="display-primary",
            rect=NativeRect(0, 0, 1920, 1080),
            dpi_percent=100,
            orientation=0,
            primary=True,
        )
        staggered = Display(
            display_id="display-staggered",
            rect=NativeRect(1920, 200, 4480, 1640),
            dpi_percent=150,
            orientation=0,
            primary=False,
        )
        group = MachineDisplayGroup(
            machine_id="machine-server",
            windows_name="ParthPC",
            displays=(primary, staggered),
        )

        cells = {cell.display_id: (cell.x, cell.y) for cell in group.cells}

        self.assertEqual(
            cells,
            {
                "display-primary": (0, 0),
                "display-staggered": (1, 0),
            },
        )

    def test_edge_ratio_maps_proportionally_between_different_resolutions(self):
        source = NativeRect(0, 0, 1920, 1080)
        destination = NativeRect(1920, 0, 4480, 1440)

        ratio = edge_ratio(source, "right", x=1919, y=540)
        point = edge_entry_point(destination, "left", ratio, inset=2)

        self.assertEqual(ratio, 0.5)
        self.assertEqual(point, (1922, 720))

    def test_primary_display_is_origin_when_an_attached_display_has_negative_coordinates(self):
        left_display = Display(
            display_id="display-left",
            rect=NativeRect(-2560, 0, 0, 1440),
            dpi_percent=150,
            orientation=0,
            primary=False,
        )
        primary = Display(
            display_id="display-primary",
            rect=NativeRect(0, 0, 1920, 1080),
            dpi_percent=100,
            orientation=0,
            primary=True,
        )
        group = MachineDisplayGroup(
            machine_id="machine-server",
            windows_name="ParthPC",
            displays=(left_display, primary),
        )

        cells = {cell.display_id: (cell.x, cell.y) for cell in group.cells}

        self.assertEqual(
            cells,
            {
                "display-left": (-1, 0),
                "display-primary": (0, 0),
            },
        )

    def test_display_group_requires_exactly_one_enabled_primary(self):
        with self.assertRaisesRegex(ValueError, "one enabled primary"):
            MachineDisplayGroup(
                "machine",
                "ParthPC",
                (self._display("one"),),
            )

        with self.assertRaisesRegex(ValueError, "one enabled primary"):
            MachineDisplayGroup(
                "machine",
                "ParthPC",
                (
                    self._display("one", primary=True),
                    self._display("two", primary=True),
                ),
            )

    def test_display_group_rejects_duplicate_stable_display_ids(self):
        with self.assertRaisesRegex(ValueError, "display IDs"):
            MachineDisplayGroup(
                "machine",
                "ParthPC",
                (
                    self._display("same", primary=True),
                    self._display("same"),
                ),
            )


class DraftTopologyTests(unittest.TestCase):
    @staticmethod
    def _single_display_group(machine_id, name, primary=True):
        return MachineDisplayGroup(
            machine_id=machine_id,
            windows_name=name,
            displays=(
                Display(
                    display_id=f"{machine_id}-display",
                    rect=NativeRect(0, 0, 1920, 1080),
                    dpi_percent=100,
                    orientation=0,
                    primary=primary,
                ),
            ),
        )

    def test_full_cell_edge_builds_a_valid_server_client_graph(self):
        server = self._single_display_group("server", "ParthPC", primary=True)
        client = self._single_display_group("client", "ParthSurface")
        draft = DraftTopology(
            server_id="server",
            machines=(
                PlacedMachine(server, x=0, y=0),
                PlacedMachine(client, x=1, y=0),
            ),
        )

        result = draft.validate()

        self.assertTrue(result.is_valid)
        self.assertEqual(result.validated.neighbors("server"), ("client",))
        self.assertEqual(result.validated.neighbors("client"), ("server",))

    def test_client_separated_by_a_gap_is_invalid(self):
        server = self._single_display_group("server", "ParthPC", primary=True)
        client = self._single_display_group("client", "ParthSurface")
        draft = DraftTopology(
            server_id="server",
            machines=(
                PlacedMachine(server, x=0, y=0),
                PlacedMachine(client, x=2, y=0),
            ),
        )

        result = draft.validate()

        self.assertFalse(result.is_valid)
        self.assertEqual(result.issues[0].code, "disconnected")
        self.assertEqual(result.issues[0].machine_ids, ("client",))

    def test_overlapping_machine_cells_report_both_machines(self):
        server = self._single_display_group("server", "ParthPC", primary=True)
        client = self._single_display_group("client", "ParthSurface")
        draft = DraftTopology(
            server_id="server",
            machines=(
                PlacedMachine(server, x=0, y=0),
                PlacedMachine(client, x=0, y=0),
            ),
        )

        result = draft.validate()

        self.assertFalse(result.is_valid)
        self.assertEqual(result.issues[0].code, "overlap")
        self.assertEqual(result.issues[0].machine_ids, ("client", "server"))

    def test_more_than_two_clients_is_invalid(self):
        server = self._single_display_group("server", "ParthPC", primary=True)
        clients = tuple(
            self._single_display_group(f"client-{index}", f"Client{index}")
            for index in range(1, 4)
        )
        draft = DraftTopology(
            server_id="server",
            machines=(
                PlacedMachine(server, x=0, y=0),
                PlacedMachine(clients[0], x=1, y=0),
                PlacedMachine(clients[1], x=2, y=0),
                PlacedMachine(clients[2], x=3, y=0),
            ),
        )

        result = draft.validate()

        self.assertFalse(result.is_valid)
        self.assertEqual(result.issues[0].code, "too-many-clients")
        self.assertEqual(
            result.issues[0].machine_ids,
            ("client-1", "client-2", "client-3"),
        )

    def test_server_must_remain_at_the_fixed_origin(self):
        server = self._single_display_group("server", "ParthPC", primary=True)
        client = self._single_display_group("client", "ParthSurface")
        draft = DraftTopology(
            server_id="server",
            machines=(
                PlacedMachine(server, x=4, y=3),
                PlacedMachine(client, x=5, y=3),
            ),
        )

        result = draft.validate()

        self.assertFalse(result.is_valid)
        self.assertEqual(result.issues[0].code, "server-not-anchored")
        self.assertEqual(result.issues[0].machine_ids, ("server",))

    def test_missing_server_is_invalid_instead_of_raising(self):
        client = self._single_display_group("client", "ParthSurface")
        draft = DraftTopology(
            server_id="server",
            machines=(PlacedMachine(client, x=0, y=0),),
        )

        result = draft.validate()

        self.assertFalse(result.is_valid)
        self.assertEqual(result.issues[0].code, "missing-server")
        self.assertEqual(result.issues[0].machine_ids, ("server",))

    def test_auto_placement_uses_right_then_left_of_the_server_primary_cell(self):
        server = self._single_display_group("server", "ParthPC")
        first_client = self._single_display_group("client-1", "ParthSurface")
        second_client = self._single_display_group("client-2", "OtherPC")
        server_only = DraftTopology(
            server_id="server",
            machines=(PlacedMachine(server, x=0, y=0),),
        )

        first_placement = server_only.find_auto_placement(first_client)
        with_first_client = DraftTopology(
            server_id="server",
            machines=(server_only.machines[0], first_placement),
        )
        second_placement = with_first_client.find_auto_placement(second_client)

        self.assertEqual((first_placement.x, first_placement.y), (1, 0))
        self.assertEqual((second_placement.x, second_placement.y), (-1, 0))

    def test_auto_placement_falls_back_to_top_when_server_displays_occupy_both_sides(self):
        left = Display(
            "server-left",
            NativeRect(-1920, 0, 0, 1080),
            100,
            0,
            False,
        )
        primary = Display(
            "server-primary",
            NativeRect(0, 0, 1920, 1080),
            100,
            0,
            True,
        )
        right = Display(
            "server-right",
            NativeRect(1920, 0, 3840, 1080),
            100,
            0,
            False,
        )
        server = MachineDisplayGroup("server", "ParthPC", (left, primary, right))
        client = self._single_display_group("client", "ParthSurface")
        draft = DraftTopology(
            server_id="server",
            machines=(PlacedMachine(server, x=0, y=0),),
        )

        placement = draft.find_auto_placement(client)

        self.assertEqual((placement.x, placement.y), (0, -1))

    def test_auto_placement_uses_the_candidate_group_boundary_on_the_right(self):
        server = self._single_display_group("server", "ParthPC")
        client_primary = Display(
            "client-primary",
            NativeRect(0, 0, 1920, 1080),
            100,
            0,
            True,
        )
        client_left = Display(
            "client-left",
            NativeRect(-1920, 0, 0, 1080),
            100,
            0,
            False,
        )
        client = MachineDisplayGroup(
            "client",
            "ParthSurface",
            (client_left, client_primary),
        )
        draft = DraftTopology(
            server_id="server",
            machines=(PlacedMachine(server, x=0, y=0),),
        )

        placement = draft.find_auto_placement(client)

        self.assertEqual((placement.x, placement.y), (2, 0))
        self.assertEqual(set(placement.occupied_cells), {(1, 0), (2, 0)})

    def test_only_a_validated_topology_can_activate_with_a_version(self):
        server = self._single_display_group("server", "ParthPC")
        client = self._single_display_group("client", "ParthSurface")
        result = DraftTopology(
            server_id="server",
            machines=(
                PlacedMachine(server, x=0, y=0),
                PlacedMachine(client, x=1, y=0),
            ),
        ).validate()

        active = result.validated.activate(version=7)

        self.assertEqual(active.version, 7)
        self.assertEqual(active.server_id, "server")
        self.assertEqual(active.neighbors("server"), ("client",))

    def test_validated_topology_maps_both_directions_of_a_display_edge(self):
        server = self._single_display_group("server", "ParthPC")
        client = self._single_display_group("client", "ParthSurface")
        validated = DraftTopology(
            server_id="server",
            machines=(
                PlacedMachine(server, x=0, y=0),
                PlacedMachine(client, x=1, y=0),
            ),
        ).validate().validated

        outgoing = validated.edge_for("server", "server-display", "right")
        returning = validated.edge_for("client", "client-display", "left")

        self.assertEqual(outgoing.destination_machine_id, "client")
        self.assertEqual(outgoing.destination_display_id, "client-display")
        self.assertEqual(outgoing.destination_side, "left")
        self.assertEqual(returning.destination_machine_id, "server")
        self.assertEqual(returning.destination_side, "right")

    def test_partial_cell_placement_is_invalid(self):
        server = self._single_display_group("server", "ParthPC")
        client = self._single_display_group("client", "ParthSurface")
        draft = DraftTopology(
            server_id="server",
            machines=(
                PlacedMachine(server, x=0, y=0),
                PlacedMachine(client, x=1, y=0.5),
            ),
        )

        result = draft.validate()

        self.assertFalse(result.is_valid)
        self.assertEqual(result.issues[0].code, "unaligned-placement")
        self.assertEqual(result.issues[0].machine_ids, ("client",))

    def test_duplicate_machine_ids_are_invalid(self):
        server = self._single_display_group("server", "ParthPC")
        first = self._single_display_group("client", "FirstPC")
        duplicate = self._single_display_group("client", "SecondPC")
        draft = DraftTopology(
            server_id="server",
            machines=(
                PlacedMachine(server, x=0, y=0),
                PlacedMachine(first, x=1, y=0),
                PlacedMachine(duplicate, x=-1, y=0),
            ),
        )

        result = draft.validate()

        self.assertFalse(result.is_valid)
        self.assertEqual(result.issues[0].code, "duplicate-machine-id")
        self.assertEqual(result.issues[0].machine_ids, ("client",))

    def test_client_can_reach_server_through_the_other_client(self):
        server = self._single_display_group("server", "ParthPC")
        first = self._single_display_group("client-1", "FirstPC")
        second = self._single_display_group("client-2", "SecondPC")
        result = DraftTopology(
            server_id="server",
            machines=(
                PlacedMachine(server, x=0, y=0),
                PlacedMachine(first, x=1, y=0),
                PlacedMachine(second, x=2, y=0),
            ),
        ).validate()

        self.assertTrue(result.is_valid)
        self.assertEqual(result.validated.neighbors("client-1"), ("client-2", "server"))
        self.assertEqual(result.validated.neighbors("client-2"), ("client-1",))

    def test_diagonal_corner_contact_is_disconnected(self):
        server = self._single_display_group("server", "ParthPC")
        client = self._single_display_group("client", "ParthSurface")
        result = DraftTopology(
            server_id="server",
            machines=(
                PlacedMachine(server, x=0, y=0),
                PlacedMachine(client, x=1, y=1),
            ),
        ).validate()

        self.assertFalse(result.is_valid)
        self.assertEqual(result.issues[0].code, "disconnected")


if __name__ == "__main__":
    unittest.main()
