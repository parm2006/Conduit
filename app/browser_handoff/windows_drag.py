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
    claimed: bool = False


class MoveTracker:
    """Thread-safe state machine that accepts only unambiguous window moves."""

    def __init__(self, *, token_ttl_seconds=1.0):
        if token_ttl_seconds <= 0:
            raise ValueError("token_ttl_seconds must be positive")
        self._token_ttl_seconds = token_ttl_seconds
        self._sessions = {}
        self._completed = deque()
        self._lock = threading.Lock()
        self._event_counts = {
            "move_start": 0,
            "location_change": 0,
            "move_end": 0,
            "other": 0,
        }
        self._events_without_session = 0
        self._tokens_created = 0
        self._tokens_consumed = 0
        self._tokens_expired = 0
        self._last_decision = "idle"

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
                self._event_counts["move_start"] += 1
                self._sessions[hwnd] = _MoveSession(
                    hwnd,
                    process_id,
                    process_created,
                    bounds,
                    bounds,
                    timestamp,
                    bool(left_button_down),
                )
                self._last_decision = "session_started"
                return

            if event == EVENT_OBJECT_LOCATIONCHANGE:
                self._event_counts["location_change"] += 1
            elif event == EVENT_SYSTEM_MOVESIZEEND:
                self._event_counts["move_end"] += 1
            else:
                self._event_counts["other"] += 1

            session = self._sessions.get(hwnd)
            if session is None:
                self._events_without_session += 1
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
                self._last_decision = "tracking_move"
                return
            if event != EVENT_SYSTEM_MOVESIZEEND:
                return

            self._sessions.pop(hwnd, None)
            if session.claimed:
                self._last_decision = "token_already_claimed"
            elif session.resized:
                self._last_decision = "rejected_resize"
            elif not session.moved:
                self._last_decision = "rejected_no_movement"
            elif not session.left_button_observed:
                self._last_decision = "rejected_no_left_button"
            else:
                self._completed.append(
                    MoveToken(
                        hwnd=session.hwnd,
                        process_id=session.process_id,
                        process_created=session.process_created,
                        bounds=session.latest_bounds,
                        completed_at=timestamp,
                    )
                )
                self._tokens_created += 1
                self._last_decision = "token_created"

    def claim_active_move(self, *, now):
        """Claim one qualifying in-progress move before injected button release.

        The claim is metadata-only. It does not touch the cursor or browser and
        marks the native session so its later move-end cannot create a second
        token.
        """
        with self._lock:
            eligible = [
                session for session in self._sessions.values()
                if session.moved and session.left_button_observed and not session.resized and not session.claimed
            ]
            if not eligible:
                return None
            session = max(eligible, key=lambda item: item.started_at)
            session.claimed = True
            self._tokens_created += 1
            self._last_decision = "active_token_claimed"
            return MoveToken(
                hwnd=session.hwnd,
                process_id=session.process_id,
                process_created=session.process_created,
                bounds=session.latest_bounds,
                completed_at=now,
            )

    def consume_eligible_move(self, *, now):
        """Consume the newest non-expired token exactly once."""
        with self._lock:
            cutoff = now - self._token_ttl_seconds
            while self._completed and self._completed[0].completed_at < cutoff:
                self._completed.popleft()
                self._tokens_expired += 1
                self._last_decision = "token_expired"
            if not self._completed:
                return None
            self._tokens_consumed += 1
            self._last_decision = "token_consumed"
            return self._completed.pop()

    def diagnostic_snapshot(self):
        """Return bounded URL-free evidence about move-token decisions."""
        with self._lock:
            return {
                "events": dict(self._event_counts),
                "events_without_session": self._events_without_session,
                "active_sessions": len(self._sessions),
                "tokens_pending": len(self._completed),
                "tokens_created": self._tokens_created,
                "tokens_consumed": self._tokens_consumed,
                "tokens_expired": self._tokens_expired,
                "last_decision": self._last_decision,
            }


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
        self._diagnostic_lock = threading.Lock()
        self._diagnostics = {
            "running": False,
            "hooks_installed": 0,
            "raw_callbacks": 0,
            "accepted_callbacks": 0,
            "filtered_invalid_window": 0,
            "filtered_non_window_object": 0,
            "callback_errors": 0,
            "last_event": "none",
        }

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

    def diagnostic_snapshot(self):
        """Return observer and tracker state without desktop content."""
        with self._diagnostic_lock:
            snapshot = dict(self._diagnostics)
        snapshot["tracker"] = self.tracker.diagnostic_snapshot()
        return snapshot

    def _update_diagnostics(self, **updates):
        with self._diagnostic_lock:
            self._diagnostics.update(updates)

    def _increment_diagnostic(self, name):
        with self._diagnostic_lock:
            self._diagnostics[name] += 1

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
            self._increment_diagnostic("raw_callbacks")
            self._update_diagnostics(last_event=_event_name(event))
            if not hwnd:
                self._increment_diagnostic("filtered_invalid_window")
                return
            if object_id != OBJID_WINDOW or child_id != CHILDID_SELF:
                self._increment_diagnostic("filtered_non_window_object")
                return
            self._increment_diagnostic("accepted_callbacks")
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
                self._increment_diagnostic("callback_errors")
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
                self._increment_diagnostic("hooks_installed")
            self._update_diagnostics(running=True)
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
            self._update_diagnostics(running=False)
            for hook in self._hooks:
                user32.UnhookWinEvent(hook)
            self._hooks.clear()


def _event_name(event):
    return {
        EVENT_SYSTEM_MOVESIZESTART: "move_start",
        EVENT_SYSTEM_MOVESIZEEND: "move_end",
        EVENT_OBJECT_LOCATIONCHANGE: "location_change",
    }.get(event, "other")


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
