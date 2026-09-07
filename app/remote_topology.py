"""Internal Client edges need explicit ownership when a single monitor is viewed.

Normal KVM leaves these edges to Windows. Remote mode adds them only to its
runtime router; saved layouts and native Server monitor movement are unchanged.
"""
from dataclasses import replace
from app.display_topology import EdgeMapping


def with_client_monitor_edges(topology):
    mappings = list(topology.edge_mappings)
    for placed in topology.machines:
        machine_id = placed.group.machine_id
        if machine_id == topology.server_id:
            continue
        cells = {(cell.x, cell.y): cell.display_id for cell in placed.group.cells}
        for (x, y), display_id in cells.items():
            for dx, dy, side, entry in ((1, 0, 'right', 'left'), (0, 1, 'bottom', 'top')):
                neighbor = cells.get((x + dx, y + dy))
                if neighbor is not None:
                    mappings.extend((EdgeMapping(machine_id, display_id, side, machine_id, neighbor, entry),
                                     EdgeMapping(machine_id, neighbor, entry, machine_id, display_id, side)))
    return replace(topology, edge_mappings=tuple(mappings))
