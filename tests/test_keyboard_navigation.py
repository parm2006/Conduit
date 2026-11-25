from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


class KeyboardNavigationTests(unittest.TestCase):
    def _run_subprocess(self, method_name):
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import unittest; "
                "from tests.test_keyboard_navigation import KeyboardNavigationTests; "
                f"suite = unittest.TestSuite([KeyboardNavigationTests({method_name!r})]); "
                "result = unittest.TextTestRunner(verbosity=2).run(suite); "
                "raise SystemExit(not result.wasSuccessful())",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)

    def test_main_window_tab_order_focus_rings_and_client_actions(self):
        self._run_subprocess("_check_main_window_keyboard_navigation")

    def test_dialogs_and_updates_keyboard_navigation(self):
        self._run_subprocess("_check_dialogs_and_updates_navigation")

    def _make_gui(self):
        from app.display_topology import DraftTopology, NativeRect, PlacedMachine
        from app.gui import ConduitGUI
        from app.windows_displays import Display, MachineDisplayGroup

        group = MachineDisplayGroup(
            machine_id="server-machine",
            windows_name="SERVER",
            displays=(
                Display("disp-1", NativeRect(0, 0, 1920, 1080), 100, 0, True, True),
            ),
        )
        with patch("app.gui.GlobalHotkeyMonitor.start"), \
                patch("app.gui.ConduitGUI._refresh_firewall_status"), \
                patch("app.gui.WindowsDisplayDiscovery.discover", return_value=group), \
                patch("app.update_panel.check_for_updates", return_value=None), \
                patch("app.update_panel.can_install", return_value=False):
            gui = ConduitGUI()
        gui.withdraw()
        gui.update()
        return gui

    def _cleanup_gui(self, gui):
        for token in gui.tk.call("after", "info"):
            try:
                gui.tk.call("after", "cancel", token)
            except Exception:
                pass
        gui.destroy()

    def _check_main_window_keyboard_navigation(self):
        from app.keyboard_nav import FOCUS_BORDER_COLOR, focus_target, tabview_tab_buttons

        gui = self._make_gui()
        try:
            gui.deiconify()
            gui.tabview.set("Server (Host)")
            gui.update()

            tabs = tabview_tab_buttons(gui.tabview)
            self.assertEqual(len(tabs), 2)

            # Start on first Server tab button, Tab through visible Server controls.
            focus_target(tabs[0]).focus_set()
            gui.update()
            self.assertEqual(tabs[0].cget("border_color"), FOCUS_BORDER_COLOR)

            expected_server_order = [
                tabs[1],
                gui.server_port_entry,
                gui.server_password_entry,
                gui.topology_editor.cancel_button,
                gui.topology_editor.apply_button,
                gui.server_start_btn,
                gui.remote_mode_toggle,
                gui.update_navigation.button,
            ]
            for control in expected_server_order:
                gui.focus_get().event_generate("<Tab>")
                gui.update()
                self.assertIs(gui.focus_get(), focus_target(control))

            # Shift+Tab walks backward cleanly.
            gui.focus_get().event_generate("<Shift-Tab>")
            gui.update()
            self.assertIs(gui.focus_get(), focus_target(gui.remote_mode_toggle))

            # Switch to Client tab via keyboard Right arrow on the first tab button.
            focus_target(tabs[0]).focus_set()
            gui.update()
            gui.focus_get().event_generate("<Right>")
            gui.update()
            self.assertEqual(gui.tabview.get(), "Client")

            expected_client_order = [
                gui.client_ip_entry,
                gui.client_port_entry,
                gui.client_password_entry,
                gui.client_connect_btn,
                gui.repair_btn,
                gui.update_navigation.button,
            ]
            # Focus is currently on tabs[1] ("Client"); Tab moves through Client controls.
            for control in expected_client_order:
                gui.focus_get().event_generate("<Tab>")
                gui.update()
                self.assertIs(gui.focus_get(), focus_target(control))
            # Tab wraps back to tabs[0], never entering read-only status_text.
            gui.focus_get().event_generate("<Tab>")
            gui.update()
            self.assertIs(gui.focus_get(), focus_target(tabs[0]))

            # Enter on focused Connect invokes connect_client via the existing command.
            connect_calls = []
            gui.client_connect_btn.configure(
                command=lambda: connect_calls.append("connect")
            )
            focus_target(gui.client_connect_btn).focus_set()
            gui.update()
            self.assertEqual(gui.client_connect_btn.cget("border_color"), FOCUS_BORDER_COLOR)
            gui.focus_get().event_generate("<Return>")
            gui.update()
            self.assertEqual(connect_calls, ["connect"])

            # Enter on focused Forget saved identity and re-pair invokes clear_client_trust.
            clear_calls = []
            orig_clear = gui.clear_client_trust
            gui.repair_btn.configure(
                command=lambda: (clear_calls.append("clear"), orig_clear())
            )
            focus_target(gui.repair_btn).focus_set()
            gui.update()
            gui.focus_get().event_generate("<Return>")
            gui.update()
            self.assertEqual(clear_calls, ["clear"])
            self.assertIs(gui.focus_get(), focus_target(gui.repair_btn))
        finally:
            self._cleanup_gui(gui)

    def _check_dialogs_and_updates_navigation(self):
        import customtkinter as ctk
        from app.keyboard_nav import focus_target
        from app.pairing_dialog import PairingDecision, PairingDialog, PairingOutcome, PairingPrompt
        from app.update_navigation import UpdateNavigation

        root = ctk.CTk()
        root.withdraw()
        root.update()
        try:
            prompt = PairingPrompt("123456", "127.0.0.1:28903", "ab" * 32)
            decision = PairingDecision()
            dialog = PairingDialog(root, prompt, decision)
            root.update()
            self.assertIs(dialog.window.focus_get(), focus_target(dialog.decline))
            dialog.window.focus_get().event_generate("<Tab>")
            root.update()
            self.assertIs(dialog.window.focus_get(), focus_target(dialog.approve))
            dialog.window.focus_get().event_generate("<Return>")
            root.update()
            self.assertEqual(decision.outcome, PairingOutcome.APPROVED)

            # Escape declines.
            decision2 = PairingDecision()
            dialog2 = PairingDialog(root, prompt, decision2)
            root.update()
            dialog2.window.focus_get().event_generate("<Escape>")
            root.update()
            self.assertEqual(decision2.outcome, PairingOutcome.DECLINED)

            # Update navigation shortcut -> Back button -> Return restores focus.
            root.geometry("400x650+20000+20000")
            root.deiconify()
            root.grid_columnconfigure(0, weight=1)
            root.grid_rowconfigure(0, weight=1)
            roles = ctk.CTkTabview(root)
            roles.grid(row=0, column=0, sticky="nsew")
            roles.add("Server (Host)")
            roles.add("Client")
            with patch("app.update_panel.check_for_updates", return_value=None), \
                    patch("app.update_panel.can_install", return_value=False):
                nav = UpdateNavigation(root, roles, on_close=lambda: None)
            root.update()
            focus_target(nav.button).focus_set()
            root.update()
            nav.button._canvas.event_generate("<Return>")
            root.update()
            self.assertTrue(nav.page.winfo_ismapped())
            self.assertIs(root.focus_get(), focus_target(nav.back))
            nav.back.event_generate("<Return>")
            root.update()
            self.assertFalse(nav.page.winfo_ismapped())
            self.assertIs(root.focus_get(), focus_target(nav.button))
            nav.panel.destroy()
            nav.page.destroy()
            nav.destroy()
            roles.destroy()
        finally:
            for token in root.tk.call("after", "info"):
                try:
                    root.tk.call("after", "cancel", token)
                except Exception:
                    pass
            root.destroy()
