"""A per-pixel-alpha, nonactivating, click-through Windows overlay.

Independent of the Tk root's visibility; all calls belong on the GUI thread.
"""


class WindowsMapOverlay:
    def __init__(self):
        import win32api
        import win32con
        import win32gui
        self.hwnd = None
        name = 'ConduitRemoteMonitorMap'
        instance = win32api.GetModuleHandle(None)
        window_class = win32gui.WNDCLASS()
        window_class.hInstance = instance
        window_class.lpszClassName = name
        window_class.lpfnWndProc = win32gui.DefWindowProc
        try:
            win32gui.RegisterClass(window_class)
        except win32gui.error as error:
            if error.winerror != 1410:  # class already registered
                raise
        self.hwnd = win32gui.CreateWindowEx(
            win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
            | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_TOPMOST,
            name, '', win32con.WS_POPUP, 0, 0, 1, 1, 0, 0, instance, None)

    def update(self, image, position):
        import ctypes
        from ctypes import wintypes
        import win32con
        import win32gui
        import win32ui
        screen_handle = win32gui.GetDC(0)
        screen = win32ui.CreateDCFromHandle(screen_handle)
        memory = screen.CreateCompatibleDC()
        bitmap = None
        old = None
        try:
            # UpdateLayeredWindow expects premultiplied BGRA pixels.
            pixels = image.convert('RGBa').tobytes('raw', 'BGRa')
            class BitmapInfo(ctypes.Structure):
                _fields_ = [('size', wintypes.DWORD), ('width', wintypes.LONG),
                            ('height', wintypes.LONG), ('planes', wintypes.WORD),
                            ('bits', wintypes.WORD), ('compression', wintypes.DWORD),
                            ('image_size', wintypes.DWORD), ('xppm', wintypes.LONG),
                            ('yppm', wintypes.LONG), ('used', wintypes.DWORD),
                            ('important', wintypes.DWORD)]
            info = BitmapInfo(ctypes.sizeof(BitmapInfo), image.width, -image.height, 1, 32)
            address = ctypes.c_void_p()
            create_dib = ctypes.windll.gdi32.CreateDIBSection
            create_dib.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
            create_dib.restype = wintypes.HBITMAP
            bitmap = create_dib(screen_handle, ctypes.byref(info), 0, ctypes.byref(address), None, 0)
            if not bitmap:
                raise ctypes.WinError()
            ctypes.memmove(address, pixels, len(pixels))
            old = win32gui.SelectObject(memory.GetSafeHdc(), bitmap)
            win32gui.UpdateLayeredWindow(self.hwnd, screen_handle, position, image.size,
                                        memory.GetSafeHdc(), (0, 0), 0,
                                        (win32con.AC_SRC_OVER, 0, 255, win32con.AC_SRC_ALPHA),
                                        win32con.ULW_ALPHA)
        finally:
            if old is not None:
                win32gui.SelectObject(memory.GetSafeHdc(), old)
            if bitmap is not None:
                win32gui.DeleteObject(bitmap)
            memory.DeleteDC()
            screen.DeleteDC()
            win32gui.ReleaseDC(0, screen_handle)

    def show(self):
        import win32con
        import win32gui
        win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                             win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                             | win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)

    def hide(self):
        import win32con
        import win32gui
        win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)

    def close(self):
        import win32gui
        if self.hwnd is not None:
            win32gui.DestroyWindow(self.hwnd)
            self.hwnd = None
