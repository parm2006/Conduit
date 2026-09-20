"""Pure, conservative correlation between native and Chromium window data."""

from dataclasses import dataclass


MAX_METADATA_AGE_SECONDS = 1.0
MAX_EDGE_DELTA_PIXELS = 32


def _require_int(value, name):
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


@dataclass(frozen=True)
class PhysicalRect:
    """A screen rectangle in physical pixels, with exclusive right/bottom."""

    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self):
        for name in ("left", "top", "right", "bottom"):
            _require_int(getattr(self, name), name)
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("rectangle must have positive dimensions")

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top


@dataclass(frozen=True)
class NativeWindowObservation:
    hwnd: int
    process_id: int
    process_created: int
    bounds: PhysicalRect
    observed_at: float


@dataclass(frozen=True)
class BrowserWindowCandidate:
    browser_instance_id: str
    window_id: int
    process_id: int
    process_created: int
    bounds: PhysicalRect
    focused: bool
    observed_at: float
    metadata_revision: int | None = None
    bridge_epoch: str | None = None


def _is_fresh(observed_at, now):
    return 0 <= now - observed_at <= MAX_METADATA_AGE_SECONDS


def _bounds_are_compatible(native, candidate):
    return all(
        abs(left - right) <= MAX_EDGE_DELTA_PIXELS
        for left, right in zip(
            (native.left, native.top, native.right, native.bottom),
            (candidate.left, candidate.top, candidate.right, candidate.bottom),
        )
    )



def match_window(native, candidates, *, now):
    """Return the sole defensible candidate, otherwise abstain.

    The extension probe must convert Chromium's device-independent bounds to
    physical pixels before calling this function. Native-host ancestry binds a
    browser connection but does not identify the Chromium process that owns a
    top-level window. There is deliberately no title, URL, or focus-only
    fallback: multiple plausible candidates result in no match.
    """
    if not _is_fresh(native.observed_at, now):
        return None

    plausible = [
        candidate
        for candidate in candidates
        if _is_fresh(candidate.observed_at, now)
        and _bounds_are_compatible(native.bounds, candidate.bounds)
    ]
    return plausible[0] if len(plausible) == 1 else None
