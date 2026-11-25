def project_safe_viewport_entry(rect, projection, position):
    """Round entry pixels inward so downscaling cannot recreate an exit hit."""
    import math
    from app.display_topology import EDGE_ENTRY_INSET

    def axis(value, start, length, destination, fitted):
        offset = max(0, min(length - 1, value - start))
        projected = offset * fitted / max(1, length)
        if offset <= EDGE_ENTRY_INSET:
            pixel = math.ceil(projected)
        elif length - 1 - offset <= EDGE_ENTRY_INSET:
            pixel = math.floor(projected)
        else:
            pixel = round(projected)
        return destination + max(0, min(fitted - 1, pixel))

    x, y, width, height = projection
    return (axis(position[0], rect.left, rect.right - rect.left, x, width),
            axis(position[1], rect.top, rect.bottom - rect.top, y, height))


def expected_entry_side(rect, position, inset=None):
    """Return which edge of rect an entry position targets, or None."""
    from app.display_topology import EDGE_ENTRY_INSET

    if rect is None or position is None:
        return None
    if inset is None:
        inset = EDGE_ENTRY_INSET
    x, y = position
    distances = {
        "left": abs(x - rect.left),
        "right": abs((rect.right - 1) - x),
        "top": abs(y - rect.top),
        "bottom": abs((rect.bottom - 1) - y),
    }
    side, distance = min(distances.items(), key=lambda item: item[1])
    return side if distance <= inset else None


def viewport_point_matches_entry_side(projection, point, side):
    """Return whether point lies in the expected entry half of projection."""
    if projection is None or point is None or side not in {"left", "right", "top", "bottom"}:
        return True
    dest_x, dest_y, fitted_w, fitted_h = projection
    x, y = point
    if side == "left":
        return x < dest_x + fitted_w / 2
    if side == "right":
        return x >= dest_x + fitted_w / 2
    if side == "top":
        return y < dest_y + fitted_h / 2
    return y >= dest_y + fitted_h / 2


def client_entry_position(
    direction, width, height, ratio, edge_inset=1, ratio_margin=10
):
    horizontal = max(
        ratio_margin, min(width - ratio_margin - 1, int(width * ratio))
    )
    vertical = max(
        ratio_margin, min(height - ratio_margin - 1, int(height * ratio))
    )
    if direction == "right":
        return edge_inset, vertical
    if direction == "left":
        return width - edge_inset - 2, vertical
    if direction == "top":
        return horizontal, height - edge_inset - 2
    if direction == "bottom":
        return horizontal, edge_inset
    raise ValueError(f"unsupported direction: {direction}")


def work_area_geometry(rect):
    left, top, right, bottom = rect
    return f"{right - left}x{bottom - top}{left:+d}{top:+d}"


def toast_rect_in_work_area(work_area, window_size, dpi, margin_dip=16):
    left, top, right, bottom = work_area
    available_width = max(0, right - left)
    available_height = max(0, bottom - top)
    width = min(max(0, window_size[0]), available_width)
    height = min(max(0, window_size[1]), available_height)
    margin = round(margin_dip * max(dpi, 96) / 96)
    x = max(left, right - width - margin)
    y = max(top, bottom - height - margin)
    return x, y, x + width, y + height


def windows_toplevel_handle(child_hwnd, get_ancestor):
    return get_ancestor(child_hwnd, 2) or child_hwnd  # GA_ROOT


def configure_windows_window_api(user32):
    import ctypes
    from ctypes import wintypes

    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL


def place_windows_window_in_work_area(child_hwnd, margin_dip=16):
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    user32 = ctypes.windll.user32
    configure_windows_window_api(user32)
    hwnd = windows_toplevel_handle(child_hwnd, user32.GetAncestor)
    monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise ctypes.WinError()
    window_rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
        raise ctypes.WinError()
    get_dpi = getattr(user32, "GetDpiForWindow", None)
    dpi = get_dpi(hwnd) if get_dpi else 96
    if not dpi:
        dpi = 96
    target = toast_rect_in_work_area(
        (info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom),
        (window_rect.right - window_rect.left, window_rect.bottom - window_rect.top),
        dpi,
        margin_dip,
    )
    x, y, right, bottom = target
    flags = 0x0001 | 0x0010  # SWP_NOSIZE | SWP_NOACTIVATE
    if not user32.SetWindowPos(hwnd, -1, x, y, 0, 0, flags):  # HWND_TOPMOST
        raise ctypes.WinError()
    return target


def windows_work_area():
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    if not ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
        raise ctypes.WinError()
    return rect.left, rect.top, rect.right, rect.bottom
