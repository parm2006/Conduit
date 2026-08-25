import logging
import os
import threading
from dataclasses import dataclass
from pynput.mouse import Controller as MouseController, Listener as MouseListener, Button
from pynput.keyboard import Controller as KeyboardController, Listener as KeyboardListener, Key, KeyCode
from app.safe_errors import error_name
from app.display_topology import edge_ratio

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


class WindowsSpecialKeyInjector:
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
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

class InputHandler:
    def __init__(self):
        self.mouse = MouseController()
        self.mouse_listener = None
        self.keyboard = KeyboardController()
        self.special_key_injector = (
            WindowsSpecialKeyInjector() if os.name == "nt" else None
        )
        self.keyboard_listener = None
        self.callbacks = {}
        self._injected_keys = {}
        self._injected_keys_lock = threading.Lock()
        
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
        self.client_topology_edge_regions = () if region is None else (region,)

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

    def stop(self):
        if self.mouse_listener:
            self.mouse_listener.stop()
            self.mouse_listener = None
        self.stop_keyboard_capture()

    def start_keyboard_capture(self):
        self.stop_keyboard_capture()
        if self.special_key_injector is not None:
            self.special_key_injector.release_active_modifiers()
        self.keyboard_listener = KeyboardListener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
            suppress=True
        )
        self.keyboard_listener.start()

    def stop_keyboard_capture(self):
        if self.keyboard_listener:
            self.keyboard_listener.stop()
            self.keyboard_listener = None

    def _on_move_edge(self, x, y):
        if hasattr(self, "topology_edge_regions"):
            for region in self.topology_edge_regions:
                if self._point_hits_region(region, x, y):
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
            return abs(x - rect.left) <= 2 and rect.top <= y < rect.bottom
        if region.source_side == "right":
            return abs(x - (rect.right - 1)) <= 2 and rect.top <= y < rect.bottom
        if region.source_side == "top":
            return abs(y - rect.top) <= 2 and rect.left <= x < rect.right
        if region.source_side == "bottom":
            return abs(y - (rect.bottom - 1)) <= 2 and rect.left <= x < rect.right
        return False

    def _on_key_press(self, key):
        self.trigger('key_press', self._serialize_key(key))

    def _on_key_release(self, key):
        self.trigger('key_release', self._serialize_key(key))

    def _serialize_key(self, key):
        virtual_key = getattr(key, 'vk', None)
        if type(virtual_key) is int and 0x60 <= virtual_key <= 0x6F:
            scan_code = getattr(key, '_scan', 0)
            if type(scan_code) is not int or not 0 <= scan_code <= 0xFF:
                scan_code = 0
            native_flags = getattr(key, '_flags', 0)
            return {
                'type': 'native_key',
                'vk': virtual_key,
                'scan': scan_code,
                'extended': bool(
                    type(native_flags) is int
                    and native_flags & WindowsSpecialKeyInjector.KEYEVENTF_EXTENDEDKEY
                ),
            }
        if hasattr(key, 'char') and key.char is not None:
            return {'type': 'char', 'value': key.char}
        elif hasattr(key, 'name'):
            return {'type': 'special', 'value': key.name}
        elif hasattr(key, 'vk') and key.vk is not None:
            return {'type': 'vk', 'value': key.vk}
        else:
            return {'type': 'unknown', 'value': str(key)}

    # --- Methods for the Client side to simulate inputs ---
    
    def inject_move(self, dx, dy):
        self.mouse.move(dx, dy)
        # Check if client mouse hits its return edge to switch back to server
        x, y = self.mouse.position
        if hasattr(self, "client_topology_edge_regions"):
            for region in self.client_topology_edge_regions:
                if self._point_hits_region(region, x, y):
                    self.trigger(
                        'client_edge_hit',
                        region.source_side,
                        edge_ratio(region.source_rect, region.source_side, x, y),
                        region,
                    )
                    return
            return
        if self.client_edge == 'left' and x <= 0:
            self.trigger('client_edge_hit', 'left', y / self.screen_height)
        elif self.client_edge == 'right' and x >= self.screen_width - 2:
            self.trigger('client_edge_hit', 'right', y / self.screen_height)
        elif self.client_edge == 'top' and y <= 0:
            self.trigger('client_edge_hit', 'top', x / self.screen_width)
        elif self.client_edge == 'bottom' and y >= self.screen_height - 2:
            self.trigger('client_edge_hit', 'bottom', x / self.screen_width)

    def inject_position(self, x, y):
        self.mouse.position = (x, y)

    def inject_click(self, button_name, pressed):
        btn = getattr(Button, button_name, None)
        if btn:
            if pressed:
                self.mouse.press(btn)
            else:
                self.mouse.release(btn)

    def inject_scroll(self, dx, dy):
        self.mouse.scroll(dx, dy)

    def inject_key_press(self, key_data):
        if key_data and key_data.get('type') == 'native_key':
            if self._inject_native_key(key_data, pressed=True):
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
        if key_data and key_data.get('type') == 'native_key':
            if self._inject_native_key(key_data, pressed=False):
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

    def _inject_native_key(self, key_data, pressed):
        virtual_key = key_data.get('vk')
        scan_code = key_data.get('scan')
        extended = key_data.get('extended')
        if not (
            type(virtual_key) is int
            and 0x60 <= virtual_key <= 0x6F
            and type(scan_code) is int
            and 0 <= scan_code <= 0xFF
            and isinstance(extended, bool)
            and self.special_key_injector is not None
            and hasattr(self.special_key_injector, 'emit_native')
        ):
            return False
        return self.special_key_injector.emit_native(
            virtual_key,
            scan_code,
            extended,
            pressed,
        )

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
        return None
