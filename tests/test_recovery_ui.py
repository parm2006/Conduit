import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from app.gui import ConduitGUI
from app.file_transfer.controller import TransferController, TransferCancelled
from app.file_transfer.status import TransferPhase
from app.file_transfer.sender import TransferSender, DestinationPasteError


class RecoveryUITests(unittest.TestCase):
    def test_status_callback_never_calls_tk_from_worker(self):
        gui = ConduitGUI.__new__(ConduitGUI)
        gui.after = Mock(side_effect=AssertionError('worker entered Tk'))
        status = SimpleNamespace(job_id='a')
        errors = []
        def notify():
            try:
                gui._on_transfer_status(status)
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=notify)
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertIs(gui._transfer_status_queue.get_nowait(), status)
        gui.after.assert_not_called()

    def test_status_drain_coalesces_on_gui_thread_and_stops_after_close(self):
        gui = ConduitGUI.__new__(ConduitGUI)
        gui.after = Mock()
        gui.transfer_toast = SimpleNamespace(show=Mock())
        old = SimpleNamespace(job_id='a', phase='transferring')
        final = SimpleNamespace(job_id='a', phase='failed')
        gui._on_transfer_status(old)
        gui._on_transfer_status(final)
        gui._drain_transfer_status()
        gui.transfer_toast.show.assert_called_once_with(final)
        gui.after.assert_called_once()
        gui._close_started = True
        gui.after.reset_mock()
        gui._on_transfer_status(old)
        gui._drain_transfer_status()
        gui.after.assert_not_called()

    def test_cancel_returns_before_slow_network_callback(self):
        gui = ConduitGUI.__new__(ConduitGUI)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def cancel(job_id):
            entered.set()
            release.wait(2)
            finished.set()
        gui.server = SimpleNamespace(transfer_controller=SimpleNamespace(status=lambda job: True), cancel_transfer=cancel)
        gui.client = None
        try:
            self.assertTrue(gui._cancel_transfer('a' * 32))
            self.assertTrue(entered.wait(1))
            self.assertFalse(finished.is_set())
        finally:
            release.set()
        self.assertTrue(finished.wait(1))

    def test_terminal_destination_stops_sender_ack_wait(self):
        controller = TransferController()
        lane = SimpleNamespace(supports_chunk_ack=True, register_callback=lambda *args: None)
        sender = TransferSender(lane, controller=controller)
        job_id = 'a' * 32
        for phase, error in ((TransferPhase.FAILED, DestinationPasteError), (TransferPhase.CANCELLED, TransferCancelled)):
            fresh_id = job_id if phase is TransferPhase.FAILED else 'b' * 32
            controller.update(fresh_id, phase, 'file', 0, 10, error_code='TransferStalledTimeout')
            with self.assertRaises(error):
                sender._check_cancelled(fresh_id)
