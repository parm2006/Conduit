"""Windows display capture including the actual Client cursor."""
from PIL import Image


def capture_monitor(rect):
    import win32con
    import win32gui
    import win32ui

    width, height = rect.right - rect.left, rect.bottom - rect.top
    screen_handle = win32gui.GetDC(0)
    screen = win32ui.CreateDCFromHandle(screen_handle)
    memory = screen.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    old = None
    try:
        bitmap.CreateCompatibleBitmap(screen, width, height)
        old = memory.SelectObject(bitmap)
        memory.BitBlt((0, 0), (width, height), screen, (rect.left, rect.top),
                     win32con.SRCCOPY | 0x40000000)  # CAPTUREBLT
        flags, cursor_handle, position = win32gui.GetCursorInfo()
        if flags & win32con.CURSOR_SHOWING:
            icon = win32gui.GetIconInfo(cursor_handle)
            try:
                win32gui.DrawIconEx(memory.GetSafeHdc(),
                                    position[0] - rect.left - icon[1],
                                    position[1] - rect.top - icon[2],
                                    cursor_handle, 0, 0, 0, None, win32con.DI_NORMAL)
            finally:
                for handle in icon[3:5]:
                    if handle:
                        win32gui.DeleteObject(handle)
        pixels = bitmap.GetBitmapBits(True)
        image = Image.frombytes('RGB', (width, height), pixels, 'raw', 'BGRX')
        return image, ((position[0] - rect.left) / width, (position[1] - rect.top) / height)
    finally:
        if old is not None:
            memory.SelectObject(old)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        screen.DeleteDC()
        win32gui.ReleaseDC(0, screen_handle)
