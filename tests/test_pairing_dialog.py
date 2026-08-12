import threading
import unittest

import customtkinter as ctk

from app.crypto import pairing_code_from_fingerprint
from app.gui import ConduitGUI
from app.network import PairingTimeout
from app.pairing_dialog import (
    PairingApprovalController, PairingDecision, PairingDialog, PairingOutcome,
    PairingPrompt, PAIRING_CODE_COLOR, enable_dark_title_bar,
)


class Peer:
    canonical = "192.0.2.10:5000"


class PairingDecisionTests(unittest.TestCase):
    def test_first_terminal_outcome_wins(self):
        decision = PairingDecision()

        self.assertTrue(decision.complete(PairingOutcome.APPROVED))
        self.assertFalse(decision.complete(PairingOutcome.DECLINED))

        self.assertEqual(decision.wait(0), PairingOutcome.APPROVED)

    def test_wait_timeout_is_terminal_and_rejects_late_approval(self):
        decision = PairingDecision()

        self.assertEqual(decision.wait(0.001), PairingOutcome.TIMED_OUT)
        self.assertFalse(decision.complete(PairingOutcome.APPROVED))
        self.assertEqual(decision.outcome, PairingOutcome.TIMED_OUT)

    def test_application_close_releases_a_waiter(self):
        decision = PairingDecision()
        result = []
        waiter = threading.Thread(target=lambda: result.append(decision.wait(1)))
        waiter.start()

        self.assertTrue(decision.complete(PairingOutcome.CLOSED))
        waiter.join(0.5)

        self.assertFalse(waiter.is_alive())
        self.assertEqual(result, [PairingOutcome.CLOSED])


class PairingPromptTests(unittest.TestCase):
    def test_comparison_code_is_always_white(self):
        self.assertEqual(PAIRING_CODE_COLOR, "white")

    def test_prompt_contains_all_comparison_data_and_actions(self):
        fingerprint = "ab" * 32

        prompt = PairingPrompt.from_peer(fingerprint, Peer())

        self.assertEqual(prompt.code, pairing_code_from_fingerprint(fingerprint))
        self.assertEqual(prompt.server, Peer.canonical)
        self.assertEqual(prompt.fingerprint, fingerprint)
        self.assertIn("identical", prompt.instruction.lower())
        self.assertEqual(prompt.approve_label, "Codes match")
        self.assertEqual(prompt.decline_label, "Decline")

    def test_dark_title_bar_targets_the_native_top_level_window(self):
        calls = []

        class Window:
            def winfo_id(self):
                return 7

        class User32:
            def GetParent(self, handle):
                self.handle = handle
                return 9

        class DwmApi:
            def DwmSetWindowAttribute(self, handle, attribute, value, size):
                calls.append((handle, attribute, size))
                return 0

        self.assertTrue(
            enable_dark_title_bar(Window(), dwmapi=DwmApi(), user32=User32())
        )
        self.assertEqual(calls[0][0:2], (9, 20))

    def test_centering_repositions_without_rescaling_window_dimensions(self):
        geometry_calls = []

        class Root:
            winfo_x = lambda self: 300
            winfo_y = lambda self: 80
            winfo_width = lambda self: 330
            winfo_height = lambda self: 560

        class Window:
            winfo_width = lambda self: 650
            winfo_reqwidth = lambda self: 650
            winfo_height = lambda self: 450
            winfo_reqheight = lambda self: 450
            winfo_screenwidth = lambda self: 1920
            winfo_screenheight = lambda self: 1080

            def geometry(self, value):
                geometry_calls.append(value)

        dialog = PairingDialog.__new__(PairingDialog)
        dialog.root = Root()
        dialog.window = Window()

        dialog._center_over_root()

        self.assertEqual(geometry_calls, ["+140+135"])

    def test_resizing_converts_physical_width_before_setting_wraplength(self):
        configured = []

        class Window:
            def _get_window_scaling(self):
                return 1.5

        class Instruction:
            def configure(self, **kwargs):
                configured.append(kwargs)

        dialog = PairingDialog.__new__(PairingDialog)
        dialog.window = Window()
        dialog.instruction = Instruction()
        event = type("Event", (), {"widget": dialog.window, "width": 540})()

        dialog._resize_content(event)

        self.assertEqual(configured, [{"wraplength": 320}])


class FakeRoot:
    def __init__(self):
        self.callbacks = []
        self.closed = False

    def after(self, delay, callback):
        if self.closed:
            raise RuntimeError("root is already destroyed")
        self.callbacks.append(callback)

    def run_next(self):
        self.callbacks.pop(0)()

    def run_all(self):
        while self.callbacks:
            self.run_next()


class FakeDialog:
    def __init__(self, root, prompt, decision):
        self.root = root
        self.prompt = prompt
        self.decision = decision
        self.close_count = 0

    def close(self):
        self.close_count += 1


class PairingApprovalControllerTests(unittest.TestCase):
    def make_controller(self, timeout=1):
        root = FakeRoot()
        dialogs = []

        def factory(parent, prompt, decision):
            dialog = FakeDialog(parent, prompt, decision)
            dialogs.append(dialog)
            return dialog

        return root, dialogs, PairingApprovalController(
            root, dialog_factory=factory, timeout=timeout,
        )

    def request_in_thread(self, controller):
        result = []
        errors = []

        def request():
            try:
                result.append(controller.request("ab" * 32, Peer()))
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=request)
        worker.start()
        return worker, result, errors

    def test_approval_and_decline_map_to_the_network_bool_contract(self):
        for outcome, expected in (
            (PairingOutcome.APPROVED, True),
            (PairingOutcome.DECLINED, False),
        ):
            with self.subTest(outcome=outcome):
                root, dialogs, controller = self.make_controller()
                worker, result, errors = self.request_in_thread(controller)
                root.run_next()
                dialogs[0].decision.complete(outcome)
                worker.join(0.5)

                self.assertFalse(worker.is_alive())
                self.assertEqual(result, [expected])
                self.assertEqual(errors, [])

    def test_timeout_closes_the_dialog_and_raises_a_typed_error(self):
        root, dialogs, controller = self.make_controller(timeout=0.01)
        worker, result, errors = self.request_in_thread(controller)
        root.run_next()
        worker.join(0.5)
        root.run_next()

        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PairingTimeout)
        self.assertIn("compare", str(errors[0]))
        self.assertEqual(dialogs[0].close_count, 1)

    def test_shutdown_closes_the_dialog_and_releases_the_waiter(self):
        root, dialogs, controller = self.make_controller()
        worker, result, errors = self.request_in_thread(controller)
        root.run_next()

        controller.shutdown()
        worker.join(0.5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PairingTimeout)
        self.assertIn("closed", str(errors[0]))
        self.assertEqual(dialogs[0].close_count, 1)

    def test_root_close_race_preserves_the_typed_closed_outcome(self):
        root, dialogs, controller = self.make_controller()
        worker, result, errors = self.request_in_thread(controller)
        root.run_next()

        root.closed = True
        controller.shutdown()
        worker.join(0.5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PairingTimeout)
        self.assertIn("closed", str(errors[0]))
        self.assertEqual(dialogs[0].close_count, 1)

    def test_status_reports_waiting_and_successful_approval(self):
        root = FakeRoot()
        dialogs = []
        statuses = []

        def factory(parent, prompt, decision):
            dialog = FakeDialog(parent, prompt, decision)
            dialogs.append(dialog)
            return dialog

        controller = PairingApprovalController(
            root, dialog_factory=factory, timeout=1, on_status=statuses.append,
        )
        worker, result, errors = self.request_in_thread(controller)
        root.run_next()
        dialogs[0].decision.complete(PairingOutcome.APPROVED)
        worker.join(0.5)
        root.run_all()

        self.assertEqual(result, [True])
        self.assertEqual(errors, [])
        self.assertEqual(
            statuses,
            [
                "Status: Waiting for pairing approval — compare the server code.",
                "Status: Pairing approved; authenticating…",
            ],
        )


class ConduitPairingIntegrationTests(unittest.TestCase):
    def test_gui_delegates_fingerprint_approval_to_the_controller(self):
        calls = []

        class Controller:
            def request(self, fingerprint, peer):
                calls.append((fingerprint, peer))
                return True

        gui = ConduitGUI.__new__(ConduitGUI)
        gui.pairing_approval = Controller()
        peer = Peer()

        self.assertTrue(gui._approve_fingerprint("ab" * 32, peer))
        self.assertEqual(calls, [("ab" * 32, peer)])

    def test_gui_shutdown_releases_pairing_before_destroying_root(self):
        events = []

        class Controller:
            def shutdown(self):
                events.append("pairing shutdown")

        gui = ConduitGUI.__new__(ConduitGUI)
        gui.overlay = None
        gui.server = None
        gui.client = None
        gui.pairing_approval = Controller()
        gui.destroy = lambda: events.append("destroy")

        gui.on_close()

        self.assertEqual(events, ["pairing shutdown", "destroy"])


class PairingDialogRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.root.geometry("330x560+300+80")
        self.root.deiconify()
        self.root.update()

    def tearDown(self):
        grab = self.root.grab_current()
        if grab is not None:
            grab.grab_release()
        for child in self.root.winfo_children():
            if isinstance(child, ctk.CTkToplevel):
                child.destroy()
        self.root.update()

    def make_dialog(self, root):
        decision = PairingDecision()
        prompt = PairingPrompt.from_peer("ab" * 32, Peer())
        dialog = PairingDialog(root, prompt, decision)
        root.update()
        return dialog, decision

    def test_real_modal_is_centered_grabbed_selectable_and_fits_actions(self):
        root = self.root
        dialog = None
        try:
            dialog, decision = self.make_dialog(root)

            self.assertTrue(dialog.window.winfo_ismapped())
            self.assertIs(dialog.window.grab_current(), dialog.window)
            self.assertEqual(dialog.details._textbox.cget("state"), "disabled")
            self.assertEqual(dialog.details._textbox.cget("wrap"), "word")
            self.assertIn(Peer.canonical, dialog.details.get("1.0", "end"))
            self.assertIn("ab" * 32, dialog.details.get("1.0", "end"))

            text = dialog.details._textbox
            text.tag_add("sel", "1.0", "1.6")
            self.assertTrue(text.tag_ranges("sel"))

            modal_center_x = dialog.window.winfo_rootx() + dialog.window.winfo_width() // 2
            modal_center_y = dialog.window.winfo_rooty() + dialog.window.winfo_height() // 2
            root_center_x = root.winfo_rootx() + root.winfo_width() // 2
            root_center_y = root.winfo_rooty() + root.winfo_height() // 2
            self.assertLessEqual(abs(modal_center_x - root_center_x), 12)
            self.assertLessEqual(abs(modal_center_y - root_center_y), 12)

            dialog.window.geometry("360x320")
            root.update()
            self.assertLessEqual(dialog.instruction.cget("wraplength"), 320)
            modal_right = dialog.window.winfo_rootx() + dialog.window.winfo_width()
            modal_bottom = dialog.window.winfo_rooty() + dialog.window.winfo_height()
            for button in (dialog.approve, dialog.decline):
                self.assertGreaterEqual(button.winfo_rootx(), dialog.window.winfo_rootx())
                self.assertGreaterEqual(button.winfo_rooty(), dialog.window.winfo_rooty())
                self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), modal_right)
                self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), modal_bottom)

            dialog._approve()
            root.update()
            self.assertEqual(decision.outcome, PairingOutcome.APPROVED)
            self.assertIsNone(root.grab_current())
        finally:
            if dialog is not None:
                dialog.close()

    def test_idle_window_manager_adjustment_is_recentered(self):
        dialog = None
        try:
            decision = PairingDecision()
            prompt = PairingPrompt.from_peer("ab" * 32, Peer())
            dialog = PairingDialog(self.root, prompt, decision)

            # Simulate Windows moving the newly decorated native window before
            # Tk processes its first idle cycle.
            dialog.window.geometry("520x360+0+0")
            self.root.update()

            modal_center_x = (
                dialog.window.winfo_rootx() + dialog.window.winfo_width() // 2
            )
            root_center_x = self.root.winfo_rootx() + self.root.winfo_width() // 2
            self.assertLessEqual(abs(modal_center_x - root_center_x), 12)
        finally:
            if dialog is not None:
                dialog.close()

    def test_escape_and_title_bar_close_decline_and_release_the_grab(self):
        root = self.root
        for close_kind in ("escape", "title bar"):
            with self.subTest(close_kind=close_kind):
                dialog = None
                try:
                    dialog, decision = self.make_dialog(root)
                    if close_kind == "escape":
                        dialog.window.event_generate("<Escape>")
                    else:
                        root.tk.call(dialog.window.protocol("WM_DELETE_WINDOW"))
                    root.update()

                    self.assertEqual(decision.outcome, PairingOutcome.DECLINED)
                    self.assertIsNone(root.grab_current())
                finally:
                    if dialog is not None:
                        dialog.close()

    def test_actual_modal_timeout_and_application_shutdown_release_waiters(self):
        root = self.root
        for ending in ("timeout", "shutdown"):
            with self.subTest(ending=ending):
                dialogs = []

                def factory(parent, prompt, decision):
                    dialog = PairingDialog(parent, prompt, decision)
                    dialogs.append(dialog)
                    return dialog

                controller = PairingApprovalController(
                    root,
                    dialog_factory=factory,
                    timeout=0.03 if ending == "timeout" else 1,
                )
                result = []
                errors = []

                def request():
                    try:
                        result.append(controller.request("ab" * 32, Peer()))
                    except Exception as error:
                        errors.append(error)

                worker = threading.Thread(target=request)
                state = {"shutdown": False, "poll_job": None}

                def poll():
                    if dialogs and ending == "shutdown" and not state["shutdown"]:
                        state["shutdown"] = True
                        controller.shutdown()
                    if worker.is_alive():
                        state["poll_job"] = root.after(5, poll)
                    else:
                        state["poll_job"] = None
                        root.quit()

                root.after(0, worker.start)
                state["poll_job"] = root.after(5, poll)
                fail_safe = root.after(1000, root.quit)
                try:
                    root.mainloop()
                    root.after_cancel(fail_safe)
                    worker.join(0.1)
                    root.update()

                    self.assertTrue(dialogs, "real modal did not open")
                    self.assertFalse(worker.is_alive(), "approval worker did not finish")
                    self.assertEqual(result, [])
                    self.assertEqual(len(errors), 1)
                    self.assertIsInstance(errors[0], PairingTimeout)
                    self.assertIn(
                        "timed out" if ending == "timeout" else "closed",
                        str(errors[0]),
                    )
                    self.assertIsNone(root.grab_current())
                finally:
                    if state["poll_job"] is not None:
                        root.after_cancel(state["poll_job"])
                    controller.shutdown()


if __name__ == "__main__":
    unittest.main()
