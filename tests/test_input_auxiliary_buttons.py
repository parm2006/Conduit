import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
from pynput.mouse import Button

from app.input_handler import InputHandler, BUTTON_NAME_ALIASES, WindowsSpecialKeyInjector, WindowsMouseInjector


class RecordingUser32:
    def __init__(self):
        self.events = []

    def keybd_event(self, virtual_key, scan_code, flags, extra_info):
        self.events.append((virtual_key, scan_code, flags, extra_info))

    def mouse_event(self, flags, dx, dy, data, extra_info):
        self.events.append(('mouse', flags, dx, dy, data, extra_info))


class RecordingMouse:
    def __init__(self):
        self.events = []

    def press(self, button):
        self.events.append(('press', button))

    def release(self, button):
        self.events.append(('release', button))


class FakeRawKey:
    def __init__(self, vk=None, char=None, name=None, scan=0, flags=0):
        if vk is not None:
            self.vk = vk
        if char is not None:
            self.char = char
        if name is not None:
            self.name = name
        self._scan = scan
        self._flags = flags


class MouseAuxiliaryButtonsAndMacroKeyTests(unittest.TestCase):
    def test_button_aliases_mapping(self):
        self.assertEqual(BUTTON_NAME_ALIASES.get('back'), 'x1')
        self.assertEqual(BUTTON_NAME_ALIASES.get('forward'), 'x2')
        self.assertEqual(BUTTON_NAME_ALIASES.get('button4'), 'x1')
        self.assertEqual(BUTTON_NAME_ALIASES.get('button5'), 'x2')
        self.assertEqual(BUTTON_NAME_ALIASES.get('xbutton1'), 'x1')
        self.assertEqual(BUTTON_NAME_ALIASES.get('xbutton2'), 'x2')

    def test_on_mouse_click_normalizes_aliases(self):
        handler = InputHandler.__new__(InputHandler)
        handler.callbacks = {}

        emitted = []
        handler.register_callback('mouse_click', lambda btn, pressed: emitted.append((btn, pressed)))

        # Pynput Button.x1
        handler._on_mouse_click(0, 0, Button.x1, True)
        self.assertEqual(emitted[-1], ('x1', True))

        # Pynput Button.x2
        handler._on_mouse_click(0, 0, Button.x2, False)
        self.assertEqual(emitted[-1], ('x2', False))

        # Left, right, middle
        handler._on_mouse_click(0, 0, Button.left, True)
        self.assertEqual(emitted[-1], ('left', True))

        handler._on_mouse_click(0, 0, Button.middle, True)
        self.assertEqual(emitted[-1], ('middle', True))

        handler._on_mouse_click(0, 0, Button.right, False)
        self.assertEqual(emitted[-1], ('right', False))

    def test_inject_click_supports_x1_x2_and_aliases(self):
        handler = InputHandler.__new__(InputHandler)
        handler.mouse = RecordingMouse()
        handler._injected_buttons_lock = MagicMock()
        handler._injected_buttons = set()

        # Inject x1 press
        handler.inject_click('x1', True)
        self.assertEqual(handler.mouse.events[-1], ('press', Button.x1))

        # Inject x2 release
        handler.inject_click('x2', False)
        self.assertEqual(handler.mouse.events[-1], ('release', Button.x2))

        # Inject 'back' alias
        handler.inject_click('back', True)
        self.assertEqual(handler.mouse.events[-1], ('press', Button.x1))

        # Inject 'forward' alias
        handler.inject_click('forward', False)
        self.assertEqual(handler.mouse.events[-1], ('release', Button.x2))

    def test_f13_to_f24_serialized_as_native_key(self):
        handler = InputHandler.__new__(InputHandler)

        # F13 = 0x7C, F24 = 0x87
        for vk in range(0x7C, 0x88):
            with self.subTest(vk=vk):
                key = FakeRawKey(vk=vk, scan=0x64, flags=0)
                serialized = handler._serialize_key(key)
                self.assertEqual(serialized['type'], 'native_key')
                self.assertEqual(serialized['vk'], vk)
                self.assertEqual(serialized['scan'], 0x64)
                self.assertFalse(serialized['extended'])

    def test_browser_and_media_keys_serialized_as_native_key(self):
        handler = InputHandler.__new__(InputHandler)

        # 0xA6 (Browser Back) to 0xB7
        for vk in range(0xA6, 0xB8):
            with self.subTest(vk=vk):
                key = FakeRawKey(vk=vk, scan=0x6A, flags=1)
                serialized = handler._serialize_key(key)
                self.assertEqual(serialized['type'], 'native_key')
                self.assertEqual(serialized['vk'], vk)
                self.assertEqual(serialized['scan'], 0x6A)
                self.assertTrue(serialized['extended'])

    def test_mmo_mouse_unmapped_key_serialized_as_native_key(self):
        handler = InputHandler.__new__(InputHandler)

        # Custom macro button sending raw VK code 0x88 without char or pynput name
        key = FakeRawKey(vk=0x88, scan=0x5B, flags=0)
        serialized = handler._serialize_key(key)
        self.assertEqual(serialized['type'], 'native_key')
        self.assertEqual(serialized['vk'], 0x88)
        self.assertEqual(serialized['scan'], 0x5B)

    def test_native_key_injected_for_extended_keys(self):
        user32 = RecordingUser32()
        handler = InputHandler.__new__(InputHandler)
        handler.special_key_injector = WindowsSpecialKeyInjector(user32)

        key_data = {'type': 'native_key', 'vk': 0x7C, 'scan': 0x64, 'extended': False}
        handler.inject_key_press(key_data)
        handler.inject_key_release(key_data)

        self.assertEqual(
            user32.events,
            [
                (0x7C, 0x64, 0, 0),
                (0x7C, 0x64, 0x0002, 0),  # KEYEVENTF_KEYUP
            ],
        )

    def test_standard_keys_retain_normal_serialization(self):
        handler = InputHandler.__new__(InputHandler)

        # Character 'a'
        key_char = FakeRawKey(vk=0x41, char='a')
        self.assertEqual(handler._serialize_key(key_char), {'type': 'char', 'value': 'a'})

        # Special key 'space'
        key_space = FakeRawKey(vk=0x20, name='space')
        self.assertEqual(handler._serialize_key(key_space), {'type': 'special', 'value': 'space'})

        # Navigation key 'page_down'
        key_pgdn = FakeRawKey(vk=0x22, name='page_down')
        self.assertEqual(handler._serialize_key(key_pgdn), {'type': 'special', 'value': 'page_down'})

    def test_gui_overlay_does_not_duplicate_when_mouse_listener_active(self):
        from app.gui import ConduitGUI

        app = ConduitGUI.__new__(ConduitGUI)
        mock_server = SimpleNamespace(
            on_mouse_click=MagicMock(),
            on_mouse_scroll=MagicMock(),
            input_handler=SimpleNamespace(
                mouse_button_listener=MagicMock(),
            ),
        )
        app.server = mock_server

        # When mouse_button_listener is active:
        event = SimpleNamespace(num=1, delta=120)
        app.on_overlay_press(event)
        app.on_overlay_release(event)
        app.on_overlay_scroll(event)

        mock_server.on_mouse_click.assert_not_called()
        mock_server.on_mouse_scroll.assert_not_called()

        # When mouse_button_listener is NOT active (fallback mode):
        mock_server.input_handler.mouse_button_listener = None
        app.on_overlay_press(event)
        mock_server.on_mouse_click.assert_called_once_with('left', True)

        app.on_overlay_release(event)
        mock_server.on_mouse_click.assert_called_with('left', False)

        app.on_overlay_scroll(event)
        mock_server.on_mouse_scroll.assert_called_once_with(0, 1)

    def test_windows_mouse_injector_calls_mouse_event(self):
        user32 = RecordingUser32()
        injector = WindowsMouseInjector(user32)
        self.assertTrue(injector.move(15, -20))
        self.assertEqual(user32.events, [('mouse', 0x0001, 15, -20, 0, 0)])

    def test_inject_move_uses_hardware_injector_when_available(self):
        user32 = RecordingUser32()
        handler = InputHandler.__new__(InputHandler)
        handler.mouse = SimpleNamespace(position=(500, 500), move=MagicMock())
        handler.mouse_injector = WindowsMouseInjector(user32)

        handler.inject_move(10, 5)

        self.assertEqual(user32.events, [('mouse', 0x0001, 10, 5, 0, 0)])
        handler.mouse.move.assert_not_called()


if __name__ == '__main__':
    unittest.main()
