"""A read-only cursor fallback, independent of physical mouse availability.

The worker owns its layered window and message pump. It never changes Windows
cursor settings or injects input; a visible system cursor always takes priority.
"""
import logging
import threading
import time

from PIL import Image, ImageDraw

from app.windows_map_overlay import WindowsMapOverlay

logger = logging.getLogger(__name__)


def system_cursor_hidden(flags):
    return not flags & 1 or bool(flags & 2)  # CURSOR_SHOWING / CURSOR_SUPPRESSED


def overlay_visible_in_rect(hwnd, rect):
    """Whether this owned window contributes pixels inside the selected output."""
    if not hwnd:
        return False
    import win32gui
    try:
        if not win32gui.IsWindowVisible(hwnd):
            return False
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        bounds = ((rect.left, rect.top, rect.right, rect.bottom)
                  if hasattr(rect, 'left') else rect)
        return left < bounds[2] and right > bounds[0] and top < bounds[3] and bottom > bounds[1]
    except Exception:
        return False


def _arrow_image():
    scale = 3
    image = Image.new('RGBA', (24 * scale, 32 * scale))
    points = [(1, 1), (1, 24), (7, 18), (12, 29), (16, 27), (11, 16), (20, 16)]
    points = [(x * scale, y * scale) for x, y in points]
    draw = ImageDraw.Draw(image)
    draw.polygon(points, fill='white')
    draw.line(points + points[:1], fill='black', width=scale, joint='curve')
    return image.resize((24, 32), Image.Resampling.LANCZOS)


class WindowsCursorOverlay:
    def __init__(self):
        self._lifecycle_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._hwnd = 0

    @property
    def hwnd(self):
        with self._state_lock:
            return self._hwnd

    def visible_in_rect(self, rect):
        return overlay_visible_in_rect(self.hwnd, rect)

    def start(self):
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return not self._stop.is_set() and bool(self.hwnd)
            self._ready.clear()
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name='cursor-fallback', daemon=True)
            self._thread.start()
            # Stay below the control handoff and native-start ACK deadlines.
            ready = self._ready.wait(.1)
            if not ready:
                self._stop.set()
            return ready and bool(self.hwnd)

    def stop(self):
        with self._lifecycle_lock:
            self._stop.set()
            thread = self._thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(1)
            stopped = thread is None or not thread.is_alive()
            if stopped:
                self._thread = None
            return stopped

    def _run(self):
        window = None
        try:
            import win32gui
            window = WindowsMapOverlay()
            image = _arrow_image()
            shown, previous_position, painted_at = False, None, 0.0
            with self._state_lock:
                self._hwnd = window.hwnd
            self._ready.set()
            while not self._stop.is_set():
                try:
                    flags, _handle, _position = win32gui.GetCursorInfo()
                    hidden = system_cursor_hidden(flags)
                    if hidden:
                        position = win32gui.GetCursorPos()
                        now = time.monotonic()
                        # A discarded capture transition needs another stable
                        # desktop update even when the logical pointer is idle.
                        repaint = position != previous_position or now - painted_at >= .25
                        if repaint:
                            window.update(image, position)
                            previous_position = position
                            painted_at = now
                        if repaint or not shown:
                            window.show()
                        shown = True
                    elif shown:
                        window.hide()
                        shown = False
                except Exception:
                    # An unavailable input desktop is not proof of a hidden cursor.
                    window.hide()
                    shown, previous_position = False, None
                win32gui.PumpWaitingMessages()
                self._stop.wait(.016)
        except Exception as error:
            logger.warning('Cursor fallback unavailable (%s)', type(error).__name__)
        finally:
            if window is not None:
                try:
                    window.close()
                except Exception as error:
                    logger.debug('Cursor fallback cleanup failed (%s)', type(error).__name__)
            with self._state_lock:
                self._hwnd = 0
            self._ready.set()
