import time
import unittest
from unittest.mock import patch
import customtkinter as ctk

from app.update_panel import UpdatePanel


class UpdatePanelTests(unittest.TestCase):
    def test_offline_check_recovers_without_blocking_tk(self):
        root = ctk.CTk()
        root.withdraw()
        try:
            with patch('app.update_panel.check_for_updates', side_effect=OSError('offline')):
                panel = UpdatePanel(root, on_close=lambda: None)
                panel.check()
                deadline = time.monotonic() + 3
                while panel.busy and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)
            self.assertFalse(panel.busy)
            self.assertIn('Could not check', panel.status.cget('text'))
            self.assertEqual(panel.action.cget('state'), 'normal')
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
