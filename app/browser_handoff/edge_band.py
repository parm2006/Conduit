"""Pure geometry for browser-window activation at configured KVM edges.

The helper deliberately knows nothing about input routing or browser APIs.  A
caller supplies a display rectangle and an already-configured edge segment;
an absent segment is therefore an unconfigured edge and cannot qualify.
Coordinates are physical pixels and rectangle right/bottom edges are
exclusive, matching :class:`app.display_topology.NativeRect`.
"""

from dataclasses import dataclass

from app.display_topology import NativeRect


EDGE_ACTIVATION_FRACTION = 0.05
_SIDES = frozenset(("left", "right", "top", "bottom"))


@dataclass(frozen=True)
class EdgeRegion:
    """A configured segment of one display edge in physical coordinates."""

    side: str
    start: int
    end: int

    def __post_init__(self):
        if self.side not in _SIDES:
            raise ValueError(f"unsupported edge side: {self.side}")
        for name in ("start", "end"):
            value = getattr(self, name)
            if type(value) is not int:
                raise ValueError(f"{name} must be an integer")
        if self.end <= self.start:
            raise ValueError("edge region must have positive length")


def configured_edge_region(display_rect, side, *, start=None, end=None):
    """Return a validated configured edge segment for ``display_rect``.

    Omitting both bounds configures the complete display edge.  Supplying
    only one bound is invalid.  The segment is not clamped: a malformed
    topology must fail closed instead of silently widening the valid region.
    """
    _validate_rect(display_rect, "display rectangle")
    if side not in _SIDES:
        raise ValueError(f"unsupported edge side: {side}")
    extent_start, extent_end = _parallel_extent(display_rect, side)
    if start is None and end is None:
        start, end = extent_start, extent_end
    elif start is None or end is None:
        raise ValueError("edge region requires both start and end")
    region = EdgeRegion(side, start, end)
    if region.start < extent_start or region.end > extent_end:
        raise ValueError("edge region must be contained by the display edge")
    return region


def activation_band(display_rect, region):
    """Return the exact 5%-deep inward band for a configured edge region."""
    _validate_rect(display_rect, "display rectangle")
    if not isinstance(region, EdgeRegion):
        raise ValueError("region must be an EdgeRegion")
    _validate_region(display_rect, region)
    depth = max(1, round(_perpendicular_extent(display_rect, region.side) * EDGE_ACTIVATION_FRACTION))
    if region.side == "left":
        return NativeRect(display_rect.left, region.start, display_rect.left + depth, region.end)
    if region.side == "right":
        return NativeRect(display_rect.right - depth, region.start, display_rect.right, region.end)
    if region.side == "top":
        return NativeRect(region.start, display_rect.top, region.end, display_rect.top + depth)
    return NativeRect(region.start, display_rect.bottom - depth, region.end, display_rect.bottom)


def window_in_activation_band(window_rect, display_rect, region):
    """Whether a browser window's relevant outer edge qualifies.

    ``False`` is returned for an absent, malformed, or unconfigured region so
    callers can safely use this as a fail-closed prefilter.  Overlap on the
    edge's parallel axis is strict; touching only at a corner is insufficient.
    """
    try:
        _validate_rect(window_rect, "window rectangle")
        band = activation_band(display_rect, region)
    except (TypeError, ValueError):
        return False

    if region.side == "left":
        edge_coordinate = window_rect.left
        in_band = band.left <= edge_coordinate <= band.right
        overlaps = _positive_overlap(window_rect.top, window_rect.bottom, region.start, region.end)
    elif region.side == "right":
        edge_coordinate = window_rect.right
        in_band = band.left <= edge_coordinate <= band.right
        overlaps = _positive_overlap(window_rect.top, window_rect.bottom, region.start, region.end)
    elif region.side == "top":
        edge_coordinate = window_rect.top
        in_band = band.top <= edge_coordinate <= band.bottom
        overlaps = _positive_overlap(window_rect.left, window_rect.right, region.start, region.end)
    else:
        edge_coordinate = window_rect.bottom
        in_band = band.top <= edge_coordinate <= band.bottom
        overlaps = _positive_overlap(window_rect.left, window_rect.right, region.start, region.end)
    return in_band and overlaps


def _validate_rect(rect, name):
    for field in ("left", "top", "right", "bottom"):
        if type(getattr(rect, field, None)) is not int:
            raise ValueError(f"{name} {field} must be an integer")
    if rect.right <= rect.left or rect.bottom <= rect.top:
        raise ValueError(f"{name} must have positive dimensions")


def _parallel_extent(display_rect, side):
    if side in ("left", "right"):
        return display_rect.top, display_rect.bottom
    return display_rect.left, display_rect.right


def _perpendicular_extent(display_rect, side):
    if side in ("left", "right"):
        return display_rect.right - display_rect.left
    return display_rect.bottom - display_rect.top


def _validate_region(display_rect, region):
    start, end = _parallel_extent(display_rect, region.side)
    if region.start < start or region.end > end:
        raise ValueError("edge region must be contained by the display edge")


def _positive_overlap(first_start, first_end, second_start, second_end):
    return min(first_end, second_end) > max(first_start, second_start)
