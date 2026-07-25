import logging
import threading
from pynput.keyboard import Controller as KeyboardController, Listener as KeyboardListener, Key, KeyCode

logger = logging.getLogger(__name__)


class GlobalHotkeyMonitor:
    """Always-active background keybind monitor for emergency exit & connection reload."""

    def __init__(self, on_emergency_exit=None, on_reload_connection=None):
        self.on_emergency_exit = on_emergency_exit
        self.on_reload_connection = on_reload_connection
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
        logger.info("Global hotkey monitor started (Ctrl+Shift+Alt+Escape & Ctrl+Shift+Alt+R)")

    def stop(self):
        if self.listener is not None:
            try:
                self.listener.stop()
            except Exception:
                pass
            self.listener = None
        with self._lock:
            self.pressed_keys.clear()

    @staticmethod
    def _normalize_key(key):
        if isinstance(key, Key):
            return key.name.lower()
        if isinstance(key, KeyCode):
            vk = getattr(key, "vk", None)
            if vk in (82, 114) or (key.char and key.char.lower() in ("r", "\x12")):
                return "r"
            if vk == 27:
                return "esc"
            if key.char:
                return key.char.lower()
        return str(key).lower()

    def _on_press(self, key):
        val = self._normalize_key(key)
        with self._lock:
            self.pressed_keys.add(val)
            has_ctrl = any(k in self.pressed_keys for k in ("ctrl", "ctrl_l", "ctrl_r"))
            has_alt = any(k in self.pressed_keys for k in ("alt", "alt_l", "alt_r", "alt_gr"))
            has_shift = any(k in self.pressed_keys for k in ("shift", "shift_l", "shift_r"))
            has_esc = val in ("esc", "escape")
            has_r = val in ("r", "R")

            if has_ctrl and has_alt and has_shift:
                if has_esc and self.on_emergency_exit:
                    logger.warning("[HOTKEY DIAGNOSTIC] Ctrl+Alt+Shift+Escape triggered globally!")
                    self.pressed_keys.clear()
                    threading.Thread(target=self.on_emergency_exit, daemon=True).start()
                elif has_r and self.on_reload_connection:
                    logger.warning("[HOTKEY DIAGNOSTIC] Ctrl+Alt+Shift+R triggered globally!")
                    self.pressed_keys.clear()
                    threading.Thread(target=self.on_reload_connection, daemon=True).start()

    def _on_release(self, key):
        val = self._normalize_key(key)
        with self._lock:
            self.pressed_keys.discard(val)
