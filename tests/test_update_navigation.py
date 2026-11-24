import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import customtkinter as ctk

from app.update_navigation import UpdateNavigation


class UpdateNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        for token in cls.root.tk.call('after', 'info'):
            cls.root.tk.call('after', 'cancel', token)
        cls.root.destroy()

    def setUp(self):
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        self.roles = ctk.CTkTabview(self.root)
        self.roles.grid(row=0, column=0, sticky='nsew')
        self.roles.add('Server (Host)')
        self.roles.add('Client')
        with patch('app.update_panel.can_install', return_value=False):
            self.navigation = UpdateNavigation(self.root, self.roles, on_close=lambda: None)

    def tearDown(self):
        self.navigation.panel.destroy()
        self.navigation.page.destroy()
        self.navigation.destroy()
        self.roles.destroy()

    def deliver(self, result=None, error=None, operation='check'):
        panel = self.navigation.panel
        panel.busy = True
        panel.results.put((operation, result, error))
        panel._poll()

    def test_back_preserves_each_selected_role_and_existing_widgets(self):
        for role in ('Server (Host)', 'Client'):
            self.roles.set(role)
            original = self.roles.tab(role)
            self.navigation.button.invoke()
            self.assertEqual(self.roles.grid_info(), {})
            self.assertTrue(self.navigation.page.grid_info())
            self.navigation.back.invoke()
            self.assertEqual(self.roles.get(), role)
            self.assertIs(self.roles.tab(role), original)
            self.assertTrue(self.roles.grid_info())
            self.assertEqual(self.navigation.page.grid_info(), {})

    def test_status_never_claims_unknown_or_failed_check_is_current(self):
        self.assertIn('not checked', self.navigation.description.get())
        self.deliver()
        self.assertIn('✓', self.navigation.description.get())
        self.deliver(error=OSError('offline'))
        self.assertIn('failed', self.navigation.description.get())
        self.assertNotIn('✓', self.navigation.description.get())
        self.assertIn('Could not check', self.navigation.panel.status.cget('text'))

    def test_available_download_and_install_error_keep_update_attention(self):
        update = SimpleNamespace(version='99.0')
        self.deliver(update)
        self.assertIn('!', self.navigation.description.get())
        self.assertEqual(self.navigation.button.cget('fg'), '#f4cc58')
        self.assertEqual(self.navigation.panel.action.cget('text'), 'Download update')
        self.deliver(error=OSError('offline'), operation='download')
        self.assertIn('!', self.navigation.description.get())
        self.assertIn('failed', self.navigation.description.get())

    def test_checking_is_reported_before_worker_completes(self):
        with patch('app.update_panel.threading.Thread'):
            self.navigation.panel.check()
        self.assertIn('Checking', self.navigation.description.get())
        self.navigation.panel.after_cancel(self.navigation.panel.timer)
        self.navigation.panel.timer = None

    def test_footer_and_keyboard_activation(self):
        self.assertEqual(str(self.navigation.button.cget('takefocus')), '1')
        self.assertTrue(self.navigation.button.bind('<Return>'))
        self.assertEqual(self.navigation.place_info()['anchor'], 'se')
        self.assertIs(self.navigation.master, self.roles)
        self.assertEqual(self.navigation.button.pack_info()['side'], 'right')
        self.assertEqual(set(self.roles._tab_dict), {'Server (Host)', 'Client'})

    def test_install_failure_preserves_verified_update_and_reports_error(self):
        self.deliver(SimpleNamespace(version='99.0'))
        panel = self.navigation.panel
        panel.download = SimpleNamespace(path='verified-setup.exe')
        with patch('app.update_panel.messagebox.askyesno', return_value=True), \
                patch('app.update_panel.verified_installer', side_effect=OSError('missing')), \
                patch('app.update_panel.launch_installer') as launch:
            panel._install()
        launch.assert_not_called()
        self.assertIn('failed', self.navigation.description.get())
        self.assertIn('verified installer', panel.status.cget('text'))

    def test_shortcut_stays_inside_panel_at_different_sizes_and_scaling(self):
        self.root.deiconify()
        try:
            for scaling in (1.0, 1.5, 2.0):
                ctk.set_widget_scaling(scaling)
                for size in ('400x650', '600x850'):
                    self.root.geometry(size)
                    self.root.update()
                    self.root.update_idletasks()
                    footer = self.navigation
                    self.assertGreaterEqual(footer.winfo_y(), 0)
                    self.assertLessEqual(footer.winfo_y() + footer.winfo_height(), self.roles.winfo_height())
                    self.assertLessEqual(footer.winfo_y() + footer.winfo_height(), self.root.winfo_height())
                    self.assertLessEqual(self.navigation.button.winfo_x() + self.navigation.button.winfo_width(), footer.winfo_width())
        finally:
            ctk.set_widget_scaling(1.0)
            self.root.withdraw()


class MainWindowUpdateNavigationTests(unittest.TestCase):
    def test_real_window_preserves_role_session_and_remote_mode(self):
        from app.gui import ConduitGUI
        with patch('app.gui.GlobalHotkeyMonitor.start'), \
                patch('app.gui.ConduitGUI._refresh_firewall_status'), \
                patch('app.update_panel.can_install', return_value=False):
            window = ConduitGUI()
        window.withdraw()
        try:
            self.assertEqual(set(window.tabview._tab_dict), {'Server (Host)', 'Client'})
            server, client = object(), object()
            window.server, window.client = server, client
            window.remote_mode_var.set(True)
            for role in ('Server (Host)', 'Client'):
                window.tabview.set(role)
                window.update_navigation.show()
                window.update_navigation.hide()
                self.assertEqual(window.tabview.get(), role)
                self.assertIs(window.server, server)
                self.assertIs(window.client, client)
                self.assertTrue(window.remote_mode_var.get())
            self.assertIs(window.update_panel, window.update_navigation.panel)
            # Let CTkTabview finish its delayed cleanup between role changes.
            window.update()
            time.sleep(.15)
            window.update()
            window.tabview.set('Server (Host)')
            window.deiconify()
            window.update()
            self.assertTrue(window.remote_mode_toggle.winfo_ismapped())
            toggle = window.remote_mode_toggle
            self.assertLessEqual(toggle.winfo_y() + toggle.winfo_height(), window.tab_server.winfo_height())
        finally:
            for token in window.tk.call('after', 'info'):
                window.tk.call('after', 'cancel', token)
            window.destroy()
