from dataclasses import dataclass


@dataclass(frozen=True)
class NativeRect:
    left: int
    top: int
    right: int
    bottom: int


def _positive_overlap(first_start, first_end, second_start, second_end):
    return min(first_end, second_end) > max(first_start, second_start)


def _touching_cell_offset(source, destination):
    vertical_overlap = _positive_overlap(
        source.top,
        source.bottom,
        destination.top,
        destination.bottom,
    )
    horizontal_overlap = _positive_overlap(
        source.left,
        source.right,
        destination.left,
        destination.right,
    )
    if source.right == destination.left and vertical_overlap:
        return 1, 0
    if source.left == destination.right and vertical_overlap:
        return -1, 0
    if source.bottom == destination.top and horizontal_overlap:
        return 0, 1
    if source.top == destination.bottom and horizontal_overlap:
        return 0, -1
    return None


def edge_ratio(rect, side, x, y):
    if side in ("left", "right"):
        position = y - rect.top
        extent = rect.bottom - rect.top
    elif side in ("top", "bottom"):
        position = x - rect.left
        extent = rect.right - rect.left
    else:
        raise ValueError(f"unsupported edge side: {side}")
    return max(0.0, min(1.0, position / extent))


def edge_entry_point(rect, side, ratio, inset=1):
    ratio = max(0.0, min(1.0, ratio))
    x_at_ratio = min(rect.right - 1, rect.left + int((rect.right - rect.left) * ratio))
    y_at_ratio = min(rect.bottom - 1, rect.top + int((rect.bottom - rect.top) * ratio))
    if side == "left":
        return rect.left + inset, y_at_ratio
    if side == "right":
        return rect.right - inset - 1, y_at_ratio
    if side == "top":
        return x_at_ratio, rect.top + inset
    if side == "bottom":
        return x_at_ratio, rect.bottom - inset - 1
    raise ValueError(f"unsupported edge side: {side}")

@dataclass(frozen=True)
class Display:
    display_id: str
    rect: NativeRect
    dpi_percent: int
    orientation: int
    primary: bool
    enabled: bool = True
    work_rect: NativeRect | None = None


@dataclass(frozen=True)
class DisplayCell:
    display_id: str
    x: int
    y: int


@dataclass(frozen=True)
class MachineDisplayGroup:
    machine_id: str
    windows_name: str
    displays: tuple[Display, ...]

    def __post_init__(self):
        if not self.displays:
            raise ValueError("machine display group cannot be empty")
        enabled_primaries = tuple(
            display for display in self.displays if display.enabled and display.primary
        )
        if len(enabled_primaries) != 1:
            raise ValueError("machine display group must have one enabled primary")
        display_ids = tuple(display.display_id for display in self.displays)
        if len(set(display_ids)) != len(display_ids):
            raise ValueError("machine display group has duplicate display IDs")

    @property
    def cells(self):
        enabled = tuple(display for display in self.displays if display.enabled)
        primary = next(display for display in enabled if display.primary)
        by_id = {display.display_id: display for display in enabled}
        positions = {primary.display_id: (0, 0)}
        occupied = {(0, 0)}
        pending = [primary]
        while pending:
            current = pending.pop(0)
            current_x, current_y = positions[current.display_id]
            neighbors = []
            for other in enabled:
                if other.display_id in positions:
                    continue
                offset = _touching_cell_offset(current.rect, other.rect)
                if offset is not None:
                    neighbors.append((other.display_id, other, offset))
            for _, other, (offset_x, offset_y) in sorted(neighbors):
                proposed = (current_x + offset_x, current_y + offset_y)
                if proposed in occupied:
                    continue
                positions[other.display_id] = proposed
                occupied.add(proposed)
                pending.append(other)

        # Windows normally exposes a connected desktop. Keep malformed or
        # deliberately gapped arrangements deterministic without inventing a
        # diagonal offset solely because monitor tops differ.
        for display in sorted(enabled, key=lambda item: item.display_id):
            if display.display_id in positions:
                continue
            delta_x = display.rect.left - primary.rect.left
            delta_y = display.rect.top - primary.rect.top
            if abs(delta_x) >= abs(delta_y):
                proposed = (1 if delta_x >= 0 else -1, 0)
            else:
                proposed = (0, 1 if delta_y >= 0 else -1)
            while proposed in occupied:
                proposed = (
                    proposed[0] + (1 if delta_x >= 0 else -1),
                    proposed[1],
                )
            positions[display.display_id] = proposed
            occupied.add(proposed)
        return tuple(
            DisplayCell(
                display_id=display.display_id,
                x=positions[display.display_id][0],
                y=positions[display.display_id][1],
            )
            for display in enabled
        )

    def display(self, display_id):
        for display in self.displays:
            if display.display_id == display_id:
                return display
        raise KeyError(display_id)


@dataclass(frozen=True)
class PlacedMachine:
    group: MachineDisplayGroup
    x: int
    y: int

    @property
    def occupied_cells(self):
        return tuple((self.x + cell.x, self.y + cell.y) for cell in self.group.cells)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    machine_ids: tuple[str, ...]


@dataclass(frozen=True)
class EdgeMapping:
    source_machine_id: str
    source_display_id: str
    source_side: str
    destination_machine_id: str
    destination_display_id: str
    destination_side: str


@dataclass(frozen=True)
class ResolvedEdge:
    mapping: EdgeMapping
    source_rect: NativeRect
    destination_rect: NativeRect
    destination_position: tuple[int, int]
    scale_x: float
    scale_y: float
    destination_dpi_percent: int


@dataclass(frozen=True)
class ValidatedTopology:
    server_id: str
    machines: tuple[PlacedMachine, ...]
    edges: tuple[tuple[str, str], ...]
    edge_mappings: tuple[EdgeMapping, ...]

    def neighbors(self, machine_id):
        adjacent = []
        for left, right in self.edges:
            if left == machine_id:
                adjacent.append(right)
            elif right == machine_id:
                adjacent.append(left)
        return tuple(sorted(adjacent))

    def edge_for(self, machine_id, display_id, side):
        for mapping in self.edge_mappings:
            if (
                mapping.source_machine_id == machine_id
                and mapping.source_display_id == display_id
                and mapping.source_side == side
            ):
                return mapping
        raise KeyError((machine_id, display_id, side))

    def activate(self, version):
        return ActiveTopology(
            version=version,
            server_id=self.server_id,
            machines=self.machines,
            edges=self.edges,
            edge_mappings=self.edge_mappings,
        )


@dataclass(frozen=True)
class ActiveTopology:
    version: int
    server_id: str
    machines: tuple[PlacedMachine, ...]
    edges: tuple[tuple[str, str], ...]
    edge_mappings: tuple[EdgeMapping, ...]

    def neighbors(self, machine_id):
        adjacent = []
        for left, right in self.edges:
            if left == machine_id:
                adjacent.append(right)
            elif right == machine_id:
                adjacent.append(left)
        return tuple(sorted(adjacent))

    def edge_for(self, machine_id, display_id, side):
        for mapping in self.edge_mappings:
            if (
                mapping.source_machine_id == machine_id
                and mapping.source_display_id == display_id
                and mapping.source_side == side
            ):
                return mapping
        raise KeyError((machine_id, display_id, side))

    def machine(self, machine_id):
        for placed in self.machines:
            if placed.group.machine_id == machine_id:
                return placed
        raise KeyError(machine_id)

    def display(self, machine_id, display_id):
        return self.machine(machine_id).group.display(display_id)

    def primary_display(self, machine_id):
        return next(
            display
            for display in self.machine(machine_id).group.displays
            if display.enabled and display.primary
        )

    def server_primary_center(self):
        primary = self.primary_display(self.server_id)
        return (
            primary.display_id,
            (
                primary.rect.left + (primary.rect.right - primary.rect.left) // 2,
                primary.rect.top + (primary.rect.bottom - primary.rect.top) // 2,
            ),
        )

    def resolve_edge(self, machine_id, display_id, side, ratio):
        mapping = self.edge_for(machine_id, display_id, side)
        source = self.display(machine_id, display_id)
        destination = self.display(
            mapping.destination_machine_id,
            mapping.destination_display_id,
        )
        server_primary = self.primary_display(self.server_id)
        server_width = server_primary.rect.right - server_primary.rect.left
        server_height = server_primary.rect.bottom - server_primary.rect.top
        destination_width = destination.rect.right - destination.rect.left
        destination_height = destination.rect.bottom - destination.rect.top
        return ResolvedEdge(
            mapping=mapping,
            source_rect=source.rect,
            destination_rect=destination.rect,
            destination_position=edge_entry_point(
                destination.rect,
                mapping.destination_side,
                ratio,
            ),
            # Conduit is per-monitor DPI aware, so pynput coordinates are native
            # pixels. Resolution ratios therefore preserve the existing cursor
            # speed contract; DPI remains explicit metadata for the destination.
            scale_x=destination_width / server_width,
            scale_y=destination_height / server_height,
            destination_dpi_percent=destination.dpi_percent,
        )


@dataclass(frozen=True)
class ValidationResult:
    validated: ValidatedTopology | None
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def is_valid(self):
        return self.validated is not None


@dataclass(frozen=True)
class DraftTopology:
    server_id: str
    machines: tuple[PlacedMachine, ...]

    def find_auto_placement(self, group):
        occupied = {
            cell
            for machine in self.machines
            for cell in machine.occupied_cells
        }
        local_cells = tuple((cell.x, cell.y) for cell in group.cells)
        min_x = min(x for x, _ in local_cells)
        max_x = max(x for x, _ in local_cells)
        min_y = min(y for _, y in local_cells)
        max_y = max(y for _, y in local_cells)
        for x, y in (
            (1 - min_x, 0),
            (-1 - max_x, 0),
            (0, -1 - max_y),
            (0, 1 - min_y),
        ):
            candidate = PlacedMachine(group=group, x=x, y=y)
            if occupied.isdisjoint(candidate.occupied_cells):
                return candidate
        return None

    def validate(self):
        machine_ids = tuple(
            machine.group.machine_id for machine in self.machines
        )
        duplicate_machine_ids = tuple(
            sorted(
                {
                    machine_id
                    for machine_id in machine_ids
                    if machine_ids.count(machine_id) > 1
                }
            )
        )
        if duplicate_machine_ids:
            return ValidationResult(
                validated=None,
                issues=(
                    ValidationIssue("duplicate-machine-id", duplicate_machine_ids),
                ),
            )
        machine_by_id = {
            machine.group.machine_id: machine for machine in self.machines
        }
        server = machine_by_id.get(self.server_id)
        if server is None:
            return ValidationResult(
                validated=None,
                issues=(ValidationIssue("missing-server", (self.server_id,)),),
            )
        if server.x != 0 or server.y != 0:
            return ValidationResult(
                validated=None,
                issues=(
                    ValidationIssue("server-not-anchored", (self.server_id,)),
                ),
            )
        unaligned = tuple(
            sorted(
                machine.group.machine_id
                for machine in self.machines
                if type(machine.x) is not int or type(machine.y) is not int
            )
        )
        if unaligned:
            return ValidationResult(
                validated=None,
                issues=(ValidationIssue("unaligned-placement", unaligned),),
            )
        client_ids = tuple(
            sorted(machine_id for machine_id in machine_by_id if machine_id != self.server_id)
        )
        if len(client_ids) > 2:
            return ValidationResult(
                validated=None,
                issues=(ValidationIssue("too-many-clients", client_ids),),
            )

        cell_owner = {}
        for machine in self.machines:
            for display_cell in machine.group.cells:
                cell = (machine.x + display_cell.x, machine.y + display_cell.y)
                previous = cell_owner.get(cell)
                if previous is not None:
                    return ValidationResult(
                        validated=None,
                        issues=(
                            ValidationIssue(
                                "overlap",
                                tuple(
                                    sorted(
                                        (previous[0], machine.group.machine_id)
                                    )
                                ),
                            ),
                        ),
                    )
                cell_owner[cell] = (
                    machine.group.machine_id,
                    display_cell.display_id,
                )

        edge_set = set()
        edge_mappings = []
        for (x, y), owner in cell_owner.items():
            for neighbor_cell, owner_side, neighbor_side in (
                ((x + 1, y), "right", "left"),
                ((x, y + 1), "bottom", "top"),
            ):
                neighbor = cell_owner.get(neighbor_cell)
                if neighbor is not None and neighbor[0] != owner[0]:
                    edge_set.add(tuple(sorted((owner[0], neighbor[0]))))
                    edge_mappings.extend(
                        (
                            EdgeMapping(
                                owner[0],
                                owner[1],
                                owner_side,
                                neighbor[0],
                                neighbor[1],
                                neighbor_side,
                            ),
                            EdgeMapping(
                                neighbor[0],
                                neighbor[1],
                                neighbor_side,
                                owner[0],
                                owner[1],
                                owner_side,
                            ),
                        )
                    )

        reachable = {self.server_id}
        pending = [self.server_id]
        while pending:
            current = pending.pop()
            for left, right in edge_set:
                if left == current and right not in reachable:
                    reachable.add(right)
                    pending.append(right)
                elif right == current and left not in reachable:
                    reachable.add(left)
                    pending.append(left)

        disconnected = tuple(
            sorted(machine_id for machine_id in machine_by_id if machine_id not in reachable)
        )
        if disconnected:
            return ValidationResult(
                validated=None,
                issues=(ValidationIssue("disconnected", disconnected),),
            )

        return ValidationResult(
            validated=ValidatedTopology(
                server_id=self.server_id,
                machines=self.machines,
                edges=tuple(sorted(edge_set)),
                edge_mappings=tuple(
                    sorted(
                        edge_mappings,
                        key=lambda mapping: (
                            mapping.source_machine_id,
                            mapping.source_display_id,
                            mapping.source_side,
                        ),
                    )
                ),
            )
        )
