import unittest

from app.file_transfer.hotkey import WindowsPasteHotkeyMonitor
from app.file_transfer.paste_coordinator import ClipboardOffer, PasteCoordinator


class FakeListener:
    def __init__(self):
        self.suppressed = 0

    def suppress_event(self):
        self.suppressed += 1


class KeyData:
    def __init__(self, vk_code, flags=0):
        self.vkCode = vk_code
        self.flags = flags


class WindowsPasteHotkeyTests(unittest.TestCase):
    def test_suppresses_only_physical_ctrl_v_when_files_are_available(self):
        requests = []
        coordinator = PasteCoordinator(lambda: requests.append("paste"))
        coordinator.set_route(
            ClipboardOffer("session-one", 1, "client", "files", 20),
            "server",
        )
        monitor = WindowsPasteHotkeyMonitor(coordinator)
        monitor.listener = FakeListener()

        self.assertTrue(monitor.filter_event(monitor.WM_KEYDOWN, KeyData(monitor.VK_CONTROL)))
        self.assertFalse(monitor.filter_event(monitor.WM_KEYDOWN, KeyData(monitor.VK_V)))
        self.assertFalse(monitor.filter_event(monitor.WM_KEYUP, KeyData(monitor.VK_V)))
        self.assertTrue(monitor.filter_event(monitor.WM_KEYUP, KeyData(monitor.VK_CONTROL)))

        self.assertEqual(requests, ["paste"])
        self.assertEqual(monitor.listener.suppressed, 2)

    def test_ignores_injected_keys_and_ordinary_ctrl_v(self):
        requests = []
        coordinator = PasteCoordinator(lambda: requests.append("paste"))
        monitor = WindowsPasteHotkeyMonitor(coordinator)
        monitor.listener = FakeListener()

        monitor.filter_event(monitor.WM_KEYDOWN, KeyData(monitor.VK_CONTROL))
        self.assertTrue(monitor.filter_event(monitor.WM_KEYDOWN, KeyData(monitor.VK_V)))
        coordinator.set_route(
            ClipboardOffer("session-one", 1, "client", "files", 20),
            "server",
        )
        self.assertTrue(
            monitor.filter_event(
                monitor.WM_KEYDOWN,
                KeyData(monitor.VK_V, monitor.LLKHF_INJECTED),
            )
        )
        self.assertEqual(requests, [])
        self.assertEqual(monitor.listener.suppressed, 0)


if __name__ == "__main__":
    unittest.main()
