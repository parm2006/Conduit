import threading
import unittest

from pynput.keyboard import Key

from app.input_handler import InputHandler


class RemoteReturnHotkeyTests(unittest.TestCase):
    def test_server_capture_detects_return_after_forwarding_second_space(self):
        forwarded = []
        callback_snapshot = []
        returned = threading.Event()

        def on_return():
            callback_snapshot.append(tuple(forwarded))
            returned.set()

        handler = InputHandler()
        handler.register_callback("return_to_server", on_return)
        handler.register_callback(
            "key_press",
            lambda key: forwarded.append(("press", key)),
        )
        handler.register_callback(
            "key_release",
            lambda key: forwarded.append(("release", key)),
        )

        handler._on_key_press(Key.ctrl_l)
        handler._on_key_press(Key.space)
        handler._on_key_release(Key.space)
        handler._on_key_press(Key.space)

        self.assertTrue(returned.wait(0.2))
        self.assertEqual(
            forwarded,
            [
                ("press", {"type": "special", "value": "ctrl_l"}),
                ("press", {"type": "special", "value": "space"}),
                ("release", {"type": "special", "value": "space"}),
                ("press", {"type": "special", "value": "space"}),
            ],
        )
        self.assertEqual(callback_snapshot, [tuple(forwarded)])

    def test_stopping_capture_discards_a_partial_return_sequence(self):
        returned = threading.Event()
        handler = InputHandler()
        handler.register_callback("return_to_server", returned.set)

        handler._on_key_press(Key.ctrl_l)
        handler._on_key_press(Key.space)
        handler._on_key_release(Key.space)
        handler.stop_keyboard_capture()

        handler._on_key_press(Key.ctrl_l)
        handler._on_key_press(Key.space)
        handler._on_key_release(Key.space)

        self.assertFalse(returned.wait(0.05))


if __name__ == "__main__":
    unittest.main()
