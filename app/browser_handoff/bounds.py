"""DIP-to-physical bounds conversion for Chromium window handoff."""

import os
import time
from app.display_topology import NativeRect


class DpiBoundsConverter:
    """Converts Chromium DIP window coordinates to Windows physical screen coordinates."""

    def __init__(self, display_backend=None, *, cache_ttl=0.5):
        self._display_backend = display_backend
        self._cache_ttl = float(cache_ttl)
        self._cached_records = None
        self._cache_time = 0.0

    def _get_display_records(self):
        now = time.monotonic()
        if self._cached_records is not None and now - self._cache_time < self._cache_ttl:
            return self._cached_records

        backend = self._display_backend
        if backend is None and os.name == "nt":
            try:
                from app.windows_displays import _Win32DisplayBackend
                backend = _Win32DisplayBackend()
            except Exception:
                backend = None

        if backend is not None:
            try:
                records = backend.snapshot()
                if records:
                    self._cached_records = records
                    self._cache_time = now
                    return records
            except Exception:
                pass

        return ()

    def to_physical(self, window):
        """Map Chromium DIP bounds to physical screen coordinates."""
        left = window.get("left")
        top = window.get("top")
        width = window.get("width")
        height = window.get("height")
        if any(type(v) is not int for v in (left, top, width, height)):
            raise ValueError("browser bounds are unavailable")
        if width <= 0 or height <= 0:
            raise ValueError("window dimensions must be positive")

        records = self._get_display_records()
        if not records:
            # Fallback to 1:1 if not on Windows or no display records
            return NativeRect(left, top, left + width, top + height)

        primary = next((r for r in records if r.primary), records[0])
        primary_scale = max(0.25, primary.dpi / 96.0)

        # Build DIP bounds for each monitor
        monitors = []
        for r in records:
            scale = max(0.25, r.dpi / 96.0)
            dip_left = round(r.rect[0] / primary_scale)
            dip_top = round(r.rect[1] / primary_scale)
            dip_width = round((r.rect[2] - r.rect[0]) / scale)
            dip_height = round((r.rect[3] - r.rect[1]) / scale)
            monitors.append({
                "record": r,
                "scale": scale,
                "dip_left": dip_left,
                "dip_top": dip_top,
                "dip_right": dip_left + dip_width,
                "dip_bottom": dip_top + dip_height,
            })

        cx = left + width / 2.0
        cy = top + height / 2.0

        best_monitor = None
        min_dist = float("inf")
        for m in monitors:
            if m["dip_left"] <= cx < m["dip_right"] and m["dip_top"] <= cy < m["dip_bottom"]:
                best_monitor = m
                break
            dx = max(m["dip_left"] - cx, 0, cx - m["dip_right"])
            dy = max(m["dip_top"] - cy, 0, cy - m["dip_bottom"])
            dist = dx * dx + dy * dy
            if dist < min_dist:
                min_dist = dist
                best_monitor = m

        if best_monitor is None:
            best_monitor = monitors[0]

        scale = best_monitor["scale"]
        rec = best_monitor["record"]
        phys_left = rec.rect[0] + round((left - best_monitor["dip_left"]) * scale)
        phys_top = rec.rect[1] + round((top - best_monitor["dip_top"]) * scale)
        phys_width = max(1, round(width * scale))
        phys_height = max(1, round(height * scale))

        return NativeRect(phys_left, phys_top, phys_left + phys_width, phys_top + phys_height)


_default_converter = DpiBoundsConverter()


def browser_bounds_to_physical(window):
    return _default_converter.to_physical(window)
