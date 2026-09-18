"""Passive WinEvent-based detection of a probable caption-bar window move.

This module only produces opaque native-window move tokens.  It never reads a
tab, changes focus, injects input, or chooses a Chromium window on its own.
"""

from collections import deque
from dataclasses import dataclass
import ctypes
import ctypes.wintypes
import os
import queue
import threading
import time

from .window_match import PhysicalRect


EVENT_SYSTEM_MOVESIZESTART = 0x000A
EVENT_SYSTEM_MOVESIZEEND = 0x000B
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
OBJID_WINDOW = 0
CHILDID_SELF = 0
WINEVENT_OUTOFCONTEXT = 0
WINEVENT_SKIPOWNPROCESS = 2
VK_LBUTTON = 0x01
WM_QUIT = 0x0012
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass(frozen=True)
class MoveToken:
    hwnd: int
    process_id: int
    process_created: int
    bounds: PhysicalRect
    completed_at: float


@dataclass
class _MoveSession:
    hwnd: int
    process_id: int
    process_created: int
    start_bounds: PhysicalRect
    latest_bounds: PhysicalRect
    started_at: float
    left_button_observed: bool
    moved: bool = False
    resized: bool = False


class MoveTracker:
    """Thread-safe state machine that accepts only unambiguous window moves."""

    def __init__(self, *, token_ttl_seconds=1.0):
        if token_ttl_seconds <= 0:
            raise ValueError("token_ttl_seconds must be positive")
        self._token_ttl_seconds = token_ttl_seconds
        self._sessions = {}
        self._completed = deque()
        self._lock = threading.Lock()

    def observe(
        self,
        event,
        *,
        hwnd,
        process_id,
        process_created,
        bounds,
        timestamp,
        left_button_down,
    ):
        """Record one normalized WinEvent without touching the desktop."""
        with self._lock:
            if event == EVENT_SYSTEM_MOVESIZESTART:
                self._sessions[hwnd] = _MoveSession(
                    hwnd,
                    process_id,
                    process_created,
                    bounds,
                    bounds,
                    timestamp,
                    bool(left_button_down),
                )
                return

            session = self._sessions.get(hwnd)
            if session is None:
                return
            if event == EVENT_OBJECT_LOCATIONCHANGE:
                session.left_button_observed = (
                    session.left_button_observed or bool(left_button_down)
                )
                if bounds.width != session.start_bounds.width or bounds.height != session.start_bounds.height:
                    session.resized = True
                if bounds.left != session.start_bounds.left or bounds.top != session.start_bounds.top:
                    session.moved = True
                session.latest_bounds = bounds
                return
            if event != EVENT_SYSTEM_MOVESIZEEND:
                return

            self._sessions.pop(hwnd, None)
            if session.left_button_observed and session.moved and not session.resized:
                self._completed.append(
                    MoveToken(
                        hwnd=session.hwnd,
                        process_id=session.process_id,
                        process_created=session.process_created,
                        bounds=session.latest_bounds,
                        completed_at=timestamp,
                    )
                )

    def consume_eligible_move(self, *, now):
        """Consume the newest non-expired token exactly once."""
        with self._lock:
            cutoff = now - self._token_ttl_seconds
            while self._completed and self._completed[0].completed_at < cutoff:
                self._completed.popleft()
            return self._completed.pop() if self._completed else None


class WinEventMoveObserver:
    """Dedicated Windows message-loop observer for move/size WinEvents."""

    def __init__(self, tracker=None):
        self.tracker = tracker or MoveTracker()
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._hooks = []
        self._callback = None
        self._startup_error = None
        self.errors = queue.SimpleQueue()

    def start(self):
        if os.name != "nt":
            raise RuntimeError("WinEventMoveObserver is only available on Windows")
        if self._thread is not None:
            raise RuntimeError("observer is already running")
        self._thread = threading.Thread(target=self._run, name="browser-window-move-observer", daemon=True)
        self._thread.start()
        if not self._ready.wait(2):
            raise RuntimeError("WinEvent observer message loop did not start")
        if self._startup_error is not None:
            error = self._startup_error
            self._thread.join(2)
            self._thread = None
            raise RuntimeError("WinEvent observer hook setup failed") from error

    def stop(self):
        self._stop.set()
        if os.name == "nt" and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(2)
            self._thread = None

    def _run(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        callback_type = ctypes.WINFUNCTYPE(
            None,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.c_uint,
            ctypes.c_uint,
        )

        def callback(_hook, event, hwnd, object_id, child_id, _thread, _event_time):
            if not hwnd or object_id != OBJID_WINDOW or child_id != CHILDID_SELF:
                return
            try:
                bounds = _window_rect(user32, hwnd)
                process_id = _window_process_id(user32, hwnd)
                process_created = _process_creation_time(kernel32, process_id)
                self.tracker.observe(
                    event,
                    hwnd=int(hwnd),
                    process_id=process_id,
                    process_created=process_created,
                    bounds=bounds,
                    timestamp=time.monotonic(),
                    left_button_down=bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000),
                )
            except Exception as error:  # callback errors must not disrupt input
                self.errors.put(error)

        self._callback = callback_type(callback)
        try:
            for event_min, event_max in (
                (EVENT_SYSTEM_MOVESIZESTART, EVENT_SYSTEM_MOVESIZEEND),
                (EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_LOCATIONCHANGE),
            ):
                hook = user32.SetWinEventHook(
                    event_min,
                    event_max,
                    None,
                    self._callback,
                    0,
                    0,
                    WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
                )
                if not hook:
                    raise ctypes.WinError()
                self._hooks.append(hook)
            self._ready.set()
            message = ctypes.wintypes.MSG()
            while not self._stop.is_set() and user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as error:
            self._startup_error = error
            self.errors.put(error)
            self._ready.set()
        finally:
            for hook in self._hooks:
                user32.UnhookWinEvent(hook)
            self._hooks.clear()


def _window_rect(user32, hwnd):
    rect = ctypes.wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError()
    return PhysicalRect(rect.left, rect.top, rect.right, rect.bottom)


def _window_process_id(user32, hwnd):
    process_id = ctypes.c_ulong()
    if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id)):
        raise ctypes.WinError()
    return process_id.value


def _process_creation_time(kernel32, process_id):
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not process:
        raise ctypes.WinError()
    try:
        created = ctypes.wintypes.FILETIME()
        exited = ctypes.wintypes.FILETIME()
        kernel = ctypes.wintypes.FILETIME()
        user = ctypes.wintypes.FILETIME()
        if not kernel32.GetProcessTimes(process, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            raise ctypes.WinError()
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel32.CloseHandle(process)
