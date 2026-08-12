import unittest

from app.file_transfer.status import TransferPhase, TransferStatus
from app.file_transfer.toast import TransferToast, toast_view


class TransferToastViewTests(unittest.TestCase):
    def test_cancel_hides_immediately_before_any_scheduled_status_refresh(self):
        events = []
        toast = TransferToast.__new__(TransferToast)
        toast.job_id = "job"
        toast._dismissed_job_id = None
        toast._hide = lambda: events.append("hidden")

        def cancel(job_id):
            events.append(("cancel", job_id, toast._dismissed_job_id))
            return True

        toast.on_cancel = cancel
        toast._cancel()

        self.assertEqual(
            events,
            [("cancel", "job", "job"), "hidden"],
        )

    def test_scheduled_refresh_cannot_reshow_a_cancelled_job(self):
        class Root:
            def after_cancel(self, timer):
                return None

        class Widget:
            def configure(self, **kwargs):
                return None

            def start(self):
                return None

            def stop(self):
                return None

            def set(self, value):
                return None

        class Window(Widget):
            def __init__(self):
                self.shown = 0

            def geometry(self, value):
                return None

            def deiconify(self):
                self.shown += 1

            def update_idletasks(self):
                return None

            def winfo_id(self):
                return 1

            def lift(self):
                return None

        toast = TransferToast.__new__(TransferToast)
        toast.root = Root()
        toast._hide_after = None
        toast._dismissed_job_id = "job"
        toast.window = Window()
        toast.title = Widget()
        toast.progress = Widget()
        toast.details = Widget()
        toast.cancel = Widget()
        status = TransferStatus(
            "job", TransferPhase.CANCELLING, "file.bin", 0, 100
        )

        toast.show(status)

        self.assertEqual(toast.window.shown, 0)

    def test_terminal_hide_is_scheduled_even_when_positioning_fails_later(self):
        callbacks = []

        class Root:
            def after(self, delay, callback):
                callbacks.append((delay, callback))
                return "timer"

        toast = TransferToast.__new__(TransferToast)
        toast.root = Root()
        toast._hide_after = None

        toast._schedule_hide(0)

        self.assertEqual(callbacks[0][0], 0)
        self.assertEqual(toast._hide_after, "timer")

    def test_full_explorer_consumption_is_described_as_copy_complete(self):
        status = TransferStatus("job", TransferPhase.COMPLETED, "large-file.bin", 100, 100)

        view = toast_view(status)

        self.assertEqual(view.title, "Copy complete")
        self.assertIn("100 B / 100 B", view.details)
        self.assertNotIn("prompt", view.details)
        self.assertEqual(view.hide_after_ms, 3000)

    def test_waiting_for_explorer_shows_no_network_numbers(self):
        status = TransferStatus(
            "job", TransferPhase.WAITING_FOR_EXPLORER, "file.bin",
            64 * 1024 * 1024, 64 * 1024 * 1024, 10 * 1024 * 1024,
        )
        view = toast_view(status)
        self.assertEqual(view.title, "Waiting for Windows Explorer")
        self.assertEqual(view.details, "Choose any Windows file prompt to continue")

    def test_every_terminal_state_has_a_hide_deadline(self):
        for phase in (TransferPhase.COMPLETED, TransferPhase.FAILED, TransferPhase.CANCELLED):
            with self.subTest(phase=phase):
                status = TransferStatus("job", phase, "file.bin", 50, 100)
                self.assertIsNotNone(toast_view(status).hide_after_ms)

    def test_explorer_timeout_uses_safe_text_and_hides_after_three_seconds(self):
        status = TransferStatus(
            "job",
            TransferPhase.FAILED,
            r"C:\Users\person\private\private-file-name.bin",
            0,
            100,
            error_code="ExplorerStartTimeout",
        )

        view = toast_view(status)

        self.assertEqual(
            view.details,
            "private-file-name.bin · Windows Explorer did not accept the paste.",
        )
        self.assertEqual(view.hide_after_ms, 3000)
        self.assertNotIn(r"C:\Users\person\private", view.details)

    def test_network_failure_includes_only_the_failed_file_name(self):
        status = TransferStatus(
            "job",
            TransferPhase.FAILED,
            "/home/person/private/archive.zip",
            0,
            100,
            error_code="OSError",
        )

        view = toast_view(status)

        self.assertEqual(
            view.details,
            "archive.zip · Conduit could not finish the network transfer.",
        )
        self.assertNotIn("/home/person/private", view.details)

    def test_confirmed_cancellation_hides_immediately(self):
        status = TransferStatus("job", TransferPhase.CANCELLED, "file.bin", 50, 100)
        self.assertEqual(toast_view(status).hide_after_ms, 0)

    def test_long_private_label_is_not_rendered_in_compact_title(self):
        status = TransferStatus("job", TransferPhase.TRANSFERRING, "x" * 500, 50, 100, 25)

        view = toast_view(status)

        self.assertEqual(view.title, "Network transfer")
        self.assertNotIn("x", view.title)
        self.assertLessEqual(len(view.details), 80)


if __name__ == "__main__":
    unittest.main()
