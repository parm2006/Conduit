import logging
import os
import threading
from dataclasses import dataclass
from pynput.mouse import Controller as MouseController, Listener as MouseListener, Button
from pynput.keyboard import Controller as KeyboardController, Listener as KeyboardListener, Key, KeyCode
from app.safe_errors import error_name
from app.display_topology import EDGE_HIT_TOLERANCE, edge_ratio
from app.global_hotkey import ReturnShortcutDetector

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TopologyEdgeRegion:
    source_machine_id: str
    source_display_id: str
    source_side: str
    destination_machine_id: str
    destination_display_id: str
    destination_side: str
    source_rect: object
    destination_rect: object


BUTTON_NAME_ALIASES = {
    'back': 'x1',
    'forward': 'x2',
    'button4': 'x1',
    'button5': 'x2',
    'xbutton1': 'x1',
    'xbutton2': 'x2',
}


class WindowsSpecialKeyInjector:
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    VIRTUAL_KEYS = {"delete": 0x2E}
    MODIFIER_KEYS = (
        (0xA0, False),  # Left Shift
        (0xA1, False),  # Right Shift
        (0xA2, False),  # Left Control
        (0xA3, True),   # Right Control
        (0xA4, False),  # Left Alt
        (0xA5, True),   # Right Alt
        (0x5B, True),   # Left Windows
        (0x5C, True),   # Right Windows
    )

    def __init__(self, user32=None):
        if user32 is None:
            import ctypes
            user32 = ctypes.windll.user32
        self.user32 = user32

    def press(self, name):
        return self._emit(name, 0)

    def release(self, name):
        return self._emit(name, self.KEYEVENTF_KEYUP)

    def emit_scan(self, scan_code, extended, pressed, virtual_key=0):
        flags = self.KEYEVENTF_SCANCODE
        if extended:
            flags |= self.KEYEVENTF_EXTENDEDKEY
        if not pressed:
            flags |= self.KEYEVENTF_KEYUP
        self.user32.keybd_event(virtual_key or 0, scan_code, flags, 0)
        return True

    def emit_native(self, virtual_key, scan_code, extended, pressed):
        flags = self.KEYEVENTF_EXTENDEDKEY if extended else 0
        if not pressed:
            flags |= self.KEYEVENTF_KEYUP
        self.user32.keybd_event(virtual_key, scan_code, flags, 0)
        return True

    def release_active_modifiers(self):
        for virtual_key, extended in self.MODIFIER_KEYS:
            if not self.user32.GetAsyncKeyState(virtual_key) & 0x8000:
                continue
            flags = self.KEYEVENTF_KEYUP
            if extended:
                flags |= self.KEYEVENTF_EXTENDEDKEY
            self.user32.keybd_event(virtual_key, 0, flags, 0)

    def _emit(self, name, flags):
        virtual_key = self.VIRTUAL_KEYS.get(name)
        if virtual_key is None:
            return False
        self.user32.keybd_event(virtual_key, 0, flags, 0)
        return True


class WindowsHardwareMouseInjector:
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_XDOWN = 0x0080
    MOUSEEVENTF_XUP = 0x0100
    MOUSEEVENTF_WHEEL = 0x0800
    MOUSEEVENTF_HWHEEL = 0x1000

    XBUTTON1 = 0x0001
    XBUTTON2 = 0x0002
    WHEEL_DELTA = 120

    BUTTON_FLAGS = {
        'left': (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, 0),
        'right': (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, 0),
        'middle': (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, 0),
        'x1': (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON1),
        'x2': (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON2),
    }

    def __init__(self, user32=None):
        if user32 is None:
            import ctypes
            user32 = ctypes.windll.user32
        self.user32 = user32

    def move(self, dx, dy):
        self.user32.mouse_event(self.MOUSEEVENTF_MOVE, int(dx), int(dy), 0, 0)
        return True

    def click(self, button_name, pressed):
        spec = self.BUTTON_FLAGS.get(button_name)
        if not spec:
            return False
        down_flag, up_flag, data = spec
        flag = down_flag if pressed else up_flag
        self.user32.mouse_event(flag, 0, 0, data, 0)
        return True

    def scroll(self, dx, dy):
        if dy != 0:
            self.user32.mouse_event(self.MOUSEEVENTF_WHEEL, 0, 0, int(dy * self.WHEEL_DELTA), 0)
        if dx != 0:
            self.user32.mouse_event(self.MOUSEEVENTF_HWHEEL, 0, 0, int(dx * self.WHEEL_DELTA), 0)
        return True

    def set_position(self, x, y):
        self.user32.SetCursorPos(int(x), int(y))
        return True


WindowsMouseInjector = WindowsHardwareMouseInjector


class InputHandler:
    def __init__(self):
        self.mouse = MouseController()
        self.mouse_listener = None
        self.mouse_button_listener = None
        self.keyboard = KeyboardController()
        self.special_key_injector = (
            WindowsSpecialKeyInjector() if os.name == "nt" else None
        )
        self.mouse_injector = (
            WindowsHardwareMouseInjector() if os.name == "nt" else None
        )
        self.keyboard_listener = None
        self._return_shortcut = ReturnShortcutDetector()
        self.callbacks = {}
        self._injected_keys = {}
        self._injected_keys_lock = threading.Lock()
        self._injected_buttons = set()
        self._injected_buttons_lock = threading.Lock()
        
        self.screen_width = 1920 # Will be updated
        self.screen_height = 1080
        
        # Spatial Layout Configuration
        self.server_edge = 'right'
        self.client_edge = 'left'

    def set_layout(self, server_edge=None, client_edge=None):
        if server_edge:
            self.server_edge = server_edge
        if client_edge:
            self.client_edge = client_edge

    def set_screen_size(self, w, h):
        self.screen_width = w
        self.screen_height = h

    def configure_topology_edges(self, topology, machine_id):
        machines = {
            placed.group.machine_id: placed.group
            for placed in topology.machines
        }
        regions = []
        for mapping in topology.edge_mappings:
            if mapping.source_machine_id != machine_id:
                continue
            source_group = machines[mapping.source_machine_id]
            destination_group = machines[mapping.destination_machine_id]
            regions.append(
                TopologyEdgeRegion(
                    source_machine_id=mapping.source_machine_id,
                    source_display_id=mapping.source_display_id,
                    source_side=mapping.source_side,
                    destination_machine_id=mapping.destination_machine_id,
                    destination_display_id=mapping.destination_display_id,
                    destination_side=mapping.destination_side,
                    source_rect=source_group.display(mapping.source_display_id).rect,
                    destination_rect=destination_group.display(
                        mapping.destination_display_id
                    ).rect,
                )
            )
        self.topology_edge_regions = tuple(regions)

    def clear_topology_edges(self):
        self.topology_edge_regions = ()

    def set_client_topology_edge(self, region):
        self.set_client_topology_edges(() if region is None else (region,))

    def set_client_topology_edges(self, regions):
        self.client_topology_edge_regions = tuple(regions)

    def register_callback(self, event_type, cb):
        if event_type not in self.callbacks:
            self.callbacks[event_type] = []
        self.callbacks[event_type].append(cb)

    def trigger(self, event_type, *args):
        for cb in self.callbacks.get(event_type, []):
            try:
                cb(*args)
            except Exception as error:
                logger.error("Callback failed (%s)", error_name(error))

    def start_edge_detection(self, edge=None):
        if edge:
            self.server_edge = edge
        self.stop()
        self.mouse_listener = MouseListener(on_move=self._on_move_edge)
        self.mouse_listener.start()
        # pynput's Windows stop path waits for its message loop to initialize.
        # Do not return control until a rapid screen transition can safely stop
        # this listener without waiting forever for that initialization signal.
        self.mouse_listener.wait()

    def stop(self):
        if self.mouse_listener:
            self.mouse_listener.stop()
            self.mouse_listener = None
        self.stop_mouse_button_capture()
        self.stop_keyboard_capture()

    def start_mouse_button_capture(self):
        self.stop_mouse_button_capture()
        self.mouse_button_listener = MouseListener(
            on_click=self._on_mouse_click,
            on_scroll=self._on_mouse_scroll,
        )
        self.mouse_button_listener.start()
        self.mouse_button_listener.wait()

    def stop_mouse_button_capture(self):
        listener = getattr(self, 'mouse_button_listener', None)
        if listener is not None:
            listener.stop()
            self.mouse_button_listener = None

    def _on_mouse_click(self, x, y, button, pressed):
        btn_name = getattr(button, 'name', None) or str(button)
        btn_name = BUTTON_NAME_ALIASES.get(btn_name, btn_name)
        self.trigger('mouse_click', btn_name, pressed)

    def _on_mouse_scroll(self, x, y, dx, dy):
        self.trigger('mouse_scroll', dx, dy)

    def start_keyboard_capture(self):
        self.stop_keyboard_capture()
        if self.special_key_injector is not None:
            self.special_key_injector.release_active_modifiers()
        listener_kwargs = {
            'on_press': self._on_key_press,
            'on_release': self._on_key_release,
            'suppress': True,
        }
        if os.name == 'nt':
            listener_kwargs['win32_event_filter'] = self._win32_keyboard_event_filter
        self.keyboard_listener = KeyboardListener(**listener_kwargs)
        self.keyboard_listener.start()
        # A switch-back can arrive immediately after capture starts. Waiting
        # here closes pynput's start/stop race, which otherwise leaves its
        # suppressing Windows hook installed while stop() blocks forever.
        self.keyboard_listener.wait()

    def _win32_keyboard_event_filter(self, msg, data):
        self._last_raw_keyboard_event = (data.vkCode, data.scanCode, data.flags)
        return True

    def stop_keyboard_capture(self):
        if self.keyboard_listener:
            self.keyboard_listener.stop()
            self.keyboard_listener = None
        detector = getattr(self, '_return_shortcut', None)
        if detector is not None:
            detector.reset()

    def _on_move_edge(self, x, y):
        if hasattr(self, "topology_edge_regions"):
            for region in self.topology_edge_regions:
                if self._point_hits_region(region, x, y):
                    logger.info(
                        "[cursor] Server edge hit display=%r side=%s "
                        "position=(%s, %s) destination=%r",
                        region.source_display_id,
                        region.source_side,
                        x,
                        y,
                        region.destination_machine_id,
                    )
                    self.trigger(
                        'edge_hit',
                        region.source_side,
                        edge_ratio(region.source_rect, region.source_side, x, y),
                        region,
                    )
                    return
            return
        if self.server_edge == 'right' and x >= self.screen_width - 2:
            self.trigger('edge_hit', 'right', y / self.screen_height)
        elif self.server_edge == 'left' and x <= 0:
            self.trigger('edge_hit', 'left', y / self.screen_height)
        elif self.server_edge == 'top' and y <= 0:
            self.trigger('edge_hit', 'top', x / self.screen_width)
        elif self.server_edge == 'bottom' and y >= self.screen_height - 2:
            self.trigger('edge_hit', 'bottom', x / self.screen_width)

    @staticmethod
    def _point_hits_region(region, x, y):
        rect = region.source_rect
        if region.source_side == "left":
            return (
                abs(x - rect.left) <= EDGE_HIT_TOLERANCE
                and rect.top <= y < rect.bottom
            )
        if region.source_side == "right":
            return (
                abs(x - (rect.right - 1)) <= EDGE_HIT_TOLERANCE
                and rect.top <= y < rect.bottom
            )
        if region.source_side == "top":
            return (
                abs(y - rect.top) <= EDGE_HIT_TOLERANCE
                and rect.left <= x < rect.right
            )
        if region.source_side == "bottom":
            return (
                abs(y - (rect.bottom - 1)) <= EDGE_HIT_TOLERANCE
                and rect.left <= x < rect.right
            )
        return False

    def _on_key_press(self, key):
        self.trigger('key_press', self._serialize_key(key))
        if self._return_shortcut.press(key):
            logger.warning(
                "[HOTKEY DIAGNOSTIC] Ctrl+Space, Space triggered during "
                "Server keyboard capture"
            )
            threading.Thread(
                target=self.trigger,
                args=('return_to_server',),
                daemon=True,
            ).start()

    def _on_key_release(self, key):
        self.trigger('key_release', self._serialize_key(key))
        self._return_shortcut.release(key)

    def _is_native_key_candidate(self, virtual_key, key):
        if not (type(virtual_key) is int and 0 <= virtual_key <= 0xFF):
            return False
        # Numpad keys (VK_NUMPAD0..VK_DIVIDE)
        if 0x60 <= virtual_key <= 0x6F:
            return True
        # Extended function keys (VK_F13..VK_F24)
        if 0x7C <= virtual_key <= 0x87:
            return True
        # Browser, Media, and App Launch keys
        if 0xA6 <= virtual_key <= 0xB7:
            return True
        # Macro / side buttons with vk but no character representation or standard pynput name
        char = getattr(key, 'char', None)
        name = getattr(key, 'name', None)
        if char is None and name is None:
            return True
        return False

    def _serialize_key(self, key):
        virtual_key = getattr(key, 'vk', None)
        scan_code = getattr(key, '_scan', None)
        flags = getattr(key, '_flags', 0)

        if (scan_code is None or scan_code == 0) and hasattr(self, '_last_raw_keyboard_event'):
            raw_vk, raw_scan, raw_flags = self._last_raw_keyboard_event
            if virtual_key is None or raw_vk == virtual_key:
                scan_code = raw_scan
                flags = raw_flags

        if (scan_code is None or scan_code == 0) and type(virtual_key) is int and os.name == 'nt':
            try:
                import ctypes
                mapped = ctypes.windll.user32.MapVirtualKeyW(virtual_key, 0)
                if mapped:
                    scan_code = mapped
            except Exception:
                pass

        extended = bool(
            type(flags) is int
            and flags & WindowsSpecialKeyInjector.KEYEVENTF_EXTENDEDKEY
        )

        if self._is_native_key_candidate(virtual_key, key):
            scan_val = scan_code if (type(scan_code) is int and 0 <= scan_code <= 0xFF) else 0
            return {
                'type': 'native_key',
                'vk': virtual_key,
                'scan': scan_val,
                'extended': extended,
            }
        if hasattr(key, 'char') and key.char is not None:
            return {'type': 'char', 'value': key.char}
        elif hasattr(key, 'name'):
            return {'type': 'special', 'value': key.name}
        elif hasattr(key, 'vk') and key.vk is not None:
            return {'type': 'vk', 'value': key.vk}
        elif type(scan_code) is int and 0 < scan_code <= 0xFF:
            return {
                'type': 'scan_code',
                'scan': scan_code,
                'extended': extended,
                'vk': virtual_key if type(virtual_key) is int else 0,
                'value': getattr(key, 'char', None) or getattr(key, 'name', None),
            }
        else:
            return {'type': 'unknown', 'value': str(key)}

    # --- Methods for the Client side to simulate inputs ---
    
    def inject_move(self, dx, dy):
        injector = getattr(self, "mouse_injector", None)
        if injector is not None:
            try:
                injector.move(dx, dy)
            except Exception:
                self.mouse.move(dx, dy)
        else:
            self.mouse.move(dx, dy)
        # Check if client mouse hits its return edge to switch back to server
        x, y = self.mouse.position
        self.check_edge_hit(x, y)

    def check_edge_hit(self, x, y):
        if hasattr(self, "client_topology_edge_regions"):
            for region in self.client_topology_edge_regions:
                if self._point_hits_region(region, x, y):
                    logger.info(
                        "[cursor] Client edge hit display=%r side=%s "
                        "position=(%s, %s) destination=%r",
                        region.source_display_id,
                        region.source_side,
                        x,
                        y,
                        region.destination_machine_id,
                    )
                    self.trigger(
                        'client_edge_hit',
                        region.source_side,
                        edge_ratio(region.source_rect, region.source_side, x, y),
                        region,
                    )
                    return True
            return False
        client_edge = getattr(self, 'client_edge', None)
        if client_edge == 'left' and x <= 0:
            self.trigger('client_edge_hit', 'left', y / self.screen_height)
            return True
        elif client_edge == 'right' and x >= self.screen_width - 2:
            self.trigger('client_edge_hit', 'right', y / self.screen_height)
            return True
        elif client_edge == 'top' and y <= 0:
            self.trigger('client_edge_hit', 'top', x / self.screen_width)
            return True
        elif client_edge == 'bottom' and y >= self.screen_height - 2:
            self.trigger('client_edge_hit', 'bottom', x / self.screen_width)
            return True
        return False

    def inject_position(self, x, y):
        before = tuple(self.mouse.position)
        target = (int(x), int(y))
        injector = getattr(self, "mouse_injector", None)
        if injector is not None and hasattr(injector, "set_position"):
            try:
                injector.set_position(*target)
            except Exception:
                self.mouse.position = target
        else:
            self.mouse.position = target
        observed = tuple(self.mouse.position)
        logger.info(
            "[cursor] Warp before=%s target=%s observed=%s",
            before,
            target,
            observed,
        )
        return observed

    def inject_click(self, button_name, pressed):
        normalized_name = BUTTON_NAME_ALIASES.get(button_name, button_name)
        injector = getattr(self, "mouse_injector", None)
        if injector is not None and hasattr(injector, "click"):
            try:
                if injector.click(normalized_name, pressed):
                    if pressed:
                        self._remember_injected_button(normalized_name)
                    else:
                        self._forget_injected_button(normalized_name)
                    return
            except Exception:
                pass
        btn = getattr(Button, normalized_name, None)
        if btn:
            if pressed:
                self.mouse.press(btn)
                self._remember_injected_button(normalized_name)
            else:
                self.mouse.release(btn)
                self._forget_injected_button(normalized_name)

    def inject_scroll(self, dx, dy):
        injector = getattr(self, "mouse_injector", None)
        if injector is not None and hasattr(injector, "scroll"):
            try:
                if injector.scroll(dx, dy):
                    return
            except Exception:
                pass
        self.mouse.scroll(dx, dy)

    def inject_key_press(self, key_data):
        if key_data and key_data.get('type') in ('scan_code', 'native_key'):
            if self._inject_hardware_key(key_data, pressed=True):
                self._remember_injected_key(key_data)
            return
        if (
            key_data and key_data.get('type') == 'special'
            and self.special_key_injector is not None
            and self.special_key_injector.press(key_data.get('value'))
        ):
            self._remember_injected_key(key_data)
            return
        key = self._deserialize_key(key_data)
        if key:
            self.keyboard.press(key)
            self._remember_injected_key(key_data)

    def inject_key_release(self, key_data):
        if key_data and key_data.get('type') in ('scan_code', 'native_key'):
            if self._inject_hardware_key(key_data, pressed=False):
                self._forget_injected_key(key_data)
            return
        if (
            key_data and key_data.get('type') == 'special'
            and self.special_key_injector is not None
            and self.special_key_injector.release(key_data.get('value'))
        ):
            self._forget_injected_key(key_data)
            return
        key = self._deserialize_key(key_data)
        if key:
            self.keyboard.release(key)
            self._forget_injected_key(key_data)

    @staticmethod
    def _injected_key_identity(key_data):
        return (
            key_data.get('type'),
            key_data.get('value'),
            key_data.get('vk'),
            key_data.get('scan'),
            key_data.get('extended'),
        )

    def _ensure_injected_key_state(self):
        if not hasattr(self, '_injected_keys_lock'):
            self._injected_keys_lock = threading.Lock()
            self._injected_keys = {}

    def _remember_injected_key(self, key_data):
        self._ensure_injected_key_state()
        with self._injected_keys_lock:
            self._injected_keys[self._injected_key_identity(key_data)] = dict(key_data)

    def _forget_injected_key(self, key_data):
        self._ensure_injected_key_state()
        with self._injected_keys_lock:
            self._injected_keys.pop(self._injected_key_identity(key_data), None)

    def release_all_injected_keys(self):
        self._ensure_injected_key_state()
        with self._injected_keys_lock:
            keys = tuple(self._injected_keys.values())
        for key_data in reversed(keys):
            try:
                self.inject_key_release(key_data)
            except Exception as error:
                logger.error("Could not release injected key (%s)", error_name(error))
        with self._injected_keys_lock:
            return not self._injected_keys

    def release_all_injected_input(self):
        keys_released = self.release_all_injected_keys()
        self._ensure_injected_button_state()
        with self._injected_buttons_lock:
            buttons = tuple(sorted(self._injected_buttons))
        for button_name in buttons:
            try:
                self.inject_click(button_name, False)
            except Exception as error:
                logger.error(
                    "Could not release injected mouse button (%s)",
                    error_name(error),
                )
        with self._injected_buttons_lock:
            return keys_released and not self._injected_buttons

    def _ensure_injected_button_state(self):
        if not hasattr(self, '_injected_buttons_lock'):
            self._injected_buttons_lock = threading.Lock()
            self._injected_buttons = set()

    def _remember_injected_button(self, button_name):
        self._ensure_injected_button_state()
        with self._injected_buttons_lock:
            self._injected_buttons.add(button_name)

    def _forget_injected_button(self, button_name):
        self._ensure_injected_button_state()
        with self._injected_buttons_lock:
            self._injected_buttons.discard(button_name)

    def _inject_hardware_key(self, key_data, pressed):
        virtual_key = key_data.get('vk')
        scan_code = key_data.get('scan')
        extended = key_data.get('extended')
        if not isinstance(extended, bool):
            return False
        valid_vk = type(virtual_key) is int and 0 <= virtual_key <= 0xFF
        valid_scan = type(scan_code) is int and 0 <= scan_code <= 0xFF
        if not (valid_vk or valid_scan):
            return False
        if virtual_key is not None and not valid_vk:
            return False
        if scan_code is not None and not valid_scan:
            return False

        injector = getattr(self, 'special_key_injector', None)
        if injector is None:
            return False

        k_type = key_data.get('type')
        if k_type == 'scan_code' and valid_scan and hasattr(injector, 'emit_scan'):
            return injector.emit_scan(scan_code, extended, pressed, virtual_key or 0)
        if valid_vk and hasattr(injector, 'emit_native'):
            return injector.emit_native(virtual_key, scan_code or 0, extended, pressed)
        if valid_scan and hasattr(injector, 'emit_scan'):
            return injector.emit_scan(scan_code, extended, pressed, virtual_key or 0)
        return False

    def _inject_native_key(self, key_data, pressed):
        return self._inject_hardware_key(key_data, pressed)

    def _deserialize_key(self, key_data):
        if not key_data: return None
        k_type = key_data.get('type')
        val = key_data.get('value')
        if k_type == 'char':
            return val
        elif k_type == 'special':
            return getattr(Key, val, None)
        elif k_type == 'vk':
            return KeyCode.from_vk(val)
        elif k_type in ('scan_code', 'native_key'):
            vk = key_data.get('vk')
            if isinstance(vk, int) and 0 <= vk <= 0xFF:
                return KeyCode.from_vk(vk)
            scan = key_data.get('scan')
            if isinstance(scan, int) and 0 <= scan <= 0xFF:
                return KeyCode.from_vk(scan)
        return None
