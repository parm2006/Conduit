import logging
import threading
import time
from pynput.keyboard import Controller as KeyboardController, Listener as KeyboardListener, Key, KeyCode

logger = logging.getLogger(__name__)


def normalize_key(key):
    if isinstance(key, Key):
        return key.name.lower()
    if isinstance(key, KeyCode):
        vk = getattr(key, "vk", None)
        if vk in (82, 114) or (key.char and key.char.lower() in ("r", "\x12")):
            return "r"
        if vk in (66, 98) or (key.char and key.char.lower() in ("b", "\x02")):
            return "b"
        if vk == 27:
            return "esc"
        if key.char:
            return key.char.lower()
    return str(key).lower()


class ReturnShortcutDetector:
    CTRL_KEYS = frozenset(("ctrl", "ctrl_l", "ctrl_r"))

    def __init__(self, *, clock=None, interval=0.75):
        if not isinstance(interval, (int, float)) or interval <= 0:
            raise ValueError("return shortcut interval must be positive")
        self._clock = clock or time.monotonic
        self._interval = float(interval)
        self._first_space_at = None
        self._pressed_keys = set()

    def press(self, key):
        value = normalize_key(key)
        already_pressed = value in self._pressed_keys
        self._pressed_keys.add(value)

        if self._first_space_at is not None and value not in {
            "space",
            *self.CTRL_KEYS,
        }:
            self._first_space_at = None

        if value != "space" or already_pressed:
            return False
        if not self.CTRL_KEYS.intersection(self._pressed_keys):
            self._first_space_at = None
            return False

        now = self._clock()
        if (
            self._first_space_at is not None
            and now - self._first_space_at <= self._interval
        ):
            self._first_space_at = None
            return True
        self._first_space_at = now
        return False

    def release(self, key):
        value = normalize_key(key)
        self._pressed_keys.discard(value)
        if value in self.CTRL_KEYS:
            self._first_space_at = None

    def reset(self):
        self._pressed_keys.clear()
        self._first_space_at = None


class GlobalHotkeyMonitor:
    """Always-active background keybind monitor for emergency exit, connection reload, and background daemon mode."""

    def __init__(
        self,
        on_emergency_exit=None,
        on_reload_connection=None,
        on_toggle_daemon=None,
        on_return_to_server=None,
        *,
        clock=None,
        return_interval=0.75,
    ):
        self.on_emergency_exit = on_emergency_exit
        self.on_reload_connection = on_reload_connection
        self.on_toggle_daemon = on_toggle_daemon
        self.on_return_to_server = on_return_to_server
        self._return_shortcut = ReturnShortcutDetector(
            clock=clock,
            interval=return_interval,
        )
        self.pressed_keys = set()
        self.listener = None
        self._lock = threading.Lock()

    def start(self):
        if self.listener is not None:
            return
        self.listener = KeyboardListener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self.listener.daemon = True
        self.listener.start()
        self.listener.wait()
        logger.info(
            "Global hotkey monitor started (Ctrl+Space, Space; "
            "Ctrl+Shift+Alt+Escape; Ctrl+Shift+Alt+R; Ctrl+Shift+Alt+B)"
        )

    def stop(self):
        if self.listener is not None:
            try:
                self.listener.stop()
            except Exception:
                pass
            self.listener = None
        with self._lock:
            self.pressed_keys.clear()
            self._return_shortcut.reset()

    @staticmethod
    def _normalize_key(key):
        return normalize_key(key)

    def _on_press(self, key):
        val = self._normalize_key(key)
        with self._lock:
            self.pressed_keys.add(val)
            has_ctrl = any(k in self.pressed_keys for k in ("ctrl", "ctrl_l", "ctrl_r"))
            has_alt = any(k in self.pressed_keys for k in ("alt", "alt_l", "alt_r", "alt_gr"))
            has_shift = any(k in self.pressed_keys for k in ("shift", "shift_l", "shift_r"))
            has_esc = val in ("esc", "escape")
            has_r = val in ("r", "R")
            has_b = val in ("b", "B")

            if self._return_shortcut.press(key) and self.on_return_to_server:
                logger.warning(
                    "[HOTKEY DIAGNOSTIC] Ctrl+Space, Space triggered on Server"
                )
                threading.Thread(
                    target=self.on_return_to_server,
                    daemon=True,
                ).start()

            if has_ctrl and has_alt and has_shift:
                if has_esc and self.on_emergency_exit:
                    logger.warning("[HOTKEY DIAGNOSTIC] Ctrl+Alt+Shift+Escape triggered globally!")
                    self.pressed_keys.clear()
                    threading.Thread(target=self.on_emergency_exit, daemon=True).start()
                elif has_r and self.on_reload_connection:
                    logger.warning("[HOTKEY DIAGNOSTIC] Ctrl+Alt+Shift+R triggered globally!")
                    self.pressed_keys.clear()
                    threading.Thread(target=self.on_reload_connection, daemon=True).start()
                elif has_b and self.on_toggle_daemon:
                    logger.warning("[HOTKEY DIAGNOSTIC] Ctrl+Alt+Shift+B triggered globally!")
                    self.pressed_keys.clear()
                    threading.Thread(target=self.on_toggle_daemon, daemon=True).start()

    def _on_release(self, key):
        val = self._normalize_key(key)
        with self._lock:
            self.pressed_keys.discard(val)
            self._return_shortcut.release(key)
