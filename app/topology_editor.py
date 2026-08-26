from dataclasses import dataclass

import customtkinter as ctk
import tkinter as tk

from app.display_topology import DraftTopology, PlacedMachine


CELL_SIZE = 40
SERVER_COLOR = "#8F99A8"
CLIENT_COLORS = ("#3B82F6", "#34D399", "#A855F7")
INVALID_COLOR = "#EF4444"


@dataclass(frozen=True)
class TopologyCellView:
    machine_id: str
    display_id: str
    x: int
    y: int
    letter: str
    color: str
    movable: bool
    invalid: bool


class TopologyEditorState:
    def __init__(self, active, draft=None):
        self.active = active
        self.draft = draft or DraftTopology(active.server_id, active.machines)
        self.invalid_machine_ids = ()
        self._client_colors = {}
        for placed in active.machines:
            machine_id = placed.group.machine_id
            if machine_id != active.server_id:
                self._assign_client_color(machine_id)

    def add_client(self, group):
        if group.machine_id == self.draft.server_id:
            return False
        existing = next(
            (
                placed
                for placed in self.draft.machines
                if placed.group.machine_id == group.machine_id
            ),
            None,
        )
        if existing is not None:
            self.draft = DraftTopology(
                server_id=self.draft.server_id,
                machines=tuple(
                    PlacedMachine(group, placed.x, placed.y)
                    if placed is existing
                    else placed
                    for placed in self.draft.machines
                ),
            )
            self.invalid_machine_ids = ()
            return True
        saved = next(
            (
                placed
                for placed in self.active.machines
                if placed.group.machine_id == group.machine_id
            ),
            None,
        )
        if saved is not None:
            candidate = PlacedMachine(group, saved.x, saved.y)
            occupied = {
                cell
                for placed in self.draft.machines
                for cell in placed.occupied_cells
            }
            if occupied.isdisjoint(candidate.occupied_cells):
                self._assign_client_color(group.machine_id)
                self.draft = DraftTopology(
                    self.draft.server_id,
                    self.draft.machines + (candidate,),
                )
                self.invalid_machine_ids = ()
                return True
        placement = self.draft.find_auto_placement(group)
        if placement is None:
            return False
        self._assign_client_color(group.machine_id)
        self.draft = DraftTopology(
            server_id=self.draft.server_id,
            machines=self.draft.machines + (placement,),
        )
        self.invalid_machine_ids = ()
        return True

    def move_machine(self, machine_id, x, y):
        if machine_id == self.draft.server_id:
            return False
        found = False
        moved = []
        for placed in self.draft.machines:
            if placed.group.machine_id == machine_id:
                moved.append(PlacedMachine(placed.group, x=x, y=y))
                found = True
            else:
                moved.append(placed)
        if not found:
            return False
        self.draft = DraftTopology(self.draft.server_id, tuple(moved))
        self.invalid_machine_ids = ()
        return True

    def refresh_machine(self, group):
        refreshed = []
        found = False
        for placed in self.draft.machines:
            if placed.group.machine_id == group.machine_id:
                refreshed.append(PlacedMachine(group, placed.x, placed.y))
                found = True
            else:
                refreshed.append(placed)
        if not found:
            return False
        self.draft = DraftTopology(self.draft.server_id, tuple(refreshed))
        self.invalid_machine_ids = ()
        return True

    def apply(self):
        result = self.draft.validate()
        if result.is_valid:
            self.active = result.validated.activate(self.active.version + 1)
            self.invalid_machine_ids = ()
            return result
        invalid = {
            machine_id
            for issue in result.issues
            for machine_id in issue.machine_ids
            if machine_id != self.draft.server_id
        }
        self.invalid_machine_ids = tuple(sorted(invalid))
        return result

    def cancel(self):
        self.draft = DraftTopology(self.active.server_id, self.active.machines)
        self.invalid_machine_ids = ()

    def commit(self, active):
        self.active = active
        self.draft = DraftTopology(active.server_id, active.machines)
        self.invalid_machine_ids = ()

    def remove_clients_from_draft(self):
        self.draft = DraftTopology(
            self.draft.server_id,
            tuple(
                placed
                for placed in self.draft.machines
                if placed.group.machine_id == self.draft.server_id
            ),
        )
        self.invalid_machine_ids = ()

    def remove_client(self, machine_id):
        if machine_id == self.draft.server_id:
            return None
        removed = next(
            (
                placed
                for placed in self.draft.machines
                if placed.group.machine_id == machine_id
            ),
            None,
        )
        if removed is None:
            return None
        self.draft = DraftTopology(
            self.draft.server_id,
            tuple(
                placed
                for placed in self.draft.machines
                if placed.group.machine_id != machine_id
            ),
        )
        self._client_colors.pop(machine_id, None)
        self.invalid_machine_ids = ()
        return removed

    def set_client_color(self, machine_id, color):
        if (
            machine_id == self.draft.server_id
            or not any(
                placed.group.machine_id == machine_id
                for placed in self.draft.machines
            )
        ):
            return False
        self._client_colors[machine_id] = color
        return True

    def client_color(self, machine_id):
        return self._client_colors[machine_id]

    def cell_views(self):
        views = []
        for placed in self.draft.machines:
            group = placed.group
            is_server = group.machine_id == self.draft.server_id
            color = (
                SERVER_COLOR
                if is_server
                else self._client_colors[group.machine_id]
            )
            letter = group.windows_name[:1].upper() or "?"
            for cell in group.cells:
                views.append(
                    TopologyCellView(
                        machine_id=group.machine_id,
                        display_id=cell.display_id,
                        x=placed.x + cell.x,
                        y=placed.y + cell.y,
                        letter=letter,
                        color=color,
                        movable=not is_server,
                        invalid=group.machine_id in self.invalid_machine_ids,
                    )
                )
        return tuple(views)

    def _assign_client_color(self, machine_id):
        if machine_id in self._client_colors:
            return
        used = set(self._client_colors.values())
        self._client_colors[machine_id] = next(
            color for color in CLIENT_COLORS if color not in used
        )


class TopologyEditor(ctk.CTkFrame):
    GRID_WIDTH = CELL_SIZE * 7
    GRID_HEIGHT = CELL_SIZE * 4

    def __init__(
        self,
        parent,
        active,
        draft=None,
        on_apply=None,
        on_cancel=None,
        on_rescan=None,
    ):
        super().__init__(
            parent,
            width=self.GRID_WIDTH,
            height=self.GRID_HEIGHT,
            fg_color="#0B111B",
            corner_radius=10,
        )
        self.grid_propagate(False)
        self.pack_propagate(False)
        self.state = TopologyEditorState(active, draft=draft)
        self.on_apply = on_apply
        self.on_cancel = on_cancel
        self.on_rescan = on_rescan
        self._drag = None
        self.canvas = tk.Canvas(
            self,
            width=self.GRID_WIDTH,
            height=self.GRID_HEIGHT,
            background="#0B111B",
            highlightthickness=0,
        )
        self.canvas.place(x=0, y=0)
        self.canvas.bind("<B1-Motion>", self._drag_motion)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        self.apply_button = ctk.CTkButton(
            self,
            text="Apply",
            width=62,
            height=26,
            command=self._apply,
        )
        self.apply_button.place(relx=1.0, x=-10, y=10, anchor="ne")
        self.cancel_button = ctk.CTkButton(
            self,
            text="Cancel",
            width=62,
            height=26,
            fg_color="#374151",
            command=self._cancel,
        )
        self.cancel_button.place(relx=1.0, x=-78, y=10, anchor="ne")
        self._render()

    def add_client(self, group):
        added = self.state.add_client(group)
        self._render()
        return added

    def remove_clients_from_draft(self):
        self.state.remove_clients_from_draft()
        self._render()

    def remove_client(self, machine_id):
        removed = self.state.remove_client(machine_id)
        self._render()
        return removed

    def set_client_color(self, machine_id, color):
        changed = self.state.set_client_color(machine_id, color)
        self._render()
        return changed

    def refresh_machine(self, group):
        refreshed = self.state.refresh_machine(group)
        self._render()
        return refreshed

    def cancel_edits(self):
        self._cancel()

    def _grid_origin(self):
        return (
            (self.GRID_WIDTH // CELL_SIZE // 2) * CELL_SIZE,
            (self.GRID_HEIGHT // CELL_SIZE // 2) * CELL_SIZE,
        )

    def _render(self):
        self.canvas.delete("all")
        for x in range(0, self.GRID_WIDTH + 1, CELL_SIZE):
            self.canvas.create_line(x, 0, x, self.GRID_HEIGHT, fill="#243041")
        for y in range(0, self.GRID_HEIGHT + 1, CELL_SIZE):
            self.canvas.create_line(0, y, self.GRID_WIDTH, y, fill="#243041")
        origin_x, origin_y = self._grid_origin()
        for cell in self.state.cell_views():
            left = origin_x + cell.x * CELL_SIZE
            top = origin_y + cell.y * CELL_SIZE
            tag = f"machine:{cell.machine_id}"
            outline = INVALID_COLOR if cell.invalid else "#D7DEE8"
            width = 3 if cell.invalid else 1
            rectangle = self.canvas.create_rectangle(
                left + 1,
                top + 1,
                left + CELL_SIZE - 1,
                top + CELL_SIZE - 1,
                fill=cell.color,
                outline=outline,
                width=width,
                tags=(tag, "machine-cell"),
            )
            label = self.canvas.create_text(
                left + CELL_SIZE / 2,
                top + CELL_SIZE / 2,
                text=cell.letter,
                fill="white",
                font=("Segoe UI", 13, "bold"),
                tags=(tag, "machine-cell"),
            )
            if cell.movable:
                for item in (rectangle, label):
                    self.canvas.tag_bind(
                        item,
                        "<ButtonPress-1>",
                        lambda event, machine_id=cell.machine_id: self._drag_start(
                            event,
                            machine_id,
                        ),
                    )
        self.apply_button.lift()
        self.cancel_button.lift()

    def _drag_start(self, event, machine_id):
        placed = next(
            machine
            for machine in self.state.draft.machines
            if machine.group.machine_id == machine_id
        )
        grid_x, grid_y = self._event_grid(event)
        self._drag = (machine_id, placed.x - grid_x, placed.y - grid_y)

    def _drag_motion(self, event):
        if self._drag is None:
            return
        machine_id, offset_x, offset_y = self._drag
        grid_x, grid_y = self._event_grid(event)
        self.state.move_machine(
            machine_id,
            x=grid_x + offset_x,
            y=grid_y + offset_y,
        )
        self._render()

    def _drag_end(self, event):
        self._drag_motion(event)
        self._drag = None

    def _event_grid(self, event):
        origin_x, origin_y = self._grid_origin()
        return (
            round((event.x - origin_x) / CELL_SIZE),
            round((event.y - origin_y) / CELL_SIZE),
        )

    def _apply(self):
        if self.on_rescan is not None and self.on_rescan() is False:
            return
        self.apply_current_draft()

    def apply_current_draft(self):
        result = self.state.draft.validate()
        if not result.is_valid:
            result = self.state.apply()
            self._render()
            if self.on_apply is not None:
                self.on_apply(result, self.state.active)
            return
        candidate = result.validated.activate(self.state.active.version + 1)
        accepted = True
        if self.on_apply is not None:
            accepted = self.on_apply(result, candidate) is not False
        if accepted:
            self.state.commit(candidate)
        self._render()

    def _cancel(self):
        self.state.cancel()
        self._render()
        if self.on_cancel is not None:
            self.on_cancel()
