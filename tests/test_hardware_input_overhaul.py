import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.input_handler import (
    InputHandler,
    WindowsSpecialKeyInjector,
    WindowsHardwareMouseInjector,
)


class RecordingUser32:
    def __init__(self):
        self.keyboard_events = []
        self.mouse_events = []

    def keybd_event(self, virtual_key, scan_code, flags, extra_info):
        self.keyboard_events.append((virtual_key, scan_code, flags, extra_info))

    def mouse_event(self, flags, dx, dy, data, extra_info):
        self.mouse_events.append((flags, dx, dy, data, extra_info))


class FakeKey:
    def __init__(self, vk=None, scan=None, char=None, name=None, flags=0):
        if vk is not None:
            self.vk = vk
        if scan is not None:
            self._scan = scan
        if char is not None:
            self.char = char
        if name is not None:
            self.name = name
        self._flags = flags


class HardwareInputOverhaulTests(unittest.TestCase):
    def setUp(self):
        self.user32 = RecordingUser32()
        self.mouse_injector = WindowsHardwareMouseInjector(self.user32)
        self.key_injector = WindowsSpecialKeyInjector(self.user32)

    def test_mouse_injector_move(self):
        self.assertTrue(self.mouse_injector.move(12, -8))
        self.assertEqual(
            self.user32.mouse_events[-1],
            (0x0001, 12, -8, 0, 0),
        )

    def test_mouse_injector_all_5_buttons(self):
        cases = [
            ('left', True, 0x0002, 0),
            ('left', False, 0x0004, 0),
            ('right', True, 0x0008, 0),
            ('right', False, 0x0010, 0),
            ('middle', True, 0x0020, 0),
            ('middle', False, 0x0040, 0),
            ('x1', True, 0x0080, 1),
            ('x1', False, 0x0100, 1),
            ('x2', True, 0x0080, 2),
            ('x2', False, 0x0100, 2),
        ]
        for btn, pressed, expected_flag, expected_data in cases:
            with self.subTest(btn=btn, pressed=pressed):
                self.assertTrue(self.mouse_injector.click(btn, pressed))
                self.assertEqual(
                    self.user32.mouse_events[-1],
                    (expected_flag, 0, 0, expected_data, 0),
                )

    def test_mouse_injector_scroll_dual_axis(self):
        # Vertical scroll
        self.assertTrue(self.mouse_injector.scroll(0, 2))
        self.assertEqual(
            self.user32.mouse_events[-1],
            (0x0800, 0, 0, 240, 0),
        )

        # Horizontal tilt scroll
        self.assertTrue(self.mouse_injector.scroll(-1, 0))
        self.assertEqual(
            self.user32.mouse_events[-1],
            (0x1000, 0, 0, -120, 0),
        )

    def test_keyboard_injector_emit_scan(self):
        # Press scan code 0x1E (A)
        self.assertTrue(self.key_injector.emit_scan(0x1E, extended=False, pressed=True, virtual_key=0x41))
        self.assertEqual(
            self.user32.keyboard_events[-1],
            (0x41, 0x1E, 0x0008, 0),  # KEYEVENTF_SCANCODE
        )

        # Release scan code 0x1E (A)
        self.assertTrue(self.key_injector.emit_scan(0x1E, extended=False, pressed=False, virtual_key=0x41))
        self.assertEqual(
            self.user32.keyboard_events[-1],
            (0x41, 0x1E, 0x0008 | 0x0002, 0),  # KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP
        )

        # Press extended key (e.g. arrow or numpad enter)
        self.assertTrue(self.key_injector.emit_scan(0x48, extended=True, pressed=True, virtual_key=0x26))
        self.assertEqual(
            self.user32.keyboard_events[-1],
            (0x26, 0x48, 0x0008 | 0x0001, 0),  # KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY
        )

    def test_serialize_key_produces_scan_code_packet(self):
        handler = InputHandler.__new__(InputHandler)

        key = FakeKey(scan=0x1E, flags=0)
        serialized = handler._serialize_key(key)
        self.assertEqual(serialized['type'], 'scan_code')
        self.assertEqual(serialized['scan'], 0x1E)
        self.assertFalse(serialized['extended'])

    def test_serialize_key_uses_last_raw_keyboard_event(self):
        handler = InputHandler.__new__(InputHandler)
        handler._last_raw_keyboard_event = (0, 0x39, 0)

        # Key without direct _scan attribute or standard name/char
        key = FakeKey()
        serialized = handler._serialize_key(key)
        self.assertEqual(serialized['type'], 'scan_code')
        self.assertEqual(serialized['scan'], 0x39)

    def test_inject_scan_code_press_and_release(self):
        handler = InputHandler.__new__(InputHandler)
        handler.special_key_injector = self.key_injector

        key_data = {
            'type': 'scan_code',
            'scan': 0x1E,
            'extended': False,
            'vk': 0x41,
            'value': 'a',
        }

        handler.inject_key_press(key_data)
        self.assertEqual(
            self.user32.keyboard_events[-1],
            (0x41, 0x1E, 0x0008, 0),
        )

        handler.inject_key_release(key_data)
        self.assertEqual(
            self.user32.keyboard_events[-1],
            (0x41, 0x1E, 0x0008 | 0x0002, 0),
        )

    def test_client_inject_click_uses_hardware_mouse_injector(self):
        handler = InputHandler.__new__(InputHandler)
        handler.mouse_injector = self.mouse_injector

        # Injected click via hardware injector
        handler.inject_click('left', True)
        self.assertEqual(
            self.user32.mouse_events[-1],
            (0x0002, 0, 0, 0, 0),
        )
        handler.inject_click('left', False)
        self.assertEqual(
            self.user32.mouse_events[-1],
            (0x0004, 0, 0, 0, 0),
        )

        # Injected Back button (x1)
        handler.inject_click('back', True)
        self.assertEqual(
            self.user32.mouse_events[-1],
            (0x0080, 0, 0, 1, 0),
        )
        handler.inject_click('back', False)
        self.assertEqual(
            self.user32.mouse_events[-1],
            (0x0100, 0, 0, 1, 0),
        )

    def test_client_inject_scroll_uses_hardware_mouse_injector(self):
        handler = InputHandler.__new__(InputHandler)
        handler.mouse_injector = self.mouse_injector

        handler.inject_scroll(0, -3)
        self.assertEqual(
            self.user32.mouse_events[-1],
            (0x0800, 0, 0, -360, 0),
        )


if __name__ == '__main__':
    unittest.main()
